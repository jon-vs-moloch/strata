"""
@module api.chat_task_admin
@purpose Register chat, session, and task-interaction endpoints separately from the main API assembly.

This surface is the conversational front door for Strata. Keeping it isolated
from eval, knowledge admin, and worker-control endpoints makes the interaction
loop easier for small-context models to inspect without carrying unrelated
operator plumbing.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import Depends, HTTPException
from strata.api.chat_runtime import ChatRuntime
from strata.communication.primitives import (
    build_communication_decision,
    deliver_communication,
    deliver_communication_decision,
)
from strata.core.lanes import canonical_session_id_for_lane, normalize_lane
from strata.api.message_feedback import (
    annotate_feedback_event,
    build_feedback_event_message,
    get_message_feedback,
    list_message_feedback_events,
    should_trigger_feedback_distillation,
    toggle_message_reaction,
)
from strata.feedback.signals import list_feedback_signals
from strata.messages.metadata import (
    get_message_metadata,
    initialize_message_metadata,
    mark_message_seen_by_system,
    mark_messages_read,
)
from strata.prioritization.feedback import classify_feedback_priority
from strata.sessions.metadata import (
    DEFAULT_SESSION_TITLE,
    ensure_session_metadata,
    ensure_generated_session_title,
    get_session_metadata,
    mark_session_read,
    resolve_session_title,
    set_session_metadata,
)


def register_chat_task_routes(
    app,
    *,
    get_storage,
    task_model_cls,
    task_type_cls,
    task_state_cls,
    model_adapter,
    semantic_memory,
    worker,
    broadcast_event,
    queue_eval_system_job,
    global_settings,
    knowledge_page_store_cls,
    slugify_page_title,
    load_dynamic_tools,
    load_specs,
    create_spec_proposal,
    resubmit_spec_proposal_with_clarification,
    find_pending_spec_clarification,
    get_active_question,
    get_question_for_source,
    mark_question_asked,
    resolve_question,
) -> Dict[str, Any]:
    exported: Dict[str, Any] = {}
    runtime = ChatRuntime(
        task_model_cls=task_model_cls,
        task_type_cls=task_type_cls,
        task_state_cls=task_state_cls,
        model_adapter=model_adapter,
        semantic_memory=semantic_memory,
        worker=worker,
        broadcast_event=broadcast_event,
        global_settings=global_settings,
        knowledge_page_store_cls=knowledge_page_store_cls,
        slugify_page_title=slugify_page_title,
        load_dynamic_tools=load_dynamic_tools,
        load_specs=load_specs,
        create_spec_proposal=create_spec_proposal,
        resubmit_spec_proposal_with_clarification=resubmit_spec_proposal_with_clarification,
        find_pending_spec_clarification=find_pending_spec_clarification,
        get_active_question=get_active_question,
        get_question_for_source=get_question_for_source,
        mark_question_asked=mark_question_asked,
        resolve_question=resolve_question,
    )

    @app.get("/tasks", response_model=List[Dict[str, Any]])
    async def list_tasks(lane: Optional[str] = None, storage=Depends(get_storage)):
        normalized_lane = normalize_lane(lane)
        if lane is not None and normalized_lane is None:
            raw_lane = str(lane or "").strip().lower()
            if raw_lane:
                raise HTTPException(status_code=400, detail="lane must be 'strong' or 'weak'")
        return runtime.list_tasks_payload(storage, lane=normalized_lane)

    @app.get("/messages")
    async def get_messages(session_id: Optional[str] = None, limit: int = 200, storage=Depends(get_storage)):
        safe_limit = max(1, min(int(limit), 1000))
        history = storage.messages.get_all(session_id=session_id)
        history = history[-safe_limit:]
        return [
            {
                "id": m.message_id,
                "session_id": m.session_id,
                "role": m.role,
                "content": m.content,
                "is_intervention": m.is_intervention,
                "created_at": m.created_at.isoformat(),
                "message_metadata": get_message_metadata(storage, m.message_id),
                "reactions": get_message_feedback(storage, m.message_id, viewer_session_id=session_id),
            }
            for m in history
        ]

    @app.post("/messages/{message_id}/react")
    async def react_to_message(message_id: str, payload: Dict[str, Any], storage=Depends(get_storage)):
        reaction = str(payload.get("reaction") or "").strip().lower()
        session_id = str(payload.get("session_id") or "default").strip() or "default"
        message = storage.messages.get_by_id(message_id)
        if not message:
            raise HTTPException(status_code=404, detail="Message not found")
        if str(message.role or "") != "assistant":
            raise HTTPException(status_code=400, detail="Reactions are only supported on assistant messages")

        try:
            result = toggle_message_reaction(
                storage,
                message=message,
                reaction=reaction,
                session_id=session_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        prioritization = classify_feedback_priority(
            message=message,
            reaction=result.get("event", {}).get("reaction") or reaction,
            action=result.get("action") or "added",
            recent_events=list_message_feedback_events(storage, session_id=session_id, limit=20),
        )
        updated_event = annotate_feedback_event(
            storage,
            event_id=str(result.get("event", {}).get("event_id") or ""),
            prioritization=prioritization,
            distillation_status="pending_attention",
        )
        if updated_event:
            result["event"] = updated_event

        feedback_event_message = build_feedback_event_message(
            action=result.get("action") or "added",
            reaction=result.get("event", {}).get("reaction") or reaction,
            message_preview=result.get("event", {}).get("message_preview") or message.content,
        )
        feedback_urgency = (
            "high"
            if str(prioritization.get("priority") or "") in {"urgent", "review_soon"}
            else "normal"
        )
        deliver_communication_decision(
            storage,
            build_communication_decision(
                role="system",
                content=feedback_event_message,
                lane=normalize_lane(session_id.split(":", 1)[0]) or "strong",
                channel="existing_session_message",
                session_id=session_id,
                audience="user",
                source_kind="feedback_event",
                source_actor="system_opened",
                opened_reason="message_feedback",
                tags=["feedback", "autonomous", str(result.get("event", {}).get("reaction") or reaction)],
                topic_summary=str(result.get("event", {}).get("message_preview") or message.content or "").strip()[:180],
                communicative_act="notification",
                urgency=feedback_urgency,
            ),
        )
        storage.commit()
        distillation_job = None
        if queue_eval_system_job and should_trigger_feedback_distillation(
            action=result.get("action") or "",
            reaction=result.get("event", {}).get("reaction") or reaction,
        ) and str(prioritization.get("priority") or "") in {"review_soon", "urgent"}:
            distillation_job = await queue_eval_system_job(
                storage,
                kind="trace_review",
                title=f"Session Feedback Distillation: {session_id}",
                description=f"Queued session trace review after user feedback reaction in session '{session_id}'.",
                payload={
                    "trace_kind": "session_trace",
                    "session_id": session_id,
                    "reviewer_tier": "strong",
                    "emit_followups": True,
                    "persist_to_task": False,
                    "spec_scope": "project",
                    "prioritization": prioritization,
                },
                session_id=session_id,
                dedupe_signature={
                    "trace_kind": "session_trace",
                    "reviewer_tier": "strong",
                    "session_id": session_id,
                },
            )
            updated_event = annotate_feedback_event(
                storage,
                event_id=str(result.get("event", {}).get("event_id") or ""),
                prioritization=prioritization,
                distillation_status="queued",
            )
            if updated_event:
                result["event"] = updated_event
        else:
            updated_event = annotate_feedback_event(
                storage,
                event_id=str(result.get("event", {}).get("event_id") or ""),
                prioritization=prioritization,
                distillation_status="batched" if prioritization.get("should_batch") else "logged",
            )
            if updated_event:
                result["event"] = updated_event
        await broadcast_event({"type": "message", "session_id": session_id})
        return {
            "status": "ok",
            "message_id": message_id,
            "feedback": result.get("feedback") or {},
            "event": result.get("event") or {},
            "prioritization": prioritization,
            "distillation_job": distillation_job,
        }

    @app.get("/admin/messages/feedback")
    async def get_message_feedback_events(session_id: Optional[str] = None, limit: int = 100, storage=Depends(get_storage)):
        return {
            "status": "ok",
            "events": list_message_feedback_events(storage, limit=limit, session_id=session_id),
        }

    @app.get("/admin/feedback/signals")
    async def get_feedback_signals(
        session_id: Optional[str] = None,
        source_type: Optional[str] = None,
        limit: int = 100,
        storage=Depends(get_storage),
    ):
        return {
            "status": "ok",
            "signals": list_feedback_signals(storage, limit=limit, session_id=session_id, source_type=source_type),
        }

    @app.post("/admin/messages/feedback/distill_session")
    async def distill_session_feedback(payload: Dict[str, Any], storage=Depends(get_storage)):
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id field required")
        reviewer_tier = str(payload.get("reviewer_tier") or "strong").strip().lower() or "strong"
        queued = await queue_eval_system_job(
            storage,
            kind="trace_review",
            title=f"Session Feedback Distillation: {session_id}",
            description=f"Queued {reviewer_tier}-tier session trace review for feedback distillation.",
            payload={
                "trace_kind": "session_trace",
                "session_id": session_id,
                "reviewer_tier": reviewer_tier,
                "emit_followups": bool(payload.get("emit_followups", True)),
                "persist_to_task": False,
                "spec_scope": str(payload.get("spec_scope") or "project"),
            },
            session_id=session_id,
            dedupe_signature={
                "trace_kind": "session_trace",
                "reviewer_tier": reviewer_tier,
                "session_id": session_id,
            },
        )
        return {"status": "ok", **queued}

    @app.get("/sessions")
    async def get_sessions(lane: Optional[str] = None, storage=Depends(get_storage)):
        normalized_lane = normalize_lane(lane)
        if lane is not None and normalized_lane is None:
            raw_lane = str(lane or "").strip().lower()
            if raw_lane:
                raise HTTPException(status_code=400, detail="lane must be 'strong' or 'weak'")
        summaries = storage.messages.get_session_summaries(lane=normalized_lane)
        for summary in summaries:
            metadata = get_session_metadata(storage, summary["session_id"])
            if not str(metadata.get("opened_by") or "").strip():
                first_role = str(summary.get("first_message_role") or summary.get("last_message_role") or "").strip().lower()
                if first_role == "user":
                    metadata["opened_by"] = "user_opened"
                    metadata["source_kind"] = metadata.get("source_kind") or "user"
                elif first_role:
                    metadata["opened_by"] = "system_opened"
                    metadata["source_kind"] = metadata.get("source_kind") or "system"
            summary["title"] = resolve_session_title(metadata) or DEFAULT_SESSION_TITLE
            summary["session_metadata"] = metadata
        return summaries

    @app.post("/sessions/{session_id}/read")
    async def mark_session_as_read(session_id: str, payload: Optional[Dict[str, Any]] = None, storage=Depends(get_storage)):
        latest_message = storage.messages.get_all(session_id=session_id)
        latest_id = latest_message[-1].message_id if latest_message else ""
        reader = str((payload or {}).get("reader") or "user").strip() or "user"
        metadata = mark_session_read(
            storage,
            session_id=session_id,
            message_id=str((payload or {}).get("message_id") or latest_id),
        )
        read_ids = [
            str(message.message_id)
            for message in latest_message
            if str(message.role or "") in {"assistant", "system"}
        ]
        mark_messages_read(storage, message_ids=read_ids, reader=reader)
        storage.commit()
        return {"status": "ok", "session_id": session_id, "session_metadata": metadata}

    @app.patch("/sessions/{session_id}")
    async def rename_session(session_id: str, payload: Dict[str, Any], storage=Depends(get_storage)):
        title = " ".join(str(payload.get("title") or "").split()).strip()
        if not title:
            raise HTTPException(status_code=400, detail="title field required")
        metadata = set_session_metadata(
            storage,
            session_id,
            {
                "custom_title": title[:80],
                "title_source": "custom",
            },
        )
        storage.commit()
        return {"status": "ok", "session_id": session_id, "title": metadata.get("custom_title"), "session_metadata": metadata}

    @app.delete("/sessions/{session_id}")
    async def delete_session(session_id: str, storage=Depends(get_storage)):
        storage.messages.archive_session(session_id)
        storage.commit()
        return {"status": "ok"}

    @app.post("/chat")
    async def post_chat(payload: Dict[str, Any], storage=Depends(get_storage)):
        preferred_tier = str(payload.get("preferred_tier") or "strong").strip().lower()
        if preferred_tier not in {"strong", "weak"}:
            preferred_tier = "strong"
        session_id = canonical_session_id_for_lane(preferred_tier, payload.get("session_id", "default"))
        content = payload.get("content", "")
        ensure_session_metadata(
            storage,
            session_id=session_id,
            opened_by="user_opened",
            opened_reason="direct_chat",
            source_kind="user",
            tags=["chat"],
        )

        message = storage.messages.create(role=payload["role"], content=content, session_id=session_id)
        initialize_message_metadata(
            storage,
            message_id=message.message_id,
            audience="system",
            delivery_channel="session_store",
            source_kind="user",
            source_actor="user_opened",
            communicative_act="message",
            tags=["chat", "user"],
        )
        mark_message_seen_by_system(storage, message_id=message.message_id, actor="chat_runtime")
        storage.commit()

        spec_reply = await runtime.handle_spec_clarification_reply(storage, payload, session_id, content)
        if spec_reply:
            return spec_reply

        task_reply = await runtime.handle_task_clarification_reply(storage, payload, session_id, content)
        if task_reply:
            return task_reply

        result = await runtime.run_chat_tool_loop(
            storage,
            session_id=session_id,
            content=content,
            preferred_tier=preferred_tier,
        )
        try:
            await ensure_generated_session_title(storage, session_id=session_id, model_adapter=model_adapter)
            storage.commit()
        except Exception:
            storage.rollback()
        return result

    @app.post("/tasks")
    async def create_task(task_data: Dict[str, Any], storage=Depends(get_storage)):
        task_payload = dict(task_data)
        requested_lane = normalize_lane(task_payload.get("lane"))
        task_payload.pop("lane", None)
        constraints = dict(task_payload.get("constraints") or {})
        lane = normalize_lane(constraints.get("lane")) or requested_lane
        if lane:
            constraints["lane"] = lane
            task_payload["session_id"] = canonical_session_id_for_lane(lane, task_payload.get("session_id"))
        task_payload["constraints"] = constraints
        task = storage.tasks.create(**task_payload)
        storage.commit()
        return {"id": task.task_id, "status": task.state.value}

    @app.post("/tasks/{task_id}/intervene")
    async def task_intervene(task_id: str, payload: Dict[str, Any], storage=Depends(get_storage)):
        task = storage.tasks.get_by_id(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        override = payload.get("override")
        if not override:
            raise HTTPException(status_code=400, detail="Override content required")

        task.description = (task.description or "") + f"\n\n[USER INTERVENTION]: {override}"
        task.state = task_state_cls.PENDING
        task.human_intervention_required = False
        queued_question = get_question_for_source(storage, source_type="task_blocked", source_id=task_id)
        if queued_question:
            resolve_question(storage, queued_question["question_id"], resolution="resolved", response=override)
        storage.commit()

        await worker.enqueue(task.task_id)

        message = storage.messages.create(
            role="user",
            content=f"Sub-agent intervention for task '{task.title}': {override}",
            session_id=task.session_id or "default",
            is_intervention=True,
            task_id=task.task_id,
        )
        initialize_message_metadata(
            storage,
            message_id=message.message_id,
            audience="system",
            delivery_channel="session_store",
            source_kind="user_intervention",
            source_actor="user_opened",
            communicative_act="message",
            tags=["task", "intervention", "user"],
        )
        mark_message_seen_by_system(storage, message_id=message.message_id, actor="task_intervention")
        storage.commit()
        return {"status": "ok"}

    exported.update(
        {
            "list_tasks": list_tasks,
            "get_messages": get_messages,
            "react_to_message": react_to_message,
            "distill_session_feedback": distill_session_feedback,
            "get_sessions": get_sessions,
            "mark_session_as_read": mark_session_as_read,
            "delete_session": delete_session,
            "rename_session": rename_session,
            "post_chat": post_chat,
            "create_task": create_task,
            "task_intervene": task_intervene,
        }
    )
    return exported
