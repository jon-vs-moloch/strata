"""
@module core.lanes
@purpose Shared lane normalization and ownership helpers.
"""

from __future__ import annotations

from typing import Any, Optional


VALID_LANES = {"trainer", "agent"}


def normalize_lane(value: Any) -> Optional[str]:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in VALID_LANES else None


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
