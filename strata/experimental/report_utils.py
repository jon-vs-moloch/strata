"""
@module experimental.report_utils
@purpose Shared report helpers for experiment persistence, normalization, and weak-gain checks.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


def normalize_experiment_report(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    current = value.get("current")
    if isinstance(current, dict):
        return current
    if "candidate_change_id" in value or "evaluation_kind" in value or "recommendation" in value:
        return value
    return {}


def iter_experiment_reports(rows: List[Any]) -> List[Dict[str, Any]]:
    reports: List[Dict[str, Any]] = []
    for row in rows:
        report = normalize_experiment_report(getattr(row, "value", None))
        if report:
            reports.append(report)
    return reports


def report_has_weak_gain(report: Dict[str, Any]) -> bool:
    deltas = report.get("deltas") or {}
    return (
        float(deltas.get("structured_eval_harness_accuracy", 0.0) or 0.0) > 0.0
        or float(deltas.get("benchmark_harness_score", 0.0) or 0.0) > 0.0
        or float(deltas.get("benchmark_harness_win_rate", 0.0) or 0.0) > 0.0
    )


def build_report_task_associations(
    *,
    source_task_id: Optional[str] = None,
    spawned_task_ids: Optional[List[str]] = None,
    associated_task_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    ordered: List[str] = []
    for candidate in [source_task_id, *(spawned_task_ids or []), *(associated_task_ids or [])]:
        task_id = str(candidate or "").strip()
        if task_id and task_id not in ordered:
            ordered.append(task_id)
    return {
        "source_task_id": str(source_task_id or "").strip() or None,
        "spawned_task_ids": [task_id for task_id in (spawned_task_ids or []) if str(task_id).strip()],
        "associated_task_ids": ordered,
    }


class ExperimentResult(BaseModel):
    success: bool
    valid: bool
    candidate_change_id: str
    baseline_metrics: dict
    candidate_metrics: dict
    deltas: dict
    recommendation: str
    notes: str
