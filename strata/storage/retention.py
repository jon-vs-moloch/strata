"""
@module storage.retention
@purpose Bound long-running storage growth without erasing the system's ability to reason from history.

Strata should not accumulate raw traces forever. This module applies conservative
retention defaults: keep active/recent data lossless, compact older terminal
history into summaries, and preserve enough aggregate signal for telemetry and
operator inspection.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from sqlalchemy.exc import OperationalError

from strata.storage.models import (
    MetricModel,
    TaskState,
)
from strata.storage.retention_compactors import (
    compact_attempts,
    compact_experiment_reports,
    compact_metrics,
    get_attempt_archive_summary as _get_attempt_archive_summary,
    get_metric_archive_summary as _get_metric_archive_summary,
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


def get_metric_archive_summary(storage) -> Dict[str, Any]:
    return _get_metric_archive_summary(storage, METRIC_ARCHIVE_KEY)


def get_attempt_archive_summary(storage) -> Dict[str, Any]:
    return _get_attempt_archive_summary(storage, ATTEMPT_ARCHIVE_KEY)


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

    try:
        summary = {
            "skipped": False,
            "policy": policy,
            "messages": _compact_messages(storage, policy),
            "metrics": compact_metrics(
                storage,
                policy,
                metric_archive_key=METRIC_ARCHIVE_KEY,
                utcnow=_utcnow,
                as_utc=_as_utc,
                metric_group_signature=_metric_group_signature,
                metric_group_to_dict=_metric_group_to_dict,
            ),
            "attempts": compact_attempts(
                storage,
                policy,
                attempt_archive_key=ATTEMPT_ARCHIVE_KEY,
                utcnow=_utcnow,
                as_utc=_as_utc,
                terminal_task_states=TERMINAL_TASK_STATES,
                truncate_text=_truncate_text,
            ),
            "experiment_reports": compact_experiment_reports(
                storage,
                policy,
                terminal_task_states=TERMINAL_TASK_STATES,
            ),
            "completed_at": _utcnow().isoformat(),
        }
        _set_retention_runtime(storage, summary)
        storage.commit()
        return summary
    except OperationalError as exc:
        if "database is locked" not in str(exc).lower():
            raise
        storage.rollback()
        summary = {
            "skipped": True,
            "reason": "database_locked",
            "policy": policy,
        }
        try:
            _set_retention_runtime(storage, summary)
            storage.commit()
        except OperationalError:
            storage.rollback()
        return summary
