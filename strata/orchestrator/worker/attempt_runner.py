"""
@module orchestrator.worker.attempt_runner
@purpose Dispatches task execution based on task type.
"""

import logging
import time
from strata.storage.models import TaskModel, TaskType, TaskState, AttemptOutcome
from strata.orchestrator.research import ResearchModule
from strata.orchestrator.decomposition import DecompositionModule
from strata.orchestrator.implementation import ImplementationModule
from strata.eval.job_runner import run_eval_job_task

logger = logging.getLogger(__name__)

async def run_attempt(task: TaskModel, storage, model_adapter, notify_fn, enqueue_fn):
    """
    @summary Execute a single task attempt.
    """
    # Start a new Attempt
    attempt = storage.attempts.create(task_id=task.task_id)
    storage.commit()
    started_at = time.perf_counter()
    
    logger.info(f"Running task {task.task_id} ({task.type.value}), Attempt {attempt.attempt_id}")

    try:
        if task.type == TaskType.RESEARCH:
            await _run_research(task, storage, model_adapter)
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
        if hasattr(model_adapter, 'last_response') and model_adapter.last_response:
            attempt.artifacts["model"] = model_adapter.last_response.model
            attempt.artifacts["provider"] = model_adapter.last_response.provider
            attempt.artifacts["usage"] = model_adapter.last_response.usage or {}
        attempt.artifacts["duration_s"] = round(time.perf_counter() - started_at, 4)
        storage.rollback()
        return False, e, attempt

async def _run_research(task, storage, model_adapter):
    research = ResearchModule(model_adapter, storage)
    report = await research.conduct_research(
        task_description=task.description,
        repo_path=task.repo_path
    )
    # Formatter would go here, simplified for brevity
    storage.messages.create(
        role="assistant",
        content=f"🔬 **Research Complete**\n{report.suggested_approach}",
        session_id=task.session_id or "default",
        task_id=task.task_id
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
    storage.messages.create(
        role="assistant",
        content=f"🛠️ **Implementation Staged**\nI've generated {len(candidate_ids)} candidates.",
        session_id=task.session_id or "default",
        task_id=task.task_id
    )
    task.state = TaskState.WORKING
    storage.commit()


async def _run_judge(task, storage, model_adapter):
    payload = await run_eval_job_task(task, storage, model_adapter)
    summary = payload.get("recommendation") or payload.get("suite_name") or payload.get("run_label") or "completed"
    storage.messages.create(
        role="assistant",
        content=f"📏 **Eval Job Complete**\n{task.title}: {summary}",
        session_id=task.session_id or "default",
        task_id=task.task_id,
    )
    task.state = TaskState.WORKING
    storage.commit()
