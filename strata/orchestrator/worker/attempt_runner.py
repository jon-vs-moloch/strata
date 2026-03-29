"""
@module orchestrator.worker.attempt_runner
@purpose Dispatches task execution based on task type.
"""

import logging
import time
from strata.communication.primitives import build_communication_decision, deliver_communication_decision
from strata.core.lanes import infer_lane_from_task
from strata.storage.models import TaskModel, TaskType, TaskState, AttemptOutcome
from strata.orchestrator.research import ResearchModule
from strata.orchestrator.decomposition import DecompositionModule
from strata.orchestrator.implementation import ImplementationModule
from strata.eval.job_runner import run_eval_job_task

logger = logging.getLogger(__name__)


def _emit_task_communication(storage, task, *, content: str, source_kind: str) -> None:
    task_type = str(getattr(task.type, "value", "") or "").lower()
    summary = str(getattr(task, "title", "") or "").strip() or str(getattr(task, "description", "") or "").strip()[:160]
    decision = build_communication_decision(
        role="assistant",
        content=content,
        lane=infer_lane_from_task(task) or "trainer",
        channel="existing_session_message",
        session_id=task.session_id or "default",
        audience="user",
        source_kind=source_kind,
        source_actor="task_runner",
        opened_reason="task_progress",
        tags=["task", task_type, "autonomous"],
        topic_summary=summary[:180],
        session_title=summary[:80],
        communicative_act="notification",
        urgency="normal",
    )
    deliver_communication_decision(storage, decision)

async def run_attempt(task: TaskModel, storage, model_adapter, notify_fn, enqueue_fn):
    """
    @summary Execute a single task attempt.
    """
    for prior_attempt in storage.attempts.get_by_task_id(task.task_id):
        if prior_attempt.outcome is None and prior_attempt.ended_at is None:
            storage.attempts.update_outcome(
                prior_attempt.attempt_id,
                AttemptOutcome.CANCELLED,
                reason="Superseded by a newer attempt.",
            )

    # Start a new Attempt
    attempt = storage.attempts.create(task_id=task.task_id)
    if task.state != TaskState.WORKING:
        task.state = TaskState.WORKING
    storage.commit()
    started_at = time.perf_counter()
    
    logger.info(f"Running task {task.task_id} ({task.type.value}), Attempt {attempt.attempt_id}")

    try:
        if task.type == TaskType.RESEARCH:
            await _run_research(task, storage, model_adapter, enqueue_fn)
        elif task.type == TaskType.DECOMP:
            await _run_decomposition(task, storage, model_adapter, enqueue_fn)
        elif task.type == TaskType.IMPL:
            await _run_implementation(task, storage, model_adapter)
        elif task.type == TaskType.JUDGE:
            await _run_judge(task, storage, model_adapter)
        else:
            raise NotImplementedError(f"Unsupported task type {task.type}")

        # If we got here without exception, it succeeded
        storage.attempts.update_outcome(attempt.attempt_id, AttemptOutcome.SUCCEEDED)
        
        # Populate operational artifacts for the attempt
        if hasattr(model_adapter, 'last_response') and model_adapter.last_response:
            attempt.artifacts["model"] = model_adapter.last_response.model
            attempt.artifacts["provider"] = model_adapter.last_response.provider
            attempt.artifacts["usage"] = model_adapter.last_response.usage or {}
        attempt.artifacts["duration_s"] = round(time.perf_counter() - started_at, 4)
            
        task.state = TaskState.COMPLETE
        storage.commit()
        await notify_fn(task.task_id, task.state.value)
        return True, None, attempt

    except Exception as e:
        artifacts = {}
        if hasattr(model_adapter, 'last_response') and model_adapter.last_response:
            artifacts["model"] = model_adapter.last_response.model
            artifacts["provider"] = model_adapter.last_response.provider
            artifacts["usage"] = model_adapter.last_response.usage or {}
        artifacts["duration_s"] = round(time.perf_counter() - started_at, 4)
        storage.rollback()
        failed_attempt = storage.attempts.get_by_id(attempt.attempt_id)
        if failed_attempt:
            failed_attempt.artifacts = {**dict(failed_attempt.artifacts or {}), **artifacts}
            storage.attempts.update_outcome(failed_attempt.attempt_id, AttemptOutcome.FAILED, reason=str(e))
            storage.commit()
            attempt = failed_attempt
        return False, e, attempt

async def _run_research(task, storage, model_adapter, enqueue_fn):
    research = ResearchModule(model_adapter, storage, enqueue_task=enqueue_fn)
    report = await research.conduct_research(
        task_description=task.description,
        repo_path=task.repo_path,
        context_hints=dict(task.constraints or {}),
    )
    # Formatter would go here, simplified for brevity
    _emit_task_communication(
        storage,
        task,
        content=f"🔬 **Research Complete**\n{report.suggested_approach}",
        source_kind="task_research_complete",
    )
    task.state = TaskState.WORKING
    storage.commit()

async def _run_decomposition(task, storage, model_adapter, enqueue_fn):
    decomp_mod = DecompositionModule(model_adapter, storage)
    decomp = await decomp_mod.decompose_task(task.title, task.description)
    for tid, proto in decomp.subtasks.items():
        sub = storage.tasks.create(
            title=proto.title,
            description=proto.description,
            session_id=task.session_id,
            parent_task_id=task.task_id,
            state=TaskState.PENDING,
            constraints={
                "target_files": proto.target_files,
                "edit_type": proto.edit_type,
                "validator": proto.validator,
                "max_diff_size": proto.max_diff_size
            }
        )
        sub.type = TaskType.IMPL
        sub.depth = task.depth + 1
        storage.commit()
        await enqueue_fn(sub.task_id)
    task.state = TaskState.WORKING
    storage.commit()

async def _run_implementation(task, storage, model_adapter):
    research_mod = ResearchModule(model_adapter, storage)
    impl_mod = ImplementationModule(model_adapter, storage, research_mod)
    candidate_ids = await impl_mod.implement_task(task.task_id)
    _emit_task_communication(
        storage,
        task,
        content=f"🛠️ **Implementation Staged**\nI've generated {len(candidate_ids)} candidates.",
        source_kind="task_implementation_staged",
    )
    task.state = TaskState.WORKING
    storage.commit()


async def _run_judge(task, storage, model_adapter):
    payload = await run_eval_job_task(task, storage, model_adapter)
    summary = payload.get("recommendation") or payload.get("suite_name") or payload.get("run_label") or "completed"
    _emit_task_communication(
        storage,
        task,
        content=f"📏 **Eval Job Complete**\n{task.title}: {summary}",
        source_kind="task_eval_complete",
    )
    task.state = TaskState.WORKING
    storage.commit()
