"""
@module storage.repositories.tasks
@purpose High-level CRUD operations for the TaskModel entity.
@owns task creation, status updates, dependency management (SQL layer)
@does_not_own session lifecycle management, business logic orchestration
@key_exports TaskRepository
"""

from typing import List, Optional
import time
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from uuid import uuid4
from strata.storage.models import TaskModel, TaskState
from strata.core.lanes import (
    default_work_pool_for_lane,
    infer_lane_from_session_id,
    infer_lane_from_task,
    infer_work_pool_from_task,
    normalize_lane,
    normalize_work_pool,
)
from strata.storage.sqlite_write import flush_with_write_lock, is_sqlite_locked_error


def _normalize_task_provenance(provenance: dict | None) -> dict:
    payload = dict(provenance or {})
    payload.setdefault("recorded_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    payload["authority_kind"] = str(payload.get("authority_kind") or "unspecified").strip() or "unspecified"
    payload["authority_ref"] = str(payload.get("authority_ref") or "").strip()
    derived_from = payload.get("derived_from")
    if isinstance(derived_from, list):
        payload["derived_from"] = [str(item).strip() for item in derived_from if str(item).strip()]
    elif derived_from:
        text = str(derived_from).strip()
        payload["derived_from"] = [text] if text else []
    else:
        payload["derived_from"] = []
    governing = payload.get("governing_spec_refs")
    if isinstance(governing, list):
        payload["governing_spec_refs"] = [str(item).strip() for item in governing if str(item).strip()]
    elif governing:
        text = str(governing).strip()
        payload["governing_spec_refs"] = [text] if text else []
    else:
        payload["governing_spec_refs"] = []
    payload["source_kind"] = str(payload.get("source_kind") or "").strip()
    payload["source_actor"] = str(payload.get("source_actor") or "system").strip() or "system"
    payload["note"] = str(payload.get("note") or "").strip()
    return payload


def _default_task_provenance(*, provenance: dict | None, parent_task_id: str | None, constraints: dict) -> dict:
    payload = _normalize_task_provenance(provenance)
    if payload["authority_kind"] != "unspecified" or payload["authority_ref"]:
        return payload

    derived_from = list(payload.get("derived_from") or [])
    source_task_id = str(constraints.get("source_task_id") or "").strip()
    if source_task_id:
        derived_from.append(f"task:{source_task_id}")
    if parent_task_id:
        derived_from.append(f"task:{parent_task_id}")
    payload["derived_from"] = [item for item in dict.fromkeys(derived_from) if item]

    if source_task_id or parent_task_id:
        payload["authority_kind"] = "derived_task"
        payload["authority_ref"] = source_task_id or parent_task_id or ""
        payload["source_kind"] = payload.get("source_kind") or "task_derivation"
        if not payload["note"]:
            payload["note"] = "Derived task created from existing task state."
    return payload


class TaskRepository:
    """
    @summary Manages structured task storage and retrieval using SQLAlchemy.
    @inputs session: SQLAlchemy Session
    @outputs side-effect driven (DB mutations) or TaskModel objects
    @side_effects writes to 'tasks' and 'task_dependencies' tables
    @depends storage.models.TaskModel
    @invariants does not commit the session (left to orchestrator)
    """
    def __init__(self, session: Session):
        """
        @summary Initialize the TaskRepository.
        @inputs session: active DB session
        @outputs none
        """
        self.session = session

    def create(self, *, flush: bool = True, **kwargs) -> TaskModel:
        """
        @summary Instantiate and persist a new TaskModel.
        @inputs dictionary of task attributes
        @outputs the created TaskModel object
        @side_effects adds object to session
        """
        constraints = dict(kwargs.get("constraints") or {})
        explicit_lane = normalize_lane(constraints.get("lane"))
        explicit_work_pool = normalize_work_pool(constraints.get("work_pool")) or normalize_work_pool(constraints.get("execution_profile"))
        parent_task = None
        if not explicit_lane and kwargs.get("parent_task_id"):
            parent_task = self.get_by_id(kwargs["parent_task_id"])
            explicit_lane = infer_lane_from_task(parent_task) if parent_task else None
        if not explicit_work_pool and kwargs.get("parent_task_id"):
            parent_task = parent_task or self.get_by_id(kwargs["parent_task_id"])
            explicit_work_pool = infer_work_pool_from_task(parent_task) if parent_task else None
        if not explicit_lane:
            explicit_lane = infer_lane_from_session_id(kwargs.get("session_id"))
        if explicit_lane:
            constraints["lane"] = explicit_lane
        if explicit_work_pool:
            constraints["work_pool"] = explicit_work_pool
            constraints.setdefault("execution_profile", explicit_work_pool)
        elif explicit_lane:
            inferred_pool = default_work_pool_for_lane(explicit_lane)
            constraints.setdefault("work_pool", inferred_pool)
            constraints.setdefault("execution_profile", inferred_pool)
        constraints["provenance"] = _default_task_provenance(
            provenance=constraints.get("provenance"),
            parent_task_id=kwargs.get("parent_task_id"),
            constraints=constraints,
        )
        kwargs["constraints"] = constraints
        kwargs.setdefault("task_id", str(uuid4()))
        bind = getattr(self.session, "bind", None)
        sqlite_enabled = str(getattr(getattr(bind, "url", None), "drivername", "") or "").startswith("sqlite")
        retries = 8 if sqlite_enabled and flush else 1
        for index in range(retries):
            task = TaskModel(**kwargs)
            self.session.add(task)
            try:
                if flush:
                    flush_with_write_lock(self.session, enabled=sqlite_enabled)  # Ensure ID is generated
                return task
            except OperationalError as exc:
                self.session.rollback()
                if not sqlite_enabled or not is_sqlite_locked_error(exc) or index >= retries - 1:
                    raise
                time.sleep(0.15 * (index + 1))
        raise RuntimeError("Task creation retries exhausted.")

    def get_by_id(self, task_id: str) -> Optional[TaskModel]:
        """
        @summary Retrieve a task by its primary key.
        @inputs task_id: string UUID
        @outputs TaskModel or None
        """
        return self.session.get(TaskModel, task_id)

    def update_state(self, task_id: str, state: TaskState):
        """
        @summary Transition a task to a new state.
        @inputs task_id, state: TaskState enum
        @outputs none
        @side_effects updates database row
        """
        task = self.get_by_id(task_id)
        if task:
            task.state = state

    def add_dependency(self, task_id: str, depends_on_id: str):
        """
        @summary Add a dependency between two tasks.
        @inputs task_id (the dependent task), depends_on_id (the task being depended on)
        """
        task = self.get_by_id(task_id)
        dep = self.get_by_id(depends_on_id)
        if task and dep:
            if dep not in task.dependencies:
                task.dependencies.append(dep)
