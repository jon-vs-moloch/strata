"""
@module orchestrator.scheduler
@purpose Implement 'Code Rails' for task prioritization and swarm flow control.
@owns task sorting, worker allocation (logic), 'next task' selection
@does_not_own specific LLM inference, DB session management
@key_exports SchedulerModule
@side_effects none
"""

from typing import List, Optional
from strata.storage.models import TaskModel, TaskState

class SchedulerModule:
    """
    @summary Provides top-down task selection logic for the swarm.
    @inputs storage: StorageManager
    @outputs The next 'ready-to-work' TaskModel
    @side_effects reads from DB session
    @depends storage.models.TaskModel
    @invariants always prefers higher priority over lower, then older over newer.
    """
    def __init__(self, storage_manager):
        """
        @summary Initialize the SchedulerModule.
        @inputs storage_manager instance
        """
        self.storage = storage_manager

    def get_next_runnable_task(self) -> Optional[TaskModel]:
        """
        @summary Core logic for swarm prioritization.
        @inputs none (reads from DB)
        @outputs the TaskModel the swarm should focus on now
        @side_effects none (read-only)
        @invariants Skip tasks that are BLOCKED or WAITING_DEPENDENCIES.
        """
        from strata.storage.models import TaskState
        
        # Build query to fetch the next runnable task
        # Patch Objective: Push dependency filtering to the database for O(1) performance
        stmt = (
            self.storage.session.query(TaskModel)
            .filter(TaskModel.state.in_([TaskState.PENDING, TaskState.WORKING]))
            # Exclude tasks that have incomplete dependencies:
            # ~TaskModel.dependencies.any(TaskModel.state != TaskState.COMPLETE)
            .filter(~TaskModel.dependencies.any(TaskModel.state != TaskState.COMPLETE))
            .order_by(TaskModel.priority.desc(), TaskModel.created_at.asc())
        )
        
        return stmt.first()

    def select_best_model(self, task_type: str, fallback_model: str = "qwen3.5-4b-claude-4.6-opus-reasoning-distilled-v2") -> str:
        """
        @summary Core empirical routing logic. Selects the highest performing model for a given task type.
        @inputs task_type: e.g. RESEARCH, IMPL, DECOMP
        @outputs the model_id with the highest historical throughput/score
        """
        from strata.storage.models import ModelTelemetry
        from sqlalchemy import func
        
        # Query highest average score for this specific task category
        best = (
            self.storage.session.query(
                ModelTelemetry.model_id, 
                func.avg(ModelTelemetry.score).label("avg_score")
            )
            .filter(ModelTelemetry.task_type == task_type)
            .group_by(ModelTelemetry.model_id)
            .order_by(func.avg(ModelTelemetry.score).desc())
            .first()
        )
        
        if best and best.model_id:
            print(f"Empirical Router: selected {best.model_id} for {task_type} (score: {best.avg_score:.1f})")
            return best.model_id
            
        return fallback_model

    def rebalance_swarm(self):
        """
        @summary Analyze and log the swarm's current throughput and bottlenecking.
        """
        pass
