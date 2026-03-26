"""
@module api.main
@purpose Expose internal storage and Strata orchestrator state to the UI via REST.
@owns API routing, JSON serialization, StorageManager lifecycle, background worker
@does_not_own business logic orchestration, database schema definitions
@key_exports app

The API is part of the harness, not just a frontend convenience layer.
It exposes the system's state, controls, and telemetry so both humans and
agents can inspect what the harness is learning and how it is behaving.
"""

import logging
from datetime import timezone
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any, Optional
import asyncio
import os
from strata.storage.services.main import StorageManager
from strata.storage.models import TaskModel, TaskType, TaskState, ParameterModel
from strata.storage.retention import get_retention_policy, get_retention_runtime, run_retention_maintenance
from strata.models.adapter import ModelAdapter
from strata.orchestrator.background import BackgroundWorker
from strata.api.hotreload import HotReloader
from strata.api.chat_tools import load_dynamic_tools
from strata.api.eval_admin import register_eval_admin_routes
from strata.api.chat_task_admin import register_chat_task_routes
from strata.api.knowledge_admin import register_knowledge_admin_routes
from strata.api.retention_admin import register_retention_admin_routes
from strata.api.spec_admin import register_spec_admin_routes
from strata.api.runtime_admin import register_runtime_admin_routes
from strata.memory.semantic import SemanticMemory
from strata.orchestrator.worker.telemetry import build_telemetry_snapshot
from strata.models.providers import get_provider_telemetry_snapshot
from strata.api.experiment_runtime import (
    apply_experiment_promotion,
    build_dashboard_snapshot,
    eval_override_signature,
    generate_eval_candidate_from_tier,
    generate_tool_candidate_from_tier,
)
from strata.knowledge.pages import KnowledgePageStore, slugify_page_title
from strata.specs.bootstrap import (
    create_spec_proposal,
    ensure_spec_files,
    get_spec_proposal,
    list_spec_proposals,
    load_specs,
    resolve_spec_proposal,
    resubmit_spec_proposal_with_clarification,
)
from strata.orchestrator.user_questions import (
    enqueue_user_question,
    get_active_question,
    get_question_for_source,
    mark_question_asked,
    resolve_question,
)

logger = logging.getLogger(__name__)

# ── Module-level singletons ────────────────────────────────────────────────────
GLOBAL_SETTINGS = {
    "max_sync_tool_iterations": 3,
    "automatic_task_generation": False,
    "testing_mode": False,
    "replay_pending_tasks_on_startup": False,
}
SETTINGS_PARAMETER_KEY = "orchestrator_global_settings"
SETTINGS_PARAMETER_DESCRIPTION = (
    "Persisted API/orchestrator settings shared between the UI and the worker startup path."
)
_model = ModelAdapter()
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_hotreloader = HotReloader(_BASE_DIR)
_memory = SemanticMemory()
_worker = BackgroundWorker(
    storage_factory=StorageManager,   # each task gets a fresh session
    model_adapter=_model,
    memory=_memory,
    settings_provider=lambda: GLOBAL_SETTINGS,
)
_event_queue = asyncio.Queue()

async def _broadcast_event(data: Dict[str, Any]):
    """Push event to SSE queue for UI consumption."""
    await _event_queue.put(data)

# Register worker update listener
_worker.set_on_update(lambda tid, state: asyncio.create_task(_broadcast_event({"type": "task_update", "task_id": tid, "state": state})))

