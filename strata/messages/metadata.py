"""
@module messages.metadata
@purpose Durable metadata helpers for individual chat messages.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable


MESSAGE_METADATA_PREFIX = "message_metadata:"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _message_metadata_key(message_id: str) -> str:
    return f"{MESSAGE_METADATA_PREFIX}{message_id}"


def get_message_metadata(storage, message_id: str) -> Dict[str, Any]:
    metadata = storage.parameters.peek_parameter(_message_metadata_key(message_id), default_value={}) or {}
    if isinstance(metadata, dict):
        merged = dict(metadata)
    else:
        merged = {}
    merged.setdefault("sent_at", "")
    merged.setdefault("delivered_at", "")
    merged.setdefault("delivery_channel", "")
    merged.setdefault("audience", "")
    merged.setdefault("source_kind", "")
    merged.setdefault("source_actor", "")
    merged.setdefault("communicative_act", "")
    merged.setdefault("response_kind", "")
    merged.setdefault("urgency", "")
    merged.setdefault("seen_by_system_at", "")
    merged.setdefault("seen_by_system_actor", "")
    merged.setdefault("read_at", "")
    merged.setdefault("read_by", "")
    merged.setdefault("delivery_records", [])
    merged.setdefault("seen_receipts", [])
    merged.setdefault("read_receipts", [])
    merged.setdefault("action_required", False)
    merged.setdefault("action_required_reason", "")
    merged.setdefault("action_required_for", "")
    merged.setdefault("tags", [])
    merged.setdefault("attachments", [])
    return merged


def set_message_metadata(storage, message_id: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    current = get_message_metadata(storage, message_id)
    next_value = {**current, **dict(metadata or {})}
    storage.parameters.set_parameter(
        _message_metadata_key(message_id),
        next_value,
        description=f"Durable metadata for message '{message_id}'.",
    )
    return next_value


def initialize_message_metadata(
    storage,
    *,
    message_id: str,
    audience: str = "",
    delivery_channel: str = "",
    source_kind: str = "",
    source_actor: str = "",
    communicative_act: str = "",
    response_kind: str = "",
    urgency: str = "",
    tags: Iterable[str] | None = None,
    sent_at: str | None = None,
    delivered_at: str | None = None,
) -> Dict[str, Any]:
    current = get_message_metadata(storage, message_id)
    sent_stamp = str(current.get("sent_at") or sent_at or _utcnow_iso())
    delivered_stamp = str(current.get("delivered_at") or delivered_at or sent_at or _utcnow_iso())
    merged_tags = list(
        dict.fromkeys(
            [str(item) for item in (current.get("tags") or []) if str(item).strip()]
            + [str(item) for item in (tags or []) if str(item).strip()]
        )
    )
    delivery_records = list(current.get("delivery_records") or [])
    if audience:
        normalized_audience = str(audience or "").strip()
        if not any(
            str(item.get("recipient") or "").strip() == normalized_audience and str(item.get("channel") or "").strip() == str(delivery_channel or "").strip()
            for item in delivery_records
            if isinstance(item, dict)
        ):
            delivery_records.append(
                {
                    "recipient": normalized_audience,
                    "channel": str(delivery_channel or "").strip(),
                    "delivered_at": delivered_stamp,
                }
            )
    updates = {
        "sent_at": sent_stamp,
        "delivered_at": delivered_stamp,
        "delivery_channel": str(current.get("delivery_channel") or delivery_channel or ""),
        "audience": str(current.get("audience") or audience or ""),
        "source_kind": str(current.get("source_kind") or source_kind or ""),
        "source_actor": str(current.get("source_actor") or source_actor or ""),
        "communicative_act": str(current.get("communicative_act") or communicative_act or ""),
        "response_kind": str(current.get("response_kind") or response_kind or ""),
        "urgency": str(current.get("urgency") or urgency or ""),
        "delivery_records": delivery_records,
        "tags": merged_tags,
    }
    return set_message_metadata(storage, message_id, updates)


def mark_message_seen_by_system(storage, *, message_id: str, actor: str = "system", seen_at: str | None = None) -> Dict[str, Any]:
    stamp = str(seen_at or _utcnow_iso())
    current = get_message_metadata(storage, message_id)
    seen_receipts = list(current.get("seen_receipts") or [])
    normalized_actor = str(actor or "system")
    if not any(str(item.get("actor") or "") == normalized_actor for item in seen_receipts if isinstance(item, dict)):
        seen_receipts.append({"actor": normalized_actor, "seen_at": stamp})
    return set_message_metadata(
        storage,
        message_id,
        {
            "seen_by_system_at": stamp,
            "seen_by_system_actor": normalized_actor,
            "seen_receipts": seen_receipts,
        },
    )


def mark_message_read(storage, *, message_id: str, reader: str = "user", read_at: str | None = None) -> Dict[str, Any]:
    stamp = str(read_at or _utcnow_iso())
    current = get_message_metadata(storage, message_id)
    read_receipts = list(current.get("read_receipts") or [])
    normalized_reader = str(reader or "user")
    if not any(str(item.get("reader") or "") == normalized_reader for item in read_receipts if isinstance(item, dict)):
        read_receipts.append({"reader": normalized_reader, "read_at": stamp})
    return set_message_metadata(
        storage,
        message_id,
        {
            "read_at": stamp,
            "read_by": normalized_reader,
            "read_receipts": read_receipts,
        },
    )


def mark_messages_read(
    storage,
    *,
    message_ids: Iterable[str],
    reader: str = "user",
    read_at: str | None = None,
) -> Dict[str, Dict[str, Any]]:
    stamp = str(read_at or _utcnow_iso())
    updates: Dict[str, Dict[str, Any]] = {}
    for message_id in message_ids:
        if not str(message_id or "").strip():
            continue
        updates[str(message_id)] = mark_message_read(storage, message_id=str(message_id), reader=reader, read_at=stamp)
    return updates
