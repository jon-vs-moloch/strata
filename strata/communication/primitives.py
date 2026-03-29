"""
@module communication.primitives
@purpose Shared delivery primitive for system-originated communication.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Dict, Iterable, Optional, Tuple
from uuid import uuid4

from strata.core.lanes import canonical_session_id_for_lane, normalize_lane
from strata.messages.metadata import initialize_message_metadata
from strata.sessions.metadata import (
    DEFAULT_SESSION_TITLE,
    ensure_session_metadata,
    get_session_metadata,
    resolve_session_title,
    set_session_metadata,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_session_id(lane: str) -> str:
    normalized_lane = normalize_lane(lane) or "strong"
    return f"{normalized_lane}:session-{uuid4().hex[:12]}"


def build_communication_decision(
    *,
    role: str,
    content: str,
    lane: str = "strong",
    channel: str = "existing_session_message",
    session_id: Optional[str] = None,
    audience: str = "user",
    source_kind: str = "system",
    source_actor: str = "system_opened",
    opened_reason: str = "",
    tags: Optional[list[str]] = None,
    disclosability: str = "user_visible",
    topic_summary: str = "",
    session_title: str = "",
    communicative_act: str = "notification",
    response_kind: str = "",
    urgency: str = "normal",
    should_send: bool = True,
    allow_user_opened_reuse: bool = True,
) -> Dict[str, Any]:
    return {
        "should_send": bool(should_send),
        "role": role,
        "content": content,
        "lane": normalize_lane(lane) or "strong",
        "channel": str(channel or "existing_session_message").strip().lower(),
        "session_id": session_id,
        "audience": str(audience or "user").strip() or "user",
        "source_kind": str(source_kind or "system").strip() or "system",
        "source_actor": str(source_actor or "system_opened").strip() or "system_opened",
        "opened_reason": str(opened_reason or "").strip(),
        "tags": list(tags or []),
        "disclosability": str(disclosability or "user_visible").strip() or "user_visible",
        "topic_summary": str(topic_summary or "").strip(),
        "session_title": str(session_title or "").strip(),
        "communicative_act": str(communicative_act or "notification").strip().lower() or "notification",
        "response_kind": str(response_kind or "").strip().lower(),
        "urgency": str(urgency or "normal").strip().lower() or "normal",
        "allow_user_opened_reuse": bool(allow_user_opened_reuse),
    }


def _tag_overlap(left: Iterable[str], right: Iterable[str]) -> int:
    left_set = {str(item).strip().lower() for item in left if str(item).strip()}
    right_set = {str(item).strip().lower() for item in right if str(item).strip()}
    return len(left_set & right_set)


def _text_terms(*values: str) -> set[str]:
    terms: set[str] = set()
    for value in values:
        for token in re.findall(r"[a-z0-9][a-z0-9_-]+", str(value or "").lower()):
            if len(token) >= 4:
                terms.add(token)
    return terms


def _text_overlap_score(summary: Dict[str, Any], metadata: Dict[str, Any], decision: Dict[str, Any]) -> int:
    decision_terms = _text_terms(
        decision.get("content") or "",
        decision.get("topic_summary") or "",
        decision.get("session_title") or "",
    )
    if not decision_terms:
        return 0

    session_terms = _text_terms(
        summary.get("last_message_preview") or "",
        metadata.get("topic_summary") or "",
        resolve_session_title(metadata) or "",
    )
    if not session_terms:
        return 0

    overlap = len(decision_terms & session_terms)
    if overlap <= 0:
        return 0
    return min(6, overlap * 2)


def _session_reuse_score(summary: Dict[str, Any], metadata: Dict[str, Any], decision: Dict[str, Any]) -> int:
    decision_source_kind = str(decision.get("source_kind") or "").strip().lower()
    metadata_source_kind = str(metadata.get("source_kind") or "").strip().lower()
    metadata_opened_by = str(metadata.get("opened_by") or "").strip().lower()
    if metadata_opened_by == "user_opened" and not bool(decision.get("allow_user_opened_reuse", True)):
        return -1
    if (
        decision_source_kind
        and metadata_source_kind
        and metadata_source_kind not in {"user", "chat"}
        and decision_source_kind != metadata_source_kind
    ):
        return -1

    decision_disclosability = str(decision.get("disclosability") or "").strip().lower()
    metadata_disclosability = str(metadata.get("disclosability") or "").strip().lower()
    if decision_disclosability and metadata_disclosability and decision_disclosability != metadata_disclosability:
        return -1

    score = 0
    if decision_source_kind and metadata_source_kind == decision_source_kind:
        score += 8

    score += min(4, _tag_overlap(metadata.get("tags") or [], decision.get("tags") or []) * 2)
    score += _text_overlap_score(summary, metadata, decision)

    decision_reason = str(decision.get("opened_reason") or "").strip().lower()
    metadata_reason = str(metadata.get("opened_reason") or "").strip().lower()
    if decision_reason and metadata_reason == decision_reason:
        score += 2

    decision_actor = str(decision.get("source_actor") or "").strip().lower()
    metadata_actor = str(metadata.get("opened_by") or "").strip().lower()
    if decision_actor and metadata_actor == decision_actor:
        score += 1

    if int(summary.get("unread_count") or 0) <= 3:
        score += 1

    if metadata_opened_by == "user_opened":
        score -= 2

    if int(summary.get("user_message_count") or 0) == 0:
        score += 1

    return score


def _find_reusable_session(storage, decision: Dict[str, Any]) -> Optional[str]:
    lane = normalize_lane(decision.get("lane")) or "strong"
    summaries = storage.messages.get_session_summaries(lane=lane)
    best: Optional[Tuple[int, str, str]] = None
    for summary in summaries:
        session_id = str(summary.get("session_id") or "").strip()
        if not session_id:
            continue
        metadata = get_session_metadata(storage, session_id)
        score = _session_reuse_score(summary, metadata, decision)
        if score < 0:
            continue
        last_message_at = str(summary.get("last_message_at") or "")
        candidate = (score, last_message_at, session_id)
        if best is None or candidate > best:
            best = candidate
    return best[2] if best and best[0] >= 5 else None


def route_communication_decision(storage, decision: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(decision or {})
    communicative_act = str(normalized.get("communicative_act") or "notification").strip().lower() or "notification"
    source_kind = str(normalized.get("source_kind") or "system").strip().lower() or "system"
    explicit_session_id = str(normalized.get("session_id") or "").strip()
    explicit_channel = str(normalized.get("channel") or "").strip().lower() or "existing_session_message"

    if communicative_act == "response":
        if explicit_channel == "new_session":
            normalized["channel"] = "new_session"
            normalized["session_id"] = None
            return normalized
        normalized["channel"] = "existing_session_message"
        return normalized

    if source_kind.startswith("autonomous_") or source_kind in {"system_notice", "alignment_notice"}:
        reusable_session_id = _find_reusable_session(storage, normalized)
        if reusable_session_id:
            normalized["channel"] = "existing_session_message"
            normalized["session_id"] = reusable_session_id
            return normalized
        normalized["channel"] = "new_session"
        normalized["session_id"] = None
        return normalized

    if explicit_session_id and explicit_channel != "new_session":
        normalized["channel"] = "existing_session_message"
        return normalized

    if source_kind in {"feedback_event", "chat_reply", "chat_error", "tool_progress"}:
        normalized["channel"] = "existing_session_message"
        return normalized

    normalized["channel"] = explicit_channel or "existing_session_message"
    return normalized


def deliver_communication(
    storage,
    *,
    role: str,
    content: str,
    lane: str = "strong",
    channel: str = "existing_session_message",
    session_id: Optional[str] = None,
    audience: str = "user",
    source_kind: str = "system",
    source_actor: str = "system_opened",
    opened_reason: str = "",
    tags: Optional[list[str]] = None,
    disclosability: str = "user_visible",
    topic_summary: str = "",
    session_title: str = "",
) -> Dict[str, Any]:
    if not str(content or "").strip():
        return {"status": "skipped", "reason": "empty_content"}

    normalized_lane = normalize_lane(lane) or "strong"
    normalized_channel = str(channel or "existing_session_message").strip().lower()
    if normalized_channel == "new_session":
        resolved_session_id = _new_session_id(normalized_lane)
    else:
        resolved_session_id = canonical_session_id_for_lane(normalized_lane, session_id or "default")

    metadata = ensure_session_metadata(
        storage,
        session_id=resolved_session_id,
        opened_by=source_actor,
        opened_reason=opened_reason or normalized_channel,
        source_kind=source_kind,
        tags=list(tags or []),
        disclosability=disclosability,
    )
    if topic_summary and not str(metadata.get("topic_summary") or "").strip():
        metadata = set_session_metadata(storage, resolved_session_id, {"topic_summary": topic_summary[:180]})
    if session_title and not resolve_session_title(metadata):
        metadata = set_session_metadata(
            storage,
            resolved_session_id,
            {
                "generated_title": str(session_title).strip()[:80] or DEFAULT_SESSION_TITLE,
                "title_source": "system_opened",
            },
        )

    message = storage.messages.create(
        role=role,
        content=content,
        session_id=resolved_session_id,
    )
    metadata = set_session_metadata(
        storage,
        resolved_session_id,
        {
            "last_communicated_at": _now_iso(),
            "last_communication_channel": normalized_channel,
            "last_communication_audience": audience,
        },
    )
    initialize_message_metadata(
        storage,
        message_id=str(getattr(message, "message_id", "") or ""),
        audience=audience,
        delivery_channel=normalized_channel,
        source_kind=source_kind,
        source_actor=source_actor,
        communicative_act="",
        response_kind="",
        urgency="",
        tags=list(tags or []),
    )
    return {
        "status": "ok",
        "session_id": resolved_session_id,
        "message_id": getattr(message, "message_id", None),
        "channel": normalized_channel,
        "session_metadata": metadata,
    }


def deliver_communication_decision(storage, decision: Dict[str, Any]) -> Dict[str, Any]:
    normalized = route_communication_decision(storage, decision)
    if not normalized.get("should_send", True):
        return {"status": "skipped", "reason": "decision_suppressed"}
    result = deliver_communication(
        storage,
        role=str(normalized.get("role") or "assistant"),
        content=str(normalized.get("content") or ""),
        lane=str(normalized.get("lane") or "strong"),
        channel=str(normalized.get("channel") or "existing_session_message"),
        session_id=normalized.get("session_id"),
        audience=str(normalized.get("audience") or "user"),
        source_kind=str(normalized.get("source_kind") or "system"),
        source_actor=str(normalized.get("source_actor") or "system_opened"),
        opened_reason=str(normalized.get("opened_reason") or ""),
        tags=list(normalized.get("tags") or []),
        disclosability=str(normalized.get("disclosability") or "user_visible"),
        topic_summary=str(normalized.get("topic_summary") or ""),
        session_title=str(normalized.get("session_title") or ""),
    )
    session_id = result.get("session_id")
    if session_id:
        metadata_updates = {
            "last_communicative_act": str(normalized.get("communicative_act") or "notification"),
            "last_response_kind": str(normalized.get("response_kind") or ""),
            "last_communication_urgency": str(normalized.get("urgency") or "normal"),
            "last_communication_source_kind": str(normalized.get("source_kind") or "system"),
            "last_communication_actor": str(normalized.get("source_actor") or "system_opened"),
            "last_communication_tags": list(normalized.get("tags") or []),
        }
        result["session_metadata"] = set_session_metadata(storage, session_id, metadata_updates)
        message_id = str(result.get("message_id") or "").strip()
        if message_id:
            initialize_message_metadata(
                storage,
                message_id=message_id,
                audience=str(normalized.get("audience") or "user"),
                delivery_channel=str(normalized.get("channel") or "existing_session_message"),
                source_kind=str(normalized.get("source_kind") or "system"),
                source_actor=str(normalized.get("source_actor") or "system_opened"),
                communicative_act=str(normalized.get("communicative_act") or "notification"),
                response_kind=str(normalized.get("response_kind") or ""),
                urgency=str(normalized.get("urgency") or "normal"),
                tags=list(normalized.get("tags") or []),
            )
    result["decision"] = normalized
    return result
