"""
@module experimental.experiment_runner
@purpose Orchestrate the bootstrap improvement loop (Strong -> Weak improvement).
@key_exports ExperimentRunner, ExperimentResult
"""

import logging
from typing import List, Dict, Any, Optional, Literal
from strata.feedback.signals import register_feedback_signal
from strata.schemas.execution import AgentExecutionContext
from strata.orchestrator.worker.telemetry import record_metric
from strata.storage.models import TaskModel, MetricModel
from strata.eval.benchmark import run_benchmark, persist_benchmark_report
from strata.eval.structured_eval import run_structured_eval, persist_structured_eval_report
from strata.eval.harness_eval import get_active_eval_harness_config
from strata.experimental.calibration import (
    infer_actual_outcome,
    normalize_prediction,
    score_prediction_against_outcome,
    update_judge_trust,
)
from strata.experimental.promotion_policy import (
    build_promotion_readiness,
    calculate_deltas,
    decide_benchmark_promotion,
    decide_promotion,
    get_promotion_policy,
)
from strata.experimental.report_store import (
    get_persisted_experiment_report,
    persist_experiment_report,
    report_parameter_key,
)
from strata.experimental.diagnostics import review_eval_trace
from strata.experimental.variants import ensure_variant, record_variant_matchup
from strata.experimental.report_utils import (
    ExperimentResult,
    iter_experiment_reports,
    normalize_experiment_report,
    report_has_weak_gain,
)

logger = logging.getLogger(__name__)


def emit_eval_attention_signal(
    storage,
    *,
    candidate_change_id: str,
    evaluation_kind: str,
    prediction_record: Dict[str, Any],
    calibration_record: Dict[str, Any],
    recommendation: str,
    deltas: Dict[str, Any],
) -> Dict[str, Any]:
    predicted_outcome = str(prediction_record.get("predicted_outcome") or "uncertain").strip().lower()
    actual_outcome = str(calibration_record.get("actual_outcome") or "neutral").strip().lower()
    if predicted_outcome == actual_outcome and predicted_outcome in {"improve", "neutral", "regress"}:
        signal_kind = "importance"
        signal_value = f"{evaluation_kind}:{actual_outcome}"
    elif predicted_outcome == "improve" and actual_outcome != "improve":
        signal_kind = "unexpected_failure"
        signal_value = f"{evaluation_kind}:expected_improve_observed_{actual_outcome}"
    elif predicted_outcome in {"regress", "neutral", "uncertain"} and actual_outcome == "improve":
        signal_kind = "unexpected_success"
        signal_value = f"{evaluation_kind}:expected_{predicted_outcome}_observed_improve"
    else:
        signal_kind = "surprise"
        signal_value = f"{evaluation_kind}:expected_{predicted_outcome}_observed_{actual_outcome}"

    return register_feedback_signal(
        storage,
        source_type="eval",
        source_id=candidate_change_id,
        signal_kind=signal_kind,
        signal_value=signal_value,
        source_actor="system",
        source_preview=(
            f"Eval candidate '{candidate_change_id}' in {evaluation_kind} predicted {predicted_outcome} "
            f"but observed {actual_outcome}. Recommendation={recommendation}."
        ),
        note=str(prediction_record.get("rationale") or ""),
        expected_outcome=predicted_outcome,
        observed_outcome=actual_outcome,
        metadata={
            "evaluation_kind": evaluation_kind,
            "recommendation": recommendation,
            "failure_family": prediction_record.get("failure_family"),
            "confidence": prediction_record.get("confidence"),
            "calibration_score": calibration_record.get("calibration_score"),
            "actual_delta": dict(deltas or {}),
        },
    )

