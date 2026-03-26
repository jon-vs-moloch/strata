"""
@module experimental.experiment_runner
@purpose Orchestrate the bootstrap improvement loop (Strong -> Weak improvement).
@key_exports ExperimentRunner, ExperimentResult
"""

import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Literal
from strata.schemas.execution import WeakExecutionContext
from strata.orchestrator.evaluation import EvaluationPipeline
from strata.orchestrator.worker.telemetry import record_metric
from strata.storage.models import TaskModel, MetricModel, ParameterModel
from strata.eval.benchmark import run_benchmark, persist_benchmark_report
from strata.eval.structured_eval import run_structured_eval, persist_structured_eval_report
from strata.experimental.report_utils import (
    ExperimentResult,
    build_report_task_associations,
    iter_experiment_reports,
    normalize_experiment_report,
    report_has_weak_gain,
)

logger = logging.getLogger(__name__)

EXPERIMENT_REPORT_PREFIX = "experiment_report"
EXPERIMENT_REPORT_DESCRIPTION = (
    "Persisted benchmark/full-eval promotion summaries so the harness can reason "
    "about exact sampled runs instead of only blended historical metrics."
)
DEFAULT_PROMOTION_POLICY = {
    "min_benchmark_wins": 2,
    "min_structured_wins": 1,
    "min_code_wins": 1,
}

