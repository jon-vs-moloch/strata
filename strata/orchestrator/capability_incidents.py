"""
@module orchestrator.capability_incidents
@purpose Durable append-only incident records for degraded capabilities.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4


CAPABILITY_INCIDENT_INDEX_KEY = "capability_incidents:index"
CAPABILITY_INCIDENT_OPEN_KEY_PREFIX = "capability_incidents:open"
CAPABILITY_INCIDENT_DETAIL_KEY_PREFIX = "capability_incidents:detail"
MAX_CAPABILITY_INCIDENTS = 600


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def capability_ref(kind: str, name: str) -> str:
    normalized_kind = str(kind or "").strip().lower() or "unknown"
    normalized_name = str(name or "").strip() or "unknown"
    return f"{normalized_kind}:{normalized_name}"


def _normalize_string_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _normalize_provenance(provenance: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    payload = dict(provenance or {})
    payload.setdefault("recorded_at", _now_iso())
    payload["source_kind"] = str(payload.get("source_kind") or "").strip()
    payload["source_actor"] = str(payload.get("source_actor") or "system").strip() or "system"
    payload["authority_kind"] = str(payload.get("authority_kind") or "unspecified").strip() or "unspecified"
    payload["authority_ref"] = str(payload.get("authority_ref") or "").strip()
    payload["derived_from"] = _normalize_string_list(payload.get("derived_from"))
    payload["governing_spec_refs"] = _normalize_string_list(payload.get("governing_spec_refs"))
    payload["note"] = str(payload.get("note") or "").strip()
    return payload


def _summary_from_incident(incident: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "incident_id": incident.get("incident_id"),
        "capability_ref": incident.get("capability_ref"),
        "capability_kind": incident.get("capability_kind"),
        "capability_name": incident.get("capability_name"),
        "status": incident.get("status"),
        "reason": incident.get("reason"),
        "opened_at": incident.get("opened_at"),
        "last_seen_at": incident.get("last_seen_at"),
        "closed_at": incident.get("closed_at"),
        "occurrence_count": incident.get("occurrence_count", 1),
        "session_ids": list(incident.get("session_ids") or []),
        "task_ids": list(incident.get("task_ids") or []),
        "attempt_ids": list(incident.get("attempt_ids") or []),
        "signal_ids": list(incident.get("signal_ids") or []),
        "snapshot": dict(incident.get("snapshot") or {}),
        "provenance": dict(incident.get("provenance") or {}),
    }


def _store_incident(storage, incident: Dict[str, Any]) -> Dict[str, Any]:
    detail_key = f"{CAPABILITY_INCIDENT_DETAIL_KEY_PREFIX}:{incident['incident_id']}"
    open_key = f"{CAPABILITY_INCIDENT_OPEN_KEY_PREFIX}:{incident['capability_ref']}"
    storage.parameters.set_parameter(
        detail_key,
        incident,
        description=f"Capability incident detail for {incident['capability_ref']}.",
    )
    if str(incident.get("status") or "").strip().lower() == "closed":
        storage.parameters.set_parameter(
            open_key,
            {},
            description=f"Open capability incident pointer for {incident['capability_ref']}.",
        )
    else:
        storage.parameters.set_parameter(
            open_key,
            {
                "incident_id": incident["incident_id"],
                "capability_ref": incident["capability_ref"],
                "status": incident.get("status"),
            },
            description=f"Open capability incident pointer for {incident['capability_ref']}.",
        )

    index = storage.parameters.peek_parameter(CAPABILITY_INCIDENT_INDEX_KEY, default_value=[]) or []
    if not isinstance(index, list):
        index = []
    summary = _summary_from_incident(incident)
    index = [
        item
        for item in index
        if not isinstance(item, dict) or str(item.get("incident_id") or "").strip() != incident["incident_id"]
    ]
    index.append(summary)
    storage.parameters.set_parameter(
        CAPABILITY_INCIDENT_INDEX_KEY,
        index[-MAX_CAPABILITY_INCIDENTS:],
        description="Append-only summaries of degraded capability incidents.",
    )
    return incident


def record_capability_incident(
    storage,
    *,
    capability_kind: str,
    capability_name: str,
    status: str = "degraded",
    reason: str = "",
    task_id: Optional[str] = None,
    attempt_id: Optional[str] = None,
    session_id: Optional[str] = None,
    signal_id: Optional[str] = None,
    source_review_id: Optional[str] = None,
    provenance: Optional[Dict[str, Any]] = None,
    snapshot: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ref = capability_ref(capability_kind, capability_name)
    now = _now_iso()
    open_pointer = storage.parameters.peek_parameter(
        f"{CAPABILITY_INCIDENT_OPEN_KEY_PREFIX}:{ref}",
        default_value={},
    ) or {}
    current = None
    current_incident_id = str((open_pointer or {}).get("incident_id") or "").strip()
    if current_incident_id:
        current = storage.parameters.peek_parameter(
            f"{CAPABILITY_INCIDENT_DETAIL_KEY_PREFIX}:{current_incident_id}",
            default_value=None,
        )
    if not isinstance(current, dict):
        current = None

    normalized_status = str(status or "degraded").strip().lower() or "degraded"
    normalized_reason = str(reason or "").strip()
    if current and str(current.get("status") or "").strip().lower() != "closed":
        incident = dict(current)
        incident["last_seen_at"] = now
        incident["status"] = normalized_status
        incident["reason"] = normalized_reason or str(incident.get("reason") or "").strip()
        incident["occurrence_count"] = int(incident.get("occurrence_count", 1) or 1) + 1
    else:
        incident = {
            "incident_id": str(uuid4()),
            "capability_ref": ref,
            "capability_kind": str(capability_kind or "").strip().lower() or "unknown",
            "capability_name": str(capability_name or "").strip() or "unknown",
            "status": normalized_status,
            "reason": normalized_reason,
            "opened_at": now,
            "last_seen_at": now,
            "closed_at": None,
            "occurrence_count": 1,
            "task_ids": [],
            "attempt_ids": [],
            "signal_ids": [],
            "review_ids": [],
            "session_ids": [],
            "snapshot": {},
            "metadata": {},
            "provenance": {},
        }

    incident["task_ids"] = sorted(set([*list(incident.get("task_ids") or []), *_normalize_string_list(task_id)]))
    incident["attempt_ids"] = sorted(set([*list(incident.get("attempt_ids") or []), *_normalize_string_list(attempt_id)]))
    incident["signal_ids"] = sorted(set([*list(incident.get("signal_ids") or []), *_normalize_string_list(signal_id)]))
    incident["review_ids"] = sorted(set([*list(incident.get("review_ids") or []), *_normalize_string_list(source_review_id)]))
    incident["session_ids"] = sorted(set([*list(incident.get("session_ids") or []), *_normalize_string_list(session_id)]))
    incident["snapshot"] = {**dict(incident.get("snapshot") or {}), **dict(snapshot or {})}
    incident["metadata"] = {**dict(incident.get("metadata") or {}), **dict(metadata or {})}
    incident["provenance"] = _normalize_provenance(provenance or incident.get("provenance"))
    return _store_incident(storage, incident)


def resolve_capability_incident(
    storage,
    *,
    incident_id: str,
    resolution_kind: str,
    note: str = "",
    provenance: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    detail_key = f"{CAPABILITY_INCIDENT_DETAIL_KEY_PREFIX}:{str(incident_id or '').strip()}"
    current = storage.parameters.peek_parameter(detail_key, default_value=None)
    if not isinstance(current, dict):
        return None
    incident = dict(current)
    if str(incident.get("status") or "").strip().lower() == "closed":
        return incident
    incident["status"] = "closed"
    incident["closed_at"] = _now_iso()
    incident["resolution"] = {
        "resolution_kind": str(resolution_kind or "").strip() or "resolved",
        "note": str(note or "").strip(),
        "provenance": _normalize_provenance(provenance),
    }
    return _store_incident(storage, incident)


def get_capability_incident(storage, *, incident_id: str) -> Optional[Dict[str, Any]]:
    detail_key = f"{CAPABILITY_INCIDENT_DETAIL_KEY_PREFIX}:{str(incident_id or '').strip()}"
    current = storage.parameters.peek_parameter(detail_key, default_value=None)
    if not isinstance(current, dict):
        return None
    return dict(current)


def annotate_capability_incident(
    storage,
    *,
    incident_id: str,
    metadata: Optional[Dict[str, Any]] = None,
    snapshot: Optional[Dict[str, Any]] = None,
    provenance: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    current = get_capability_incident(storage, incident_id=incident_id)
    if not current:
        return None
    incident = dict(current)
    incident["last_seen_at"] = _now_iso()
    incident["metadata"] = {**dict(incident.get("metadata") or {}), **dict(metadata or {})}
    incident["snapshot"] = {**dict(incident.get("snapshot") or {}), **dict(snapshot or {})}
    if provenance:
        incident["provenance"] = _normalize_provenance(
            {**dict(incident.get("provenance") or {}), **dict(provenance or {})}
        )
    return _store_incident(storage, incident)


def list_capability_incidents(
    storage,
    *,
    capability_ref_value: Optional[str] = None,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    rows = storage.parameters.peek_parameter(CAPABILITY_INCIDENT_INDEX_KEY, default_value=[]) or []
    if not isinstance(rows, list):
        rows = []
    incidents = [dict(item) for item in rows if isinstance(item, dict)]
    if capability_ref_value:
        incidents = [item for item in incidents if str(item.get("capability_ref") or "").strip() == str(capability_ref_value).strip()]
    if task_id:
        incidents = [item for item in incidents if str(task_id).strip() in {str(v).strip() for v in list(item.get("task_ids") or [])}]
    if session_id:
        incidents = [item for item in incidents if str(session_id).strip() in {str(v).strip() for v in list(item.get("session_ids") or [])}]
    if status:
        incidents = [item for item in incidents if str(item.get("status") or "").strip().lower() == str(status).strip().lower()]
    safe_limit = max(1, min(int(limit or 50), MAX_CAPABILITY_INCIDENTS))
    return incidents[-safe_limit:]