class ExperimentRunner:
    """
    @summary Coordinates the "Strong improves Weak" experimental loop.
    """
    def __init__(self, storage_manager, model_adapter):
        self.storage = storage_manager
        self.model = model_adapter

    def _persist_experiment_report(self, result: ExperimentResult, **kwargs: Any) -> None:
        persist_experiment_report(self.storage, result, **kwargs)

    def _calculate_deltas(self, baseline: Dict[str, float], candidate: Dict[str, float]) -> Dict[str, float]:
        return calculate_deltas(baseline, candidate)

    def _decide_promotion(self, deltas: Dict[str, float]) -> str:
        return decide_promotion(deltas)

    def _decide_benchmark_promotion(
        self,
        deltas: Dict[str, float],
        promotion_readiness: Optional[Dict[str, Any]] = None,
    ) -> str:
        return decide_benchmark_promotion(deltas, promotion_readiness)

    def _resolve_eval_variant_pair(
        self,
        *,
        candidate_change_id: str,
        baseline_change_id: str,
        eval_harness_config_override: Optional[Dict[str, Any]],
        proposal_metadata: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        baseline_payload = get_active_eval_harness_config()
        baseline_policy_payload = get_promotion_policy(self.storage)
        if baseline_change_id:
            baseline_report = self.get_persisted_experiment_report(baseline_change_id)
            if isinstance(baseline_report, dict):
                baseline_payload = dict(baseline_report.get("eval_harness_config_override") or baseline_payload)
                baseline_policy_payload = dict(
                    (baseline_report.get("variant_assignment") or {}).get("baseline_promotion_policy_payload")
                    or baseline_policy_payload
                )
        candidate_payload = dict(eval_harness_config_override or baseline_payload)
        candidate_policy_payload = get_promotion_policy(self.storage)
        baseline_variant = ensure_variant(
            self.storage,
            kind="eval_harness_bundle",
            payload=baseline_payload,
            label=baseline_change_id or "baseline",
            family="eval_harness",
            metadata={"role": "baseline"},
        )
        candidate_variant = ensure_variant(
            self.storage,
            kind="eval_harness_bundle",
            payload=candidate_payload,
            label=candidate_change_id,
            family="eval_harness",
            metadata={"role": "candidate", **dict(proposal_metadata or {})},
        )
        baseline_policy_variant = ensure_variant(
            self.storage,
            kind="promotion_policy_bundle",
            payload=baseline_policy_payload,
            label=f"{baseline_change_id or 'baseline'}_promotion_policy",
            family="promotion_policy",
            metadata={"role": "baseline"},
        )
        candidate_policy_variant = ensure_variant(
            self.storage,
            kind="promotion_policy_bundle",
            payload=candidate_policy_payload,
            label=f"{candidate_change_id}_promotion_policy",
            family="promotion_policy",
            metadata={"role": "candidate", **dict(proposal_metadata or {})},
        )
        return {
            "family": "eval_harness",
            "baseline_variant_id": baseline_variant.get("variant_id"),
            "candidate_variant_id": candidate_variant.get("variant_id"),
            "baseline_promotion_policy_variant_id": baseline_policy_variant.get("variant_id"),
            "candidate_promotion_policy_variant_id": candidate_policy_variant.get("variant_id"),
            "baseline_promotion_policy_payload": baseline_policy_payload,
            "candidate_promotion_policy_payload": candidate_policy_payload,
        }

    def _record_variant_outcome(
        self,
        *,
        domain: str,
        variant_assignment: Dict[str, Any],
        candidate_change_id: str,
        actual_delta: Dict[str, Any],
        recommendation: str,
    ) -> Dict[str, Any]:
        candidate_variant_id = str(variant_assignment.get("candidate_variant_id") or "").strip()
        baseline_variant_id = str(variant_assignment.get("baseline_variant_id") or "").strip()
        if not candidate_variant_id or not baseline_variant_id:
            return {}
        actual_outcome = infer_actual_outcome(actual_delta)
        if actual_outcome == "improve":
            left_score = 1.0
        elif actual_outcome == "regress":
            left_score = 0.0
        else:
            left_score = 0.5
        eval_harness_snapshot = record_variant_matchup(
            self.storage,
            domain=domain,
            left_variant_id=candidate_variant_id,
            right_variant_id=baseline_variant_id,
            left_score=left_score,
            context={
                "candidate_change_id": candidate_change_id,
                "recommendation": recommendation,
                "actual_outcome": actual_outcome,
            },
        )
        policy_snapshot = {}
        candidate_policy_variant_id = str(variant_assignment.get("candidate_promotion_policy_variant_id") or "").strip()
        baseline_policy_variant_id = str(variant_assignment.get("baseline_promotion_policy_variant_id") or "").strip()
        if candidate_policy_variant_id and baseline_policy_variant_id:
            policy_snapshot = record_variant_matchup(
                self.storage,
                domain=f"promotion_policy:{domain}",
                left_variant_id=candidate_policy_variant_id,
                right_variant_id=baseline_policy_variant_id,
                left_score=left_score,
                context={
                    "candidate_change_id": candidate_change_id,
                    "recommendation": recommendation,
                    "actual_outcome": actual_outcome,
                },
            )
        return {
            "eval_harness": eval_harness_snapshot,
            "promotion_policy": policy_snapshot,
        }

    def _score_benchmark_report(self, report: Dict[str, Any]) -> float:
        prompt_count = max(1, int(report.get("prompt_count", 0) or 0))
        harness_wins = float(report.get("harness_wins", 0) or 0.0)
        ties = float(report.get("ties", 0) or 0.0)
        return max(0.0, min(1.0, (harness_wins + 0.5 * ties) / prompt_count))

    def _score_structured_report(self, report: Dict[str, Any]) -> float:
        harness_accuracy = float(report.get("harness_accuracy", 0.0) or 0.0)
        baseline_accuracy = float(report.get("baseline_accuracy", 0.0) or 0.0)
        if harness_accuracy > baseline_accuracy:
            return 1.0
        if harness_accuracy < baseline_accuracy:
            return 0.0
        return 0.5

    def _record_ab_run_evidence(
        self,
        *,
        domain: str,
        variant_assignment: Dict[str, Any],
        candidate_change_id: str,
        benchmark_reports: Optional[List[Dict[str, Any]]] = None,
        structured_reports: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        candidate_variant_id = str(variant_assignment.get("candidate_variant_id") or "").strip()
        baseline_variant_id = str(variant_assignment.get("baseline_variant_id") or "").strip()
        if not candidate_variant_id or not baseline_variant_id:
            return {"matchup_count": 0, "recent_matchups": []}
        evidence: List[Dict[str, Any]] = []
        for report in benchmark_reports or []:
            snapshot = record_variant_matchup(
                self.storage,
                domain=f"{domain}:benchmark_ab",
                left_variant_id=candidate_variant_id,
                right_variant_id=baseline_variant_id,
                left_score=self._score_benchmark_report(report),
                context={
                    "candidate_change_id": candidate_change_id,
                    "evidence_type": "benchmark_run",
                    "run_label": report.get("run_label"),
                },
            )
            if snapshot:
                evidence.append({"kind": "benchmark_run", "run_label": report.get("run_label"), "snapshot": snapshot})
        for report in structured_reports or []:
            snapshot = record_variant_matchup(
                self.storage,
                domain=f"{domain}:structured_ab",
                left_variant_id=candidate_variant_id,
                right_variant_id=baseline_variant_id,
                left_score=self._score_structured_report(report),
                context={
                    "candidate_change_id": candidate_change_id,
                    "evidence_type": "structured_run",
                    "run_label": report.get("run_label"),
                    "suite_name": report.get("suite_name"),
                },
            )
            if snapshot:
                evidence.append(
                    {
                        "kind": "structured_run",
                        "run_label": report.get("run_label"),
                        "suite_name": report.get("suite_name"),
                        "snapshot": snapshot,
                    }
                )
        return {
            "matchup_count": len(evidence),
            "recent_matchups": evidence[-10:],
        }

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
        progress_fn=None,
    ) -> ExperimentResult:
        """
        @summary Compare a labeled candidate benchmark run against the stored baseline benchmark.
        """
        logger.info(f"Running benchmark gate for candidate change {candidate_change_id}...")
        safe_runs = max(1, run_count)
        def _progress(label: str, detail: str = "", progress_label: str = "benchmark gate") -> None:
            if progress_fn:
                progress_fn(
                    step="system_job",
                    label=label,
                    detail=detail,
                    progress_label=progress_label,
                )
        variant_assignment = self._resolve_eval_variant_pair(
            candidate_change_id=candidate_change_id,
            baseline_change_id=baseline_change_id,
            eval_harness_config_override=eval_harness_config_override,
            proposal_metadata=proposal_metadata,
        )
        benchmark_reports: List[Dict[str, Any]] = []
        for run_index in range(safe_runs):
            run_label = f"run {run_index + 1}/{safe_runs}"
            _progress("Running benchmark", run_label, "benchmark")
            report = await run_benchmark(
                api_url=api_url,
                run_label=f"{candidate_change_id}-benchmark-{run_index + 1}",
                eval_harness_config_override=eval_harness_config_override,
                progress_fn=progress_fn,
            )
            benchmark_reports.append(report)
            persist_benchmark_report(
                self.storage,
                report,
                candidate_change_id=candidate_change_id,
                run_mode="weak_eval",
                model_id="benchmark/harness",
                variant_assignment=variant_assignment,
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
        diagnostic_review = await review_eval_trace(
            self.model,
            candidate_change_id=candidate_change_id,
            baseline_change_id=baseline_change_id,
            benchmark_reports=benchmark_reports,
        )
        prediction_record = normalize_prediction(diagnostic_review)
        prediction_record["judge_tier"] = str(diagnostic_review.get("reviewer_tier") or "trainer")
        prediction_record["failure_family"] = str(diagnostic_review.get("failure_family") or "")
        prediction_outcome = {
            "candidate_change_id": candidate_change_id,
            "actual_metrics": candidate_metrics,
            "actual_delta": deltas,
            "promotion_result": recommendation,
            "run_count": safe_runs,
            "observed_domains": ["benchmark", "eval"],
        }
        calibration_record = score_prediction_against_outcome(
            prediction_record,
            actual_delta=deltas,
            promotion_result=recommendation,
            observed_domains=prediction_outcome["observed_domains"],
            run_count=safe_runs,
        )
        judge_trust_snapshot = update_judge_trust(
            self.storage,
            judge_tier=prediction_record["judge_tier"],
            prediction=prediction_record,
            calibration_record=calibration_record,
        )
        attention_signal = emit_eval_attention_signal(
            self.storage,
            candidate_change_id=candidate_change_id,
            evaluation_kind="benchmark",
            prediction_record=prediction_record,
            calibration_record=calibration_record,
            recommendation=recommendation,
            deltas=deltas,
        )
        variant_rating_snapshot = self._record_variant_outcome(
            domain="eval_harness_benchmark",
            variant_assignment=variant_assignment,
            candidate_change_id=candidate_change_id,
            actual_delta=deltas,
            recommendation=recommendation,
        )
        ab_evidence_summary = self._record_ab_run_evidence(
            domain="eval_harness_benchmark",
            variant_assignment=variant_assignment,
            candidate_change_id=candidate_change_id,
            benchmark_reports=benchmark_reports,
        )
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
            diagnostic_review=diagnostic_review,
            prediction_record=prediction_record,
            prediction_outcome=prediction_outcome,
            calibration_record=calibration_record,
            judge_trust_snapshot=judge_trust_snapshot,
            ab_evidence_summary={**(ab_evidence_summary or {}), "attention_signal": attention_signal},
            variant_assignment=variant_assignment,
            variant_rating_snapshot=variant_rating_snapshot,
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
        progress_fn=None,
    ) -> ExperimentResult:
        """
        @summary Run repeated benchmark and structured-eval passes, then compare against baseline.
        """
        safe_runs = max(1, run_count)
        def _progress(label: str, detail: str = "", progress_label: str = "full eval") -> None:
            if progress_fn:
                progress_fn(
                    step="system_job",
                    label=label,
                    detail=detail,
                    progress_label=progress_label,
                )

        _progress("Preparing eval gate", str(candidate_change_id or "candidate"), "prepare eval gate")
        variant_assignment = self._resolve_eval_variant_pair(
            candidate_change_id=candidate_change_id,
            baseline_change_id=baseline_change_id,
            eval_harness_config_override=eval_harness_config_override,
            proposal_metadata=proposal_metadata,
        )
        benchmark_reports: List[Dict[str, Any]] = []
        structured_reports: List[Dict[str, Any]] = []
        for run_index in range(safe_runs):
            run_label = f"run {run_index + 1}/{safe_runs}"
            _progress("Running benchmark", run_label, "benchmark")
            benchmark_report = await run_benchmark(
                api_url=api_url,
                run_label=f"{candidate_change_id}-benchmark-{run_index + 1}",
                eval_harness_config_override=eval_harness_config_override,
                progress_fn=progress_fn,
            )
            benchmark_reports.append(benchmark_report)
            persist_benchmark_report(
                self.storage,
                benchmark_report,
                candidate_change_id=candidate_change_id,
                run_mode="weak_eval" if candidate_change_id != "baseline" else "baseline",
                model_id="benchmark/harness",
                variant_assignment=variant_assignment,
            )
            _progress("Running structured eval", run_label, "structured eval")
            structured_report = await run_structured_eval(
                api_url=api_url,
                suite_name=suite_name,
                run_label=f"{candidate_change_id}-structured-{run_index + 1}",
                eval_harness_config_override=eval_harness_config_override,
                progress_fn=progress_fn,
            )
            structured_reports.append(structured_report)
            persist_structured_eval_report(
                self.storage,
                structured_report,
                candidate_change_id=candidate_change_id,
                run_mode="weak_eval" if candidate_change_id != "baseline" else "baseline",
                model_id="structured_eval/harness",
                variant_assignment=variant_assignment,
            )
        _progress("Calculating metrics", str(candidate_change_id or "candidate"), "metrics")
        candidate_metrics = self._gather_metrics(candidate_change_id)
        baseline_metrics = self._gather_metrics(baseline_change_id)
        deltas = self._calculate_deltas(baseline_metrics, candidate_metrics)
        _progress("Assessing promotion readiness", suite_name, "promotion readiness")
        promotion_readiness = build_promotion_readiness(
            self.storage,
            evaluation_kind="full_eval",
            benchmark_reports=benchmark_reports,
            structured_reports=structured_reports,
        )
        recommendation = decide_benchmark_promotion(deltas, promotion_readiness)
        _progress("Reviewing eval trace", suite_name, "trace review")
        diagnostic_review = await review_eval_trace(
            self.model,
            candidate_change_id=candidate_change_id,
            baseline_change_id=baseline_change_id,
            benchmark_reports=benchmark_reports,
            structured_reports=structured_reports,
            suite_name=suite_name,
        )
        _progress("Scoring calibration", str(candidate_change_id or "candidate"), "calibration")
        prediction_record = normalize_prediction(diagnostic_review)
        prediction_record["judge_tier"] = str(diagnostic_review.get("reviewer_tier") or "trainer")
        prediction_record["failure_family"] = str(diagnostic_review.get("failure_family") or "")
        prediction_outcome = {
            "candidate_change_id": candidate_change_id,
            "actual_metrics": candidate_metrics,
            "actual_delta": deltas,
            "promotion_result": recommendation,
            "run_count": safe_runs,
            "observed_domains": ["benchmark", "structured_eval", suite_name],
        }
        calibration_record = score_prediction_against_outcome(
            prediction_record,
            actual_delta=deltas,
            promotion_result=recommendation,
            observed_domains=prediction_outcome["observed_domains"],
            run_count=safe_runs,
        )
        judge_trust_snapshot = update_judge_trust(
            self.storage,
            judge_tier=prediction_record["judge_tier"],
            prediction=prediction_record,
            calibration_record=calibration_record,
        )
        _progress("Recording eval evidence", recommendation, "persist evidence")
        attention_signal = emit_eval_attention_signal(
            self.storage,
            candidate_change_id=candidate_change_id,
            evaluation_kind=f"full_eval:{suite_name}",
            prediction_record=prediction_record,
            calibration_record=calibration_record,
            recommendation=recommendation,
            deltas=deltas,
        )
        variant_rating_snapshot = self._record_variant_outcome(
            domain=f"eval_harness_full_eval:{suite_name}",
            variant_assignment=variant_assignment,
            candidate_change_id=candidate_change_id,
            actual_delta=deltas,
            recommendation=recommendation,
        )
        ab_evidence_summary = self._record_ab_run_evidence(
            domain=f"eval_harness_full_eval:{suite_name}",
            variant_assignment=variant_assignment,
            candidate_change_id=candidate_change_id,
            benchmark_reports=benchmark_reports,
            structured_reports=structured_reports,
        )
        _progress("Persisting full eval report", recommendation, "persist report")
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
            diagnostic_review=diagnostic_review,
            prediction_record=prediction_record,
            prediction_outcome=prediction_outcome,
            calibration_record=calibration_record,
            judge_trust_snapshot=judge_trust_snapshot,
            ab_evidence_summary={**(ab_evidence_summary or {}), "attention_signal": attention_signal},
            variant_assignment=variant_assignment,
            variant_rating_snapshot=variant_rating_snapshot,
            source_task_id=source_task_id,
            spawned_task_ids=spawned_task_ids,
            associated_task_ids=associated_task_ids,
        )
        _progress("Full eval complete", recommendation, "full eval complete")
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
            diagnostic_review={},
            ab_evidence_summary={"matchup_count": 0, "recent_matchups": []},
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
        context = AgentExecutionContext(
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
                execution_context="agent",
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
