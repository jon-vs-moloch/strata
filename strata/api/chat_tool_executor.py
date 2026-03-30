"""
@module api.chat_tool_executor
@purpose Execute chat-exposed built-in tool calls without bloating chat_runtime.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List
from strata.core.lanes import infer_lane_from_session_id
from strata.context.loaded_files import list_loaded_context_files, load_context_file, unload_context_file
from strata.feedback.signals import register_feedback_signal
from strata.orchestrator.tool_health import record_tool_execution, should_throttle_tool
from strata.orchestrator.trainer_controls import (
    build_branch_state_summary,
    invalidate_task_premise,
    record_self_audit_request,
    rewrite_task_plan,
    set_task_verification_posture,
)


class ChatToolExecutor:
    def __init__(self, **deps: Any):
        self.deps = deps

    async def execute_tool_call(self, storage, *, call: Dict[str, Any], session_id: str, content: str, knowledge_pages):
        task_state_cls = self.deps["task_state_cls"]
        task_type_cls = self.deps["task_type_cls"]
        slugify_page_title = self.deps["slugify_page_title"]
        load_specs = self.deps["load_specs"]
        create_spec_proposal = self.deps["create_spec_proposal"]
        queue_eval_system_job = self.deps.get("queue_eval_system_job")
        get_active_question = self.deps.get("get_active_question")
        resolve_question = self.deps.get("resolve_question")
        get_question_for_source = self.deps.get("get_question_for_source")

        func_name = call.get("function", {}).get("name")
        tool_call_id = call.get("id", "call_xyz")
        try:
            args = json.loads(call.get("function", {}).get("arguments", "{}"))
        except Exception:
            args = {}
        reason = str(args.get("reason") or "").strip()
        lane = infer_lane_from_session_id(session_id)
        referenced_task_id = str(args.get("task_id") or args.get("source_id") or "").strip()
        referenced_task = storage.tasks.get_by_id(referenced_task_id) if referenced_task_id else None
        task_type = getattr(getattr(referenced_task, "type", None), "value", None) if referenced_task else None

        tool_outputs_generated = False
        async_task_id = None

        throttle = should_throttle_tool(
            storage,
            tool_name=func_name,
            lane=lane,
            task_type=task_type,
        )
        if throttle.get("throttle"):
            tool_content = (
                f"Tool '{func_name}' is currently {throttle.get('status')} for this scope and has been circuit-broken. "
                f"Reason: {throttle.get('reason')}. Route to a different tool or queue tooling repair before retrying."
            )
            record_tool_execution(
                storage,
                tool_name=func_name,
                outcome="blocked",
                lane=lane,
                task_type=task_type,
                task_id=getattr(referenced_task, "task_id", None),
                session_id=session_id,
                source="chat_tool_executor",
                failure_kind="circuit_breaker",
                details={"health": throttle.get("health") or {}},
            )
            storage.commit()
            return {
                "tool_message": {"role": "tool", "tool_call_id": tool_call_id, "name": func_name, "content": tool_content},
                "tool_outputs_generated": False,
                "async_task_id": None,
                "tool_reason": reason,
                "tool_name": func_name,
            }

        if func_name == "inspect_branch_state":
            task_id = str(args.get("task_id") or "").strip()
            tool_content = json.dumps(
                build_branch_state_summary(
                    storage,
                    task_id=task_id,
                    question_lookup=get_question_for_source,
                ),
                indent=2,
            )
            tool_outputs_generated = True
        elif func_name == "request_self_audit":
            task_id = str(args.get("task_id") or "").strip()
            focus = str(args.get("focus") or "").strip()
            task = storage.tasks.get_by_id(task_id) if task_id else None
            if not task:
                tool_content = f"Task '{task_id}' was not found."
            else:
                record = record_self_audit_request(task, focus=focus, actor="trainer")
                queued = None
                if queue_eval_system_job:
                    queued = await queue_eval_system_job(
                        storage,
                        kind="trace_review",
                        title=f"Agent Self-Audit: {str(task.title or task.task_id)[:72]}",
                        description="Queued agent-tier self-audit for a task branch.",
                        payload={
                            "trace_kind": "task_trace",
                            "task_id": task.task_id,
                            "reviewer_tier": "agent",
                            "emit_followups": False,
                            "persist_to_task": True,
                            "spec_scope": "project",
                            "self_audit_focus": focus,
                        },
                        session_id=task.session_id or session_id,
                    )
                storage.commit()
                tool_content = json.dumps(
                    {
                        "status": "ok",
                        "task_id": task.task_id,
                        "self_audit_request": record["self_audit_request"],
                        "queued_review": queued,
                    },
                    indent=2,
                )
                tool_outputs_generated = True
        elif func_name == "rewrite_plan":
            task_id = str(args.get("task_id") or "").strip()
            task = storage.tasks.get_by_id(task_id) if task_id else None
            if not task:
                tool_content = f"Task '{task_id}' was not found."
            else:
                update = rewrite_task_plan(
                    storage,
                    task=task,
                    plan=str(args.get("plan") or ""),
                    rationale=str(args.get("rationale") or ""),
                    actor="trainer",
                )
                storage.commit()
                await self.deps["worker"].enqueue(task.task_id)
                async_task_id = task.task_id
                tool_content = json.dumps({"status": "ok", "task_id": task.task_id, **update}, indent=2)
                tool_outputs_generated = True
        elif func_name == "invalidate_premise":
            task_id = str(args.get("task_id") or "").strip()
            task = storage.tasks.get_by_id(task_id) if task_id else None
            if not task:
                tool_content = f"Task '{task_id}' was not found."
            else:
                update = invalidate_task_premise(
                    storage,
                    task=task,
                    premise=str(args.get("premise") or ""),
                    correction=str(args.get("correction") or ""),
                    actor="trainer",
                )
                storage.commit()
                await self.deps["worker"].enqueue(task.task_id)
                async_task_id = task.task_id
                tool_content = json.dumps({"status": "ok", "task_id": task.task_id, **update}, indent=2)
                tool_outputs_generated = True
        elif func_name == "set_verification_posture":
            task_id = str(args.get("task_id") or "").strip()
            task = storage.tasks.get_by_id(task_id) if task_id else None
            if not task:
                tool_content = f"Task '{task_id}' was not found."
            else:
                update = set_task_verification_posture(
                    storage,
                    task=task,
                    posture=str(args.get("posture") or ""),
                    rationale=str(args.get("rationale") or ""),
                    actor="trainer",
                )
                storage.commit()
                tool_content = json.dumps({"status": "ok", "task_id": task.task_id, **update}, indent=2)
                tool_outputs_generated = True
        elif func_name == "resolve_user_question":
            pending_question = get_active_question(storage, session_id) if get_active_question else {}
            question_id = str(args.get("question_id") or "").strip()
            answer = str(args.get("answer") or "").strip()
            resolution = str(args.get("resolution") or "resolved").strip().lower() or "resolved"
            if not pending_question or not question_id or str(pending_question.get("question_id") or "") != question_id:
                tool_content = "No matching open question is attached to this session."
            elif not answer:
                tool_content = "A non-empty interpreted answer is required before resolving the question."
            else:
                source_type = str(pending_question.get("source_type") or "").strip().lower()
                if source_type == "task_blocked":
                    task_id = str(pending_question.get("source_id") or "").strip()
                    task = storage.tasks.get_by_id(task_id) if task_id else None
                    if task:
                        task.description = (task.description or "") + f"\n\nUser clarification:\n{answer}"
                        if str(pending_question.get("escalation_mode") or "blocking").strip().lower() != "non_blocking":
                            task.human_intervention_required = False
                            task.state = task_state_cls.PENDING
                            storage.commit()
                            await self.deps["worker"].enqueue(task.task_id)
                        else:
                            storage.commit()
                if resolve_question:
                    resolve_question(storage, question_id, resolution=resolution, response=answer)
                storage.commit()
                tool_content = f"Resolved question {question_id} with the interpreted answer and updated the blocked work."
                tool_outputs_generated = True
        elif func_name == "kickoff_background_research":
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
            tool_content = f"Successfully enqueued formation implementation task {task.task_id}."
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
            tool_content = "No active or matching tasks found in the database." if not tasks else "Current Formation Status:\n" + "\n".join(
                f"- {task.title} ({task.task_id}): {task.state.value}" for task in tasks
            )
            tool_outputs_generated = True
        elif func_name == "submit_feedback_signal":
            signal = register_feedback_signal(
                storage,
                source_type=str(args.get("source_type") or "system"),
                source_id=str(args.get("source_id") or ""),
                signal_kind=str(args.get("signal_kind") or "highlight"),
                signal_value=str(args.get("signal_value") or ""),
                source_actor="agent",
                session_id=session_id,
                source_preview=str(args.get("source_preview") or content),
                note=str(args.get("note") or reason),
                expected_outcome=str(args.get("expected_outcome") or ""),
                observed_outcome=str(args.get("observed_outcome") or ""),
                metadata={"tool_name": "submit_feedback_signal", "tool_reason": reason},
            )
            prioritized = signal.get("prioritization") or {}
            distillation_job = None
            if queue_eval_system_job and str(prioritized.get("priority") or "") in {"review_soon", "urgent"}:
                source_type = str(signal.get("source_type") or "")
                target_session_id = session_id if source_type in {"message", "session", "system"} else ""
                if target_session_id:
                    distillation_job = await queue_eval_system_job(
                        storage,
                        kind="trace_review",
                        title=f"Signal Distillation: {target_session_id}",
                        description=f"Queued session trace review after internal feedback signal for '{target_session_id}'.",
                        payload={
                            "trace_kind": "session_trace",
                            "session_id": target_session_id,
                            "reviewer_tier": "trainer",
                            "emit_followups": True,
                            "persist_to_task": False,
                            "spec_scope": "project",
                            "prioritization": prioritized,
                        },
                        session_id=target_session_id,
                        dedupe_signature={
                            "trace_kind": "session_trace",
                            "reviewer_tier": "trainer",
                            "session_id": target_session_id,
                        },
                    )
            tool_content = json.dumps(
                {
                    "signal": signal,
                    "distillation_job": distillation_job,
                },
                indent=2,
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
        elif func_name == "inspect_knowledge_maintenance":
            report = knowledge_pages.get_maintenance_report()
            if not report:
                tool_content = "No knowledge maintenance report is available yet. Run compaction or refresh maintenance first."
            else:
                tool_content = json.dumps(report, indent=2)
            tool_outputs_generated = True
        elif func_name == "flag_knowledge_issue":
            slug = str(args.get("slug") or "")
            issue_type = str(args.get("issue_type") or "correction")
            reason = str(args.get("reason") or "knowledge issue detected")
            related_slugs = [str(item) for item in (args.get("related_slugs") or [])]
            evidence = [str(item) for item in (args.get("evidence_hints") or [])]
            task = knowledge_pages.enqueue_update_task(
                slug=slug,
                reason=f"[{issue_type}] {reason}",
                session_id=session_id,
                target_scope=str(args.get("target_scope") or "codebase"),
                evidence=evidence,
                related_slugs=related_slugs,
                operation=f"knowledge_{issue_type}",
            )
            storage.commit()
            await self.deps["worker"].enqueue(task.task_id)
            async_task_id = task.task_id
            tool_content = (
                f"Queued knowledge maintenance task {task.task_id} for page '{slugify_page_title(slug)}' "
                f"with issue type '{issue_type}'."
            )
        elif func_name == "read_spec":
            scope = str(args.get("scope") or "project")
            specs = load_specs(storage=storage)
            path = ".knowledge/specs/constitution.md" if scope == "global" else ".knowledge/specs/project_spec.md"
            body = (specs.get("constitution") or specs.get("global_spec", "")) if scope == "global" else specs.get("project_spec", "")
            tool_content = f"Source: {path}\n\n{body}"
            tool_outputs_generated = True
        elif func_name == "list_loaded_context_files":
            registry = list_loaded_context_files(storage)
            files = registry.get("files") or []
            if not files:
                tool_content = "No workspace files are currently pinned into round-level context."
            else:
                tool_content = "Loaded workspace context files:\n" + "\n".join(
                    f"- {entry.get('path')} ({entry.get('estimated_tokens')} est. tokens)"
                    for entry in files
                )
            tool_outputs_generated = True
        elif func_name == "load_context_file":
            result = load_context_file(
                storage,
                str(args.get("path") or ""),
                source="chat_tool_executor.load_context_file",
            )
            if result.get("status") == "over_budget":
                tool_content = (
                    f"Cannot load {result.get('path')} yet. It would exceed the persistent context budget "
                    f"({result.get('current_tokens')} + {result.get('estimated_tokens')} > {result.get('budget_tokens')}). "
                    "Unload one or more files first."
                )
            elif result.get("status") == "missing":
                tool_content = f"Context file not found: {result.get('path')}"
            else:
                tool_content = (
                    f"Loaded {result.get('path')} into persistent round-level context "
                    f"({result.get('estimated_tokens')} est. tokens)."
                )
            tool_outputs_generated = True
        elif func_name == "unload_context_file":
            result = unload_context_file(storage, str(args.get("path") or ""))
            tool_content = (
                f"Unloaded {result.get('path')} from persistent round-level context."
                if result.get("removed")
                else f"{result.get('path')} was not currently loaded."
            )
            tool_outputs_generated = True
        elif func_name == "propose_spec_update":
            scope = str(args.get("scope") or "project")
            proposed_change = str(args.get("proposed_change") or "").strip()
            rationale = str(args.get("rationale") or "").strip()
            user_signal = str(args.get("user_signal") or content).strip()
            claimed_mutation_class = str(
                args.get("claimed_mutation_class") or "clarification_with_no_behavior_change"
            ).strip()
            proposal_kind = str(args.get("proposal_kind") or "clarification").strip()
            current_specs = load_specs(storage=storage)
            current_spec = (
                current_specs.get("constitution") or current_specs.get("global_spec", "")
                if scope == "global"
                else current_specs.get("project_spec", "")
            )
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
                    "claimed_mutation_class": claimed_mutation_class,
                    "proposal_kind": proposal_kind,
                },
            )
            task.type = task_type_cls.RESEARCH
            try:
                proposal = create_spec_proposal(
                    storage,
                    scope=scope,
                    proposed_change=proposed_change,
                    rationale=rationale,
                    user_signal=user_signal,
                    session_id=session_id,
                    source="chat_agent",
                    review_task_id=task.task_id,
                    claimed_mutation_class=claimed_mutation_class,
                    proposal_kind=proposal_kind,
                )
            except ValueError as exc:
                storage.session.rollback()
                tool_content = f"Spec proposal rejected: {exc}"
                return {
                    "tool_message": {"role": "tool", "tool_call_id": tool_call_id, "name": func_name, "content": tool_content},
                    "tool_outputs_generated": tool_outputs_generated,
                    "async_task_id": async_task_id,
                    "tool_reason": reason,
                    "tool_name": func_name,
                }
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

        outcome = "broken" if str(tool_content).startswith("Error: Tool '") else "success"
        record_tool_execution(
            storage,
            tool_name=func_name,
            outcome=outcome,
            lane=lane,
            task_type=task_type,
            task_id=getattr(referenced_task, "task_id", None),
            session_id=session_id,
            source="chat_tool_executor",
            failure_kind="not_implemented" if outcome == "broken" else None,
            details={"tool_outputs_generated": tool_outputs_generated, "async_task_id": async_task_id},
        )
        storage.commit()

        return {
            "tool_message": {"role": "tool", "tool_call_id": tool_call_id, "name": func_name, "content": tool_content},
            "tool_outputs_generated": tool_outputs_generated,
            "async_task_id": async_task_id,
            "tool_reason": reason,
            "tool_name": func_name,
        }
