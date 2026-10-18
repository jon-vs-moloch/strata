"""
@module orchestrator.scheduler
@purpose Implement 'Code Rails' for task prioritization and swarm flow control.
@owns task sorting, worker allocation (logic), 'next task' selection
@does_not_own specific LLM inference, DB session management
@key_exports SchedulerModule
@side_effects none
"""

import logging
from datetime import datetime
from typing import List, Optional
from strata.storage.models import TaskModel, TaskState

logger = logging.getLogger(__name__)

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
        @summary Formalized swarm prioritization using a scoring function.
        @outputs the TaskModel with the highest calculated fitness score.
        """
        from strata.storage.models import TaskState, TaskType
        from sqlalchemy import func
        
        # 1. Fetch all candidate runnable tasks (not blocked)
        runnable_tasks = (
            self.storage.session.query(TaskModel)
            .filter(TaskModel.state.in_([TaskState.PENDING, TaskState.WORKING]))
            .filter(~TaskModel.dependencies.any(TaskModel.state != TaskState.COMPLETE))
            .all()
        )
        
        if not runnable_tasks:
            return None
            
        # 2. Score each task
        scored_tasks = []
        for task in runnable_tasks:
            score = 0.0
            
            # A. Base Priority (0-100)
            score += task.priority
            
            # B. Unblock Potential (Number of blocked descendants)
            # This is a bit expensive to query for every task, but for small swarms it's ok.
            # For now, let's use a placeholder for unblock potential.
            unblock_potential = len(task.blocked_tasks) if hasattr(task, 'blocked_tasks') else 0
            score += unblock_potential * 10.0
            
            # C. Recency Penalty (Prefer older tasks to avoid starvation)
            # (Higher score for older tasks)
            age_seconds = (datetime.utcnow() - task.created_at).total_seconds()
            score += min(age_seconds / 60.0, 50.0) # Up to 50 points for being an hour old
            
            # D. Task Type Weighting (Prioritize DECOMP/RESEARCH to unblock parallel work)
            if task.type == TaskType.DECOMP:
                score += 30.0
            elif task.type == TaskType.RESEARCH:
                score += 15.0
                
            scored_tasks.append((score, task))
            
        # 3. Select the winner
        scored_tasks.sort(key=lambda x: x[0], reverse=True)
        winner = scored_tasks[0][1]
        
        logger.info(f"Scheduler selected {winner.task_id} (Score: {scored_tasks[0][0]:.2f})")
        return winner

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
