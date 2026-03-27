"""
@module specs.bootstrap
@purpose Ensure durable spec files exist for alignment and operator guidance.
@owns spec directory bootstrap, default spec content, spec loading
@does_not_own higher-level alignment policy decisions
@key_exports ensure_spec_files, load_specs
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from strata.observability.context import record_context_load
from strata.experimental.audit_registry import audit_stored_artifact

ROOT = Path(__file__).resolve().parents[2]
SPECS_DIR = ROOT / ".knowledge" / "specs"
CONSTITUTION_PATH = SPECS_DIR / "constitution.md"
LEGACY_GLOBAL_SPEC_PATH = SPECS_DIR / "global_spec.md"
PROJECT_SPEC_PATH = SPECS_DIR / "project_spec.md"
SPEC_PROPOSALS_INDEX_KEY = "spec_proposals:index"
SPEC_PROPOSAL_KEY_PREFIX = "spec_proposal:"
SPEC_REGISTRY_KEY_PREFIX = "spec_registry"
SPEC_SCOPE_INDEX_KEY = "spec_registry:index"
MAX_TERMINAL_SPEC_PROPOSALS = 100
DEFAULT_ALLOWED_MUTATION_CLASSES = [
    "policy_weight_adjustment",
    "clarification_with_no_behavior_change",
    "new_forbidden_action",
    "new_required_logging_field",
    "metric_priority_restatement",
]

DEFAULT_CONSTITUTION = """# Constitution

This file stores persistent, cross-project instructions and preferences for Strata.

Canonical location: `.knowledge/specs/constitution.md`

Current durable guidance:
- prefer explicit evaluation over vague hope; if we want an outcome, we should measure it
- respect disclosure and permission boundaries for knowledge and memory
- prefer modest resource use and gentle local-hardware defaults unless the operator asks otherwise
- preserve provenance for spec changes, knowledge synthesis, and promotions so decisions stay explainable

If there are no durable constitutional preferences yet, leave this file in place and update it as they become clear.
"""

DEFAULT_PROJECT_SPEC = """# Project Spec

This file stores the current high-level vision for the active Strata project.

Canonical location: `.knowledge/specs/project_spec.md`

Current project intent:
- extract useful work from small local models by pushing rigor into the system rather than the model
- improve outputs through multi-step refinement, validation against downstream data, and explicit evaluation
- use a stronger tier to improve the harness until the weak tier can improve the system itself
- treat repo structure, modularity, and progressive disclosure as supports for small models with small context
- keep bootstrap progress measurable through evals, telemetry, and promotion evidence

Canonical supporting references:
- `README.md`
- `docs/spec/project-philosophy.md`
- `docs/spec/codemap.md`

