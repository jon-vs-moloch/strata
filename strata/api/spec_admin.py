"""
@module api.spec_admin
@purpose Register durable-spec read and proposal-review endpoints separately from the main API assembly.

Specs define the system's durable intent. Isolating their admin surface helps
small models reason about governance without also ingesting chat flow or worker
control code.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException


def register_spec_admin_routes(
    app,
    *,
    get_storage,
    load_specs,
    list_spec_proposals,
    get_spec_proposal,
    create_spec_proposal,
    resolve_spec_proposal,
    enqueue_user_question,
) -> Dict[str, Any]:
    exported: Dict[str, Any] = {}

    @app.get("/admin/specs")
    async def get_specs():
        return {"status": "ok", "specs": load_specs()}

    @app.get("/admin/spec_proposals")
    async def get_spec_proposals(status: Optional[str] = None, limit: int = 50, storage=Depends(get_storage)):
        proposals = list_spec_proposals(storage, status=status, limit=limit)
        return {"status": "ok", "proposals": proposals}

    @app.get("/admin/spec_proposals/{proposal_id}")
    async def get_spec_proposal_detail(proposal_id: str, storage=Depends(get_storage)):
        proposal = get_spec_proposal(storage, proposal_id)
        if not proposal:
            raise HTTPException(status_code=404, detail="Spec proposal not found")
        return {"status": "ok", "proposal": proposal}

    @app.post("/admin/spec_proposals")
    async def create_spec_proposal_endpoint(payload: Dict[str, Any], storage=Depends(get_storage)):
        scope = str(payload.get("scope") or "project")
        proposed_change = str(payload.get("proposed_change") or "").strip()
        rationale = str(payload.get("rationale") or "").strip()
        if not proposed_change or not rationale:
            raise HTTPException(status_code=400, detail="proposed_change and rationale are required")
        proposal = create_spec_proposal(
            storage,
            scope=scope,
            proposed_change=proposed_change,
            rationale=rationale,
            user_signal=str(payload.get("user_signal") or ""),
            session_id=payload.get("session_id"),
            source=str(payload.get("source") or "api"),
            review_task_id=payload.get("review_task_id"),
        )
        storage.commit()
        return {"status": "ok", "proposal": proposal}

    @app.post("/admin/spec_proposals/{proposal_id}/resolve")
    async def resolve_spec_proposal_endpoint(proposal_id: str, payload: Dict[str, Any], storage=Depends(get_storage)):
        resolution = str(payload.get("resolution") or "").strip()
        if not resolution:
            raise HTTPException(status_code=400, detail="resolution is required")
        try:
            proposal = resolve_spec_proposal(
                storage,
                proposal_id=proposal_id,
                resolution=resolution,
                reviewer_notes=str(payload.get("reviewer_notes") or ""),
                clarification_request=str(payload.get("clarification_request") or ""),
                reviewer=str(payload.get("reviewer") or "operator"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not proposal:
            raise HTTPException(status_code=404, detail="Spec proposal not found")
        if proposal.get("status") == "needs_clarification" and proposal.get("session_id"):
            enqueue_user_question(
                storage,
                session_id=proposal.get("session_id") or "default",
                question=proposal.get("clarification_request") or "More detail is required before this spec change can be reviewed.",
                source_type="spec_clarification",
                source_id=proposal_id,
                context={
                    "scope": proposal.get("scope"),
                    "proposed_change": proposal.get("proposed_change"),
                },
            )
        storage.commit()
        return {"status": "ok", "proposal": proposal}

    exported.update(
        {
            "get_specs": get_specs,
            "get_spec_proposals": get_spec_proposals,
            "get_spec_proposal_detail": get_spec_proposal_detail,
            "create_spec_proposal_endpoint": create_spec_proposal_endpoint,
            "resolve_spec_proposal_endpoint": resolve_spec_proposal_endpoint,
        }
    )
    return exported
