"""
@module orchestrator.worker.queue_recovery
@purpose Recover orphaned tasks on startup.
"""

import logging
from strata.storage.models import TaskModel, TaskState

logger = logging.getLogger(__name__)

async def recover_tasks(storage_factory, queue):
    """
    @summary Sweep for tasks stuck in WORKING and reset to PENDING.
    """
    storage = storage_factory()
    try:
        orphaned = storage.session.query(TaskModel).filter(TaskModel.state == TaskState.WORKING).all()
        for task in orphaned:
            logger.warning(f"Re-queueing orphaned runtime task: {task.task_id}")
            task.state = TaskState.PENDING
        storage.commit()
        
        # Repopulate the active queue with all PENDING tasks
        queued = storage.session.query(TaskModel).filter(TaskModel.state == TaskState.PENDING).all()
        for task in queued:
            queue.put_nowait(task.task_id)
            logger.info(f"Loaded existing queued task: {task.task_id}")
            
    except Exception as e:
        logger.error(f"Queue recovery sweep failed: {e}")
    finally:
        storage.session.close()