This file should exist even when it is sparse, because alignment and maintenance tasks depend on having a stable place to look for project intent.
"""


def ensure_spec_files() -> Dict[str, str]:
    SPECS_DIR.mkdir(parents=True, exist_ok=True)
    if not CONSTITUTION_PATH.exists():
        if LEGACY_GLOBAL_SPEC_PATH.exists():
            CONSTITUTION_PATH.write_text(LEGACY_GLOBAL_SPEC_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            CONSTITUTION_PATH.write_text(DEFAULT_CONSTITUTION.strip() + "\n", encoding="utf-8")
    if not PROJECT_SPEC_PATH.exists():
        PROJECT_SPEC_PATH.write_text(DEFAULT_PROJECT_SPEC.strip() + "\n", encoding="utf-8")
    return {
        "specs_dir": str(SPECS_DIR),
        "constitution_path": str(CONSTITUTION_PATH),
        "global_spec_path": str(CONSTITUTION_PATH),
        "project_spec_path": str(PROJECT_SPEC_PATH),
    }


def load_specs(*, storage=None) -> Dict[str, str]:
    ensure_spec_files()
    constitution = CONSTITUTION_PATH.read_text(encoding="utf-8")
    project_spec = PROJECT_SPEC_PATH.read_text(encoding="utf-8")
    record_context_load(
        artifact_type="spec",
        identifier="constitution",
        content=constitution,
        source="specs.bootstrap.load_specs",
        metadata={"path": str(CONSTITUTION_PATH)},
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
        "constitution": constitution,
        "global_spec": constitution,
        "project_spec": project_spec,
    }


def spec_is_bootstrap_placeholder(spec_text: str) -> bool:
    normalized = str(spec_text or "").strip()
    if not normalized:
        return True
    markers = [
        "Suggested contents:",
        "If there are no durable constitutional preferences yet",
        "This file should exist even when it is sparse",
    ]
    return any(marker in normalized for marker in markers)


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
    return CONSTITUTION_PATH if str(scope).strip().lower() == "global" else PROJECT_SPEC_PATH


def _content_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _registry_key(scope: str) -> str:
    normalized_scope = "global" if str(scope).strip().lower() == "global" else "project"
    return f"{SPEC_REGISTRY_KEY_PREFIX}:{normalized_scope}"


def _scope_index_row(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "scope": record.get("scope"),
        "active_version": record.get("active_version"),
        "updated_at": record.get("updated_at"),
        "history_count": len(record.get("history") or []),
    }


def _user_message_refs(attribution: Dict[str, Any]) -> List[Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []
    for item in list((attribution or {}).get("message_citations") or []):
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip().lower() != "user":
            continue
        refs.append(
            {
                "message_id": item.get("message_id"),
                "created_at": item.get("created_at"),
                "excerpt": item.get("excerpt"),
            }
        )
    return refs


def ensure_spec_registry(storage, *, scope: str) -> Dict[str, Any]:
    ensure_spec_files()
    normalized_scope = "global" if str(scope).strip().lower() == "global" else "project"
    existing = storage.parameters.peek_parameter(_registry_key(normalized_scope), default_value=None)
    if isinstance(existing, dict) and existing.get("active_version"):
        return dict(existing)
    path = _spec_path_for_scope(normalized_scope)
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    initial_version = f"{normalized_scope}_bootstrap_{_content_hash(content)[:10]}"
    now = datetime.now(timezone.utc).isoformat()
    spec_artifact = {
        "artifact_type": "spec_artifact",
        "scope": normalized_scope,
        "version": initial_version,
        "prior_version": None,
        "status": "active",
        "allowed_mutation_classes": list(DEFAULT_ALLOWED_MUTATION_CLASSES),
        "adoption_provenance": [],
        "adoption_audit_artifact_id": None,
        "activated_at": now,
        "content_hash": _content_hash(content),
        "content": content,
    }
    registry = {
        "scope": normalized_scope,
        "active_version": initial_version,
        "updated_at": now,
        "history": [spec_artifact],
    }
    storage.parameters.set_parameter(
        _registry_key(normalized_scope),
        registry,
        description=f"Versioned {normalized_scope} spec registry.",
    )
    index = storage.parameters.peek_parameter(SPEC_SCOPE_INDEX_KEY, default_value=[]) or []
    if not isinstance(index, list):
        index = []
    index = [dict(row) for row in index if isinstance(row, dict) and row.get("scope") != normalized_scope]
    index.append(_scope_index_row(registry))
    storage.parameters.set_parameter(
        SPEC_SCOPE_INDEX_KEY,
        index,
        description="Versioned spec registry overview by scope.",
    )
    return registry


def get_active_spec_record(storage, *, scope: str) -> Dict[str, Any]:
    registry = ensure_spec_registry(storage, scope=scope)
    active_version = registry.get("active_version")
    for row in reversed(list(registry.get("history") or [])):
        if isinstance(row, dict) and row.get("version") == active_version:
            return dict(row)
    return {}


def validate_mutation_class(storage, *, scope: str, claimed_mutation_class: str) -> Dict[str, Any]:
    active_spec = get_active_spec_record(storage, scope=scope)
    allowed = list(active_spec.get("allowed_mutation_classes") or [])
    normalized_class = str(claimed_mutation_class or "").strip()
    return {
        "active_spec_version": active_spec.get("version"),
        "claimed_mutation_class": normalized_class,
        "allowed_mutation_classes": allowed,
        "allowed": normalized_class in allowed,
    }


def _persist_registry(storage, registry: Dict[str, Any]) -> None:
    storage.parameters.set_parameter(
        _registry_key(str(registry.get("scope") or "project")),
        registry,
        description=f"Versioned {registry.get('scope', 'project')} spec registry.",
    )
    index = storage.parameters.peek_parameter(SPEC_SCOPE_INDEX_KEY, default_value=[]) or []
    if not isinstance(index, list):
        index = []
    index = [dict(row) for row in index if isinstance(row, dict) and row.get("scope") != registry.get("scope")]
    index.append(_scope_index_row(registry))
    storage.parameters.set_parameter(
        SPEC_SCOPE_INDEX_KEY,
        index,
        description="Versioned spec registry overview by scope.",
    )


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
    claimed_mutation_class: str = "clarification_with_no_behavior_change",
    proposal_kind: str = "clarification",
) -> Dict[str, Any]:
    ensure_spec_files()
    normalized_scope = "global" if str(scope).strip().lower() == "global" else "project"
    registry = ensure_spec_registry(storage, scope=normalized_scope)
    active_spec = get_active_spec_record(storage, scope=normalized_scope)
    proposal_id = f"spec_{uuid4().hex[:12]}"
    current_specs = load_specs(storage=storage)
    now = datetime.now(timezone.utc).isoformat()
    proposal_attribution = attribution or build_spec_attribution(
        storage,
        session_id=session_id,
        user_signal=user_signal,
    )
    user_refs = _user_message_refs(proposal_attribution)
    if not user_refs:
        raise ValueError("Spec proposals require explicit user-message provenance.")
    mutation_validation = validate_mutation_class(
        storage,
        scope=normalized_scope,
        claimed_mutation_class=claimed_mutation_class,
    )
    proposal = {
        "proposal_id": proposal_id,
        "scope": normalized_scope,
        "status": "pending_review",
        "proposal_kind": str(proposal_kind or "clarification").strip(),
        "proposed_change": str(proposed_change).strip(),
        "rationale": str(rationale).strip(),
        "user_signal": str(user_signal).strip(),
        "session_id": session_id,
        "source": source,
        "review_task_id": review_task_id,
        "attribution": proposal_attribution,
        "requested_by_user_refs": user_refs,
        "claimed_mutation_class": str(claimed_mutation_class or "").strip(),
        "validation_status": "valid" if mutation_validation.get("allowed") else "invalid",
        "validation_details": mutation_validation,
        "current_spec_snapshot": current_specs["global_spec" if normalized_scope == "global" else "project_spec"],
        "target_spec_version": active_spec.get("version"),
        "governing_spec_version": registry.get("active_version"),
        "created_at": now,
        "updated_at": now,
        "resolution": None,
        "resolution_notes": "",
        "clarification_request": "",
        "applied_at": None,
        "governance_audit_artifact_id": None,
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
        if not proposal.get("requested_by_user_refs"):
            raise ValueError("Spec proposal cannot activate without explicit user-message provenance.")
        validation_details = dict(proposal.get("validation_details") or {})
        if not validation_details.get("allowed"):
            raise ValueError("Spec proposal claimed a mutation class that is not allowed by the active spec.")
        scope = str(proposal.get("scope") or "project")
        active_spec = get_active_spec_record(storage, scope=scope)
        proposal["audit_timeline_artifact_id"] = proposal.get("audit_timeline_artifact_id") or proposal_id
        storage.parameters.set_parameter(
            f"audit_artifact:timeline:{proposal_id}",
            {
                "artifact_id": proposal_id,
                "artifact_type": "timeline_artifact",
                "trace_kind": "spec_change",
                "created_at_utc": now,
                "applicable_spec_version": active_spec.get("version"),
                "parent_artifact_ids": [],
                "events": [
                    {
                        "id": f"{proposal_id}:spec_change_proposed",
                        "timeline_id": proposal_id,
                        "type": "spec_change_proposed",
                        "timestamp_utc": proposal.get("created_at") or now,
                        "source_trace_refs": proposal.get("requested_by_user_refs") or [],
                        "payload": {
                            "proposal_id": proposal_id,
                            "scope": scope,
                            "claimed_mutation_class": proposal.get("claimed_mutation_class"),
                            "validation_status": proposal.get("validation_status"),
                        },
                        "inferred": False,
                    }
                ],
                "metadata": {"proposal_id": proposal_id, "scope": scope},
                "content_hash": _content_hash(str(proposal.get("proposed_change") or "")),
            },
            description=f"Timeline artifact for spec proposal {proposal_id}.",
        )
        audit_artifact = audit_stored_artifact(
            storage,
            artifact_type="timeline",
            artifact_id=proposal_id,
            spec_version_used=active_spec.get("version"),
            rationale="Current spec must judge the proposed next spec before activation.",
        )
        proposal["governance_audit_artifact_id"] = audit_artifact.get("artifact_id")
        proposal["governance_audit_summary"] = audit_artifact.get("summary_verdict")
        if str((audit_artifact.get("summary_verdict") or {}).get("status") or "") == "fail":
            raise ValueError("Spec proposal failed current-spec audit and cannot activate.")
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
        current_content = path.read_text(encoding="utf-8")
        registry = ensure_spec_registry(storage, scope=scope)
        history = list(registry.get("history") or [])
        prior_version = registry.get("active_version")
        for row in history:
            if isinstance(row, dict) and row.get("version") == prior_version:
                row["status"] = "superseded"
        next_version = f"{scope}_{_content_hash(current_content)[:10]}"
        history.append(
            {
                "artifact_type": "spec_artifact",
                "scope": scope,
                "version": next_version,
                "prior_version": prior_version,
                "status": "active",
                "allowed_mutation_classes": list(
                    (active_spec.get("allowed_mutation_classes") or DEFAULT_ALLOWED_MUTATION_CLASSES)
                ),
                "adoption_provenance": list(proposal.get("requested_by_user_refs") or []),
                "adoption_audit_artifact_id": proposal.get("governance_audit_artifact_id"),
                "activated_at": now,
                "content_hash": _content_hash(current_content),
                "content": current_content,
                "proposal_id": proposal_id,
                "claimed_mutation_class": proposal.get("claimed_mutation_class"),
            }
        )
        registry["active_version"] = next_version
        registry["updated_at"] = now
        registry["history"] = history[-100:]
        _persist_registry(storage, registry)

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
