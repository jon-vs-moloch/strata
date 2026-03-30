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
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any, Optional
import asyncio
import os
from sqlalchemy.exc import OperationalError
from strata.storage.services.main import StorageManager
from strata.storage.models import TaskModel, TaskType, TaskState, ParameterModel, task_state_api_value
from strata.storage.retention import get_retention_policy, get_retention_runtime, run_retention_maintenance
from strata.models.adapter import ModelAdapter
from strata.models.providers import GenericOpenAICompatibleProvider
from strata.orchestrator.background import BackgroundWorker
from strata.api.hotreload import HotReloader
from strata.api.chat_tools import load_dynamic_tools
from strata.api.eval_admin import register_eval_admin_routes
from strata.api.chat_task_admin import register_chat_task_routes
from strata.api.knowledge_admin import register_knowledge_admin_routes
from strata.api.retention_admin import register_retention_admin_routes
from strata.api.spec_admin import register_spec_admin_routes
from strata.api.runtime_admin import register_runtime_admin_routes
from strata.core.lanes import canonical_session_id_for_lane, infer_lane_from_session_id, normalize_lane
from strata.memory.semantic import SemanticMemory
from strata.orchestrator.worker.telemetry import build_telemetry_snapshot
from strata.models.providers import get_provider_telemetry_snapshot
from strata.observability.context import get_context_load_telemetry, scan_codebase_context_pressure
from strata.api.experiment_runtime import (
    apply_experiment_promotion,
    build_dashboard_snapshot,
    eval_override_signature,
    generate_eval_candidate_from_tier,
    generate_tool_candidate_from_tier,
    resolve_eval_proposal_against_history,
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
from strata.procedures.registry import ensure_onboarding_task
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
    "automatic_task_generation": True,
    "testing_mode": False,
    "replay_pending_tasks_on_startup": True,
    "heavy_reflection_mode": False,
    "inference_throttle_policy": {
        "throttle_mode": "hard",
        "operator_comfort": {
            "profile": "quiet",
            "ambiguity_bias": "prefer_quiet",
            "allow_annoying_if_explicit": False,
            "context": {
                "machine_in_use": True,
                "room_occupied": True,
                "ambient_noise_masking": False,
            },
        },
    },
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
_worker_start_task: Optional[asyncio.Task] = None


def _log_worker_start_result(task: asyncio.Task) -> None:
    try:
        task.result()
        logger.info("Background worker startup task completed.")
    except asyncio.CancelledError:
        logger.info("Background worker startup task cancelled.")
    except Exception as exc:
        logger.error("Background worker startup failed after API boot: %s", exc)

class EventBroadcaster:
    """Fan out runtime events to active SSE subscribers without retaining an unbounded backlog."""

    def __init__(self, *, queue_size: int = 128):
        self._queue_size = max(1, int(queue_size))
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    async def publish(self, data: Dict[str, Any]) -> None:
        async with self._lock:
            subscribers = list(self._subscribers)
        for queue in subscribers:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(data)
            except asyncio.QueueFull:
                continue


_event_broadcaster = EventBroadcaster()

async def _broadcast_event(data: Dict[str, Any]):
    """Push event to active SSE subscribers without retaining orphaned backlog."""
    await _event_broadcaster.publish(data)

# Register worker update listener
_worker.set_on_update(lambda tid, state: asyncio.create_task(_broadcast_event({"type": "task_update", "task_id": tid, "state": state})))

def _normalized_settings(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    normalized = dict(GLOBAL_SETTINGS)
    if payload:
        normalized.update(payload)
        current_policy = dict(GLOBAL_SETTINGS.get("inference_throttle_policy") or {})
        incoming_policy = dict(payload.get("inference_throttle_policy") or {})
        merged_policy = dict(current_policy)
        merged_policy.update({k: v for k, v in incoming_policy.items() if k != "operator_comfort"})
        current_comfort = dict(current_policy.get("operator_comfort") or {})
        incoming_comfort = dict(incoming_policy.get("operator_comfort") or {})
        merged_comfort = dict(current_comfort)
        merged_comfort.update({k: v for k, v in incoming_comfort.items() if k != "context"})
        current_context = dict(current_comfort.get("context") or {})
        incoming_context = dict(incoming_comfort.get("context") or {})
        merged_context = dict(current_context)
        merged_context.update(incoming_context)
        merged_comfort["context"] = merged_context
        merged_policy["operator_comfort"] = merged_comfort
        normalized["inference_throttle_policy"] = merged_policy
    return normalized


def _build_dashboard_snapshot(storage: StorageManager, limit: int = 10) -> Dict[str, Any]:
    return build_dashboard_snapshot(
        storage,
        limit=limit,
        build_telemetry_snapshot=build_telemetry_snapshot,
        get_provider_telemetry_snapshot=get_provider_telemetry_snapshot,
        get_retention_runtime=get_retention_runtime,
        get_context_load_telemetry=get_context_load_telemetry,
    )


def _apply_experiment_promotion(storage: StorageManager, candidate_change_id: str, *, force: bool = False) -> Dict[str, Any]:
    return apply_experiment_promotion(storage, candidate_change_id, force=force, model_adapter=_model)


async def _generate_eval_candidate_from_tier(
    proposer_tier: str,
    current_config: Dict[str, Any],
    **kwargs,
) -> Dict[str, Any]:
    return await generate_eval_candidate_from_tier(
        proposer_tier,
        current_config,
        model_adapter_factory=ModelAdapter,
        **kwargs,
    )


async def _resolve_eval_proposal_against_history(
    proposal: Dict[str, Any],
    *,
    current_config: Dict[str, Any],
    recent_candidates: list[Dict[str, Any]],
    seen_candidates: Optional[list[Dict[str, Any]]] = None,
    proposal_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return await resolve_eval_proposal_against_history(
        proposal,
        current_config=current_config,
        recent_candidates=recent_candidates,
        seen_candidates=seen_candidates,
        proposal_config=proposal_config,
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
    global _worker_start_task
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
        GenericOpenAICompatibleProvider.set_runtime_policy(
            GLOBAL_SETTINGS.get("inference_throttle_policy") or {}
        )
        try:
            run_retention_maintenance(storage)
        except OperationalError as exc:
            if "database is locked" not in str(exc).lower():
                raise
            logger.warning("Skipping startup retention maintenance due to database lock contention.")
            storage.rollback()
        try:
            scan_codebase_context_pressure(storage, base_dir=_BASE_DIR)
            storage.commit()
        except OperationalError as exc:
            if "database is locked" not in str(exc).lower():
                raise
            logger.warning("Skipping startup context-pressure scan due to database lock contention.")
            storage.rollback()
        seeded_onboarding = ensure_onboarding_task(storage, _worker)
        if seeded_onboarding is not None:
            logger.info("Seeded onboarding task %s during API startup.", getattr(seeded_onboarding, "task_id", "unknown"))
    finally:
        storage.close()
    _worker_start_task = asyncio.create_task(_worker.start(), name="background-worker-startup")
    _worker_start_task.add_done_callback(_log_worker_start_result)
    logger.info("Strata API started; background worker booting asynchronously")
    yield
    if _worker_start_task and not _worker_start_task.done():
        _worker_start_task.cancel()
        try:
            await _worker_start_task
        except asyncio.CancelledError:
            pass
    _worker_start_task = None
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
    return task.state in {TaskState.PENDING, TaskState.WORKING}


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
    last_error: OperationalError | None = None
    for attempt in range(5):
        if dedupe_signature:
            existing = _find_existing_eval_job(storage, str(eval_job.get("kind") or ""), dedupe_signature)
            if existing:
                return existing
        try:
            lane = normalize_lane(eval_job.get("reviewer_tier")) or infer_lane_from_session_id(session_id) or "trainer"
            resolved_session_id = str(session_id or "").strip() or None
            if resolved_session_id is None:
                resolved_session_id = canonical_session_id_for_lane(lane, session_id)
            task = storage.tasks.create(
                title=title,
                description=description,
                session_id=resolved_session_id,
                state=TaskState.PENDING,
                constraints={
                    "lane": lane,
                    "eval_job": eval_job,
                },
            )
            task.type = TaskType.JUDGE
            storage.commit()
            await _worker.enqueue(task.task_id)
            return task
        except OperationalError as exc:
            if "database is locked" not in str(exc).lower():
                raise
            last_error = exc
            storage.rollback()
            await asyncio.sleep(0.2 * (attempt + 1))
    raise HTTPException(status_code=503, detail=f"Database busy while queueing eval job: {last_error}")


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
    blocked_retry_window = timedelta(minutes=5)
    def _find_matching_system_job() -> Optional[Dict[str, Any]]:
        if not dedupe_signature:
            return None
        tasks = (
            storage.session.query(TaskModel)
            .filter(TaskModel.type == TaskType.JUDGE)
            .all()
        )
        for task in tasks:
            if task.state == TaskState.BLOCKED:
                updated_at = task.updated_at or task.created_at
                if updated_at is None:
                    continue
                if (datetime.utcnow() - updated_at.replace(tzinfo=None)) > blocked_retry_window:
                    continue
            elif not _task_is_active(task):
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
                status = "already_queued"
                if task.state == TaskState.BLOCKED:
                    status = "recent_failure"
                return {"task_id": task.task_id, "status": status, "kind": kind}
        return None

    last_error: OperationalError | None = None
    for attempt in range(5):
        existing = _find_matching_system_job()
        if existing:
            return existing
        try:
            lane = normalize_lane(payload.get("reviewer_tier")) or infer_lane_from_session_id(session_id) or "trainer"
            resolved_session_id = str(session_id or "").strip() or None
            if resolved_session_id is None:
                resolved_session_id = canonical_session_id_for_lane(lane, session_id)
            task = storage.tasks.create(
                title=title,
                description=description,
                session_id=resolved_session_id,
                state=TaskState.PENDING,
                type=TaskType.JUDGE,
                constraints={
                    "lane": lane,
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
        except OperationalError as exc:
            if "database is locked" not in str(exc).lower():
                raise
            last_error = exc
            storage.rollback()
            await asyncio.sleep(0.2 * (attempt + 1))
    raise HTTPException(status_code=503, detail=f"Database busy while queueing system job: {last_error}")


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
        get_context_load_telemetry=get_context_load_telemetry,
    ),
    apply_experiment_promotion=lambda storage, candidate_change_id, force=False: apply_experiment_promotion(
        storage,
        candidate_change_id,
        force=force,
        model_adapter=_model,
    ),
    generate_eval_candidate_from_tier=lambda proposer_tier, current_config, **kwargs: generate_eval_candidate_from_tier(
        proposer_tier,
        current_config,
        model_adapter_factory=ModelAdapter,
        **kwargs,
    ),
    resolve_eval_proposal_against_history=lambda proposal, **kwargs: resolve_eval_proposal_against_history(
        proposal,
        model_adapter_factory=ModelAdapter,
        **kwargs,
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
    queue_eval_system_job=_queue_eval_system_job,
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
    event_broadcaster=_event_broadcaster,
    hotreloader=_hotreloader,
    base_dir=_BASE_DIR,
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
                "state": task_state_api_value(task.state),
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
    uvicorn.run(
        "strata.api.main:app",
        host=os.environ.get("STRATA_API_HOST", "0.0.0.0"),
        port=int(os.environ.get("STRATA_API_PORT", "8000")),
        reload=str(os.environ.get("STRATA_API_RELOAD", "")).strip().lower() in {"1", "true", "yes", "on"},
    )
