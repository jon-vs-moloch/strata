"""
@module experimental.artifact_pipeline
@purpose Shared helpers for turning traces and feedback into durable audit artifacts and follow-up knowledge work.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from strata.experimental.audit_registry import audit_timeline_artifact, persist_timeline_artifact
from strata.sessions.metadata import record_session_audit, set_session_metadata
from strata.specs.bootstrap import get_active_spec_record


SESSION_TRACE_REVIEW_KEY_PREFIX = "session_trace_review"


def _session_review_key(session_id: str) -> str:
    return f"{SESSION_TRACE_REVIEW_KEY_PREFIX}:{session_id}"


def persist_trace_review_artifacts(
    storage,
    *,
    trace_kind: str,
    trace_summary: Dict[str, Any],
    review: Dict[str, Any],
    spec_scope: str = "project",
    reviewer_tier: Optional[str] = None,
    candidate_change_id: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    active_spec = get_active_spec_record(storage, scope=spec_scope)
    timeline_artifact = persist_timeline_artifact(
        storage,
        trace_kind=trace_kind,
        trace_summary=trace_summary,
        applicable_spec_version=active_spec.get("version"),
        metadata={
            "reviewer_tier": reviewer_tier or review.get("reviewer_tier"),
            "candidate_change_id": candidate_change_id,
        },
    )
    audit_artifact = audit_timeline_artifact(
        storage,
        timeline_artifact=timeline_artifact,
        spec_version_used=active_spec.get("version"),
        rationale="Durable trace review artifacts should be auditable against the governing spec.",
        confidence=float(review.get("confidence", 0.7) or 0.7),
        extra_context={
            "reviewer_tier": reviewer_tier or review.get("reviewer_tier"),
            "trace_kind": trace_kind,
            "associated_review_status": review.get("status"),
        },
    )
    return {
        "timeline_artifact": timeline_artifact,
        "audit_artifact": audit_artifact,
    }


def append_trace_review_to_session(storage, *, session_id: str, review: Dict[str, Any], limit: int = 10) -> Dict[str, Any]:
    session_key = _session_review_key(session_id)
    current = storage.parameters.peek_parameter(session_key, default_value={}) or {}
    rows = list((current.get("reviews") or [])) if isinstance(current, dict) else []
    slim_review = {
        "recorded_at": review.get("recorded_at"),
        "trace_kind": review.get("trace_kind"),
        "reviewer_tier": review.get("reviewer_tier"),
        "overall_assessment": review.get("overall_assessment"),
        "primary_failure_mode": review.get("primary_failure_mode"),
        "recommended_title": review.get("recommended_title"),
        "summary": review.get("summary"),
        "targeted_interventions": review.get("targeted_interventions") or [],
        "telemetry_to_watch": review.get("telemetry_to_watch") or [],
        "timeline_artifact_id": review.get("timeline_artifact_id"),
        "audit_artifact_id": review.get("audit_artifact_id"),
    }
    rows.append(slim_review)
    payload = {
        "session_id": session_id,
        "reviews": rows[-max(1, limit):],
        "updated_at": review.get("recorded_at"),
    }
    storage.parameters.set_parameter(
        session_key,
        payload,
        description=f"Recent trace reviews for chat session {session_id}.",
    )
    recommended_title = " ".join(str(review.get("recommended_title") or "").split()).strip()[:80]
    if recommended_title:
        set_session_metadata(
            storage,
            session_id,
            {
                "recommended_title": recommended_title,
                "title_recommendation_source": "session_trace_review",
                "title_recommendation_recorded_at": review.get("recorded_at"),
            },
        )
    record_session_audit(
        storage,
        session_id=session_id,
        audited_at=review.get("recorded_at"),
        reviewer_tier=str(review.get("reviewer_tier") or ""),
    )
    return payload


def enqueue_review_followups(
    storage,
    *,
    trace_kind: str,
    trace_summary: Dict[str, Any],
    review: Dict[str, Any],
    session_id: Optional[str] = None,
    knowledge_page_store_cls=None,
) -> List[str]:
    queued_task_ids: List[str] = []
    if knowledge_page_store_cls is None:
        return queued_task_ids
    knowledge_pages = knowledge_page_store_cls(storage)
    evidence = [
        str(item)
        for item in [
            review.get("summary"),
            review.get("primary_failure_mode"),
            *(review.get("evidence") or [])[:4],
            *(trace_summary.get("feedback_summaries") or [])[:4],
        ]
        if str(item or "").strip()
    ]
    def _compact_evidence(rows):
        compact = []
        for item in rows:
            text = " ".join(str(item or "").split()).strip()
            if not text:
                continue
            if len(text) > 220:
                text = text[:217].rstrip() + "..."
            if text not in compact:
                compact.append(text)
            if len(compact) >= 6:
                break
        return compact
    compact_evidence = _compact_evidence(evidence)
    if trace_kind == "session_trace" and session_id:
        task = knowledge_pages.enqueue_update_task(
            slug="user-profile",
            reason=f"[session_feedback] Distill durable user preferences and chat-quality signal from session '{session_id}'.",
            session_id=session_id,
            target_scope="chat",
            evidence=compact_evidence,
            domain="user",
            operation="knowledge_refresh",
            provenance={
                "source_kind": "trace_review",
                "source_actor": str(review.get("reviewer_tier") or "trainer").strip().lower() or "trainer",
                "authority_kind": "spec_policy",
                "authority_ref": "trace_review_followup",
                "derived_from": [
                    *( [f"session:{session_id}"] if session_id else [] ),
                    *( [f"timeline:{review.get('timeline_artifact_id')}"] if review.get("timeline_artifact_id") else [] ),
                ],
                "governing_spec_refs": [
                    ".knowledge/specs/constitution.md",
                    ".knowledge/specs/project_spec.md",
                    "docs/spec/step-runtime-flow.md",
                ],
                "note": "Knowledge refresh queued from trace review follow-up.",
            },
        )
        queued_task_ids.append(task.task_id)
    for intervention in review.get("targeted_interventions") or []:
        if str((intervention or {}).get("kind") or "").strip().lower() != "spec":
            continue
        task = knowledge_pages.enqueue_update_task(
            slug="project-spec",
            reason=f"[trace_review] Session review suggests project-level guidance changes: {str((intervention or {}).get('description') or '').strip()}",
            session_id=session_id,
            target_scope="codebase",
            evidence=compact_evidence,
            domain="project",
            operation="knowledge_refresh",
            provenance={
                "source_kind": "trace_review",
                "source_actor": str(review.get("reviewer_tier") or "trainer").strip().lower() or "trainer",
                "authority_kind": "spec_policy",
                "authority_ref": "trace_review_followup",
                "derived_from": [
                    *( [f"session:{session_id}"] if session_id else [] ),
                    *( [f"timeline:{review.get('timeline_artifact_id')}"] if review.get("timeline_artifact_id") else [] ),
                ],
                "governing_spec_refs": [
                    ".knowledge/specs/constitution.md",
                    ".knowledge/specs/project_spec.md",
                    "docs/spec/step-runtime-flow.md",
                ],
                "note": "Knowledge refresh queued from trace review follow-up.",
            },
        )
        queued_task_ids.append(task.task_id)
        break
    return queued_task_ids