class ExperimentRunner:
    """
    @summary Coordinates the "Strong improves Weak" experimental loop.
    """
    def __init__(self, storage_manager, model_adapter):
        self.storage = storage_manager
        self.model = model_adapter

    def _report_parameter_key(self, candidate_change_id: str) -> str:
        return f"{EXPERIMENT_REPORT_PREFIX}:{candidate_change_id}"

    def _promotion_policy(self) -> Dict[str, int]:
        policy = self.storage.parameters.peek_parameter(
            "bootstrap_promotion_policy",
            default_value=DEFAULT_PROMOTION_POLICY,
        ) or DEFAULT_PROMOTION_POLICY
        merged = dict(DEFAULT_PROMOTION_POLICY)
        if isinstance(policy, dict):
            merged.update({k: int(v) for k, v in policy.items() if str(v).isdigit() or isinstance(v, int)})
        return merged

    def _benchmark_improved(self, report: Dict[str, Any]) -> bool:
        harness_wins = int(report.get("harness_wins", 0) or 0)
        baseline_wins = int(report.get("baseline_wins", 0) or 0)
        harness_score = float(report.get("average_harness_score", 0.0) or 0.0)
        baseline_score = float(report.get("average_baseline_score", 0.0) or 0.0)
        return harness_wins > baseline_wins or harness_score > baseline_score

    def _structured_improved(self, report: Dict[str, Any]) -> bool:
        harness_accuracy = float(report.get("harness_accuracy", 0.0) or 0.0)
        baseline_accuracy = float(report.get("baseline_accuracy", 0.0) or 0.0)
        return harness_accuracy > baseline_accuracy

    def _build_promotion_readiness(
        self,
        *,
        evaluation_kind: str,
        benchmark_reports: Optional[List[Dict[str, Any]]] = None,
        structured_reports: Optional[List[Dict[str, Any]]] = None,
        code_validation: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        policy = self._promotion_policy()
        benchmark_reports = benchmark_reports or []
        structured_reports = structured_reports or []
        benchmark_wins = sum(1 for report in benchmark_reports if self._benchmark_improved(report))
        structured_wins = sum(1 for report in structured_reports if self._structured_improved(report))
        code_wins = 1 if code_validation and code_validation.get("promoted") else 0

        ready = False
        if evaluation_kind == "benchmark":
            ready = benchmark_wins >= policy["min_benchmark_wins"]
        elif evaluation_kind == "full_eval":
            ready = (
                benchmark_wins >= policy["min_benchmark_wins"]
                and structured_wins >= policy["min_structured_wins"]
            )
        elif evaluation_kind == "tool_promotion":
            ready = code_wins >= policy["min_code_wins"]

        return {
            "policy": policy,
            "benchmark_win_runs": benchmark_wins,
            "benchmark_total_runs": len(benchmark_reports),
            "structured_win_runs": structured_wins,
            "structured_total_runs": len(structured_reports),
            "code_win_runs": code_wins,
            "ready_for_promotion": ready,
        }

    def _persist_experiment_report(
        self,
        result: ExperimentResult,
        *,
        baseline_change_id: str,
        evaluation_kind: str,
        run_count: int,
        benchmark_reports: Optional[List[Dict[str, Any]]] = None,
        structured_reports: Optional[List[Dict[str, Any]]] = None,
        suite_name: Optional[str] = None,
        eval_harness_config_override: Optional[Dict[str, Any]] = None,
        proposal_metadata: Optional[Dict[str, Any]] = None,
        promotion_readiness: Optional[Dict[str, Any]] = None,
        code_validation: Optional[Dict[str, Any]] = None,
        source_task_id: Optional[str] = None,
        spawned_task_ids: Optional[List[str]] = None,
        associated_task_ids: Optional[List[str]] = None,
    ) -> None:
        task_associations = build_report_task_associations(
            source_task_id=source_task_id,
            spawned_task_ids=spawned_task_ids,
            associated_task_ids=associated_task_ids,
        )
        report_payload = {
            "candidate_change_id": result.candidate_change_id,
            "baseline_change_id": baseline_change_id,
            "evaluation_kind": evaluation_kind,
            "run_count": run_count,
            "suite_name": suite_name,
            "recommendation": result.recommendation,
            "notes": result.notes,
            "success": result.success,
            "valid": result.valid,
            "baseline_metrics": result.baseline_metrics,
            "candidate_metrics": result.candidate_metrics,
            "deltas": result.deltas,
            "benchmark_reports": benchmark_reports or [],
            "structured_reports": structured_reports or [],
            "eval_harness_config_override": eval_harness_config_override,
            "proposal_metadata": proposal_metadata or {},
            "promotion_readiness": promotion_readiness or {},
            "code_validation": code_validation or {},
            "task_associations": task_associations,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        self.storage.parameters.set_parameter(
            key=self._report_parameter_key(result.candidate_change_id),
            value=report_payload,
            description=EXPERIMENT_REPORT_DESCRIPTION,
        )
        self._attach_report_to_tasks(result.candidate_change_id, task_associations)
        self.storage.commit()

    def _attach_report_to_tasks(self, candidate_change_id: str, task_associations: Dict[str, Any]) -> None:
        report_ref = {
            "candidate_change_id": candidate_change_id,
            "linked_at": datetime.now(timezone.utc).isoformat(),
            "source_task_id": task_associations.get("source_task_id"),
        }
        for task_id in task_associations.get("associated_task_ids") or []:
            task = self.storage.tasks.get_by_id(task_id)
            if not task:
                continue
            constraints = dict(task.constraints or {})
            report_refs = list(constraints.get("associated_reports") or [])
            if not any(str(item.get("candidate_change_id")) == candidate_change_id for item in report_refs if isinstance(item, dict)):
                report_refs.append(dict(report_ref))
            constraints["associated_reports"] = report_refs[-25:]
            task.constraints = constraints

    def get_persisted_experiment_report(self, candidate_change_id: str) -> Optional[Dict[str, Any]]:
        row = (
            self.storage.session.query(ParameterModel)
            .filter_by(key=self._report_parameter_key(candidate_change_id))
            .first()
        )
        if not row:
            return None
        report = normalize_experiment_report(row.value)
        return report or None

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
        promotion_readiness = self._build_promotion_readiness(
            evaluation_kind="benchmark",
            benchmark_reports=benchmark_reports,
        )
        recommendation = self._decide_benchmark_promotion(deltas, promotion_readiness)
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
        self._persist_experiment_report(
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
        promotion_readiness = self._build_promotion_readiness(
            evaluation_kind="full_eval",
            benchmark_reports=benchmark_reports,
            structured_reports=structured_reports,
        )
        recommendation = self._decide_benchmark_promotion(deltas, promotion_readiness)
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
        self._persist_experiment_report(
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
        promotion_readiness = self._build_promotion_readiness(
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
        self._persist_experiment_report(
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

    def _decide_benchmark_promotion(
        self,
        deltas: Dict[str, float],
        promotion_readiness: Optional[Dict[str, Any]] = None,
    ) -> str:
        if promotion_readiness and not promotion_readiness.get("ready_for_promotion", False):
            return "insufficient_signal"
        score_delta = deltas.get("benchmark_score_delta", 0.0)
        harness_win_delta = deltas.get("benchmark_harness_win_rate", 0.0)
        structured_accuracy_delta = deltas.get("structured_eval_harness_accuracy", 0.0)
        structured_latency_delta = deltas.get("structured_eval_harness_latency_s", 0.0)
        if structured_accuracy_delta > 0.0:
            return "promote"
        if structured_accuracy_delta < 0.0:
            return "reject"
        if score_delta > 0.5 or harness_win_delta > 0.15:
            return "promote"
        if score_delta < -0.5:
            return "reject"
        if structured_latency_delta < -5.0:
            return "promote"
        return "insufficient_signal"
