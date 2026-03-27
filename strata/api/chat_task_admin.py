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
    async def list_tasks(storage=Depends(get_storage)):
        return runtime.list_tasks_payload(storage)

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
            }
            for m in history
        ]

    @app.get("/sessions")
    async def get_sessions(storage=Depends(get_storage)):
        return storage.messages.get_session_summaries()

    @app.delete("/sessions/{session_id}")
    async def delete_session(session_id: str, storage=Depends(get_storage)):
        storage.messages.archive_session(session_id)
        storage.commit()
        return {"status": "ok"}

    @app.post("/chat")
    async def post_chat(payload: Dict[str, Any], storage=Depends(get_storage)):
        session_id = payload.get("session_id", "default")
        content = payload.get("content", "")
        preferred_tier = str(payload.get("preferred_tier") or "strong").strip().lower()
        if preferred_tier not in {"strong", "weak"}:
            preferred_tier = "strong"

        storage.messages.create(role=payload["role"], content=content, session_id=session_id)
        storage.commit()

        spec_reply = await runtime.handle_spec_clarification_reply(storage, payload, session_id, content)
        if spec_reply:
            return spec_reply

        task_reply = await runtime.handle_task_clarification_reply(storage, payload, session_id, content)
        if task_reply:
            return task_reply

        return await runtime.run_chat_tool_loop(
            storage,
            session_id=session_id,
            content=content,
            preferred_tier=preferred_tier,
        )

    @app.post("/tasks")
    async def create_task(task_data: Dict[str, Any], storage=Depends(get_storage)):
        task = storage.tasks.create(**task_data)
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

        storage.messages.create(
            role="user",
            content=f"Sub-agent intervention for task '{task.title}': {override}",
            session_id=task.session_id or "default",
            is_intervention=True,
            task_id=task.task_id,
        )
        storage.commit()
        return {"status": "ok"}

    exported.update(
        {
            "list_tasks": list_tasks,
            "get_messages": get_messages,
            "get_sessions": get_sessions,
            "delete_session": delete_session,
            "post_chat": post_chat,
            "create_task": create_task,
            "task_intervene": task_intervene,
        }
    )
    return exported
