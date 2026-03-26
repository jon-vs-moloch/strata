"""
@module storage.retention_compactors
@purpose Heavy compaction helpers used by storage.retention.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, Iterable, List, Tuple

from strata.experimental.report_utils import normalize_experiment_report
from strata.storage.models import MetricModel, ParameterModel, TaskModel


def merge_metric_archive(
    existing_groups: Iterable[Dict[str, Any]],
    new_groups: Iterable[Dict[str, Any]],
    *,
    limit: int,
) -> List[Dict[str, Any]]:
    merged: Dict[Tuple[Any, ...], Dict[str, Any]] = {}

    def consume(group: Dict[str, Any]) -> None:
        signature = (
            group.get("date"),
            group.get("metric_name"),
            group.get("model_id"),
            group.get("task_type"),
            group.get("run_mode"),
            group.get("execution_context"),
            group.get("candidate_change_id"),
        )
        current = merged.get(signature)
        if not current:
            merged[signature] = dict(group)
            return
        current["count"] = int(current.get("count", 0)) + int(group.get("count", 0))
        current["sum_value"] = float(current.get("sum_value", 0.0)) + float(group.get("sum_value", 0.0))
        current["min_value"] = min(float(current.get("min_value", 0.0)), float(group.get("min_value", 0.0)))
        current["max_value"] = max(float(current.get("max_value", 0.0)), float(group.get("max_value", 0.0)))
        current["last_seen"] = max(str(current.get("last_seen") or ""), str(group.get("last_seen") or ""))

    for group in existing_groups:
        if isinstance(group, dict):
            consume(group)
    for group in new_groups:
        if isinstance(group, dict):
            consume(group)

    ordered = sorted(
        merged.values(),
        key=lambda item: (str(item.get("last_seen") or ""), str(item.get("metric_name") or "")),
        reverse=True,
    )
    return ordered[:limit]


def get_metric_archive_summary(storage, metric_archive_key: str) -> Dict[str, Any]:
    payload = storage.parameters.peek_parameter(
        metric_archive_key,
        default_value={"groups": [], "archived_row_count": 0, "candidate_ids": []},
    )
    return payload if isinstance(payload, dict) else {"groups": [], "archived_row_count": 0, "candidate_ids": []}


def compact_metrics(
    storage,
    policy: Dict[str, Any],
    *,
    metric_archive_key: str,
    utcnow,
    as_utc,
    metric_group_signature,
    metric_group_to_dict,
) -> Dict[str, Any]:
    raw_keep_days = max(1, policy["metric_raw_keep_days"])
    raw_keep_count = max(1, policy["metric_raw_keep_count"])
    archive_group_limit = max(1, policy["metric_archive_group_limit"])
    cutoff = utcnow() - timedelta(days=raw_keep_days)

    metrics = storage.session.query(MetricModel).order_by(MetricModel.timestamp.desc(), MetricModel.id.desc()).all()
    if len(metrics) <= raw_keep_count:
        return {"archived_metrics": 0, "archive_groups": 0}

    old_metrics = [
        metric
        for metric in metrics[raw_keep_count:]
        if as_utc(metric.timestamp) and as_utc(metric.timestamp) < cutoff
    ]
    if not old_metrics:
        return {"archived_metrics": 0, "archive_groups": 0}

    grouped: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    candidate_ids = set()
    for metric in old_metrics:
        signature = metric_group_signature(metric)
        current = grouped.setdefault(
            signature,
            {
                "count": 0,
                "sum_value": 0.0,
                "min_value": float(metric.value),
                "max_value": float(metric.value),
                "last_seen": metric.timestamp.isoformat() if metric.timestamp else None,
            },
        )
        current["count"] += 1
        current["sum_value"] += float(metric.value)
        current["min_value"] = min(float(current["min_value"]), float(metric.value))
        current["max_value"] = max(float(current["max_value"]), float(metric.value))
        last_seen = metric.timestamp.isoformat() if metric.timestamp else None
        if last_seen and str(last_seen) > str(current.get("last_seen") or ""):
            current["last_seen"] = last_seen
        if metric.candidate_change_id:
            candidate_ids.add(str(metric.candidate_change_id))

    archive_payload = get_metric_archive_summary(storage, metric_archive_key)
    new_groups = [metric_group_to_dict(signature, group) for signature, group in grouped.items()]
    merged_groups = merge_metric_archive(archive_payload.get("groups") or [], new_groups, limit=archive_group_limit)
    archived_candidate_ids = set(str(item) for item in archive_payload.get("candidate_ids") or [])
    archived_candidate_ids.update(candidate_ids)
    storage.parameters.set_parameter(
        metric_archive_key,
        {
            "groups": merged_groups,
            "archived_row_count": int(archive_payload.get("archived_row_count", 0)) + len(old_metrics),
            "candidate_ids": sorted(archived_candidate_ids),
            "last_compacted_at": utcnow().isoformat(),
        },
        description="Compacted metric history summaries for long-running telemetry.",
    )
    for metric in old_metrics:
        storage.session.delete(metric)
    return {"archived_metrics": len(old_metrics), "archive_groups": len(new_groups)}


def get_attempt_archive_summary(storage, attempt_archive_key: str) -> Dict[str, Any]:
    payload = storage.parameters.peek_parameter(
        attempt_archive_key,
        default_value={"archived_count": 0, "by_outcome": {}, "by_resolution": {}, "tasks": []},
    )
    return payload if isinstance(payload, dict) else {"archived_count": 0, "by_outcome": {}, "by_resolution": {}, "tasks": []}


def compact_attempts(
    storage,
    policy: Dict[str, Any],
    *,
    attempt_archive_key: str,
    utcnow,
    as_utc,
    terminal_task_states,
    truncate_text,
) -> Dict[str, Any]:
    keep_per_task = max(1, policy["terminal_attempt_keep_per_task"])
    compaction_days = max(1, policy["terminal_attempt_compaction_days"])
    cutoff = utcnow() - timedelta(days=compaction_days)

    terminal_tasks = storage.session.query(TaskModel).filter(TaskModel.state.in_(list(terminal_task_states))).all()
    archive_payload = get_attempt_archive_summary(storage, attempt_archive_key)
    by_outcome = dict(archive_payload.get("by_outcome") or {})
    by_resolution = dict(archive_payload.get("by_resolution") or {})
    task_entries = list(archive_payload.get("tasks") or [])
    archived_attempts = 0
    touched_tasks = 0

    for task in terminal_tasks:
        updated_at = as_utc(task.updated_at) or as_utc(task.created_at)
        if not updated_at or updated_at >= cutoff:
            continue
        attempts = storage.attempts.get_by_task_id(task.task_id)
        if len(attempts) <= keep_per_task:
            continue
        to_archive = attempts[keep_per_task:]
        if not to_archive:
            continue
        touched_tasks += 1
        archived_attempts += len(to_archive)
        archived_summary = dict((task.constraints or {}).get("archived_attempt_summary") or {})
        archived_summary["archived_count"] = int(archived_summary.get("archived_count", 0)) + len(to_archive)
        archived_summary["last_archived_at"] = utcnow().isoformat()
        archived_summary["latest_archived_attempt_started_at"] = (
            to_archive[0].started_at.isoformat() if to_archive and to_archive[0].started_at else None
        )
        outcome_counts = dict(archived_summary.get("by_outcome") or {})
        resolution_counts = dict(archived_summary.get("by_resolution") or {})

        for attempt in to_archive:
            if attempt.outcome:
                outcome_key = attempt.outcome.value
                outcome_counts[outcome_key] = int(outcome_counts.get(outcome_key, 0)) + 1
                by_outcome[outcome_key] = int(by_outcome.get(outcome_key, 0)) + 1
            if attempt.resolution:
                resolution_key = attempt.resolution.value
                resolution_counts[resolution_key] = int(resolution_counts.get(resolution_key, 0)) + 1
                by_resolution[resolution_key] = int(by_resolution.get(resolution_key, 0)) + 1
            storage.session.delete(attempt)

        archived_summary["by_outcome"] = outcome_counts
        archived_summary["by_resolution"] = resolution_counts
        constraints = dict(task.constraints or {})
        constraints["archived_attempt_summary"] = archived_summary
        task.constraints = constraints
        task_entries.append(
            {
                "task_id": task.task_id,
                "title": truncate_text(task.title, 80),
                "archived_attempt_count": len(to_archive),
                "recorded_at": utcnow().isoformat(),
            }
        )

    storage.parameters.set_parameter(
        attempt_archive_key,
        {
            "archived_count": int(archive_payload.get("archived_count", 0)) + archived_attempts,
            "by_outcome": by_outcome,
            "by_resolution": by_resolution,
            "tasks": task_entries[-100:],
            "last_compacted_at": utcnow().isoformat() if archived_attempts else archive_payload.get("last_compacted_at"),
        },
        description="Aggregate summary of archived task attempts compacted out of the hot DB tail.",
    )
    return {"archived_attempts": archived_attempts, "touched_tasks": touched_tasks}


def compact_experiment_reports(
    storage,
    policy: Dict[str, Any],
    *,
    terminal_task_states,
) -> Dict[str, Any]:
    keep_full = max(0, policy["experiment_keep_full_reports"])
    keep_promoted = max(0, policy["experiment_keep_promoted_full_reports"])
    promoted_state = storage.parameters.peek_parameter(
        "promoted_eval_candidates",
        default_value={"current": None, "history": []},
    ) or {"current": None, "history": []}
    promoted_ids = [str(item.get("candidate_change_id")) for item in promoted_state.get("history", []) if item.get("candidate_change_id")]
    protected = set(promoted_ids[-keep_promoted:])
    if promoted_state.get("current"):
        protected.add(str(promoted_state.get("current")))

    rows = (
        storage.session.query(ParameterModel)
        .filter(ParameterModel.key.like("experiment_report:%"))
        .order_by(ParameterModel.updated_at.desc())
        .all()
    )
    compacted = 0
    for index, row in enumerate(rows):
        current = normalize_experiment_report(row.value)
        if not isinstance(current, dict):
            continue
        candidate_change_id = str(current.get("candidate_change_id") or "")
        task_associations = current.get("task_associations") or {}
        linked_task_ids = [
            str(task_id) for task_id in (task_associations.get("associated_task_ids") or []) if str(task_id or "").strip()
        ]
        if linked_task_ids:
            linked_tasks = [storage.tasks.get_by_id(task_id) for task_id in linked_task_ids]
            if any(task and task.state not in terminal_task_states for task in linked_tasks):
                continue
        if index < keep_full or candidate_change_id in protected or current.get("payload_compacted"):
            continue
        compacted_payload = {
            "candidate_change_id": current.get("candidate_change_id"),
            "baseline_change_id": current.get("baseline_change_id"),
            "evaluation_kind": current.get("evaluation_kind"),
            "run_count": current.get("run_count"),
            "suite_name": current.get("suite_name"),
            "recommendation": current.get("recommendation"),
            "notes": current.get("notes"),
            "proposal_metadata": current.get("proposal_metadata") or {},
            "promotion_readiness": current.get("promotion_readiness") or {},
            "deltas": current.get("deltas") or {},
            "task_associations": task_associations,
            "recorded_at": current.get("recorded_at"),
            "payload_compacted": True,
            "summary": {
                "benchmark_report_count": len(current.get("benchmark_reports") or []),
                "structured_report_count": len(current.get("structured_reports") or []),
                "had_eval_harness_override": bool(current.get("eval_harness_config_override")),
                "code_validation_keys": sorted((current.get("code_validation") or {}).keys()),
                "associated_task_count": len(linked_task_ids),
            },
        }
        row.value = {**row.value, "current": compacted_payload} if isinstance(row.value, dict) and isinstance(row.value.get("current"), dict) else compacted_payload
        compacted += 1
    return {"compacted_reports": compacted}
