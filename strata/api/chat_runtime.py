"""
@module api.chat_runtime
@purpose Shared runtime helpers for the chat/task route surface.

The chat routes need to stay readable. This module carries the synchronous tool
loop, clarification handling, and task serialization so route registration can
stay small and declarative.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from strata.observability.context import record_context_load
from strata.api.chat_tool_executor import ChatToolExecutor
from strata.context.loaded_files import build_loaded_context_block
from strata.models.adapter import ModelAdapter
from strata.schemas.execution import StrongExecutionContext, WeakExecutionContext

class ChatRuntime:
    def __init__(self, **deps: Any):
        self.deps = deps
        self.tool_executor = ChatToolExecutor(**deps)

    def list_tasks_payload(self, storage) -> List[Dict[str, Any]]:
        from sqlalchemy.orm import selectinload

        task_model_cls = self.deps["task_model_cls"]
        get_question_for_source = self.deps["get_question_for_source"]
        tasks = storage.session.query(task_model_cls).options(selectinload(task_model_cls.attempts)).all()
        return [
            {
                "id": task.task_id,
                "parent_id": task.parent_task_id,
                "title": task.title,
                "description": task.description,
                "status": task.state.value.lower(),
                "type": task.type.value.lower(),
                "depth": task.depth,
                "human_intervention_required": task.human_intervention_required,
                "system_job": (task.constraints or {}).get("system_job"),
                "system_job_result": (task.constraints or {}).get("system_job_result"),
                "generated_reports": (task.constraints or {}).get("generated_reports", []),
                "pending_question": (
                    get_question_for_source(storage, source_type="task_blocked", source_id=task.task_id).get("question")
                    if task.human_intervention_required
                    else None
                ),
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "updated_at": task.updated_at.isoformat() if task.updated_at else None,
                "attempts": [
                    {
                        "id": attempt.attempt_id,
                        "outcome": attempt.outcome.value.lower() if attempt.outcome else None,
                        "resolution": attempt.resolution.value.lower() if attempt.resolution else None,
                        "started_at": attempt.started_at.isoformat(),
                        "ended_at": attempt.ended_at.isoformat() if attempt.ended_at else None,
                        "reason": attempt.reason,
                    }
                    for attempt in task.attempts
                ],
            }
            for task in tasks
        ]

    async def handle_spec_clarification_reply(self, storage, payload: Dict[str, Any], session_id: str, content: str):
        pending_spec_proposal = self.deps["find_pending_spec_clarification"](storage, session_id)
        if not (
            pending_spec_proposal
            and payload.get("role") == "user"
            and pending_spec_proposal.get("_pending_question", {}).get("status") == "asked"
        ):
            return None

        updated_proposal = self.deps["resubmit_spec_proposal_with_clarification"](
            storage,
            proposal_id=pending_spec_proposal["proposal_id"],
            clarification_response=content,
            source="user",
        )
        current_specs = self.deps["load_specs"]()
        scope = updated_proposal.get("scope", "project")
        current_spec = current_specs.get("global_spec" if scope == "global" else "project_spec", "")
        task = storage.tasks.create(
            title=f"Spec Clarification Review ({scope.title()}): {updated_proposal.get('proposal_id')}",
            description=(
                f"Re-review a spec proposal after user clarification.\n\n"
                f"Current {scope} spec:\n{current_spec}\n\n"
                f"Proposed change:\n{updated_proposal.get('proposed_change')}\n\n"
                f"Rationale:\n{updated_proposal.get('rationale')}\n\n"
                f"Accumulated user signal:\n{updated_proposal.get('user_signal')}\n\n"
                "Tasks:\n"
                "1. Re-check contradictions or ambiguity.\n"
                "2. If still unresolved, produce a tighter clarification request.\n"
                "3. If resolved, recommend a clean spec delta without directly editing the spec.\n"
            ),
            session_id=session_id,
            state=self.deps["task_state_cls"].PENDING,
            constraints={
                "target_scope": "codebase",
                "spec_operation": "review_proposal",
                "spec_scope": scope,
                "spec_proposal_id": updated_proposal["proposal_id"],
                "proposed_change": updated_proposal.get("proposed_change"),
                "rationale": updated_proposal.get("rationale"),
                "user_signal": updated_proposal.get("user_signal"),
            },
        )
        task.type = self.deps["task_type_cls"].RESEARCH
        storage.commit()
        await self.deps["worker"].enqueue(task.task_id)
        self.deps["resolve_question"](
            storage,
            pending_spec_proposal["_pending_question"]["question_id"],
            resolution="resolved",
            response=content,
        )
        storage.messages.create(
            role="assistant",
            content=(
                f"I’ve attached your clarification to spec proposal {updated_proposal['proposal_id']} "
                f"and kicked off a fresh review task ({task.task_id})."
            ),
            session_id=session_id,
        )
        storage.commit()
        return {
            "status": "ok",
            "reply": (
                f"I’ve attached your clarification to spec proposal {updated_proposal['proposal_id']} "
                f"and kicked off a fresh review."
            ),
            "spec_proposal_id": updated_proposal["proposal_id"],
            "task_id": task.task_id,
        }

    async def handle_task_clarification_reply(self, storage, payload: Dict[str, Any], session_id: str, content: str):
        pending_question = self.deps["get_active_question"](storage, session_id)
        if not (pending_question and payload.get("role") == "user" and pending_question.get("status") == "asked"):
            return None
        if pending_question.get("source_type") != "task_blocked":
            return None

        task_id = str(pending_question.get("source_id") or "")
        task = storage.tasks.get_by_id(task_id)
        if not task:
            return None

        task.description = (task.description or "") + f"\n\nUser clarification:\n{content.strip()}"
        task.human_intervention_required = False
        task.state = self.deps["task_state_cls"].PENDING
        storage.commit()
        await self.deps["worker"].enqueue(task.task_id)
        self.deps["resolve_question"](storage, pending_question["question_id"], resolution="resolved", response=content)
        storage.messages.create(
            role="assistant",
            content=f"I’ve attached your clarification to task {task.task_id} and re-queued it.",
            session_id=session_id,
        )
        storage.commit()
        return {
            "status": "ok",
            "reply": f"I’ve attached your clarification to task {task.task_id} and re-queued it.",
            "task_id": task.task_id,
        }

    def build_chat_messages(self, storage, *, session_id: str, content: str, pending_question: Optional[Dict[str, Any]]):
        knowledge_pages = self.deps["knowledge_page_store_cls"](storage)
        past_memories = self.deps["semantic_memory"].query_memory(content, n_results=5)
        memory_context = ""
        if isinstance(past_memories, dict) and past_memories.get("documents") and past_memories["documents"][0]:
            joined = "\n".join(past_memories["documents"][0])
            record_context_load(
                artifact_type="semantic_memory",
                identifier=f"memory_query:{content[:80]}",
                content=joined,
                source="api.chat_runtime.build_chat_messages",
                metadata={"session_id": session_id},
                storage=storage,
            )
            memory_context = "\n\nRELEVANT PAST CONTEXT:\n" + joined

        active_tools = self.deps["load_dynamic_tools"]()
        tool_summaries = []
        for tool in active_tools:
            function = tool.get("function", {})
            name = function.get("name")
            description = function.get("description")
            if name and description:
                tool_summaries.append(f"- {name}: {description}")
        tool_summary_text = "\n".join(tool_summaries) if tool_summaries else "- No active tools are currently exposed."

        messages = [
            {
                "role": "system",
                "content": f"""You are Strata, a unified AI engineering system. You manage a swarm of background agents, but present yourself as a single, first-person entity.
