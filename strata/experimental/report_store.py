"""
@module experimental.report_store
@purpose Persist experiment reports and attach them back to related tasks.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from strata.experimental.report_utils import build_report_task_associations, normalize_experiment_report
from strata.storage.models import ParameterModel


EXPERIMENT_REPORT_PREFIX = "experiment_report"
EXPERIMENT_REPORT_DESCRIPTION = (
    "Persisted benchmark/full-eval promotion summaries so the harness can reason "
    "about exact sampled runs instead of only blended historical metrics."
)


def report_parameter_key(candidate_change_id: str) -> str:
    return f"{EXPERIMENT_REPORT_PREFIX}:{candidate_change_id}"


def attach_report_to_tasks(storage, candidate_change_id: str, task_associations: Dict[str, Any]) -> None:
    report_ref = {
        "candidate_change_id": candidate_change_id,
        "linked_at": datetime.now(timezone.utc).isoformat(),
        "source_task_id": task_associations.get("source_task_id"),
    }
    for task_id in task_associations.get("associated_task_ids") or []:
        task = storage.tasks.get_by_id(task_id)
        if not task:
            continue
        constraints = dict(task.constraints or {})
        report_refs = list(constraints.get("associated_reports") or [])
        if not any(str(item.get("candidate_change_id")) == candidate_change_id for item in report_refs if isinstance(item, dict)):
            report_refs.append(dict(report_ref))
        constraints["associated_reports"] = report_refs[-25:]
        task.constraints = constraints


def persist_experiment_report(
    storage,
    result,
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
    diagnostic_review: Optional[Dict[str, Any]] = None,
    prediction_record: Optional[Dict[str, Any]] = None,
    prediction_outcome: Optional[Dict[str, Any]] = None,
    calibration_record: Optional[Dict[str, Any]] = None,
    judge_trust_snapshot: Optional[Dict[str, Any]] = None,
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
        "diagnostic_review": diagnostic_review or {},
        "prediction_record": prediction_record or {},
        "prediction_outcome": prediction_outcome or {},
        "calibration_record": calibration_record or {},
        "judge_trust_snapshot": judge_trust_snapshot or {},
        "task_associations": task_associations,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    storage.parameters.set_parameter(
        key=report_parameter_key(result.candidate_change_id),
        value=report_payload,
        description=EXPERIMENT_REPORT_DESCRIPTION,
    )
    attach_report_to_tasks(storage, result.candidate_change_id, task_associations)
    storage.commit()


def get_persisted_experiment_report(storage, candidate_change_id: str) -> Optional[Dict[str, Any]]:
    row = (
        storage.session.query(ParameterModel)
        .filter_by(key=report_parameter_key(candidate_change_id))
        .first()
    )
    if not row:
        return None
    report = normalize_experiment_report(row.value)
    return report or None
