"""
@module experimental.experiment_runner
@purpose Orchestrate the bootstrap improvement loop (Strong -> Weak improvement).
@key_exports ExperimentRunner, ExperimentResult
"""

import logging
from typing import List, Dict, Any, Optional, Literal
from strata.schemas.execution import WeakExecutionContext
from strata.orchestrator.worker.telemetry import record_metric
from strata.storage.models import TaskModel, MetricModel
from strata.eval.benchmark import run_benchmark, persist_benchmark_report
from strata.eval.structured_eval import run_structured_eval, persist_structured_eval_report
from strata.experimental.promotion_policy import (
    build_promotion_readiness,
    calculate_deltas,
    decide_benchmark_promotion,
    decide_promotion,
)
from strata.experimental.report_store import (
    get_persisted_experiment_report,
    persist_experiment_report,
    report_parameter_key,
)
from strata.experimental.report_utils import (
    ExperimentResult,
    iter_experiment_reports,
    normalize_experiment_report,
    report_has_weak_gain,
)

logger = logging.getLogger(__name__)

class ExperimentRunner:
    """
    @summary Coordinates the "Strong improves Weak" experimental loop.
    """
    def __init__(self, storage_manager, model_adapter):
        self.storage = storage_manager
        self.model = model_adapter

    def _persist_experiment_report(self, result: ExperimentResult, **kwargs: Any) -> None:
        persist_experiment_report(self.storage, result, **kwargs)

    def get_persisted_experiment_report(self, candidate_change_id: str) -> Optional[Dict[str, Any]]:
        return get_persisted_experiment_report(self.storage, candidate_change_id)

    async def run_benchmark_gate(
        self,
        candidate_change_id: str,
        *,
        api_url: str = "http://127.0.0.1:8000",
        baseline_change_id: str = "baseline",
        run_count: int = 1,
        eval_harness_config_override: Optional[Dict[str, Any]] = None,
        proposal_metadata: Optional[Dict[str, Any]] = None,
        source_task_id: Optional[str] = None,
        spawned_task_ids: Optional[List[str]] = None,
        associated_task_ids: Optional[List[str]] = None,
    ) -> ExperimentResult:
        """
        @summary Compare a labeled candidate benchmark run against the stored baseline benchmark.
        """
        logger.info(f"Running benchmark gate for candidate change {candidate_change_id}...")
        safe_runs = max(1, run_count)
        benchmark_reports: List[Dict[str, Any]] = []
        for run_index in range(safe_runs):
            report = await run_benchmark(
                api_url=api_url,
                run_label=f"{candidate_change_id}-benchmark-{run_index + 1}",
                eval_harness_config_override=eval_harness_config_override,
            )
            benchmark_reports.append(report)
            persist_benchmark_report(
                self.storage,
                report,
                candidate_change_id=candidate_change_id,
                run_mode="weak_eval",
                model_id="benchmark/harness",
            )
        candidate_metrics = self._gather_metrics(candidate_change_id)
        baseline_metrics = self._gather_metrics(baseline_change_id)
        deltas = self._calculate_deltas(baseline_metrics, candidate_metrics)
        promotion_readiness = build_promotion_readiness(
            self.storage,
            evaluation_kind="benchmark",
            benchmark_reports=benchmark_reports,
        )
        recommendation = decide_benchmark_promotion(deltas, promotion_readiness)
        result = ExperimentResult(
            success=True,
            valid=True,
            candidate_change_id=candidate_change_id,
            baseline_metrics=baseline_metrics,
            candidate_metrics=candidate_metrics,
            deltas=deltas,
            recommendation=recommendation,
            notes=f"Benchmark gate completed across {safe_runs} run(s).",
        )
        persist_experiment_report(
            self.storage,
            result,
            baseline_change_id=baseline_change_id,
            evaluation_kind="benchmark",
            run_count=safe_runs,
            benchmark_reports=benchmark_reports,
            eval_harness_config_override=eval_harness_config_override,
            proposal_metadata=proposal_metadata,
            promotion_readiness=promotion_readiness,
            source_task_id=source_task_id,
            spawned_task_ids=spawned_task_ids,
            associated_task_ids=associated_task_ids,
        )
        return result

    async def run_full_eval_gate(
        self,
        candidate_change_id: str,
        *,
        api_url: str = "http://127.0.0.1:8000",
        baseline_change_id: str = "baseline",
        suite_name: str = "bootstrap_mcq_v1",
        run_count: int = 1,
        eval_harness_config_override: Optional[Dict[str, Any]] = None,
        proposal_metadata: Optional[Dict[str, Any]] = None,
        source_task_id: Optional[str] = None,
        spawned_task_ids: Optional[List[str]] = None,
        associated_task_ids: Optional[List[str]] = None,
    ) -> ExperimentResult:
        """
        @summary Run repeated benchmark and structured-eval passes, then compare against baseline.
        """
        safe_runs = max(1, run_count)
        benchmark_reports: List[Dict[str, Any]] = []
        structured_reports: List[Dict[str, Any]] = []
        for run_index in range(safe_runs):
            benchmark_report = await run_benchmark(
                api_url=api_url,
                run_label=f"{candidate_change_id}-benchmark-{run_index + 1}",
                eval_harness_config_override=eval_harness_config_override,
            )
            benchmark_reports.append(benchmark_report)
            persist_benchmark_report(
                self.storage,
                benchmark_report,
                candidate_change_id=candidate_change_id,
                run_mode="weak_eval" if candidate_change_id != "baseline" else "baseline",
                model_id="benchmark/harness",
            )
            structured_report = await run_structured_eval(
                api_url=api_url,
                suite_name=suite_name,
                run_label=f"{candidate_change_id}-structured-{run_index + 1}",
                eval_harness_config_override=eval_harness_config_override,
            )
            structured_reports.append(structured_report)
            persist_structured_eval_report(
                self.storage,
                structured_report,
                candidate_change_id=candidate_change_id,
                run_mode="weak_eval" if candidate_change_id != "baseline" else "baseline",
                model_id="structured_eval/harness",
            )
        candidate_metrics = self._gather_metrics(candidate_change_id)
        baseline_metrics = self._gather_metrics(baseline_change_id)
        deltas = self._calculate_deltas(baseline_metrics, candidate_metrics)
        promotion_readiness = build_promotion_readiness(
            self.storage,
            evaluation_kind="full_eval",
            benchmark_reports=benchmark_reports,
            structured_reports=structured_reports,
        )
        recommendation = decide_benchmark_promotion(deltas, promotion_readiness)
        result = ExperimentResult(
            success=True,
            valid=True,
            candidate_change_id=candidate_change_id,
            baseline_metrics=baseline_metrics,
            candidate_metrics=candidate_metrics,
            deltas=deltas,
            recommendation=recommendation,
            notes=f"Full eval gate completed across {safe_runs} run(s) using suite '{suite_name}'.",
        )
        persist_experiment_report(
            self.storage,
            result,
            baseline_change_id=baseline_change_id,
            evaluation_kind="full_eval",
            run_count=safe_runs,
            benchmark_reports=benchmark_reports,
            structured_reports=structured_reports,
            suite_name=suite_name,
            eval_harness_config_override=eval_harness_config_override,
            proposal_metadata=proposal_metadata,
            promotion_readiness=promotion_readiness,
            source_task_id=source_task_id,
            spawned_task_ids=spawned_task_ids,
            associated_task_ids=associated_task_ids,
        )
        return result

    def record_tool_promotion_result(
        self,
        *,
        candidate_change_id: str,
        baseline_change_id: str = "baseline",
        validation_result: Dict[str, Any],
        proposal_metadata: Optional[Dict[str, Any]] = None,
        source_task_id: Optional[str] = None,
        spawned_task_ids: Optional[List[str]] = None,
        associated_task_ids: Optional[List[str]] = None,
    ) -> ExperimentResult:
        candidate_metrics = {"tool_promotion_success": 1.0 if validation_result.get("promoted") else 0.0}
        baseline_metrics = self._gather_metrics(baseline_change_id)
        deltas = self._calculate_deltas(baseline_metrics, candidate_metrics)
        promotion_readiness = build_promotion_readiness(
            self.storage,
            evaluation_kind="tool_promotion",
            code_validation=validation_result,
        )
        recommendation: Literal["promote", "reject", "insufficient_signal"]
        recommendation = "promote" if promotion_readiness.get("ready_for_promotion") else "reject"
        result = ExperimentResult(
            success=bool(validation_result.get("promoted")),
            valid=bool(validation_result.get("promoted")),
            candidate_change_id=candidate_change_id,
            baseline_metrics=baseline_metrics,
            candidate_metrics=candidate_metrics,
            deltas=deltas,
            recommendation=recommendation,
            notes=validation_result.get("details", "Tool promotion cycle completed."),
        )
        persist_experiment_report(
            self.storage,
            result,
            baseline_change_id=baseline_change_id,
            evaluation_kind="tool_promotion",
            run_count=1,
            proposal_metadata=proposal_metadata,
            promotion_readiness=promotion_readiness,
            code_validation=validation_result,
            source_task_id=source_task_id,
            spawned_task_ids=spawned_task_ids,
            associated_task_ids=associated_task_ids,
        )
        return result

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
        deltas = calculate_deltas(baseline_metrics, candidate_metrics)
        
        # 6. Recommendation Logic
        recommendation = decide_promotion(deltas)
        
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
