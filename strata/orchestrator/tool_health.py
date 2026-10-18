"""
@module orchestrator.tool_health
@purpose Record tool execution telemetry and throttle known-bad tools before they loop.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from strata.storage.models import TaskModel, TaskState, ToolExecutionEventModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_task_type(task_type: Optional[str]) -> Optional[str]:
    text = str(task_type or "").strip()
    return text or None


def _task_type_value(task: Optional[TaskModel]) -> Optional[str]:
    if task is None:
        return None
    value = getattr(getattr(task, "type", None), "value", None)
    return _normalize_task_type(value or getattr(task, "type", None))


def _classify_failure_kind(details: Dict[str, Any]) -> str:
    text_bits = [
        str(details.get("error") or ""),
        str(details.get("message") or ""),
        str(details.get("tool_result") or ""),
    ]
    haystack = " ".join(bit.strip().lower() for bit in text_bits if bit.strip())
    if not haystack:
        return "unknown"
    if "timed out" in haystack or "timeout" in haystack:
        return "timeout"
    if "rate limit" in haystack:
        return "rate_limit"
    if "not implemented" in haystack:
        return "not_implemented"
    if "not found" in haystack or "does not exist" in haystack or "missing" in haystack:
        return "missing"
    if "failed" in haystack or "error" in haystack:
        return "execution_error"
    return "unknown"


def record_tool_execution(
    storage,
    *,
    tool_name: str,
    outcome: str,
    lane: Optional[str] = None,
    task_type: Optional[str] = None,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
    source: Optional[str] = None,
    failure_kind: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> ToolExecutionEventModel:
    payload = dict(details or {})
    event = ToolExecutionEventModel(
        tool_name=str(tool_name or "").strip(),
        outcome=str(outcome or "success").strip().lower(),
        lane=str(lane or "").strip() or None,
        task_type=_normalize_task_type(task_type),
        task_id=str(task_id or "").strip() or None,
        session_id=str(session_id or "").strip() or None,
        source=str(source or "").strip() or None,
        failure_kind=str(failure_kind or _classify_failure_kind(payload)).strip() or None,
        details=payload,
    )
    storage.session.add(event)
    return event


def _tool_repair_after(storage, *, tool_name: str, after: datetime) -> bool:
    for task in storage.session.query(TaskModel).all():
        constraints = dict(getattr(task, "constraints", {}) or {})
        if str(constraints.get("tool_modification_target") or "").strip() != str(tool_name or "").strip():
            continue
        if str(constraints.get("target_scope") or "").strip().lower() != "tooling":
            continue
        if getattr(task, "state", None) not in {TaskState.CANCELLED, TaskState.ABANDONED}:
            return True
    return False


def assess_tool_health(
    storage,
    *,
    tool_name: str,
    lane: Optional[str] = None,
    task_type: Optional[str] = None,
    recent_limit: int = 6,
) -> Dict[str, Any]:
    normalized_tool = str(tool_name or "").strip()
    normalized_lane = str(lane or "").strip() or None
    normalized_task_type = _normalize_task_type(task_type)
    if not normalized_tool:
        return {"status": "unknown", "reason": "missing_tool_name"}

    scoped_query = (
        storage.session.query(ToolExecutionEventModel)
        .filter(ToolExecutionEventModel.tool_name == normalized_tool)
        .order_by(ToolExecutionEventModel.created_at.desc())
    )
    global_events = scoped_query.limit(max(4, recent_limit)).all()
    scoped_events = [
        row
        for row in global_events
        if (normalized_lane is None or row.lane == normalized_lane)
        and (normalized_task_type is None or row.task_type == normalized_task_type)
    ][:recent_limit]

    def _status_for(events) -> tuple[str, Optional[ToolExecutionEventModel], str]:
        if not events:
            return "healthy", None, ""
        failures = [row for row in events if row.outcome in {"degraded", "broken", "blocked"}]
        broken = [row for row in events if row.outcome in {"broken", "blocked"}]
        if len(broken) >= 2 or (
            len(events) >= 3 and all(row.outcome in {"degraded", "broken", "blocked"} for row in events[:3])
        ):
            return "broken", failures[0], "repeated recent failures"
        if len(failures) >= 2:
            return "degraded", failures[0], "multiple recent failures"
        return "healthy", None, ""

    scoped_status, scoped_event, scoped_reason = _status_for(scoped_events)
    global_status, global_event, global_reason = _status_for(global_events)
    last_scoped_failure = next((row for row in scoped_events if row.outcome in {"degraded", "broken", "blocked"}), None)
    last_global_failure = next((row for row in global_events if row.outcome in {"degraded", "broken", "blocked"}), None)
    event = scoped_event or global_event or last_scoped_failure or last_global_failure
    status = scoped_status if scoped_status != "healthy" else global_status
    reason = scoped_reason or global_reason
    if event and _tool_repair_after(storage, tool_name=normalized_tool, after=event.created_at):
        return {
            "status": "healthy",
            "scope": "repaired",
            "reason": "tool remediation exists after the last failing run",
            "tool_name": normalized_tool,
        }
    return {
        "status": status,
        "scope": "scoped" if scoped_status != "healthy" else "global",
        "reason": reason,
        "tool_name": normalized_tool,
        "lane": normalized_lane,
        "task_type": normalized_task_type,
        "last_failure_at": event.created_at.isoformat() if event else None,
        "failure_kind": getattr(event, "failure_kind", None) if event else None,
    }


def should_throttle_tool(
    storage,
    *,
    tool_name: str,
    lane: Optional[str] = None,
    task_type: Optional[str] = None,
) -> Dict[str, Any]:
    health = assess_tool_health(storage, tool_name=tool_name, lane=lane, task_type=task_type)
    status = str(health.get("status") or "healthy").strip().lower()
    if status == "broken":
        return {
            "throttle": True,
            "status": "broken",
            "reason": health.get("reason") or "tool is currently broken for this scope",
            "health": health,
        }
    if status == "degraded":
        return {
            "throttle": True,
            "status": "degraded",
            "reason": health.get("reason") or "tool is currently degraded for this scope",
            "health": health,
        }
    return {"throttle": False, "status": status, "health": health}


def tool_scope_for_task(task: Optional[TaskModel], *, session_id: Optional[str] = None, lane: Optional[str] = None) -> Dict[str, Optional[str]]:
    return {
        "lane": str(lane or (getattr(task, "constraints", {}) or {}).get("lane") or "").strip() or None,
        "task_type": _task_type_value(task),
        "task_id": str(getattr(task, "task_id", "") or "").strip() or None,
        "session_id": str(session_id or getattr(task, "session_id", "") or "").strip() or None,
    }
