"""
@module feedback.signals
@purpose Durable registry for generic feedback and attention signals from users or internal agents.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from uuid import uuid4

from strata.prioritization.feedback import classify_feedback_priority


FEEDBACK_SIGNAL_INDEX_KEY = "feedback_signal:index"
MAX_FEEDBACK_SIGNALS = 600


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_feedback_signals(
    storage,
    *,
    limit: int = 100,
    session_id: Optional[str] = None,
    source_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    rows = storage.parameters.peek_parameter(FEEDBACK_SIGNAL_INDEX_KEY, default_value=[]) or []
    if not isinstance(rows, list):
        rows = []
    signals = [dict(row) for row in rows if isinstance(row, dict)]
    if session_id:
        signals = [row for row in signals if str(row.get("session_id") or "") == str(session_id)]
    if source_type:
        signals = [row for row in signals if str(row.get("source_type") or "") == str(source_type)]
    safe_limit = max(1, min(int(limit), MAX_FEEDBACK_SIGNALS))
    return signals[-safe_limit:]


def _append_feedback_signal(storage, signal: Dict[str, Any]) -> Dict[str, Any]:
    rows = storage.parameters.peek_parameter(FEEDBACK_SIGNAL_INDEX_KEY, default_value=[]) or []
    if not isinstance(rows, list):
        rows = []
    rows.append(dict(signal))
    storage.parameters.set_parameter(
        FEEDBACK_SIGNAL_INDEX_KEY,
        rows[-MAX_FEEDBACK_SIGNALS:],
        description="Recent durable feedback and attention signals from users and internal agents.",
    )
    return signal


def prioritize_feedback_signal(
    *,
    signal_kind: str,
    signal_value: str,
    source_type: str,
    source_preview: str,
    expected_outcome: str = "",
    observed_outcome: str = "",
    recent_signals: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    normalized_kind = str(signal_kind or "").strip().lower()
    normalized_value = str(signal_value or "").strip().lower()
    recent = list(recent_signals or [])

    if normalized_kind == "reaction":
        return classify_feedback_priority(
            message=SimpleNamespace(content=source_preview),
            reaction=normalized_value,
            action="added",
            recent_events=recent,
        )

    if normalized_kind in {"surprise", "unexpected_success", "unexpected_failure"}:
        surprise_score = 0.92 if normalized_kind == "surprise" else 0.88
        return {
            "priority": "urgent",
            "reason_family": "expectation_violation",
            "target_surface": "agent_knowledge" if source_type != "eval" else "telemetry",
            "should_interrupt": True,
            "should_batch": False,
            "needs_clarification": False,
            "expected_user_response": expected_outcome or "expected",
            "observed_user_response": observed_outcome or signal_value,
            "surprise_score": surprise_score,
            "alignment_risk": 0.78,
            "message_kind": source_type,
            "reaction_cluster_score": 0.0,
            "confidence": 0.81,
            "rationale": "Explicitly flagged surprise or expectation violation should receive immediate attention.",
            "llm_adjudication": None,
        }

    if normalized_kind in {"importance", "highlight", "emphasize"}:
        return {
            "priority": "review_soon",
            "reason_family": "explicit_attention_request",
            "target_surface": "agent_knowledge",
            "should_interrupt": False,
            "should_batch": False,
            "needs_clarification": False,
            "expected_user_response": expected_outcome or "neutral",
            "observed_user_response": observed_outcome or signal_value,
            "surprise_score": 0.58,
            "alignment_risk": 0.42,
            "message_kind": source_type,
            "reaction_cluster_score": 0.0,
            "confidence": 0.8,
            "rationale": "Explicit importance/highlight signals should be reviewed even without a strong mismatch.",
            "llm_adjudication": None,
        }

    if normalized_kind in {"response", "correction"}:
        return {
            "priority": "review_soon",
            "reason_family": "textual_feedback",
            "target_surface": "user_profile" if source_type in {"message", "session"} else "agent_knowledge",
            "should_interrupt": normalized_kind == "correction",
            "should_batch": False,
            "needs_clarification": normalized_kind == "correction",
            "expected_user_response": expected_outcome or "unknown",
            "observed_user_response": observed_outcome or signal_value,
            "surprise_score": 0.72 if normalized_kind == "correction" else 0.49,
            "alignment_risk": 0.74 if normalized_kind == "correction" else 0.44,
            "message_kind": source_type,
            "reaction_cluster_score": 0.0,
            "confidence": 0.76,
            "rationale": "Textual correction or response likely carries interpretable alignment signal.",
            "llm_adjudication": None,
        }

    return {
        "priority": "batch",
        "reason_family": "generic_feedback",
        "target_surface": "telemetry",
        "should_interrupt": False,
        "should_batch": True,
        "needs_clarification": False,
        "expected_user_response": expected_outcome or "unknown",
        "observed_user_response": observed_outcome or signal_value,
        "surprise_score": 0.35,
        "alignment_risk": 0.25,
        "message_kind": source_type,
        "reaction_cluster_score": 0.0,
        "confidence": 0.7,
        "rationale": "Generic signal recorded for later batch review.",
        "llm_adjudication": None,
    }


def register_feedback_signal(
    storage,
    *,
    source_type: str,
    source_id: str,
    signal_kind: str,
    signal_value: str,
    source_actor: str,
    session_id: str = "",
    source_preview: str = "",
    note: str = "",
    expected_outcome: str = "",
    observed_outcome: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    recent = list_feedback_signals(storage, session_id=session_id or None, limit=30)
    prioritization = prioritize_feedback_signal(
        signal_kind=signal_kind,
        signal_value=signal_value,
        source_type=source_type,
        source_preview=source_preview,
        expected_outcome=expected_outcome,
        observed_outcome=observed_outcome,
        recent_signals=recent,
    )
    signal = {
        "signal_id": f"signal_{uuid4().hex[:12]}",
        "source_type": str(source_type or "").strip(),
        "source_id": str(source_id or "").strip(),
        "signal_kind": str(signal_kind or "").strip().lower(),
        "signal_value": str(signal_value or "").strip(),
        "source_actor": str(source_actor or "").strip() or "system",
        "session_id": str(session_id or "").strip(),
        "source_preview": str(source_preview or "").strip()[:220],
        "note": str(note or "").strip()[:500],
        "expected_outcome": str(expected_outcome or "").strip(),
        "observed_outcome": str(observed_outcome or "").strip(),
        "metadata": dict(metadata or {}),
        "prioritization": prioritization,
        "created_at": _now(),
        "status": "queued_attention" if prioritization.get("priority") in {"review_soon", "urgent"} else "logged",
    }
    return _append_feedback_signal(storage, signal)
