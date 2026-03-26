"""
@module specs.bootstrap
@purpose Ensure durable spec files exist for alignment and operator guidance.
@owns spec directory bootstrap, default spec content, spec loading
@does_not_own higher-level alignment policy decisions
@key_exports ensure_spec_files, load_specs
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from strata.observability.context import record_context_load

ROOT = Path(__file__).resolve().parents[2]
SPECS_DIR = ROOT / ".knowledge" / "specs"
GLOBAL_SPEC_PATH = SPECS_DIR / "global_spec.md"
PROJECT_SPEC_PATH = SPECS_DIR / "project_spec.md"
SPEC_PROPOSALS_INDEX_KEY = "spec_proposals:index"
SPEC_PROPOSAL_KEY_PREFIX = "spec_proposal:"
MAX_TERMINAL_SPEC_PROPOSALS = 100

DEFAULT_GLOBAL_SPEC = """# Global Spec

This file stores persistent, cross-project instructions and preferences for Strata.

Suggested contents:
- durable operator preferences
- disclosure or safety constraints
- hardware or rate-limit preferences
- global product goals that should outlive a single task

If there are no durable global preferences yet, leave this file in place and update it as they become clear.
"""

DEFAULT_PROJECT_SPEC = """# Project Spec

This file stores the current high-level vision for the active Strata project.

Suggested contents:
- what the system is trying to accomplish
- important design constraints
- the current bootstrap/eval objective
- any active architectural priorities