def _normalized_settings(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    normalized = dict(GLOBAL_SETTINGS)
    if payload:
        normalized.update(payload)
    return normalized


def _build_dashboard_snapshot(storage: StorageManager, limit: int = 10) -> Dict[str, Any]:
    return build_dashboard_snapshot(
        storage,
        limit=limit,
        build_telemetry_snapshot=build_telemetry_snapshot,
        get_provider_telemetry_snapshot=get_provider_telemetry_snapshot,
        get_retention_runtime=get_retention_runtime,
    )


def _apply_experiment_promotion(storage: StorageManager, candidate_change_id: str, *, force: bool = False) -> Dict[str, Any]:
    return apply_experiment_promotion(storage, candidate_change_id, force=force, model_adapter=_model)


async def _generate_eval_candidate_from_tier(proposer_tier: str, current_config: Dict[str, Any]) -> Dict[str, Any]:
    return await generate_eval_candidate_from_tier(
        proposer_tier,
        current_config,
        model_adapter_factory=ModelAdapter,
    )


async def _generate_tool_candidate_from_tier(
    proposer_tier: str,
    *,
    tool_name: str,
    task_description: str,
) -> Dict[str, Any]:
    return await generate_tool_candidate_from_tier(
        proposer_tier,
        tool_name=tool_name,
        task_description=task_description,
        model_adapter_factory=ModelAdapter,
    )

# ── App lifecycle ──────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    from strata.storage.models import Base
    from strata.storage.services.main import _engine
    Base.metadata.create_all(_engine)
    ensure_spec_files()
    storage = StorageManager()
    try:
        persisted_settings = storage.parameters.get_parameter(
            key=SETTINGS_PARAMETER_KEY,
            default_value=dict(GLOBAL_SETTINGS),
            description=SETTINGS_PARAMETER_DESCRIPTION,
        ) or {}
        GLOBAL_SETTINGS.update(_normalized_settings(persisted_settings))
        run_retention_maintenance(storage)
        storage.commit()
    finally:
        storage.close()
    await _worker.start()
    logger.info("Strata API started")
    yield
    await _worker.stop()
    logger.info("Strata API stopped")

app = FastAPI(title="Strata API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_storage():
    storage = StorageManager()
    try:
        yield storage
    finally:
        storage.close()


def _find_pending_spec_clarification(storage: StorageManager, session_id: str) -> Optional[Dict[str, Any]]:
    pending = get_active_question(storage, session_id)
    if not pending or pending.get("source_type") != "spec_clarification":
        return None
    proposal_id = str(pending.get("source_id") or "")
    if not proposal_id:
        return None
    proposal = get_spec_proposal(storage, proposal_id)
    if proposal and proposal.get("status") == "needs_clarification":
        proposal = dict(proposal)
        proposal["_pending_question"] = pending
        return proposal
    return None


def _task_is_active(task: TaskModel) -> bool:
    return task.state not in {TaskState.COMPLETE, TaskState.CANCELLED, TaskState.ABANDONED}


def _find_existing_eval_job(storage: StorageManager, job_kind: str, signature: Dict[str, Any]) -> Optional[TaskModel]:
    tasks = (
        storage.session.query(TaskModel)
        .filter(TaskModel.type == TaskType.JUDGE)
        .all()
    )
    for task in tasks:
        if not _task_is_active(task):
            continue
        eval_job = dict((task.constraints or {}).get("eval_job") or {})
        if str(eval_job.get("kind") or "") != job_kind:
            continue
        same = True
        for key, value in signature.items():
            if eval_job.get(key) != value:
                same = False
                break
        if same:
            return task
    return None


async def _enqueue_eval_job_task(
    storage: StorageManager,
    *,
    title: str,
    description: str,
    session_id: Optional[str],
    eval_job: Dict[str, Any],
    dedupe_signature: Optional[Dict[str, Any]] = None,
) -> TaskModel:
    if dedupe_signature:
        existing = _find_existing_eval_job(storage, str(eval_job.get("kind") or ""), dedupe_signature)
        if existing:
            return existing
    task = storage.tasks.create(
        title=title,
        description=description,
        session_id=session_id,
        state=TaskState.PENDING,
        constraints={"eval_job": eval_job},
    )
    task.type = TaskType.JUDGE
    storage.commit()
    await _worker.enqueue(task.task_id)
    return task


async def _queue_eval_system_job(
    storage: StorageManager,
    *,
    kind: str,
    title: str,
    description: str,
    payload: Dict[str, Any],
    session_id: Optional[str] = None,
    dedupe_signature: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if dedupe_signature:
        tasks = (
            storage.session.query(TaskModel)
            .filter(TaskModel.type == TaskType.JUDGE)
            .all()
        )
        for task in tasks:
            if not _task_is_active(task):
                continue
            system_job = dict((task.constraints or {}).get("system_job") or {})
            if str(system_job.get("kind") or "") != kind:
                continue
            existing_payload = dict(system_job.get("payload") or {})
            matches = True
            for key, value in dedupe_signature.items():
                if existing_payload.get(key) != value:
                    matches = False
                    break
            if matches:
                return {"task_id": task.task_id, "status": "already_queued", "kind": kind}
    task = storage.tasks.create(
        title=title,
        description=description,
        session_id=session_id,
        state=TaskState.PENDING,
        type=TaskType.JUDGE,
        constraints={
            "system_job": {
                "kind": kind,
                "payload": payload,
            }
        },
    )
    storage.commit()
    await _worker.enqueue(task.task_id)
    return {
        "task_id": task.task_id,
        "status": "queued",
        "kind": kind,
    }


globals().update(register_eval_admin_routes(
    app,
    get_storage=get_storage,
    model_adapter=_model,
    queue_eval_system_job=_queue_eval_system_job,
    build_dashboard_snapshot=lambda storage, limit: build_dashboard_snapshot(
        storage,
        limit=limit,
        build_telemetry_snapshot=build_telemetry_snapshot,
        get_provider_telemetry_snapshot=get_provider_telemetry_snapshot,
        get_retention_runtime=get_retention_runtime,
    ),
    apply_experiment_promotion=lambda storage, candidate_change_id, force=False: apply_experiment_promotion(
        storage,
        candidate_change_id,
        force=force,
        model_adapter=_model,
    ),
    generate_eval_candidate_from_tier=lambda proposer_tier, current_config: generate_eval_candidate_from_tier(
        proposer_tier,
        current_config,
        model_adapter_factory=ModelAdapter,
    ),
    generate_tool_candidate_from_tier=lambda proposer_tier, tool_name, task_description: generate_tool_candidate_from_tier(
        proposer_tier,
        tool_name=tool_name,
        task_description=task_description,
        model_adapter_factory=ModelAdapter,
    ),
    eval_override_signature=eval_override_signature,
    get_provider_telemetry_snapshot=get_provider_telemetry_snapshot,
))

globals().update(register_retention_admin_routes(
    app,
    get_storage=get_storage,
    get_retention_policy=get_retention_policy,
    get_retention_runtime=get_retention_runtime,
    run_retention_maintenance=run_retention_maintenance,
))

globals().update(register_spec_admin_routes(
    app,
    get_storage=get_storage,
    load_specs=load_specs,
    list_spec_proposals=list_spec_proposals,
    get_spec_proposal=get_spec_proposal,
    create_spec_proposal=create_spec_proposal,
    resolve_spec_proposal=resolve_spec_proposal,
    enqueue_user_question=enqueue_user_question,
))

globals().update(register_knowledge_admin_routes(
    app,
    get_storage=get_storage,
    base_dir=_BASE_DIR,
    knowledge_page_store_cls=KnowledgePageStore,
    slugify_page_title=slugify_page_title,
    worker=_worker,
))

globals().update(register_chat_task_routes(
    app,
    get_storage=get_storage,
    task_model_cls=TaskModel,
    task_type_cls=TaskType,
    task_state_cls=TaskState,
    model_adapter=_model,
    semantic_memory=_memory,
    worker=_worker,
    broadcast_event=_broadcast_event,
    global_settings=GLOBAL_SETTINGS,
    knowledge_page_store_cls=KnowledgePageStore,
    slugify_page_title=slugify_page_title,
    load_dynamic_tools=lambda: load_dynamic_tools(base_dir=_BASE_DIR, global_settings=GLOBAL_SETTINGS),
    load_specs=load_specs,
    create_spec_proposal=create_spec_proposal,
    resubmit_spec_proposal_with_clarification=resubmit_spec_proposal_with_clarification,
    find_pending_spec_clarification=_find_pending_spec_clarification,
    get_active_question=get_active_question,
    get_question_for_source=get_question_for_source,
    mark_question_asked=mark_question_asked,
    resolve_question=resolve_question,
))

globals().update(register_runtime_admin_routes(
    app,
    get_storage=get_storage,
    model_adapter=_model,
    global_settings=GLOBAL_SETTINGS,
    normalized_settings=_normalized_settings,
    settings_parameter_key=SETTINGS_PARAMETER_KEY,
    settings_parameter_description=SETTINGS_PARAMETER_DESCRIPTION,
    worker=_worker,
    event_queue=_event_queue,
    hotreloader=_hotreloader,
))


# ── Standard endpoints ──────────────────────────────────────────────────────────
@app.get("/admin/evals/jobs")
async def list_eval_jobs(
    active_only: bool = False,
    limit: int = 50,
    storage: StorageManager = Depends(get_storage),
):
    safe_limit = max(1, min(limit, 200))
    tasks = (
        storage.session.query(TaskModel)
        .filter(TaskModel.type == TaskType.JUDGE)
        .order_by(TaskModel.updated_at.desc())
        .all()
    )
    if active_only:
        tasks = [task for task in tasks if _task_is_active(task)]
    tasks = tasks[:safe_limit]
    jobs = []
    for task in tasks:
        constraints = dict(task.constraints or {})
        jobs.append(
            {
                "task_id": task.task_id,
                "title": task.title,
                "state": task.state.value.lower(),
                "session_id": task.session_id,
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "updated_at": task.updated_at.isoformat() if task.updated_at else None,
                "system_job": constraints.get("system_job"),
                "system_job_result": constraints.get("system_job_result"),
                "generated_reports": constraints.get("generated_reports", []),
                "associated_reports": constraints.get("associated_reports", []),
            }
        )
    return {"status": "ok", "jobs": jobs}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("strata.api.main:app", host="0.0.0.0", port=8000, reload=True)
