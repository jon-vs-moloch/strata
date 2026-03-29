"""
@module sessions.metadata
@purpose Durable metadata helpers for chat sessions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

from strata.models.adapter import ModelAdapter
from strata.schemas.execution import AgentExecutionContext


SESSION_METADATA_PREFIX = "session_metadata:"
DEFAULT_SESSION_TITLE = "New Session"
DEFAULT_DISCLOSABILITY = "user_visible"


def _normalize_participant_names(raw: Any) -> Dict[str, str]:
    source = dict(raw or {}) if isinstance(raw, dict) else {}
    trainer = str(source.get("trainer") or "").strip()
    if not trainer or trainer.lower() == "trainer-agent":
        trainer = "Trainer"
    agent = str(source.get("agent") or "").strip() or "Agent"
    user = str(source.get("user") or "").strip() or "You"
    system = str(source.get("system") or "").strip() or "System"
    return {
        "user": user,
        "trainer": trainer,
        "agent": agent,
        "system": system,
    }


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _session_metadata_key(session_id: str) -> str:
    return f"{SESSION_METADATA_PREFIX}{session_id}"


def get_session_metadata(storage, session_id: str) -> Dict[str, Any]:
    metadata = storage.parameters.peek_parameter(_session_metadata_key(session_id), default_value={}) or {}
    if isinstance(metadata, dict):
        merged = dict(metadata)
    else:
        merged = {}
    merged.setdefault("tags", [])
    merged.setdefault("disclosability", DEFAULT_DISCLOSABILITY)
    merged.setdefault("opened_by", "")
    merged.setdefault("opened_reason", "")
    merged.setdefault("source_kind", "")
    merged.setdefault("topic_summary", "")
    merged.setdefault("created_at", "")
    merged.setdefault("last_audited_at", "")
    merged.setdefault("last_read_at", "")
    merged.setdefault("last_read_message_id", "")
    merged.setdefault("last_communication_source_kind", "")
    merged.setdefault("last_communication_actor", "")
    merged.setdefault("last_communication_tags", [])
    merged["participant_names"] = _normalize_participant_names(merged.get("participant_names"))
    merged.setdefault("action_required", False)
    merged.setdefault("action_required_reason", "")
    return merged


def set_session_metadata(storage, session_id: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    current = get_session_metadata(storage, session_id)
    next_value = {**current, **dict(metadata or {})}
    next_value["participant_names"] = _normalize_participant_names(next_value.get("participant_names"))
    storage.parameters.set_parameter(
        _session_metadata_key(session_id),
        next_value,
        description=f"Durable metadata for chat session '{session_id}'.",
    )
    return next_value


def ensure_session_metadata(
    storage,
    *,
    session_id: str,
    opened_by: str = "",
    opened_reason: str = "",
    source_kind: str = "",
    tags: Optional[list[str]] = None,
    disclosability: str = DEFAULT_DISCLOSABILITY,
) -> Dict[str, Any]:
    current = get_session_metadata(storage, session_id)
    updates: Dict[str, Any] = {}
    if not str(current.get("created_at") or "").strip():
        updates["created_at"] = _utcnow_iso()
    if opened_by and not str(current.get("opened_by") or "").strip():
        updates["opened_by"] = opened_by
    if opened_reason and not str(current.get("opened_reason") or "").strip():
        updates["opened_reason"] = opened_reason
    if source_kind and not str(current.get("source_kind") or "").strip():
        updates["source_kind"] = source_kind
    if disclosability and not str(current.get("disclosability") or "").strip():
        updates["disclosability"] = disclosability
    if tags:
        existing_tags = [str(item) for item in (current.get("tags") or []) if str(item).strip()]
        merged_tags = list(dict.fromkeys(existing_tags + [str(item) for item in tags if str(item).strip()]))
        updates["tags"] = merged_tags
    if not updates:
        return current
    return set_session_metadata(storage, session_id, updates)


def mark_session_read(storage, *, session_id: str, message_id: str = "", read_at: Optional[str] = None) -> Dict[str, Any]:
    return set_session_metadata(
        storage,
        session_id,
        {
            "last_read_at": str(read_at or _utcnow_iso()),
            "last_read_message_id": str(message_id or ""),
        },
    )


def record_session_audit(storage, *, session_id: str, audited_at: Optional[str] = None, reviewer_tier: str = "") -> Dict[str, Any]:
    updates = {
        "last_audited_at": str(audited_at or _utcnow_iso()),
    }
    if reviewer_tier:
        updates["last_audited_by"] = str(reviewer_tier)
    return set_session_metadata(storage, session_id, updates)


def list_session_metadata(storage, session_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    return {
        str(session_id): get_session_metadata(storage, str(session_id))
        for session_id in session_ids
        if str(session_id or "").strip()
    }


def resolve_session_title(metadata: Dict[str, Any]) -> Optional[str]:
    for field in ("custom_title", "generated_title", "recommended_title"):
        value = str((metadata or {}).get(field) or "").strip()
        if value:
            return value
    return None


def _fallback_generated_title(history: list) -> str:
    first_user = next((str(row.content or "").strip() for row in history if str(row.role or "") == "user" and str(row.content or "").strip()), "")
    if not first_user:
        return DEFAULT_SESSION_TITLE
    words = [part.strip(".,:;!?()[]{}\"'") for part in first_user.split()]
    words = [part for part in words if part][:6]
    if not words:
        return DEFAULT_SESSION_TITLE
    title = " ".join(words).strip()
    return title[:60] or DEFAULT_SESSION_TITLE


async def ensure_generated_session_title(storage, *, session_id: str, model_adapter, min_messages: int = 2) -> Dict[str, Any]:
    existing = get_session_metadata(storage, session_id)
    if str(existing.get("custom_title") or "").strip() or str(existing.get("generated_title") or "").strip():
        return existing

    history = storage.messages.get_all(session_id=session_id)
    relevant = [row for row in history if str(row.role or "") in {"user", "assistant"} and str(row.content or "").strip()]
    if len(relevant) < min_messages:
        return existing

    prompt_messages = []
    for row in relevant[:4]:
        prompt_messages.append(f"{str(row.role or '').upper()}: {str(row.content or '').strip()[:240]}")

    generated_title = ""
    try:
        if isinstance(model_adapter, ModelAdapter):
            title_adapter = ModelAdapter(context=AgentExecutionContext(run_id=f"session-title:{session_id}"))
            title_adapter._selected_models = dict(getattr(model_adapter, "_selected_models", {}))
        else:
            title_adapter = model_adapter
        response = await title_adapter.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Generate a concise chat title.\n"
                        "Rules:\n"
                        "- 2 to 6 words\n"
                        "- no quotes\n"
                        "- no punctuation beyond hyphens if needed\n"
                        "- describe the user's actual topic, not the assistant\n"
                        "- output only the title"
                    ),
                },
                {"role": "user", "content": "\n".join(prompt_messages)},
            ]
        )
        generated_title = str(response.get("content") or "").strip().strip('"').strip("'")
    except Exception:
        generated_title = ""

    if not generated_title:
        generated_title = _fallback_generated_title(relevant)

    generated_title = " ".join(generated_title.split())[:60].strip() or DEFAULT_SESSION_TITLE
    return set_session_metadata(
        storage,
        session_id,
        {
            "generated_title": generated_title,
            "recommended_title": generated_title,
            "title_source": "generated",
        },
    )
