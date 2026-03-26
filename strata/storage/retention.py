"""
@module storage.retention
@purpose Bound long-running storage growth without erasing the system's ability to reason from history.

Strata should not accumulate raw traces forever. This module applies conservative
retention defaults: keep active/recent data lossless, compact older terminal
history into summaries, and preserve enough aggregate signal for telemetry and
operator inspection.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select

from strata.storage.models import (
    AttemptModel,
    AttemptOutcome,
    MetricModel,
    MessageModel,
    ParameterModel,
    TaskModel,
    TaskState,
)


DB_RETENTION_POLICY_KEY = "db_retention_policy"
DB_RETENTION_RUNTIME_KEY = "db_retention_runtime"
MESSAGE_ARCHIVE_PREFIX = "message_archive"
METRIC_ARCHIVE_KEY = "metrics_archive_summary"
ATTEMPT_ARCHIVE_KEY = "attempt_archive_summary"

DB_RETENTION_POLICY_DESCRIPTION = (
    "Conservative retention policy that bounds raw DB growth while preserving "
    "summaries needed for telemetry, history, and operator visibility."
)

DEFAULT_DB_RETENTION_POLICY: Dict[str, Any] = {
    "enabled": True,
    "cooldown_minutes": 30,
    "message_keep_per_session": 200,
    "message_archive_epoch_limit": 24,
    "metric_raw_keep_days": 3,
    "metric_raw_keep_count": 5000,
    "metric_archive_group_limit": 2000,
    "terminal_attempt_keep_per_task": 3,
    "terminal_attempt_compaction_days": 7,
    "experiment_keep_full_reports": 64,
    "experiment_keep_promoted_full_reports": 32,
}

TERMINAL_TASK_STATES = {
    TaskState.COMPLETE,
    TaskState.ABANDONED,
    TaskState.CANCELLED,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_experiment_report(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    current = value.get("current")
    if isinstance(current, dict):
        return current
    if "candidate_change_id" in value or "evaluation_kind" in value or "recommendation" in value:
        return value
    return {}


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if not dt:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _truncate_text(value: str, limit: int = 180) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _message_archive_key(session_id: str) -> str:
    return f"{MESSAGE_ARCHIVE_PREFIX}:{session_id}"


def get_retention_policy(storage) -> Dict[str, Any]:
    raw = storage.parameters.peek_parameter(
        DB_RETENTION_POLICY_KEY,
        default_value=DEFAULT_DB_RETENTION_POLICY,
    ) or DEFAULT_DB_RETENTION_POLICY
    policy = dict(DEFAULT_DB_RETENTION_POLICY)
    if isinstance(raw, dict):
        policy.update(raw)
    for key in (
        "cooldown_minutes",
        "message_keep_per_session",
        "message_archive_epoch_limit",
        "metric_raw_keep_days",
        "metric_raw_keep_count",
        "metric_archive_group_limit",
        "terminal_attempt_keep_per_task",
        "terminal_attempt_compaction_days",
        "experiment_keep_full_reports",
        "experiment_keep_promoted_full_reports",
    ):
        policy[key] = _safe_int(policy.get(key), DEFAULT_DB_RETENTION_POLICY[key])
    policy["enabled"] = bool(policy.get("enabled", True))
    return policy


def get_retention_runtime(storage) -> Dict[str, Any]:
    runtime = storage.parameters.peek_parameter(
        DB_RETENTION_RUNTIME_KEY,
        default_value={"last_run_at": None, "last_summary": {}},
    )
    return runtime if isinstance(runtime, dict) else {"last_run_at": None, "last_summary": {}}


def _set_retention_runtime(storage, summary: Dict[str, Any]) -> None:
    runtime = {
        "last_run_at": _utcnow().isoformat(),
        "last_summary": summary,
    }
    storage.parameters.set_parameter(
        DB_RETENTION_RUNTIME_KEY,
        runtime,
        description="Last DB retention maintenance run and its compaction summary.",
    )


def _merge_archive_epochs(
    existing: Dict[str, Any],
    epoch: Dict[str, Any],
    *,
    limit: int,
) -> Dict[str, Any]:
    payload = dict(existing or {})
    epochs = list(payload.get("epochs") or [])
    aggregate = dict(payload.get("aggregate") or {})
    epochs.append(epoch)
    if len(epochs) > limit:
        overflow = epochs[:-limit]
        epochs = epochs[-limit:]
        aggregate["archived_count"] = int(aggregate.get("archived_count", 0)) + sum(
            int(item.get("archived_count", 0) or 0) for item in overflow
        )
        aggregate["first_archived_at"] = (
            aggregate.get("first_archived_at")
            or (overflow[0].get("first_created_at") if overflow else None)
        )
        if overflow:
            aggregate["last_archived_at"] = overflow[-1].get("archived_at")
    payload["epochs"] = epochs
    payload["aggregate"] = aggregate
    payload["last_archived_at"] = epoch.get("archived_at")
    return payload


def _compact_messages(storage, policy: Dict[str, Any]) -> Dict[str, Any]:
    keep_per_session = max(1, policy["message_keep_per_session"])
    epoch_limit = max(1, policy["message_archive_epoch_limit"])
    sessions = storage.messages.get_sessions()
    archived_messages = 0
    touched_sessions = 0

    for session_id in sessions:
        messages = storage.messages.get_all(session_id=session_id)
        if len(messages) <= keep_per_session:
            continue

        to_archive = messages[:-keep_per_session]
        if not to_archive:
            continue

        summary = {
            "session_id": session_id,
            "archived_at": _utcnow().isoformat(),
            "archived_count": len(to_archive),
            "first_created_at": to_archive[0].created_at.isoformat() if to_archive[0].created_at else None,
            "last_created_at": to_archive[-1].created_at.isoformat() if to_archive[-1].created_at else None,
            "sample_messages": [
                {
                    "role": msg.role,
                    "content": _truncate_text(msg.content, 120),
                    "created_at": msg.created_at.isoformat() if msg.created_at else None,
                }
                for msg in (to_archive[:2] + to_archive[-2:])[:4]
            ],
            "summary": f"{len(to_archive)} older messages archived for session '{session_id}'.",
        }
        archive_key = _message_archive_key(session_id)
        existing = storage.parameters.peek_parameter(
            archive_key,
            default_value={"session_id": session_id, "epochs": [], "aggregate": {}},
        ) or {"session_id": session_id, "epochs": [], "aggregate": {}}
        updated = _merge_archive_epochs(existing, summary, limit=epoch_limit)
        updated["session_id"] = session_id
        storage.parameters.set_parameter(
            archive_key,
            updated,
            description=f"Archived message summaries for session '{session_id}'.",
        )

        for msg in to_archive:
            storage.session.delete(msg)
        archived_messages += len(to_archive)
        touched_sessions += 1

    return {
        "archived_messages": archived_messages,
        "touched_sessions": touched_sessions,
    }


def _metric_group_signature(metric: MetricModel) -> Tuple[Any, ...]:
    day = metric.timestamp.date().isoformat() if metric.timestamp else "unknown"
    return (
        day,
        metric.metric_name,
        metric.model_id,
        metric.task_type,
        metric.run_mode,
        metric.execution_context,
        metric.candidate_change_id,
    )


def _metric_group_to_dict(signature: Tuple[Any, ...], group: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "date": signature[0],
        "metric_name": signature[1],
        "model_id": signature[2],
        "task_type": signature[3],
        "run_mode": signature[4],
        "execution_context": signature[5],
        "candidate_change_id": signature[6],
        "count": int(group["count"]),
        "sum_value": float(group["sum_value"]),
        "min_value": float(group["min_value"]),
        "max_value": float(group["max_value"]),
        "last_seen": group["last_seen"],
    }


def _merge_metric_archive(
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


def get_metric_archive_summary(storage) -> Dict[str, Any]:
    payload = storage.parameters.peek_parameter(
        METRIC_ARCHIVE_KEY,
        default_value={"groups": [], "archived_row_count": 0, "candidate_ids": []},
    )
    return payload if isinstance(payload, dict) else {"groups": [], "archived_row_count": 0, "candidate_ids": []}


def _compact_metrics(storage, policy: Dict[str, Any]) -> Dict[str, Any]:
    raw_keep_days = max(1, policy["metric_raw_keep_days"])
    raw_keep_count = max(1, policy["metric_raw_keep_count"])
    archive_group_limit = max(1, policy["metric_archive_group_limit"])
    cutoff = _utcnow() - timedelta(days=raw_keep_days)

    metrics = (
        storage.session.query(MetricModel)
        .order_by(MetricModel.timestamp.desc(), MetricModel.id.desc())
        .all()
    )
    if len(metrics) <= raw_keep_count:
        return {"archived_metrics": 0, "archive_groups": 0}

    keep_ids = {metric.id for metric in metrics[:raw_keep_count]}
    old_metrics = [
        metric
        for metric in metrics[raw_keep_count:]
        if _as_utc(metric.timestamp) and _as_utc(metric.timestamp) < cutoff
    ]
    if not old_metrics:
        return {"archived_metrics": 0, "archive_groups": 0}

    grouped: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    candidate_ids = set()
    for metric in old_metrics:
        signature = _metric_group_signature(metric)
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

    archive_payload = get_metric_archive_summary(storage)
    new_groups = [_metric_group_to_dict(signature, group) for signature, group in grouped.items()]
    merged_groups = _merge_metric_archive(
        archive_payload.get("groups") or [],
        new_groups,
        limit=archive_group_limit,
    )
    archived_candidate_ids = set(str(item) for item in archive_payload.get("candidate_ids") or [])
    archived_candidate_ids.update(candidate_ids)
    updated_archive = {
        "groups": merged_groups,
        "archived_row_count": int(archive_payload.get("archived_row_count", 0)) + len(old_metrics),
        "candidate_ids": sorted(archived_candidate_ids),
        "last_compacted_at": _utcnow().isoformat(),
    }
    storage.parameters.set_parameter(
        METRIC_ARCHIVE_KEY,
        updated_archive,
        description="Compacted metric history summaries for long-running telemetry.",
    )
    for metric in old_metrics:
        storage.session.delete(metric)
    return {
        "archived_metrics": len(old_metrics),
        "archive_groups": len(new_groups),
    }


def get_attempt_archive_summary(storage) -> Dict[str, Any]:
    payload = storage.parameters.peek_parameter(
        ATTEMPT_ARCHIVE_KEY,
        default_value={"archived_count": 0, "by_outcome": {}, "by_resolution": {}, "tasks": []},
    )
    return payload if isinstance(payload, dict) else {"archived_count": 0, "by_outcome": {}, "by_resolution": {}, "tasks": []}


def _compact_attempts(storage, policy: Dict[str, Any]) -> Dict[str, Any]:
    keep_per_task = max(1, policy["terminal_attempt_keep_per_task"])
    compaction_days = max(1, policy["terminal_attempt_compaction_days"])
    cutoff = _utcnow() - timedelta(days=compaction_days)

    terminal_tasks = (
        storage.session.query(TaskModel)
        .filter(TaskModel.state.in_(list(TERMINAL_TASK_STATES)))
        .all()
    )
    archive_payload = get_attempt_archive_summary(storage)
    by_outcome = dict(archive_payload.get("by_outcome") or {})
    by_resolution = dict(archive_payload.get("by_resolution") or {})
    task_entries = list(archive_payload.get("tasks") or [])

    archived_attempts = 0
    touched_tasks = 0

    for task in terminal_tasks:
        updated_at = _as_utc(task.updated_at) or _as_utc(task.created_at)
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
        archived_summary["last_archived_at"] = _utcnow().isoformat()
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
                "title": _truncate_text(task.title, 80),
                "archived_attempt_count": len(to_archive),
                "recorded_at": _utcnow().isoformat(),
            }
        )

    updated_archive = {
        "archived_count": int(archive_payload.get("archived_count", 0)) + archived_attempts,
        "by_outcome": by_outcome,
        "by_resolution": by_resolution,
        "tasks": task_entries[-100:],
        "last_compacted_at": _utcnow().isoformat() if archived_attempts else archive_payload.get("last_compacted_at"),
    }
    storage.parameters.set_parameter(
        ATTEMPT_ARCHIVE_KEY,
        updated_archive,
        description="Aggregate summary of archived task attempts compacted out of the hot DB tail.",
    )
    return {
        "archived_attempts": archived_attempts,
        "touched_tasks": touched_tasks,
    }


def _compact_experiment_reports(storage, policy: Dict[str, Any]) -> Dict[str, Any]:
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
        current = _normalize_experiment_report(row.value)
        if not isinstance(current, dict):
            continue
        candidate_change_id = str(current.get("candidate_change_id") or "")
        task_associations = current.get("task_associations") or {}
        linked_task_ids = [
            str(task_id)
            for task_id in (task_associations.get("associated_task_ids") or [])
            if str(task_id or "").strip()
        ]
        if linked_task_ids:
            linked_tasks = [storage.tasks.get_by_id(task_id) for task_id in linked_task_ids]
            if any(task and task.state not in TERMINAL_TASK_STATES for task in linked_tasks):
                continue
        if index < keep_full or candidate_change_id in protected:
            continue
        if current.get("payload_compacted"):
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
        if isinstance(row.value, dict) and isinstance(row.value.get("current"), dict):
            row.value = {
                **row.value,
                "current": compacted_payload,
            }
        else:
            row.value = compacted_payload
        compacted += 1
    return {"compacted_reports": compacted}


def run_retention_maintenance(storage, *, force: bool = False) -> Dict[str, Any]:
    policy = get_retention_policy(storage)
    runtime = get_retention_runtime(storage)
    if not policy.get("enabled", True):
        summary = {"skipped": True, "reason": "disabled", "policy": policy}
        _set_retention_runtime(storage, summary)
        storage.commit()
        return summary

    if not force and runtime.get("last_run_at"):
        try:
            last_run = datetime.fromisoformat(str(runtime["last_run_at"]))
            cooldown = timedelta(minutes=max(1, policy["cooldown_minutes"]))
            if _as_utc(last_run) and _utcnow() - _as_utc(last_run) < cooldown:
                return {
                    "skipped": True,
                    "reason": "cooldown",
                    "last_run_at": runtime.get("last_run_at"),
                    "policy": policy,
                }
        except Exception:
            pass

    summary = {
        "skipped": False,
        "policy": policy,
        "messages": _compact_messages(storage, policy),
        "metrics": _compact_metrics(storage, policy),
        "attempts": _compact_attempts(storage, policy),
        "experiment_reports": _compact_experiment_reports(storage, policy),
        "completed_at": _utcnow().isoformat(),
    }
    _set_retention_runtime(storage, summary)
    storage.commit()
    return summary