If internal processes hit a BLOCKED state, explain the issue to the USER directly.
If the user expresses a durable desired future state, persistent preference, or architectural constraint, prefer `propose_spec_update` over casual implementation drift. Durable intent should be reviewed against the spec, not silently improvised.
When you call a tool, include a short `reason` argument explaining what you are doing and why. If you give the user any intermediate update, make it conversational and useful, not a raw system trace.
{memory_context}

Available Tools:
{tool_summary_text}
""",
            }
        ]
        if pending_question and pending_question.get("status") == "pending":
            brief_question = pending_question.get("brief_question") or pending_question.get("question")
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Internal pending user question:\n"
                        f"- source_type: {pending_question.get('source_type')}\n"
                        f"- question: {pending_question.get('question')}\n"
                        f"- concise_delivery: {brief_question}\n\n"
                        "Before doing anything else, ask exactly one concise question in no more than two short sentences. "
                        "Do not use numbered lists, bullet lists, long background explanations, or internal implementation details. "
                        "Do not answer the question yourself. Do not call tools before asking it."
                    ),
                }
            )
            self.deps["mark_question_asked"](storage, pending_question["question_id"])
            storage.commit()

        history_records = storage.messages.get_all(session_id=session_id)
        loaded_context_block = build_loaded_context_block(
            storage,
            source="api.chat_runtime.build_chat_messages.loaded_context",
        )
        if loaded_context_block:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Persistent workspace context is currently loaded for this round.\n"
                        "Treat these files as explicitly pinned until they are unloaded:\n\n"
                        f"{loaded_context_block}"
                    ),
                }
            )
        if history_records:
            history_text = "\n".join(str(message.content or "") for message in history_records[-5:])
            record_context_load(
                artifact_type="session_history",
                identifier=session_id,
                content=history_text,
                source="api.chat_runtime.build_chat_messages",
                metadata={"message_count": min(5, len(history_records))},
                storage=storage,
            )
        for message in history_records[-5:]:
            messages.append({"role": message.role, "content": message.content})
        return messages, active_tools, knowledge_pages, pending_question

    async def run_chat_tool_loop(self, storage, *, session_id: str, content: str, preferred_tier: str = "strong"):
        preferred_tier = str(preferred_tier or "strong").lower()
        chat_context = (
            WeakExecutionContext(run_id=f"chat:{session_id}")
            if preferred_tier == "weak"
            else StrongExecutionContext(run_id=f"chat:{session_id}")
        )
        chat_adapter = ModelAdapter(context=chat_context)
        chat_adapter._selected_models = dict(getattr(self.deps["model_adapter"], "_selected_models", {}))
        weak_fallback_adapter = ModelAdapter(context=WeakExecutionContext(run_id=f"chat-weak:{session_id}"))
        weak_fallback_adapter._selected_models = dict(chat_adapter._selected_models)
        active_adapter = chat_adapter
        downgraded_to_weak = preferred_tier == "weak"
        pending_question = self.deps["get_active_question"](storage, session_id)
        messages, active_tools, knowledge_pages, pending_question = self.build_chat_messages(
            storage, session_id=session_id, content=content, pending_question=pending_question
        )
        if pending_question and pending_question.get("status") == "pending":
            active_tools = []
        max_iters = self.deps["global_settings"].get("max_sync_tool_iterations", 3)
        iteration = 0

        while iteration < max_iters:
            model_response = await active_adapter.chat(messages, tools=active_tools)
            if model_response.get("status") == "error":
                error_message = str(model_response.get("message") or model_response.get("content") or "").strip()
                if active_tools and "Function calling is not enabled" in error_message:
                    active_tools = []
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "The selected strong-tier model rejected tool calling for this request. "
                                "Answer directly without tools."
                            ),
                        }
                    )
                    continue
                if (
                    not downgraded_to_weak
                    and active_adapter is chat_adapter
                    and (
                        "Developer instruction is not enabled" in error_message
                        or "Function calling is not enabled" in error_message
                    )
                ):
                    downgraded_to_weak = True
                    active_adapter = weak_fallback_adapter
                    active_tools = []
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "The configured strong-tier chat route rejected the current instruction/tool format. "
                                "Continue this reply on the weak tier without tools, and keep the answer concise."
                            ),
                        }
                    )
                    continue
                final_reply = error_message or "I encountered an error processing that."
                storage.messages.create(role="assistant", content=final_reply, session_id=session_id)
                storage.commit()
                return {"status": "ok", "reply": final_reply}
            tool_calls = model_response.get("tool_calls")
            content_val = model_response.get("content")
            chain_of_thought = str(content_val) if content_val else ""

            if chain_of_thought and chain_of_thought.strip() and not tool_calls:
                storage.messages.create(role="assistant", content=chain_of_thought.strip(), session_id=session_id)
                storage.commit()
                await self.deps["broadcast_event"]({"type": "message", "session_id": session_id})
                return {"status": "ok", "reply": chain_of_thought.strip()}

            if tool_calls and isinstance(tool_calls, list) and len(tool_calls) > 0:
                tool_outputs_generated = False
                async_task_ids: List[str] = []
                messages.append({"role": "assistant", "content": chain_of_thought or None, "tool_calls": tool_calls})
                invocation_updates: List[str] = []
                for call in tool_calls:
                    result = await self.tool_executor.execute_tool_call(
                        storage, call=call, session_id=session_id, content=content, knowledge_pages=knowledge_pages
                    )
                    messages.append(result["tool_message"])
                    reason = str(result.get("tool_reason") or "").strip()
                    if reason:
                        invocation_updates.append(reason)
                    elif not chain_of_thought.strip():
                        invocation_updates.append(f"I'm using `{result.get('tool_name')}` to move this forward.")
                    tool_outputs_generated = tool_outputs_generated or result["tool_outputs_generated"]
                    if result["async_task_id"]:
                        async_task_ids.append(result["async_task_id"])
                summary = ""
                if invocation_updates:
                    summary = " ".join(
                        sentence if sentence.endswith((".", "!", "?")) else f"{sentence}."
                        for sentence in invocation_updates[:3]
                    )
                if tool_outputs_generated:
                    if summary:
                        storage.messages.create(role="assistant", content=summary, session_id=session_id)
                        storage.commit()
                    iteration += 1
                    continue
                reply = chain_of_thought.strip() or summary or (" ".join(invocation_updates[:3]).strip() if invocation_updates else "I’ve kicked off the relevant system work.")
                if reply:
                    storage.messages.create(role="assistant", content=reply, session_id=session_id)
                    storage.commit()
                return {"status": "ok", "reply": reply, "task_ids": async_task_ids}

            final_reply = model_response.get("content", "I encountered an error processing that.")
            storage.messages.create(role="assistant", content=final_reply, session_id=session_id)
            storage.commit()
            return {"status": "ok", "reply": final_reply}

        messages.append(
            {
                "role": "system",
                "content": "You have reached the tool call limit. You MUST synthesize the data gathered so far and reply to the user immediately. Do not attempt further tool calls.",
            }
        )
        final_response = await active_adapter.chat(messages)
        reply = final_response.get("content", "I hit the maximum iteration limit for synchronous tool usage without reaching a conclusion.")
        if not reply or not reply.strip():
            reply = "I couldn't synthesize the final results."
        storage.messages.create(role="assistant", content=reply, session_id=session_id)
        storage.commit()
        return {"status": "ok", "reply": reply}
