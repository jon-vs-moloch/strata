"""
@module procedures.registry
@purpose Durable procedure definitions and queue helpers.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional

from strata.core.lanes import canonical_session_id_for_lane, normalize_lane
from strata.storage.models import TaskModel, TaskState, TaskType


PROCEDURE_REGISTRY_KEY = "procedure_registry"
DEFAULT_PROCEDURE_LANE = "agent"
ONBOARDING_PROCEDURE_ID = "operator_onboarding"
DEFAULT_PROCEDURES: Dict[str, Dict[str, Any]] = {
    ONBOARDING_PROCEDURE_ID: {
        "procedure_id": ONBOARDING_PROCEDURE_ID,
        "title": "Operator Onboarding",
        "summary": "Establish the agent's identity, operator defaults, and baseline trust posture.",
        "repeatable": True,
        "target_lane": DEFAULT_PROCEDURE_LANE,
        "task_type": "RESEARCH",
        "instructions": (
            "Run the onboarding checklist carefully. Treat this as a rich, verifiable procedure rather than a generic task. "
            "If an item cannot be completed directly, surface it as an explicit pending question or attention item instead of silently skipping it."
        ),
        "checklist": [
            {
                "id": "agent_name",
                "title": "Choose or confirm the agent name",
                "verification": "The session metadata must contain participant_names for user, agent, trainer, and system.",
            },
            {
                "id": "identity_language",
                "title": "Confirm the preferred role language",
                "verification": "The system should know it has an operator-facing agent and a trainer supervisor.",
            },
            {
                "id": "verification_posture",
                "title": "Establish the starting verification posture",
                "verification": "The system should know whether to begin with aggressive verification while trust anneals.",
            },
            {
                "id": "runtime_posture",
                "title": "Confirm local/cloud and quiet-hardware preferences",
                "verification": "The operator's comfort constraints should be written into durable settings or queued as pending clarification.",
            },
            {
                "id": "open_questions",
                "title": "Surface unresolved onboarding items",
                "verification": "Every unresolved onboarding issue should become either a queued user question or a durable attention item.",
            },
        ],
        "success_criteria": {
            "required_checklist_ids": [
                "agent_name",
                "identity_language",
                "verification_posture",
                "runtime_posture",
                "open_questions",
            ],
            "deliverables": [
                "A concise onboarding summary",
                "Updated participant naming or a queued naming question",
                "Durable record of unresolved onboarding items",
            ],
        },
    }
}


def _default_registry() -> Dict[str, Any]:
    return {"procedures": deepcopy(DEFAULT_PROCEDURES)}


def _normalize_procedure(definition: Dict[str, Any]) -> Dict[str, Any]:
    procedure_id = str(definition.get("procedure_id") or "").strip()
    if not procedure_id:
        raise ValueError("procedure_id is required")
    normalized = deepcopy(definition)
    normalized["procedure_id"] = procedure_id
    normalized["title"] = str(normalized.get("title") or procedure_id.replace("_", " ").title()).strip()
    normalized["summary"] = str(normalized.get("summary") or "").strip()
    normalized["repeatable"] = bool(normalized.get("repeatable", True))
    normalized["target_lane"] = normalize_lane(normalized.get("target_lane")) or DEFAULT_PROCEDURE_LANE
    normalized["task_type"] = str(normalized.get("task_type") or "RESEARCH").strip().upper()
    normalized["instructions"] = str(normalized.get("instructions") or "").strip()
    normalized["checklist"] = [
        {
            "id": str(item.get("id") or f"step_{index + 1}").strip(),
            "title": str(item.get("title") or "").strip(),
            "verification": str(item.get("verification") or "").strip(),
        }
        for index, item in enumerate(normalized.get("checklist") or [])
        if str(item.get("title") or "").strip()
    ]
    normalized["success_criteria"] = dict(normalized.get("success_criteria") or {})
    return normalized


def get_procedure_registry(storage) -> Dict[str, Any]:
    registry = storage.parameters.peek_parameter(PROCEDURE_REGISTRY_KEY, default_value=_default_registry()) or _default_registry()
    procedures = {
        procedure_id: _normalize_procedure(definition)
        for procedure_id, definition in dict(registry.get("procedures") or {}).items()
    }
    if not procedures:
        procedures = deepcopy(DEFAULT_PROCEDURES)
    return {"procedures": procedures}


def list_procedures(storage) -> List[Dict[str, Any]]:
    registry = get_procedure_registry(storage)
    return sorted(registry["procedures"].values(), key=lambda item: item["title"].lower())


def get_procedure(storage, procedure_id: str) -> Dict[str, Any]:
    normalized_id = str(procedure_id or "").strip()
    registry = get_procedure_registry(storage)
    procedure = registry["procedures"].get(normalized_id)
    if not procedure:
        raise KeyError(f"Unknown procedure: {normalized_id}")
    return procedure


def save_procedure(storage, definition: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _normalize_procedure(definition)
    registry = get_procedure_registry(storage)
    procedures = dict(registry.get("procedures") or {})
    procedures[normalized["procedure_id"]] = normalized
    storage.parameters.set_parameter(
        PROCEDURE_REGISTRY_KEY,
        {"procedures": procedures},
        description="Durable registry of reusable procedures such as onboarding or maintenance checklists.",
    )
    return normalized


def queue_procedure(storage, worker, *, procedure_id: str, session_id: Optional[str] = None, lane: Optional[str] = None):
    procedure = get_procedure(storage, procedure_id)
    target_lane = normalize_lane(lane) or procedure.get("target_lane") or DEFAULT_PROCEDURE_LANE
    resolved_session_id = canonical_session_id_for_lane(target_lane, session_id or "default")
    checklist = list(procedure.get("checklist") or [])
    checklist_lines = "\n".join(
        f"- [{item['id']}] {item['title']} :: verify by {item['verification']}"
        for item in checklist
    )
    description = (
        f"{procedure.get('instructions')}\n\n"
        f"Checklist:\n{checklist_lines}\n\n"
        "When you finish or get blocked, report checklist status explicitly."
    ).strip()
    task = storage.tasks.create(
        title=f"Procedure: {procedure.get('title')}",
        description=description,
        session_id=resolved_session_id,
        state=TaskState.PENDING,
        constraints={
            "lane": target_lane,
            "procedure_id": procedure["procedure_id"],
            "procedure_title": procedure.get("title"),
            "procedure_summary": procedure.get("summary"),
            "procedure_checklist": checklist,
            "procedure_repeatable": bool(procedure.get("repeatable", True)),
            "verification_required": True,
        },
        success_criteria=procedure.get("success_criteria") or {},
    )
    try:
        task.type = TaskType[str(procedure.get("task_type") or "RESEARCH").upper()]
    except Exception:
        task.type = TaskType.RESEARCH
    storage.commit()
    return task


def list_procedure_tasks(storage, procedure_id: str) -> List[TaskModel]:
    normalized_id = str(procedure_id or "").strip()
    if not normalized_id:
        return []
    procedure = get_procedure(storage, normalized_id)
    expected_title = f"Procedure: {procedure.get('title')}"
    matches: List[TaskModel] = []
    for task in storage.session.query(TaskModel).all():
        constraints = dict(getattr(task, "constraints", {}) or {})
        if constraints.get("procedure_id") == normalized_id or str(getattr(task, "title", "") or "").strip() == expected_title:
            matches.append(task)
    matches.sort(key=lambda item: getattr(item, "created_at", None) or 0)
    return matches


def get_procedure_status(storage, procedure_id: str) -> Dict[str, Any]:
    tasks = list_procedure_tasks(storage, procedure_id)
    active_states = {TaskState.PENDING, TaskState.WORKING, TaskState.BLOCKED}
    latest = tasks[-1] if tasks else None
    has_active = any(getattr(task, "state", None) in active_states for task in tasks)
    has_completed = any(getattr(task, "state", None) == TaskState.COMPLETE for task in tasks)
    return {
        "procedure_id": procedure_id,
        "has_any": bool(tasks),
        "has_active": has_active,
        "has_completed": has_completed,
        "needs_queue": not has_active and not has_completed,
        "task_ids": [str(task.task_id) for task in tasks],
        "latest_task_id": str(latest.task_id) if latest is not None else None,
        "latest_state": getattr(getattr(latest, "state", None), "value", None) if latest is not None else None,
    }


def get_onboarding_status(storage) -> Dict[str, Any]:
    return get_procedure_status(storage, ONBOARDING_PROCEDURE_ID)


def ensure_onboarding_task(storage, worker, *, session_id: Optional[str] = None, lane: Optional[str] = None):
    status = get_onboarding_status(storage)
    if not status.get("needs_queue"):
        return None
    return queue_procedure(
        storage,
        worker,
        procedure_id=ONBOARDING_PROCEDURE_ID,
        session_id=session_id,
        lane=lane,
    )
