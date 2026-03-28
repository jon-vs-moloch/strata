"""
@module api.message_feedback
@purpose Persist lightweight message-reaction feedback without requiring a schema migration.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4


MESSAGE_FEEDBACK_KEY_PREFIX = "message_feedback"
MESSAGE_FEEDBACK_INDEX_KEY = "message_feedback:index"
MAX_MESSAGE_FEEDBACK_EVENTS = 500
ALLOWED_MESSAGE_REACTIONS = {"thumbs_up", "thumbs_down", "heart", "emphasis", "confused"}
SIGNALFUL_MESSAGE_REACTIONS = {"thumbs_down", "confused", "emphasis"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _feedback_key(message_id: str) -> str:
    return f"{MESSAGE_FEEDBACK_KEY_PREFIX}:{message_id}"


def _normalize_feedback_payload(value: Any, *, message_id: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {
            "message_id": message_id,
            "items": [],
            "counts": {},
            "updated_at": None,
        }
    items = [dict(item) for item in (value.get("items") or []) if isinstance(item, dict)]
    counts = {}
    for item in items:
        reaction = str(item.get("reaction") or "").strip()
        if reaction:
            counts[reaction] = counts.get(reaction, 0) + 1
    return {
        "message_id": message_id,
        "items": items,
        "counts": counts,
        "updated_at": value.get("updated_at"),
    }


def get_message_feedback(storage, message_id: str, *, viewer_session_id: Optional[str] = None) -> Dict[str, Any]:
    payload = storage.parameters.peek_parameter(_feedback_key(message_id), default_value=None)
    normalized = _normalize_feedback_payload(payload, message_id=message_id)
    viewer_reactions = []
    if viewer_session_id:
        viewer_reactions = [
            str(item.get("reaction") or "")
            for item in normalized.get("items") or []
            if str(item.get("session_id") or "") == str(viewer_session_id)
        ]
    return {
        "message_id": message_id,
        "counts": normalized.get("counts") or {},
        "viewer_reactions": sorted({reaction for reaction in viewer_reactions if reaction}),
        "updated_at": normalized.get("updated_at"),
        "item_count": len(normalized.get("items") or []),
    }


def _append_feedback_index_event(storage, event: Dict[str, Any]) -> None:
    rows = storage.parameters.peek_parameter(MESSAGE_FEEDBACK_INDEX_KEY, default_value=[]) or []
    if not isinstance(rows, list):
        rows = []
    rows.append(dict(event))
    storage.parameters.set_parameter(
        MESSAGE_FEEDBACK_INDEX_KEY,
        rows[-MAX_MESSAGE_FEEDBACK_EVENTS:],
        description="Recent durable chat-feedback events for downstream audit and distillation.",
    )


def list_message_feedback_events(storage, *, limit: int = 100, session_id: Optional[str] = None) -> list[Dict[str, Any]]:
    rows = storage.parameters.peek_parameter(MESSAGE_FEEDBACK_INDEX_KEY, default_value=[]) or []
    if not isinstance(rows, list):
        rows = []
    events = [dict(row) for row in rows if isinstance(row, dict)]
    if session_id:
        events = [row for row in events if str(row.get("session_id") or "") == str(session_id)]
    safe_limit = max(1, min(int(limit), MAX_MESSAGE_FEEDBACK_EVENTS))
    return events[-safe_limit:]


def toggle_message_reaction(
    storage,
    *,
    message,
    reaction: str,
    session_id: str,
) -> Dict[str, Any]:
    normalized_reaction = str(reaction or "").strip().lower()
    if normalized_reaction not in ALLOWED_MESSAGE_REACTIONS:
        raise ValueError(f"Unsupported reaction '{reaction}'.")

    message_id = str(getattr(message, "message_id", "") or "").strip()
    if not message_id:
        raise ValueError("message_id is required")

    current = _normalize_feedback_payload(
        storage.parameters.peek_parameter(_feedback_key(message_id), default_value=None),
        message_id=message_id,
    )
    items = list(current.get("items") or [])
    existing_index = next(
        (
            idx
            for idx, item in enumerate(items)
            if str(item.get("session_id") or "") == str(session_id)
            and str(item.get("reaction") or "") == normalized_reaction
        ),
        None,
    )
    action = "added"
    if existing_index is not None:
        items.pop(existing_index)
        action = "removed"
    else:
        items.append(
            {
                "reaction_id": f"reaction_{uuid4().hex[:12]}",
                "reaction": normalized_reaction,
                "session_id": str(session_id),
                "message_id": message_id,
                "message_role": str(getattr(message, "role", "") or ""),
                "message_preview": str(getattr(message, "content", "") or "").strip()[:180],
                "created_at": _now(),
            }
        )

    updated_payload = {
        "message_id": message_id,
        "items": items,
        "updated_at": _now(),
    }
    storage.parameters.set_parameter(
        _feedback_key(message_id),
        updated_payload,
        description=f"Message feedback state for message {message_id}.",
    )
    event = {
        "event_id": f"feedback_{uuid4().hex[:12]}",
        "action": action,
        "reaction": normalized_reaction,
        "message_id": message_id,
        "session_id": str(session_id),
        "message_role": str(getattr(message, "role", "") or ""),
        "message_preview": str(getattr(message, "content", "") or "").strip()[:180],
        "created_at": _now(),
        "distillation_status": "pending",
    }
    _append_feedback_index_event(storage, event)
    return {
        "action": action,
        "event": event,
        "feedback": get_message_feedback(storage, message_id, viewer_session_id=session_id),
    }


def build_feedback_event_message(*, action: str, reaction: str, message_preview: str) -> str:
    normalized_action = "removed" if str(action or "").strip().lower() == "removed" else "added"
    preview = str(message_preview or "").strip()
    if len(preview) > 120:
        preview = f"{preview[:117].rstrip()}..."
    reaction_label = reaction.replace("_", " ")
    if normalized_action == "removed":
        return f"User removed {reaction_label} reaction from assistant message: \"{preview}\""
    return f"User reacted to assistant message with {reaction_label}: \"{preview}\""


def should_trigger_feedback_distillation(*, action: str, reaction: str) -> bool:
    return str(action or "").strip().lower() == "added" and str(reaction or "").strip().lower() in SIGNALFUL_MESSAGE_REACTIONS
