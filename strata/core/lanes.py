"""
@module core.lanes
@purpose Shared lane normalization and ownership helpers.
"""

from __future__ import annotations

from typing import Any, Optional


VALID_LANES = {"trainer", "agent"}
VALID_WORK_POOLS = {"trainer", "local_agent", "remote_agent"}
WORK_POOL_ALIASES = {
    "agent": "local_agent",
}


def normalize_lane(value: Any) -> Optional[str]:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in VALID_LANES else None


def normalize_work_pool(value: Any) -> Optional[str]:
    normalized = str(value or "").strip().lower()
    normalized = WORK_POOL_ALIASES.get(normalized, normalized)
    return normalized if normalized in VALID_WORK_POOLS else None


def default_work_pool_for_lane(lane: Any) -> str:
    normalized_lane = normalize_lane(lane) or "agent"
    return "trainer" if normalized_lane == "trainer" else "local_agent"


def infer_lane_from_session_id(session_id: Optional[str]) -> Optional[str]:
    raw = str(session_id or "").strip().lower()
    if raw.startswith("trainer:"):
        return "trainer"
    if raw.startswith("agent:"):
        return "agent"
    return None


def canonical_session_id_for_lane(lane: Any, session_id: Optional[str] = None) -> str:
    normalized_lane = normalize_lane(lane) or "trainer"
    raw = str(session_id or "").strip()
    inferred_lane = infer_lane_from_session_id(raw)
    if inferred_lane == normalized_lane and raw:
        return raw
    if not raw or raw == "default":
        return f"{normalized_lane}:default"
    if inferred_lane and inferred_lane != normalized_lane:
        suffix = raw.split(":", 1)[1] if ":" in raw else raw
        suffix = suffix or "default"
        return f"{normalized_lane}:{suffix}"
    return f"{normalized_lane}:{raw}"


def session_matches_lane(session_id: Optional[str], lane: Any) -> bool:
    normalized_lane = normalize_lane(lane)
    if not normalized_lane:
        return True
    return infer_lane_from_session_id(session_id) == normalized_lane


def infer_lane_from_task(task: Any) -> Optional[str]:
    constraints = dict(getattr(task, "constraints", {}) or {})
    return normalize_lane(constraints.get("lane")) or infer_lane_from_session_id(getattr(task, "session_id", None))


def infer_work_pool_from_task(task: Any) -> Optional[str]:
    constraints = dict(getattr(task, "constraints", {}) or {})
    return (
        normalize_work_pool(constraints.get("work_pool"))
        or normalize_work_pool(constraints.get("execution_profile"))
        or (
            default_work_pool_for_lane(infer_lane_from_task(task))
            if infer_lane_from_task(task)
            else None
        )
    )


def infer_execution_profile_from_task(task: Any) -> Optional[str]:
    return infer_work_pool_from_task(task)
