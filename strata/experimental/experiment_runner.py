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
        logger.info(f"Starting experiment for candidate change {candidate_change_id}...")
        
        # 1. Prepare Isolated Context
        context = WeakExecutionContext(
            run_id=f"exp_{candidate_change_id}",
            candidate_change_id=candidate_change_id,
            evaluation_run=True
        )
        
        # 2. Reset / Initialize metrics for this candidate
        
        # 3. Execution (This would typically call the BackgroundWorker or a specialized TaskRunner)
        # For this patch, we'll assume the tasks are run externally or we provide a helper to run them.
        
        # 4. Collection Logic (Mocked for first ignition)
        candidate_metrics = self._gather_metrics(candidate_change_id)
        baseline_metrics = self._gather_metrics("baseline") # logical baseline
        
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
