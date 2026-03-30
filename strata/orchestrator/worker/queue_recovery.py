"""
@module orchestrator.worker.queue_recovery
@purpose Recover orphaned tasks on startup.
"""

import logging
from datetime import datetime, timedelta
from strata.storage.models import TaskModel, TaskState

logger = logging.getLogger(__name__)


def _task_replay_rank(task: TaskModel) -> tuple:
    constraints = dict(getattr(task, "constraints", {}) or {})
    phase = str(constraints.get("recovery_phase") or "").strip().lower()
    phase_rank = {"inspect": 0, "decide": 1, "cash_out": 2}.get(phase, 9)
    has_hints = bool(constraints.get("source_hints")) or bool(constraints.get("preferred_start_paths"))
    has_parent = 0 if getattr(task, "parent_task_id", None) else 1
    recency = getattr(task, "updated_at", None) or getattr(task, "created_at", None)
    recency_key = 0.0
    if recency is not None:
        try:
            recency_key = -float(recency.timestamp())
        except Exception:
            recency_key = 0.0
    return (
        has_parent,
        0 if has_hints else 1,
        phase_rank,
        recency_key,
        -int(getattr(task, "depth", 0) or 0),
        str(getattr(task, "task_id", "") or ""),
    )

async def recover_tasks(
    storage_factory,
    queue,
    *,
    recover_orphaned_running: bool = True,
    requeue_existing_pending: bool = False,
    pending_task_max_age_minutes: int | None = None,
    task_filter=None,
):
    """
    @summary Sweep for tasks stuck in WORKING and optionally reload existing PENDING tasks.

    The default startup behavior is intentionally conservative: tasks that were
    actively running when the process died are recovered, but stale pending
    backlog is not blindly replayed unless explicitly enabled. In normal Strata
    operation we do enable pending replay, because decomposed child tasks should
    resume immediately after restart instead of waiting for an idle timeout.
    """
    storage = storage_factory()
    logger.info("Starting recovery sweep for orphaned tasks...")
    try:
        if not recover_orphaned_running and not requeue_existing_pending:
            logger.info("Startup recovery disabled; leaving existing queue state untouched.")
            return

        orphaned = storage.session.query(TaskModel).filter(TaskModel.state == TaskState.WORKING).all()
        if task_filter is not None:
            orphaned = [task for task in orphaned if task_filter(task)]
        logger.info(f"Scanning for orphaned tasks, found {len(orphaned)}")
        recovered_orphaned = []
        for task in orphaned:
            task.state = TaskState.PENDING
            if recover_orphaned_running:
                logger.warning(f"Re-queueing orphaned runtime task: {task.task_id}")
                recovered_orphaned.append(task)
        storage.commit()
        if orphaned and not recover_orphaned_running:
            logger.info("Testing/startup guard active; orphaned running tasks were reset to pending without requeue.")

        for task in sorted(recovered_orphaned, key=_task_replay_rank):
            queue.put_nowait(task.task_id)
            logger.info(f"Recovered runtime task into active queue: {task.task_id}")

        if not requeue_existing_pending:
            logger.info("Skipping reload of pre-existing pending tasks on startup.")
            logger.info("Recovery sweep complete!")
            return

        queued_query = storage.session.query(TaskModel).filter(TaskModel.state == TaskState.PENDING)
        queued = queued_query.all()
        if task_filter is not None:
            queued = [task for task in queued if task_filter(task)]
        if pending_task_max_age_minutes:
            cutoff = datetime.utcnow() - timedelta(minutes=pending_task_max_age_minutes)
            queued = [task for task in queued if task.updated_at and task.updated_at >= cutoff]
            logger.info(
                "Pending startup replay is enabled with an age cutoff of %s minutes; %s tasks qualify.",
                pending_task_max_age_minutes,
                len(queued),
            )
        else:
            logger.info(f"Pending startup replay is enabled; found {len(queued)} tasks to reload.")

        recovered_ids = {task.task_id for task in recovered_orphaned}
        for task in sorted(queued, key=_task_replay_rank):
            if task.task_id in recovered_ids:
                continue
            queue.put_nowait(task.task_id)
            logger.info(f"Loaded existing queued task: {task.task_id}")
        logger.info("Recovery sweep complete!")
    except Exception as e:
        logger.error(f"Queue recovery sweep failed: {e}")
    finally:
        storage.session.close()
