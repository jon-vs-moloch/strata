"""
@module api.chat_runtime
@purpose Shared runtime helpers for the chat/task route surface.

The chat routes need to stay readable. This module carries the synchronous tool
loop, clarification handling, and task serialization so route registration can
stay small and declarative.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


class ChatRuntime:
    def __init__(self, **deps: Any):
        self.deps = deps

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
            memory_context = "\n\nRELEVANT PAST CONTEXT:\n" + "\n".join(past_memories["documents"][0])

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
{memory_context}

Available Tools:
{tool_summary_text}
""",
            }
        ]
        if pending_question and pending_question.get("status") == "pending":
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Internal pending user question:\n"
                        f"- source_type: {pending_question.get('source_type')}\n"
                        f"- question: {pending_question.get('question')}\n\n"
                        "Before doing anything else, ask the user this question naturally and briefly. "
                        "Do not mention internal queues or implementation details."
                    ),
                }
            )
            self.deps["mark_question_asked"](storage, pending_question["question_id"])
            storage.commit()

        history_records = storage.messages.get_all(session_id=session_id)
        for message in history_records[-5:]:
            messages.append({"role": message.role, "content": message.content})
        return messages, active_tools, knowledge_pages

    async def execute_tool_call(self, storage, *, call: Dict[str, Any], session_id: str, content: str, knowledge_pages):
        task_state_cls = self.deps["task_state_cls"]
        task_type_cls = self.deps["task_type_cls"]
        slugify_page_title = self.deps["slugify_page_title"]
        load_specs = self.deps["load_specs"]
        create_spec_proposal = self.deps["create_spec_proposal"]

        func_name = call.get("function", {}).get("name")
        tool_call_id = call.get("id", "call_xyz")
        try:
            args = json.loads(call.get("function", {}).get("arguments", "{}"))
        except Exception:
            args = {}

        tool_outputs_generated = False
        async_task_id = None

        if func_name == "kickoff_background_research":
            desc = args.get("description", content)
            scope = args.get("target_scope", "codebase")
            task = storage.tasks.create(
                title=f"Research [{scope.upper()}]: {desc[:50]}",
                description=desc,
                session_id=session_id,
                state=task_state_cls.PENDING,
                constraints={"target_scope": scope},
            )
            task.type = task_type_cls.RESEARCH
            storage.commit()
            await self.deps["worker"].enqueue(task.task_id)
            async_task_id = task.task_id
            tool_content = f"Successfully enqueued background research task {task.task_id}."
        elif func_name == "kickoff_swarm_task":
            title = args.get("title", f"Auto-Task: {content[:30]}...")
            desc = args.get("description", content)
            task = storage.tasks.create(title=title, description=desc, session_id=session_id, state=task_state_cls.PENDING)
            storage.commit()
            await self.deps["worker"].enqueue(task.task_id)
            async_task_id = task.task_id
            tool_content = f"Successfully enqueued swarm implementation task {task.task_id}."
        elif func_name == "search_web":
            query = args.get("query")
            task = storage.tasks.create(
                title=f"Web Search: {query}",
                description=f"Perform a targeted web search for: {query}. Synthesize the results and provide a concise answer.",
                session_id=session_id,
                state=task_state_cls.PENDING,
                constraints={"target_scope": "web"},
            )
            task.type = task_type_cls.RESEARCH
            storage.commit()
            await self.deps["worker"].enqueue(task.task_id)
            tool_content = f"I am searching the web for '{query}'. I will synthesize the findings and post them here shortly."
            tool_outputs_generated = True
        elif func_name == "check_swarm_status":
            target_id = args.get("task_id")
            if target_id:
                tasks = storage.session.query(self.deps["task_model_cls"]).filter(self.deps["task_model_cls"].task_id == target_id).all()
            else:
                tasks = storage.session.query(self.deps["task_model_cls"]).filter(self.deps["task_model_cls"].state != task_state_cls.COMPLETE).all()
            tool_content = "No active or matching tasks found in the database." if not tasks else "Current Swarm Status:\n" + "\n".join(
                f"- {task.title} ({task.task_id}): {task.state.value}" for task in tasks
            )
            tool_outputs_generated = True
        elif func_name == "list_knowledge_pages":
            pages = knowledge_pages.list_pages(
                query=args.get("query"),
                tag=args.get("tag"),
                domain=args.get("domain"),
                audience="agent",
                limit=int(args.get("limit") or 8),
            )
            tool_content = "No synthesized knowledge pages matched that query."
            if pages:
                tool_content = "Knowledge Page Metadata:\n" + "\n".join(
                    f"- {page.get('slug')}: {page.get('title')} | summary={page.get('summary')} | "
                    f"domain={page.get('domain')} | visibility={page.get('visibility_policy')} | "
                    f"last_updated={page.get('last_updated')} | tags={page.get('tags') or []}"
                    for page in pages
                )
            tool_outputs_generated = True
        elif func_name == "get_knowledge_page_metadata":
            slug = str(args.get("slug") or "")
            metadata_view = knowledge_pages.get_page_metadata_view(slug, audience="agent")
            if metadata_view.get("status") == "missing":
                tool_content = f"No synthesized knowledge page found for '{slug}'."
            elif metadata_view.get("status") == "restricted":
                meta = metadata_view.get("page_metadata") or {}
                tool_content = (
                    f"Knowledge page '{slug}' exists but is permission-restricted for the current audience. "
                    f"Domain={meta.get('domain')} visibility={meta.get('visibility_policy')}. "
                    "Use a summarized, consent-aware response or request operator intervention if direct disclosure is needed."
                )
            else:
                tool_content = json.dumps(metadata_view.get("page") or {}, indent=2)
            tool_outputs_generated = True
        elif func_name == "read_knowledge_page":
            slug = str(args.get("slug") or "")
            heading = args.get("heading")
            if heading:
                section_view = knowledge_pages.get_page_section_view(slug, str(heading), audience="agent")
                if section_view.get("status") == "missing":
                    tool_content = f"No synthesized knowledge page found for '{slug}'."
                elif section_view.get("status") == "restricted":
                    meta = section_view.get("page_metadata") or {}
                    tool_content = (
                        f"Knowledge page '{slug}' exists but section access is permission-restricted. "
                        f"Domain={meta.get('domain')} visibility={meta.get('visibility_policy')}. "
                        "If the user needs this, provide only a safe high-level explanation or ask for consent/operator review."
                    )
                else:
                    section = section_view.get("section") or {}
                    prefix = "Summary-only view due to disclosure rules:\n" if section.get("content_redacted") else ""
                    tool_content = prefix + (section.get("content") or f"No section '{heading}' found in knowledge page '{slug}'.")
            else:
                page_view = knowledge_pages.get_page_view(slug, audience="agent")
                if page_view.get("status") == "missing":
                    tool_content = f"No synthesized knowledge page found for '{slug}'."
                elif page_view.get("status") == "restricted":
                    meta = page_view.get("page_metadata") or {}
                    tool_content = (
                        f"Knowledge page '{slug}' exists but is permission-restricted. "
                        f"Domain={meta.get('domain')} visibility={meta.get('visibility_policy')}. "
                        "Do not quote it directly; summarize cautiously or ask for consent/operator intervention if needed."
                    )
                else:
                    page = page_view.get("page") or {}
                    prefix = "Summary-only view due to disclosure rules:\n" if page.get("content_redacted") else ""
                    tool_content = prefix + (page.get("body") or "")
            tool_outputs_generated = True
        elif func_name == "update_knowledge":
            slug = str(args.get("slug") or "")
            reason = str(args.get("reason") or "knowledge gap detected")
            task = knowledge_pages.enqueue_update_task(
                slug=slug,
                reason=reason,
                session_id=session_id,
                target_scope=str(args.get("target_scope") or "codebase"),
                evidence=[str(item) for item in (args.get("evidence_hints") or [])],
                domain=args.get("domain"),
            )
            storage.commit()
            await self.deps["worker"].enqueue(task.task_id)
            async_task_id = task.task_id
            tool_content = f"Queued knowledge update task {task.task_id} for page '{slugify_page_title(slug)}'."
        elif func_name == "read_spec":
            scope = str(args.get("scope") or "project")
            specs = load_specs()
            tool_content = specs.get("global_spec", "") if scope == "global" else specs.get("project_spec", "")
            tool_outputs_generated = True
        elif func_name == "propose_spec_update":
            scope = str(args.get("scope") or "project")
            proposed_change = str(args.get("proposed_change") or "").strip()
            rationale = str(args.get("rationale") or "").strip()
            user_signal = str(args.get("user_signal") or content).strip()
            current_specs = load_specs()
            current_spec = current_specs.get("global_spec" if scope == "global" else "project_spec", "")
            title = f"Spec Review ({scope.title()}): {proposed_change[:48] or rationale[:48] or 'pending proposal'}"
            review_prompt = (
                f"Review a proposed {scope} spec update.\n\n"
                f"Current {scope} spec:\n{current_spec}\n\n"
                f"Proposed change:\n{proposed_change}\n\n"
                f"Rationale:\n{rationale}\n\n"
                f"Triggering user signal:\n{user_signal}\n\n"
                "Tasks:\n1. Compare the proposal against the current spec.\n2. Identify contradictions, ambiguity, or missing details.\n"
                "3. Draft clarification questions if needed.\n4. Suggest a clean spec delta without directly editing the spec file.\n"
                "5. Treat the spec as durable gospel unless the user explicitly wants to change it.\n"
            )
            task = storage.tasks.create(
                title=title,
                description=review_prompt,
                session_id=session_id,
                state=task_state_cls.PENDING,
                constraints={
                    "target_scope": "codebase",
                    "spec_operation": "review_proposal",
                    "spec_scope": scope,
                    "proposed_change": proposed_change,
                    "rationale": rationale,
                    "user_signal": user_signal,
                },
            )
            task.type = task_type_cls.RESEARCH
            proposal = create_spec_proposal(
                storage,
                scope=scope,
                proposed_change=proposed_change,
                rationale=rationale,
                user_signal=user_signal,
                session_id=session_id,
                source="chat_agent",
                review_task_id=task.task_id,
            )
            task.constraints["spec_proposal_id"] = proposal["proposal_id"]
            storage.commit()
            await self.deps["worker"].enqueue(task.task_id)
            async_task_id = task.task_id
            tool_content = (
                f"Queued reviewed spec proposal task {task.task_id} for the {scope} spec "
                f"(proposal_id={proposal['proposal_id']}). "
                "I will treat this as durable intent under review rather than editing the spec directly."
            )
        else:
            tool_content = f"Error: Tool '{func_name}' not implemented."

        return {
            "tool_message": {"role": "tool", "tool_call_id": tool_call_id, "name": func_name, "content": tool_content},
            "tool_outputs_generated": tool_outputs_generated,
            "async_task_id": async_task_id,
        }

    async def run_chat_tool_loop(self, storage, *, session_id: str, content: str):
        pending_question = self.deps["get_active_question"](storage, session_id)
        messages, active_tools, knowledge_pages = self.build_chat_messages(
            storage, session_id=session_id, content=content, pending_question=pending_question
        )
        max_iters = self.deps["global_settings"].get("max_sync_tool_iterations", 3)
        iteration = 0

        while iteration < max_iters:
            model_response = await self.deps["model_adapter"].chat(messages, tools=active_tools)
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
                if chain_of_thought and chain_of_thought.strip():
                    storage.messages.create(role="assistant", content=chain_of_thought.strip(), session_id=session_id)
                    storage.commit()
                else:
                    names = [call.get("function", {}).get("name") for call in tool_calls]
                    chain_of_thought = f"Invoking system tools: {', '.join(names)}"
                    storage.messages.create(role="assistant", content=chain_of_thought, session_id=session_id)
                    storage.commit()

                messages.append({"role": "assistant", "content": chain_of_thought or None, "tool_calls": tool_calls})
                for call in tool_calls:
                    result = await self.execute_tool_call(
                        storage, call=call, session_id=session_id, content=content, knowledge_pages=knowledge_pages
                    )
                    messages.append(result["tool_message"])
                    tool_outputs_generated = tool_outputs_generated or result["tool_outputs_generated"]
                    if result["async_task_id"]:
                        async_task_ids.append(result["async_task_id"])
                if tool_outputs_generated:
                    iteration += 1
                    continue
                return {"status": "ok", "reply": chain_of_thought.strip(), "task_ids": async_task_ids}

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
        final_response = await self.deps["model_adapter"].chat(messages)
        reply = final_response.get("content", "I hit the maximum iteration limit for synchronous tool usage without reaching a conclusion.")
        if not reply or not reply.strip():
            reply = "I couldn't synthesize the final results."
        storage.messages.create(role="assistant", content=reply, session_id=session_id)
        storage.commit()
        return {"status": "ok", "reply": reply}
