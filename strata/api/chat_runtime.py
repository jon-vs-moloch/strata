"""
@module api.chat_runtime
@purpose Shared runtime helpers for the chat/task route surface.

The chat routes need to stay readable. This module carries the synchronous tool
loop, clarification handling, and task serialization so route registration can
stay small and declarative.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from strata.communication.primitives import build_communication_decision, deliver_communication_decision
from strata.observability.context import record_context_load
from strata.api.chat_tool_executor import ChatToolExecutor
from strata.context.loaded_files import build_loaded_context_block
from strata.core.lanes import infer_lane_from_session_id
from strata.models.adapter import ModelAdapter
from strata.schemas.execution import TrainerExecutionContext, AgentExecutionContext


class ChatRuntime:
    def __init__(self, **deps: Any):
        self.deps = deps
        self.tool_executor = ChatToolExecutor(**deps)

    def _slim_system_job_result(self, result: Any) -> Any:
        if not isinstance(result, dict):
            return result
        payload = dict(result)
        nested_result = payload.get("result")
        if isinstance(nested_result, dict):
            skipped = list(nested_result.get("skipped") or [])
            evaluated = list(nested_result.get("evaluated") or [])
            promoted = list(nested_result.get("promoted") or [])
            payload["result"] = {
                "current_eval_harness_config": nested_result.get("current_eval_harness_config"),
                "evaluated": evaluated[:3],
                "promoted": promoted[:3],
                "skipped": skipped[:3],
                "summary": {
                    "evaluated_count": len(evaluated),
                    "promoted_count": len(promoted),
                    "skipped_count": len(skipped),
                },
            }
        return payload

    def _slim_attempt_artifacts(self, artifacts: Any) -> Dict[str, Any]:
        payload = dict(artifacts or {})
        result_summary = payload.get("result_summary")
        if isinstance(result_summary, dict):
            skipped = list(result_summary.get("skipped") or [])
            evaluated = list(result_summary.get("evaluated") or [])
            promoted = list(result_summary.get("promoted") or [])
            payload["result_summary"] = {
                "summary": {
                    "evaluated_count": len(evaluated),
                    "promoted_count": len(promoted),
                    "skipped_count": len(skipped),
                },
                "evaluated": evaluated[:2],
                "promoted": promoted[:2],
                "skipped": skipped[:2],
            }
        return payload

    def list_tasks_payload(self, storage, *, lane: Optional[str] = None) -> List[Dict[str, Any]]:
        from sqlalchemy.orm import selectinload

        task_model_cls = self.deps["task_model_cls"]
        get_question_for_source = self.deps["get_question_for_source"]
        tasks = storage.session.query(task_model_cls).options(selectinload(task_model_cls.attempts)).all()
        normalized_lane = str(lane or "").strip().lower() or None
        payload = []
        for task in tasks:
            task_lane = (task.constraints or {}).get("lane") or infer_lane_from_session_id(getattr(task, "session_id", None))
            task_pending_question = (
                get_question_for_source(storage, source_type="task_blocked", source_id=task.task_id)
                if task.human_intervention_required
                else {}
            )
            if normalized_lane and str(task_lane or "").strip().lower() != normalized_lane:
                continue
            sorted_attempts = sorted(
                list(task.attempts or []),
                key=lambda attempt: (
                    str(attempt.started_at.isoformat() if attempt.started_at else ""),
                    str(attempt.ended_at.isoformat() if attempt.ended_at else ""),
                ),
                reverse=True,
            )
            payload.append(
                {
                    "id": task.task_id,
                    "parent_id": task.parent_task_id,
                    "session_id": task.session_id,
                    "title": task.title,
                    "description": task.description,
                    "status": task.state.value.lower(),
                    "type": task.type.value.lower(),
                    "depth": task.depth,
                    "human_intervention_required": task.human_intervention_required,
                    "system_job": (task.constraints or {}).get("system_job"),
                    "system_job_result": self._slim_system_job_result((task.constraints or {}).get("system_job_result")),
                    "generated_reports": (task.constraints or {}).get("generated_reports", []),
                    "paused": bool((task.constraints or {}).get("paused")),
                    "lane": task_lane,
                    "pending_question": (
                        {
                            "question_id": task_pending_question.get("question_id"),
                            "session_id": task_pending_question.get("session_id"),
                            "status": task_pending_question.get("status"),
                            "source_type": task_pending_question.get("source_type"),
                            "question": task_pending_question.get("question"),
                            "brief_question": task_pending_question.get("brief_question"),
                        }
                        if task_pending_question
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
                            "artifacts": self._slim_attempt_artifacts(attempt.artifacts),
                            "evidence": dict(attempt.evidence or {}),
                            "plan_review": dict(attempt.plan_review or {}),
                        }
                        for attempt in sorted_attempts
                    ],
                }
            )
        return payload

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
        current_spec = (
            current_specs.get("constitution") or current_specs.get("global_spec", "")
            if scope == "global"
            else current_specs.get("project_spec", "")
        )
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
        await self.emit_chat_communication(
            storage,
            session_id=session_id,
            content=(
                f"I’ve attached your clarification to spec proposal {updated_proposal['proposal_id']} "
                f"and kicked off a fresh review task ({task.task_id})."
            ),
            communicative_act="response",
            response_kind="acknowledgement",
            source_kind="spec_clarification_reply",
        )
        return {
            "status": "ok",
            "reply": (
                f"I’ve attached your clarification to spec proposal {updated_proposal['proposal_id']} "
                f"and kicked off a fresh review."
            ),
            "spec_proposal_id": updated_proposal["proposal_id"],
            "task_id": task.task_id,
        }

    async def handle_task_clarification_reply(
        self,
        storage,
        payload: Dict[str, Any],
        session_id: str,
        content: str,
        *,
        question_id: str,
    ):
        pending_question = self.deps["get_active_question"](storage, session_id)
        if not (
            pending_question
            and payload.get("role") == "user"
            and pending_question.get("status") == "asked"
            and str(pending_question.get("question_id") or "") == str(question_id or "")
        ):
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
        await self.emit_chat_communication(
            storage,
            session_id=session_id,
            content=f"I’ve attached your clarification to task {task.task_id} and re-queued it.",
            communicative_act="response",
            response_kind="acknowledgement",
            source_kind="task_clarification_reply",
        )
        return {
            "status": "ok",
            "reply": f"I’ve attached your clarification to task {task.task_id} and re-queued it.",
            "task_id": task.task_id,
        }

    async def handle_explicit_question_answer(self, storage, payload: Dict[str, Any], session_id: str, content: str):
        question_id = str(payload.get("answer_question_id") or payload.get("question_id") or "").strip()
        if not question_id:
            return None

        pending_spec_proposal = self.deps["find_pending_spec_clarification"](storage, session_id)
        if (
            pending_spec_proposal
            and pending_spec_proposal.get("_pending_question", {}).get("status") == "asked"
            and str(pending_spec_proposal["_pending_question"].get("question_id") or "") == question_id
        ):
            return await self.handle_spec_clarification_reply(storage, payload, session_id, content)

        return await self.handle_task_clarification_reply(
            storage,
            payload,
            session_id,
            content,
            question_id=question_id,
        )

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
                "content": f"""You are Strata, a unified AI engineering system. You coordinate a formation of background workers, but present yourself as a single, first-person entity.
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
        elif pending_question and pending_question.get("status") == "asked":
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "There is still an unresolved user question attached to this session.\n"
                        f"- question_id: {pending_question.get('question_id')}\n"
                        f"- source_type: {pending_question.get('source_type')}\n"
                        f"- open_question: {pending_question.get('question')}\n\n"
                        "Treat the user's next messages as normal conversation unless they actually answer this question. "
                        "If the user has answered it, call `resolve_user_question` with your interpreted answer. "
                        "If they have not, continue the conversation naturally and ask a narrower follow-up when helpful. "
                        "Do not assume every user message is automatically an answer."
                    ),
                }
            )

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

    async def emit_chat_communication(
        self,
        storage,
        *,
        session_id: str,
        content: str,
        communicative_act: str = "response",
        response_kind: str = "answer",
        source_kind: str = "chat_reply",
        urgency: str = "normal",
    ) -> Dict[str, Any]:
        lane = infer_lane_from_session_id(session_id) or "trainer"
        decision = build_communication_decision(
            role="assistant",
            content=content,
            lane=lane,
            channel="existing_session_message",
            session_id=session_id,
            audience="user",
            source_kind=source_kind,
            source_actor="chat_runtime",
            opened_reason="chat_reply",
            tags=["chat", response_kind or communicative_act],
            topic_summary=str(content or "").strip()[:180],
            communicative_act=communicative_act,
            response_kind=response_kind,
            urgency=urgency,
        )
        result = deliver_communication_decision(storage, decision)
        storage.commit()
        await self.deps["broadcast_event"]({"type": "message", "session_id": session_id})
        return result

    async def run_chat_tool_loop(self, storage, *, session_id: str, content: str, preferred_tier: str = "trainer"):
        preferred_tier = str(preferred_tier or "trainer").lower()
        chat_context = (
            AgentExecutionContext(run_id=f"chat:{session_id}")
            if preferred_tier == "agent"
            else TrainerExecutionContext(run_id=f"chat:{session_id}")
        )
        chat_adapter = ModelAdapter(context=chat_context)
        chat_adapter._selected_models = dict(getattr(self.deps["model_adapter"], "_selected_models", {}))
        agent_fallback_adapter = ModelAdapter(context=AgentExecutionContext(run_id=f"chat-agent:{session_id}"))
        agent_fallback_adapter._selected_models = dict(chat_adapter._selected_models)
        active_adapter = chat_adapter
        downgraded_to_agent = preferred_tier == "agent"
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
                                "The selected trainer model rejected tool calling for this request. "
                                "Answer directly without tools."
                            ),
                        }
                    )
                    continue
                if (
                    not downgraded_to_agent
                    and active_adapter is chat_adapter
                    and (
                        "Developer instruction is not enabled" in error_message
                        or "Function calling is not enabled" in error_message
                    )
                ):
                    downgraded_to_agent = True
                    active_adapter = agent_fallback_adapter
                    active_tools = []
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "The configured trainer chat route rejected the current instruction or tool format. "
                                "Continue this reply on the agent tier without tools, and keep the answer concise."
                            ),
                        }
                    )
                    continue
                final_reply = error_message or "I encountered an error processing that."
                await self.emit_chat_communication(
                    storage,
                    session_id=session_id,
                    content=final_reply,
                    communicative_act="response",
                    response_kind="error",
                    source_kind="chat_error",
                    urgency="high",
                )
                return {"status": "ok", "reply": final_reply}
            tool_calls = model_response.get("tool_calls")
            content_val = model_response.get("content")
            chain_of_thought = str(content_val) if content_val else ""

            if chain_of_thought and chain_of_thought.strip() and not tool_calls:
                await self.emit_chat_communication(
                    storage,
                    session_id=session_id,
                    content=chain_of_thought.strip(),
                    communicative_act="response",
                    response_kind="answer",
                    source_kind="chat_reply",
                )
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
                        await self.emit_chat_communication(
                            storage,
                            session_id=session_id,
                            content=summary,
                            communicative_act="notification",
                            response_kind="progress",
                            source_kind="tool_progress",
                        )
                    iteration += 1
                    continue
                reply = chain_of_thought.strip() or summary or (" ".join(invocation_updates[:3]).strip() if invocation_updates else "I’ve kicked off the relevant system work.")
                if reply:
                    await self.emit_chat_communication(
                        storage,
                        session_id=session_id,
                        content=reply,
                        communicative_act="response",
                        response_kind="answer",
                        source_kind="chat_reply",
                    )
                return {"status": "ok", "reply": reply, "task_ids": async_task_ids}

            final_reply = model_response.get("content", "I encountered an error processing that.")
            await self.emit_chat_communication(
                storage,
                session_id=session_id,
                content=final_reply,
                communicative_act="response",
                response_kind="answer",
                source_kind="chat_reply",
            )
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
        await self.emit_chat_communication(
            storage,
            session_id=session_id,
            content=reply,
            communicative_act="response",
            response_kind="answer",
            source_kind="chat_reply",
        )
        return {"status": "ok", "reply": reply}
