"""
@module experimental.experiment_runner
@purpose Orchestrate the bootstrap improvement loop (Strong -> Weak improvement).
@key_exports ExperimentRunner, ExperimentResult
"""

import os
import json
import logging
from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field
from strata.schemas.execution import WeakExecutionContext
from strata.orchestrator.evaluation import EvaluationPipeline
from strata.orchestrator.worker.telemetry import record_metric
from strata.storage.models import TaskModel, MetricModel

logger = logging.getLogger(__name__)

class ExperimentResult(BaseModel):
    success: bool
    valid: bool
    candidate_change_id: str
    baseline_metrics: dict
    candidate_metrics: dict
    deltas: dict
    recommendation: Literal["promote", "reject", "insufficient_signal"]
    notes: str

class ExperimentRunner:
    """
    @summary Coordinates the "Strong improves Weak" experimental loop.
    """
    def __init__(self, storage_manager, model_adapter):
        self.storage = storage_manager
        self.model = model_adapter

    async def evaluate_candidate_change(
        self,
        candidate_change_id: str,
        eval_task_ids: List[str],
    ) -> ExperimentResult:
        """
        @summary Run a batch of tasks in weak-eval mode and compare results against a recorded baseline.
        """
        from strata.orchestrator.worker.attempt_runner import run_attempt
        from strata.storage.models import AttemptOutcome
        
        logger.info(f"Starting experiment for candidate change {candidate_change_id}...")
        
        # 1. Prepare Isolated Context
        context = WeakExecutionContext(
            run_id=f"exp_{candidate_change_id}",
            candidate_change_id=candidate_change_id,
            evaluation_run=True
        )
        # Bind once to ensure the model adapter is in the right state
        self.model.bind_execution_context(context)
        
        # 2. Execution Loop
        for task_id in eval_task_ids:
            task = self.storage.session.query(TaskModel).filter_by(task_id=task_id).first()
            if not task:
                logger.warning(f"Task {task_id} not found, skipping in experiment.")
                continue
            
            logger.info(f"Running eval task {task_id} under experiment {candidate_change_id}...")
            
            async def notify_noop(tid, state): pass
            async def enqueue_noop(tid): pass
            
            success, error, attempt = await run_attempt(
                task, self.storage, self.model, notify_noop, enqueue_noop
            )
            
            # 3. Explicitly Record High-Level Metrics (Success/Failure)
            # (EvaluationPipeline records the internal fitness metrics like valid_candidate_rate)
            m_id = f"{attempt.artifacts.get('provider', 'unknown')}/{attempt.artifacts.get('model', 'unknown')}"
            t_type = task.type.value if hasattr(task.type, "value") else str(task.type)
            
            record_metric(
                self.storage,
                metric_name="task_success" if success else "task_failure",
                value=1.0,
                model_id=m_id,
                task_id=task_id,
                task_type=t_type,
                run_mode="weak_eval",
                execution_context="weak",
                candidate_change_id=candidate_change_id,
                details={"error": str(error)} if not success else {}
            )

        # 4. Collection Logic
        candidate_metrics = self._gather_metrics(candidate_change_id)
        # Baseline is normally change_id="baseline" or None depending on how it was recorded
        baseline_metrics = self._gather_metrics("baseline") 
        
        # 5. Delta Analysis
        deltas = self._calculate_deltas(baseline_metrics, candidate_metrics)
        
        # 6. Recommendation Logic
        recommendation = self._decide_promotion(deltas)
        
        return ExperimentResult(
            success=True,
            valid=True,
            candidate_change_id=candidate_change_id,
            baseline_metrics=baseline_metrics,
            candidate_metrics=candidate_metrics,
            deltas=deltas,
            recommendation=recommendation,
            notes=f"Experiment completed for {len(eval_task_ids)} tasks."
        )

    def _gather_metrics(self, change_id: str) -> Dict[str, float]:
        """
        @summary Aggregates metrics from the database for a specific change ID.
        """
        from sqlalchemy import func
        results = (
            self.storage.session.query(
                MetricModel.metric_name,
                func.avg(MetricModel.value).label("avg_value")
            )
            .filter(MetricModel.candidate_change_id == change_id)
            .group_by(MetricModel.metric_name)
            .all()
        )
        return {r.metric_name: r.avg_value for r in results}

    def _calculate_deltas(self, baseline: dict, candidate: dict) -> Dict[str, float]:
        deltas = {}
        all_keys = set(baseline.keys()) | set(candidate.keys())
        for k in all_keys:
            b_val = baseline.get(k, 0.0)
            c_val = candidate.get(k, 0.0)
            deltas[k] = c_val - b_val
        return deltas

    def _decide_promotion(self, deltas: Dict[str, float]) -> str:
        # Rule: Promote if valid_candidate_rate improved and no major regressions
        vcr_delta = deltas.get("valid_candidate_rate", 0.0)
        failure_delta = deltas.get("task_failure", 0.0)
        
        if vcr_delta > 0.05 and failure_delta <= 0:
            return "promote"
        elif vcr_delta < -0.05:
            return "reject"
        else:
            return "insufficient_signal"
