"""
@module prioritization.feedback
@purpose Surprise-first prioritization for high-frequency user feedback signals.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


SURPRISE_HIGH = 0.85
SURPRISE_MEDIUM = 0.6


def _message_preview(message: Any) -> str:
    return str(getattr(message, "content", "") or "").strip()


def _message_kind(text: str) -> str:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return "empty"
    if re.search(r"\b(good morning|good evening|good afternoon|hello|hi there|hey)\b", normalized) and len(normalized) <= 120:
        return "greeting"
    if "?" in normalized:
        return "question"
    if "\n" in normalized or len(normalized) >= 180:
        return "substantive"
    if len(normalized) <= 40:
        return "brief"
    return "answer"


def _observed_response(reaction: str) -> str:
    mapping = {
        "thumbs_up": "positive",
        "heart": "positive",
        "emphasis": "salient",
        "thumbs_down": "negative",
        "confused": "confused",
    }
    return mapping.get(str(reaction or "").strip().lower(), "unknown")


def _expected_response(message_kind: str) -> str:
    if message_kind == "greeting":
        return "neutral_or_positive"
    if message_kind in {"substantive", "answer", "question"}:
        return "positive_or_clarifying"
    return "neutral"


def _reaction_cluster_score(reaction: str, recent_events: List[Dict[str, Any]]) -> float:
    normalized = str(reaction or "").strip().lower()
    same = [
        event for event in recent_events
        if str(event.get("action") or "").strip().lower() == "added"
        and str(event.get("reaction") or "").strip().lower() == normalized
    ]
    return min(1.0, len(same) / 3.0)


def classify_feedback_priority(
    *,
    message: Any,
    reaction: str,
    action: str,
    recent_events: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    normalized_reaction = str(reaction or "").strip().lower()
    normalized_action = str(action or "").strip().lower()
    text = _message_preview(message)
    kind = _message_kind(text)
    observed = _observed_response(normalized_reaction)
    expected = _expected_response(kind)
    recent = list(recent_events or [])
    cluster_score = _reaction_cluster_score(normalized_reaction, recent)

    surprise_score = 0.15
    alignment_risk = 0.1
    priority = "ignore"
    target_surface = "telemetry"
    reason_family = "routine_feedback"
    should_interrupt = False
    should_batch = True
    needs_clarification = False
    rationale = "Routine low-stakes feedback."

    if normalized_action != "added":
        rationale = "Removed reactions should update telemetry but do not require attention."
    elif normalized_reaction == "confused":
        surprise_score = 0.95 if kind == "greeting" else 0.9
        alignment_risk = 0.9
        priority = "urgent"
        target_surface = "user_profile"
        reason_family = "unexpected_confusion"
        should_interrupt = True
        should_batch = False
        needs_clarification = True
        rationale = "Confusion is a strong sign the system's current model of the user or situation may be wrong."
    elif normalized_reaction == "thumbs_down":
        if kind == "greeting":
            surprise_score = 0.88
            alignment_risk = 0.82
            priority = "urgent"
            target_surface = "user_profile"
            reason_family = "unexpected_negative_on_greeting"
            should_interrupt = True
            should_batch = False
            rationale = "Negative feedback on a low-stakes greeting is surprising and suggests a user-model mismatch."
        else:
            surprise_score = 0.68
            alignment_risk = 0.72
            priority = "review_soon"
            target_surface = "agent_knowledge"
            reason_family = "negative_feedback"
            should_interrupt = False
            should_batch = False
            rationale = "Negative feedback on substantive behavior deserves review but may not require immediate interruption."
    elif normalized_reaction == "emphasis":
        surprise_score = 0.55 if kind in {"substantive", "answer"} else 0.35
        alignment_risk = 0.35
        priority = "batch" if kind == "greeting" else "review_soon"
        target_surface = "user_profile" if kind == "greeting" else "agent_knowledge"
        reason_family = "salient_feedback"
        should_batch = not (kind in {"substantive", "answer"} and cluster_score >= 0.67)
        rationale = "Emphasis indicates salience and can reveal preferences, but it is usually lower urgency than confusion."
    elif normalized_reaction in {"thumbs_up", "heart"}:
        if kind == "greeting":
            surprise_score = 0.12
            alignment_risk = 0.05
            priority = "ignore"
            target_surface = "telemetry"
            should_batch = True
            rationale = "Positive feedback on an expected-friendly greeting is low surprise."
        else:
            surprise_score = 0.42 if normalized_reaction == "heart" else 0.28
            alignment_risk = 0.12
            priority = "batch"
            target_surface = "user_profile"
            reason_family = "positive_preference_signal"
            should_batch = True
            rationale = "Positive feedback can reveal preferences, but most positive reactions should batch unless unusually surprising."

    if cluster_score >= 0.67 and priority in {"batch", "review_soon"}:
        surprise_score = max(surprise_score, SURPRISE_MEDIUM)
        alignment_risk = max(alignment_risk, 0.55)
        priority = "review_soon"
        rationale = f"{rationale} Repetition increases confidence that this pattern matters."

    return {
        "priority": priority,
        "reason_family": reason_family,
        "target_surface": target_surface,
        "should_interrupt": should_interrupt,
        "should_batch": should_batch,
        "needs_clarification": needs_clarification,
        "expected_user_response": expected,
        "observed_user_response": observed,
        "surprise_score": round(surprise_score, 4),
        "alignment_risk": round(alignment_risk, 4),
        "message_kind": kind,
        "reaction_cluster_score": round(cluster_score, 4),
        "confidence": 0.72 if surprise_score >= SURPRISE_MEDIUM else 0.82,
        "rationale": rationale,
        "llm_adjudication": None,
    }