This file should exist even when it is sparse, because alignment and maintenance tasks depend on having a stable place to look for project intent.
"""


def ensure_spec_files() -> Dict[str, str]:
    SPECS_DIR.mkdir(parents=True, exist_ok=True)
    if not GLOBAL_SPEC_PATH.exists():
        GLOBAL_SPEC_PATH.write_text(DEFAULT_GLOBAL_SPEC.strip() + "\n", encoding="utf-8")
    if not PROJECT_SPEC_PATH.exists():
        PROJECT_SPEC_PATH.write_text(DEFAULT_PROJECT_SPEC.strip() + "\n", encoding="utf-8")
    return {
        "specs_dir": str(SPECS_DIR),
        "global_spec_path": str(GLOBAL_SPEC_PATH),
        "project_spec_path": str(PROJECT_SPEC_PATH),
    }


def load_specs(*, storage=None) -> Dict[str, str]:
    ensure_spec_files()
    global_spec = GLOBAL_SPEC_PATH.read_text(encoding="utf-8")
    project_spec = PROJECT_SPEC_PATH.read_text(encoding="utf-8")
    record_context_load(
        artifact_type="spec",
        identifier="global_spec",
        content=global_spec,
        source="specs.bootstrap.load_specs",
        metadata={"path": str(GLOBAL_SPEC_PATH)},
        storage=storage,
    )
    record_context_load(
        artifact_type="spec",
        identifier="project_spec",
        content=project_spec,
        source="specs.bootstrap.load_specs",
        metadata={"path": str(PROJECT_SPEC_PATH)},
        storage=storage,
    )
    return {
        "global_spec": global_spec,
        "project_spec": project_spec,
    }


def _proposal_key(proposal_id: str) -> str:
    return f"{SPEC_PROPOSAL_KEY_PREFIX}{proposal_id}"


def _compact_proposal_index(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    active = [
        row for row in rows
        if isinstance(row, dict) and row.get("status") not in {"approved", "rejected"}
    ]
    terminal = [
        row for row in rows
        if isinstance(row, dict) and row.get("status") in {"approved", "rejected"}
    ]
    terminal.sort(key=lambda row: row.get("updated_at", ""), reverse=True)
    return active + terminal[:MAX_TERMINAL_SPEC_PROPOSALS]


def _archive_old_resolved_proposals(storage) -> None:
    rows = storage.parameters.peek_parameter(SPEC_PROPOSALS_INDEX_KEY, default_value=[]) or []
    if not isinstance(rows, list):
        return
    kept_ids = {
        row.get("proposal_id")
        for row in _compact_proposal_index(rows)
        if isinstance(row, dict) and row.get("proposal_id")
    }
    for row in rows:
        if not isinstance(row, dict):
            continue
        proposal_id = row.get("proposal_id")
        if not proposal_id or proposal_id in kept_ids:
            continue
        payload = get_spec_proposal(storage, proposal_id)
        if not payload:
            continue
        payload["archived"] = True
        payload["current_spec_snapshot"] = ""
        payload["user_signal"] = str(payload.get("user_signal") or "")[:400]
        payload["resolution_notes"] = str(payload.get("resolution_notes") or "")[:400]
        attribution = dict(payload.get("attribution") or {})
        if attribution:
            attribution["conversational_context"] = str(attribution.get("conversational_context") or "")[:600]
            attribution["message_citations"] = list(attribution.get("message_citations") or [])[-8:]
            payload["attribution"] = attribution
        storage.parameters.set_parameter(
            _proposal_key(proposal_id),
            payload,
            description=f"Archived {payload.get('scope', 'project')} spec proposal {proposal_id}.",
        )


def _spec_path_for_scope(scope: str) -> Path:
    return GLOBAL_SPEC_PATH if str(scope).strip().lower() == "global" else PROJECT_SPEC_PATH


def build_spec_attribution(
    storage,
    *,
    session_id: Optional[str],
    user_signal: str,
    max_messages: int = 6,
) -> Dict[str, Any]:
    citations: List[Dict[str, Any]] = []
    conversational_context = ""
    if not session_id or not hasattr(storage, "messages"):
        return {
            "driver_session_id": session_id,
            "driver_user_signal": str(user_signal).strip(),
            "message_citations": citations,
            "conversational_context": conversational_context,
        }

    try:
        history = storage.messages.get_all(session_id=session_id)
    except Exception:
        history = []
    trimmed = history[-max(1, max_messages):]
    conversational_context = "\n".join(
        f"{getattr(msg, 'role', 'unknown')}: {str(getattr(msg, 'content', '') or '').strip()}"
        for msg in trimmed
    ).strip()
    for msg in trimmed:
        citations.append(
            {
                "message_id": getattr(msg, "message_id", None),
                "role": getattr(msg, "role", None),
                "created_at": getattr(msg, "created_at", None).isoformat() if getattr(msg, "created_at", None) else None,
                "excerpt": str(getattr(msg, "content", "") or "").strip()[:280],
            }
        )
    return {
        "driver_session_id": session_id,
        "driver_user_signal": str(user_signal).strip(),
        "message_citations": citations,
        "conversational_context": conversational_context,
    }


def create_spec_proposal(
    storage,
    *,
    scope: str,
    proposed_change: str,
    rationale: str,
    user_signal: str = "",
    session_id: Optional[str] = None,
    source: str = "chat_agent",
    review_task_id: Optional[str] = None,
    attribution: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ensure_spec_files()
    normalized_scope = "global" if str(scope).strip().lower() == "global" else "project"
    proposal_id = f"spec_{uuid4().hex[:12]}"
    current_specs = load_specs(storage=storage)
    now = datetime.now(timezone.utc).isoformat()
    proposal = {
        "proposal_id": proposal_id,
        "scope": normalized_scope,
        "status": "pending_review",
        "proposed_change": str(proposed_change).strip(),
        "rationale": str(rationale).strip(),
        "user_signal": str(user_signal).strip(),
        "session_id": session_id,
        "source": source,
        "review_task_id": review_task_id,
        "attribution": attribution or build_spec_attribution(
            storage,
            session_id=session_id,
            user_signal=user_signal,
        ),
        "current_spec_snapshot": current_specs["global_spec" if normalized_scope == "global" else "project_spec"],
        "created_at": now,
        "updated_at": now,
        "resolution": None,
        "resolution_notes": "",
        "clarification_request": "",
        "applied_at": None,
    }
    storage.parameters.set_parameter(
        _proposal_key(proposal_id),
        proposal,
        description=f"Reviewed {normalized_scope} spec proposal {proposal_id}.",
    )
    index = storage.parameters.peek_parameter(SPEC_PROPOSALS_INDEX_KEY, default_value=[]) or []
    if not isinstance(index, list):
        index = []
    index = [row for row in index if isinstance(row, dict) and row.get("proposal_id") != proposal_id]
    index.append(
        {
            "proposal_id": proposal_id,
            "scope": normalized_scope,
            "status": proposal["status"],
            "created_at": now,
            "updated_at": now,
            "source": source,
            "review_task_id": review_task_id,
            "summary": proposal["proposed_change"][:180],
        }
    )
    compacted_index = _compact_proposal_index(index)
    storage.parameters.set_parameter(
        SPEC_PROPOSALS_INDEX_KEY,
        compacted_index,
        description="Index of durable spec proposals and their current review status.",
    )
    _archive_old_resolved_proposals(storage)
    return proposal


def get_spec_proposal(storage, proposal_id: str) -> Dict[str, Any]:
    value = storage.parameters.peek_parameter(_proposal_key(proposal_id), default_value={}) or {}
    return value if isinstance(value, dict) else {}


def list_spec_proposals(storage, *, status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    rows = storage.parameters.peek_parameter(SPEC_PROPOSALS_INDEX_KEY, default_value=[]) or []
    if not isinstance(rows, list):
        return []
    filtered = []
    wanted_status = str(status or "").strip().lower()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if wanted_status and str(row.get("status") or "").lower() != wanted_status:
            continue
        filtered.append(row)
    filtered.sort(key=lambda row: row.get("updated_at", ""), reverse=True)
    return filtered[: max(1, limit)]


def resolve_spec_proposal(
    storage,
    *,
    proposal_id: str,
    resolution: str,
    reviewer_notes: str = "",
    clarification_request: str = "",
    reviewer: str = "operator",
) -> Dict[str, Any]:
    proposal = get_spec_proposal(storage, proposal_id)
    if not proposal:
        return {}
    normalized_resolution = str(resolution).strip().lower()
    if normalized_resolution not in {"approved", "rejected", "needs_clarification"}:
        raise ValueError("resolution must be approved, rejected, or needs_clarification")

    now = datetime.now(timezone.utc).isoformat()
    proposal["status"] = normalized_resolution
    proposal["resolution"] = normalized_resolution
    proposal["resolution_notes"] = str(reviewer_notes).strip()
    proposal["clarification_request"] = str(clarification_request).strip()
    proposal["reviewed_by"] = reviewer
    proposal["updated_at"] = now

    if normalized_resolution == "approved":
        path = _spec_path_for_scope(proposal.get("scope", "project"))
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        appended = (
            f"\n\n## Accepted Update ({now})\n"
            f"- Proposal ID: {proposal_id}\n"
            f"- Source: {proposal.get('source')}\n"
            f"- User Signal: {proposal.get('user_signal') or 'n/a'}\n"
            f"- Rationale: {proposal.get('rationale') or 'n/a'}\n\n"
            f"{proposal.get('proposed_change', '').strip()}\n"
        )
        citations = list((proposal.get("attribution") or {}).get("message_citations") or [])
        if citations:
            appended += "\n### User Message Citations\n"
            for citation in citations[-6:]:
                appended += (
                    f"- {citation.get('role') or 'unknown'} "
                    f"[{citation.get('message_id') or 'n/a'}]: "
                    f"{citation.get('excerpt') or ''}\n"
                )
        path.write_text(current.rstrip() + appended, encoding="utf-8")
        proposal["applied_at"] = now

    storage.parameters.set_parameter(
        _proposal_key(proposal_id),
        proposal,
        description=f"Reviewed {proposal.get('scope', 'project')} spec proposal {proposal_id}.",
    )

    index = storage.parameters.peek_parameter(SPEC_PROPOSALS_INDEX_KEY, default_value=[]) or []
    updated_rows = []
    for row in index if isinstance(index, list) else []:
        if not isinstance(row, dict):
            continue
        if row.get("proposal_id") == proposal_id:
            row = dict(row)
            row["status"] = normalized_resolution
            row["updated_at"] = now
            row["resolution_notes"] = proposal["resolution_notes"]
        updated_rows.append(row)
    compacted_rows = _compact_proposal_index(updated_rows)
    storage.parameters.set_parameter(
        SPEC_PROPOSALS_INDEX_KEY,
        compacted_rows,
        description="Index of durable spec proposals and their current review status.",
    )
    _archive_old_resolved_proposals(storage)
    return proposal


def resubmit_spec_proposal_with_clarification(
    storage,
    *,
    proposal_id: str,
    clarification_response: str,
    source: str = "user",
) -> Dict[str, Any]:
    proposal = get_spec_proposal(storage, proposal_id)
    if not proposal:
        return {}
    now = datetime.now(timezone.utc).isoformat()
    prior_signal = str(proposal.get("user_signal") or "").strip()
    combined_signal = prior_signal
    if clarification_response.strip():
        combined_signal = (
            (prior_signal + "\n\n" if prior_signal else "")
            + f"Clarification Response ({now}): {clarification_response.strip()}"
        )
    proposal["user_signal"] = combined_signal
    proposal["status"] = "pending_review"
    proposal["resolution"] = None
    proposal["resolution_notes"] = ""
    proposal["clarification_request"] = ""
    proposal["updated_at"] = now
    proposal["last_resubmitted_by"] = source
    storage.parameters.set_parameter(
        _proposal_key(proposal_id),
        proposal,
        description=f"Reviewed {proposal.get('scope', 'project')} spec proposal {proposal_id}.",
    )
    index = storage.parameters.peek_parameter(SPEC_PROPOSALS_INDEX_KEY, default_value=[]) or []
    updated_rows = []
    for row in index if isinstance(index, list) else []:
        if not isinstance(row, dict):
            continue
        if row.get("proposal_id") == proposal_id:
            row = dict(row)
            row["status"] = "pending_review"
            row["updated_at"] = now
            row["summary"] = proposal.get("proposed_change", "")[:180]
        updated_rows.append(row)
    compacted_rows = _compact_proposal_index(updated_rows)
    storage.parameters.set_parameter(
        SPEC_PROPOSALS_INDEX_KEY,
        compacted_rows,
        description="Index of durable spec proposals and their current review status.",
    )
    _archive_old_resolved_proposals(storage)
    return proposal
