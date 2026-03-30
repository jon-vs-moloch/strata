"""
@module orchestrator.worker.resolution_policy
@purpose Map task failures to structural resolutions.
@owns deterministic failure policy, LLM-based failure analysis
"""

import logging
import asyncio
from typing import Optional, List
from strata.core.lanes import infer_lane_from_task
from strata.experimental.trace_review import build_attempt_intelligence, render_attempt_intelligence
from strata.storage.models import TaskModel, AttemptModel, AttemptResolution, AttemptOutcome, TaskState, TaskType
from strata.schemas.core import AttemptResolutionSchema, SubtaskDraft
from strata.core.policy import requires_validator
from strata.orchestrator.research import load_research_iteration_policy
from strata.orchestrator.user_questions import (
    enqueue_user_question,
    ensure_question_escalation_for_source,
    get_question_for_source,
)

logger = logging.getLogger(__name__)


def _tool_repair_shape(improvement_reason: str) -> tuple[str, TaskType]:
    normalized = str(improvement_reason or "unknown").strip().lower()
    if normalized == "tool_broken":
        return "Tool Fix", TaskType.BUG_FIX
    if normalized == "tool_missing":
        return "New Tool", TaskType.IMPL
    if normalized == "tool_too_weak":
        return "Tool Upgrade", TaskType.REFACTOR
    if normalized == "tool_misused":
        return "Tool Guidance", TaskType.REFACTOR
    return "Tool Repair", TaskType.IMPL


def _failure_kind(error: Exception) -> str:
    return str(getattr(error, "failure_kind", "") or "").strip().lower()


def _iteration_autopsy(error: Exception) -> dict:
    payload = getattr(error, "autopsy", None)
    return dict(payload or {}) if isinstance(payload, dict) else {}


def _is_recovery_shell_task(task: TaskModel) -> bool:
    title = str(getattr(task, "title", "") or "").strip().lower()
    description = str(getattr(task, "description", "") or "").strip().lower()
    return title == "error recover" or "research manually" in description


def _looks_like_clarification_task(task: TaskModel) -> bool:
    text = " ".join(
        str(part or "").strip().lower()
        for part in [getattr(task, "title", ""), getattr(task, "description", "")]
        if str(part or "").strip()
    )
    if not text:
        return False
    hints = [
        "pending question",
        "attention item",
        "clarif",
        "confirm",
        "choose",
        "preference",
        "operator",
        "unresolved",
        "what should i",
        "needs your input",
    ]
    return any(hint in text for hint in hints)


def _recovery_focus_task(storage, task: TaskModel) -> TaskModel:
    if storage is None or not hasattr(storage, "tasks"):
        return task
    visited = set()
    current = task
    fallback = task
    while current and getattr(current, "task_id", None) and current.task_id not in visited:
        visited.add(current.task_id)
        if not _is_recovery_shell_task(current) and current.type != TaskType.DECOMP:
            return current
        fallback = current
        parent_task_id = getattr(current, "parent_task_id", None)
        current = storage.tasks.get_by_id(parent_task_id) if parent_task_id else None
    return fallback


def _clip_text(value: str, limit: int = 220) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _build_blocked_question(task: TaskModel, *, storage, reasoning: str, error_text: str = "") -> tuple[str, dict]:
    focus = _recovery_focus_task(storage, task)
    focus_title = str(getattr(focus, "title", "") or getattr(task, "title", "") or "this task").strip()
    focus_description = _clip_text(str(getattr(focus, "description", "") or getattr(task, "description", "") or ""))
    root_task_id = str(getattr(focus, "task_id", "") or getattr(task, "task_id", "")).strip()
    current_title = str(getattr(task, "title", "") or "").strip()
    preface = (
        f"I’m blocked on '{focus_title}' and need your guidance before continuing."
        if focus_title
        else "I’m blocked and need your guidance before continuing."
    )
    if _looks_like_clarification_task(focus):
        question = (
            f"{preface} This work appears to require clarification or a decision rather than more autonomous research. "
            f"Please answer the open question or provide the missing preference/constraint so I can proceed. "
            f"Current blockage: {reasoning}"
        )
    else:
        question = (
            f"{preface} Please tell me what to change or clarify so I can proceed. "
            f"Current blockage: {reasoning}"
        )
    context = {
        "reasoning": reasoning,
        "error": error_text,
        "title": current_title or focus_title,
        "focus_task_id": root_task_id,
        "focus_title": focus_title,
        "focus_description": focus_description,
        "lane": infer_lane_from_task(task),
    }
    return question, context


def _enqueue_blocked_question_once(storage, task: TaskModel, *, reasoning: str, error_text: str = "") -> None:
    existing = get_question_for_source(storage, source_type="task_blocked", source_id=task.task_id)
    if existing:
        ensure_question_escalation_for_source(
            storage,
            source_type="task_blocked",
            source_id=task.task_id,
            escalation_mode="blocking",
            rationale=reasoning or error_text or "The branch is now blocked on required external input.",
        )
        return
    question, context = _build_blocked_question(task, storage=storage, reasoning=reasoning, error_text=error_text)
    enqueue_user_question(
        storage,
        session_id=task.session_id or "default",
        question=question,
        source_type="task_blocked",
        source_id=task.task_id,
        lane=infer_lane_from_task(task),
        context=context,
    )


def _count_iteration_limit_failures_in_lineage(storage, task: TaskModel) -> int:
    if storage is None or not hasattr(storage, "attempts") or not hasattr(storage, "tasks"):
        return 0
    visited = set()
    current = task
    total = 0
    while current and getattr(current, "task_id", None) and current.task_id not in visited:
        visited.add(current.task_id)
        for attempt in storage.attempts.get_by_task_id(current.task_id):
            reason = str(getattr(attempt, "reason", "") or "").lower()
            evidence = dict(getattr(attempt, "evidence", {}) or {})
            if (
                getattr(getattr(attempt, "outcome", None), "value", None) == AttemptOutcome.FAILED.value
                and (
                    evidence.get("failure_kind") == "iteration_budget_exhausted"
                    or "iteration limit reached" in reason
                )
            ):
                total += 1
        parent_task_id = getattr(current, "parent_task_id", None)
        current = storage.tasks.get_by_id(parent_task_id) if parent_task_id else None
    return total


def _reattempt_limit_for_task(task: TaskModel, policy: dict) -> int:
    if task.type == TaskType.RESEARCH:
        return int(policy.get("research_reattempt_limit", 2) or 2)
    if _is_recovery_shell_task(task):
        return int(policy.get("recovery_shell_reattempt_limit", 1) or 1)
    return int(policy.get("default_reattempt_limit", 3) or 3)

async def determine_resolution(task: TaskModel, error: Exception, model_adapter, storage) -> AttemptResolutionSchema:
    """
    @summary Choose a resolution strategy for a failed task.
    @inputs task: the failed task, error: the exception that occurred
    @outputs AttemptResolutionSchema
    """
    
    # 1. DETERMINISTIC MAPPING (Priority)
    err_str = str(error).lower()
    policy = load_research_iteration_policy(storage) if storage is not None else {}
    failure_kind = _failure_kind(error)
    iteration_autopsy = _iteration_autopsy(error)
    lineage_iteration_failures = _count_iteration_limit_failures_in_lineage(storage, task)
    
    # A. Parse Errors -> internal_replan
    if any(x in err_str for x in ["syntaxerror", "parse error", "invalid json"]):
        return AttemptResolutionSchema(
            reasoning=f"Structural failure detected: {err_str}. Forcing internal replan.",
            resolution="internal_replan"
        )
        
    # B. Timeout/Network -> reattempt
    if any(x in err_str for x in ["timeout", "deadline exceeded", "network", "httpx"]):
        return AttemptResolutionSchema(
            reasoning=f"Transient network/timeout failure: {err_str}.",
            resolution="reattempt"
        )

    # B2. Iteration exhaustion on research-like work -> decompose
    if failure_kind == "iteration_budget_exhausted" or "iteration limit reached" in err_str:
        if lineage_iteration_failures >= int(policy.get("lineage_iteration_limit", 4) or 4):
            return AttemptResolutionSchema(
                reasoning=(
                    "The branch has repeatedly exhausted its autonomous iteration budget across the current "
                    "task lineage. Stop recursive recovery and escalate the branch for higher-level replacement "
                    "or abandonment."
                ),
                resolution="abandon_to_parent" if _is_recovery_shell_task(task) or task.type != TaskType.RESEARCH else "blocked",
                new_subtasks=[],
            )
        if task.type == TaskType.RESEARCH:
            if _looks_like_clarification_task(task):
                return AttemptResolutionSchema(
                    reasoning=(
                        "The research task exhausted its iteration budget while gathering clarifications or decisions. "
                        "Stop autonomous looping and escalate for explicit guidance instead of decomposing into generic recovery work."
                    ),
                    resolution="blocked",
                    new_subtasks=[],
                )
            return AttemptResolutionSchema(
                reasoning=(
                    "The task exhausted its autonomous iteration budget during research. "
                    "Treat this as a scope/plan problem and decompose instead of blindly retrying."
                ),
                resolution="decompose",
                new_subtasks=[],
            )
        if _is_recovery_shell_task(task):
            archived_path = str((iteration_autopsy.get("archived_transcript") or {}).get("path") or "").strip()
            archive_hint = f" Archived transcript: {archived_path}." if archived_path else ""
            return AttemptResolutionSchema(
                reasoning=(
                    "Recovery-shell implementation exhausted its iteration budget. Restructure the approach using "
                    "the captured autopsy instead of recursively retrying the same manual-research shell."
                    f"{archive_hint}"
                ),
                resolution="internal_replan",
                new_subtasks=[],
            )

    # B3. Failed decomposition on recovery work should not recurse forever
    if (
        "decomposition produced no actionable subtasks" in err_str
        or (
            task.type == TaskType.DECOMP
            and any(x in err_str for x in ["parse error", "invalid json", "produced no actionable subtasks"])
        )
    ):
        return AttemptResolutionSchema(
            reasoning=(
                "Decomposition failed to produce a usable plan. Do not spawn generic recovery shells; "
                "route this to trainer supervision so the failing branch can be replaced or abandoned explicitly."
            ),
            resolution="abandon_to_parent",
            new_subtasks=[],
        )
        
    # C. Missing Dependency -> blocked
    if any(x in err_str for x in ["not found", "missing", "no such file"]):
        return AttemptResolutionSchema(
            reasoning=f"Physical resource missing: {err_str}. Marking as blocked.",
            resolution="blocked"
        )

    # 2. LLM-BASED ANALYSIS (Fallback/Override)
    logger.info("Falling back to LLM for complex failure analysis.")
    attempt_intelligence = render_attempt_intelligence(
        build_attempt_intelligence(storage, task=task) if storage is not None else {}
    )
    prompt = f"""You are a failure analysis agent for Strata.
A background task has failed. Evaluate the error and determine the structural fix.

TASK: {task.title}
DESCRIPTION: {task.description}
ERROR: {str(error)}
{attempt_intelligence}

RESOLUTIONS:
- reattempt: Use for transient/random errors.
- decompose: The goal is too large; break it into simpler subtasks.
- internal_replan: The current approach/method is flawed; replan at this level.
- abandon_to_parent: This leaf task is impossible OR requires architectural decisions beyond this scope.
- blocked: Use when the task is blocked by a missing physical requirement that ONLY a human can provide (e.g. missing API keys, manual environment setup, clarification on ambiguous user intent, access to a private resource).
- improve_tooling: Use when the task failed because a specific tool is missing or inadequate.

If you choose improve_tooling, also provide:
- tool_modification_target: the exact tool or capability involved
- tool_improvement_reason: one of tool_too_weak, tool_broken, tool_missing, tool_misused, unknown

Use tool_broken when the tool is behaving incorrectly and should be treated as untrustworthy until fixed.
Use tool_too_weak when the tool works but is insufficient for the task.

Return only one JSON object matching the requested schema.
Do not add prose before or after the object.
"""
    try:
        response = await model_adapter.chat(
            messages=[{"role": "system", "content": prompt}],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "attempt_resolution",
                    "strict": True,
                    "schema": AttemptResolutionSchema.model_json_schema()
                }
            }
        )
        raw_content = response.get("content", "{}")
        data = model_adapter.extract_structured_object(raw_content)
        if "error" in data:
            raise ValueError(data["error"])
        return AttemptResolutionSchema(**data)
    except Exception as e:
        logger.error(f"Failed to parse structured LLM resolution: {e}. Falling back to REATTEMPT.")
        return AttemptResolutionSchema(
            reasoning=f"Resolution analysis failed: {e}",
            resolution="reattempt",
            new_subtasks=[]
        )

async def apply_resolution(task: TaskModel, resolution_data: AttemptResolutionSchema, error: Exception, storage, enqueue_fn):
    """
    @summary Apply the resolution decision to the task graph.
    """
    from strata.storage.models import TaskState, TaskType
    res = resolution_data.resolution
    task_lane = infer_lane_from_task(task)
    logger.info(f"Applying Resolution: {res.upper()} for task {task.task_id} ({resolution_data.reasoning})")

    def _demote_existing_blocking_question(rationale: str) -> None:
        ensure_question_escalation_for_source(
            storage,
            source_type="task_blocked",
            source_id=task.task_id,
            escalation_mode="non_blocking",
            rationale=rationale,
        )
    
    if res == "reattempt":
        attempts = storage.attempts.get_by_task_id(task.task_id)
        failed_attempts = [attempt for attempt in attempts if attempt.outcome == AttemptOutcome.FAILED]
        error_text = str(error).lower()
        policy = load_research_iteration_policy(storage)
        max_reattempts = _reattempt_limit_for_task(task, policy)
        repeated_iteration_limit = "iteration limit reached" in error_text
        lineage_iteration_failures = _count_iteration_limit_failures_in_lineage(storage, task)
        if (
            repeated_iteration_limit
            and (
                len(failed_attempts) >= max_reattempts
                or lineage_iteration_failures >= int(policy.get("lineage_iteration_limit", 4) or 4)
            )
        ):
            task.state = TaskState.BLOCKED
            task.human_intervention_required = True
            _enqueue_blocked_question_once(
                storage,
                task,
                reasoning=(
                    f"Task hit repeated autonomous iteration limits after {len(failed_attempts)} failed attempts on "
                    f"this branch and {lineage_iteration_failures} across its lineage."
                ),
                error_text=str(error),
            )
            storage.commit()
            return
        task.state = TaskState.PENDING
        _demote_existing_blocking_question("A new autonomous retry path is available, so user input is no longer the only route forward.")
        await enqueue_fn(task.task_id)
        
    elif res == "decompose" or res == "internal_replan":
        if resolution_data.new_subtasks:
            for sub_proto in resolution_data.new_subtasks:
                sub = storage.tasks.create(
                    parent_task_id=task.task_id,
                    title=f"Recovery: {sub_proto.title}",
                    description=sub_proto.description,
                    session_id=task.session_id,
                    state=TaskState.PENDING,
                    depth=task.depth + 1,
                    constraints={"lane": task_lane} if task_lane else None,
                )
                sub.type = TaskType.IMPL
                # Mandatory policy check
                if requires_validator(sub):
                    sub.constraints["validator_required"] = True
                    logger.warning(f"Task {sub.task_id} requires a validator per system policy.")
                storage.commit()
                await enqueue_fn(sub.task_id)
            task.state = TaskState.WORKING
        else:
            focus = _recovery_focus_task(storage, task)
            focus_title = str(getattr(focus, "title", "") or task.title or "task").strip()
            focus_description = _clip_text(str(getattr(focus, "description", "") or task.description or ""))
            failover = storage.tasks.create(
                title=f"Recovery Plan for {focus_title}",
                description=(
                    f"Rebuild a bounded recovery plan for the original task '{focus_title}'. "
                    f"Original task context: {focus_description}. "
                    f"Current failing task: '{task.title}'. "
                    f"Failure analysis: {resolution_data.reasoning}. "
                    f"Original error: {error}. "
                    "Do not produce generic 'Error Recover' shells, manual-research placeholders, or empty file-list work. "
                    "If the branch actually needs human clarification or an external decision, surface that explicitly instead of inventing implementation subtasks."
                ),
                parent_task_id=task.task_id,
                type=TaskType.DECOMP,
                state=TaskState.PENDING,
                depth=task.depth + 1,
                constraints={
                    **({"lane": task_lane} if task_lane else {}),
                    "recovery_focus_task_id": getattr(focus, "task_id", None),
                    "recovery_focus_title": focus_title,
                    "recovery_focus_description": focus_description,
                    "avoid_generic_recovery_shell": True,
                } if task_lane or focus_title else None,
            )
            storage.commit()
            await enqueue_fn(failover.task_id)
            task.state = TaskState.WORKING
            _demote_existing_blocking_question("The branch has a fresh autonomous recovery plan, so guidance can remain advisory while work continues.")
            
    elif res == "abandon_to_parent":
        task.state = TaskState.ABANDONED
        if str(task_lane or "").strip().lower() == "agent":
            try:
                from strata.api.main import _queue_eval_system_job

                await _queue_eval_system_job(
                    storage,
                    kind="trace_review",
                    title=f"Trainer Intervention: {str(task.title or task.task_id)[:72]}",
                    description="Queued trainer intervention after a branch was abandoned to prevent recursive recovery loops.",
                    payload={
                        "trace_kind": "task_trace",
                        "task_id": task.task_id,
                        "reviewer_tier": "trainer",
                        "emit_followups": True,
                        "persist_to_task": True,
                        "spec_scope": "project",
                        "supervision_reason": "abandon_to_parent_recovery_loop",
                        "trace_payload": {
                            "origin_lane": task_lane,
                            "abandon_reason": resolution_data.reasoning,
                            "requested_action": "replace the failing branch with a bounded intervention or explicitly terminate it",
                        },
                    },
                    session_id="trainer:default",
                )
            except Exception as review_err:
                logger.warning("Failed to queue trainer intervention for abandoned task %s: %s", task.task_id, review_err)
        storage.apply_dependency_cascade()

    elif res == "improve_tooling":
        target = resolution_data.tool_modification_target or "unknown_tool"
        improvement_reason = resolution_data.tool_improvement_reason or "unknown"
        repair_prefix, repair_type = _tool_repair_shape(improvement_reason)
        task.state = TaskState.BLOCKED
        repair_task = storage.tasks.create(
            title=f"{repair_prefix}: {target}",
            description=(
                "The Orchestrator failed an objective because a tool lane needs intervention. "
                f"Target: {target}. Improvement reason: {improvement_reason}. "
                f"Reasoning: {resolution_data.reasoning}."
            ),
            session_id=task.session_id,
            state=TaskState.PENDING,
            type=repair_type,
            depth=task.depth + 1,
            priority=float(task.priority or 0.0),
            constraints={
                "lane": task_lane,
                "target_scope": "tooling",
                "source_task_id": task.task_id,
                "source_task_priority": float(task.priority or 0.0),
                "tool_modification_target": target,
                "tool_improvement_reason": improvement_reason,
                "tool_improvement_reasoning": resolution_data.reasoning,
            },
        )
        storage.commit()
        storage.tasks.add_dependency(task.task_id, repair_task.task_id)
        storage.commit()
        _demote_existing_blocking_question("Tooling remediation is now queued, so the prior user escalation can become non-blocking while recovery continues.")
        await enqueue_fn(repair_task.task_id)

    elif res == "blocked":
        task.state = TaskState.BLOCKED
        task.human_intervention_required = True
        queued_trainer_review = None
        if str(task_lane or "").strip().lower() == "agent":
            try:
                from strata.api.main import _queue_eval_system_job

                queued_trainer_review = await _queue_eval_system_job(
                    storage,
                    kind="trace_review",
                    title=f"Agent Escalation Review: {str(task.title or task.task_id)[:72]}",
                    description="Queued trainer review for agent-lane work that requested escalation.",
                    payload={
                        "trace_kind": "task_trace",
                        "task_id": task.task_id,
                        "reviewer_tier": "trainer",
                        "emit_followups": True,
                        "persist_to_task": True,
                        "spec_scope": "project",
                        "supervision_reason": "agent_blocked_escalation",
                        "trace_payload": {
                            "escalation_reason": resolution_data.reasoning,
                            "origin_lane": task_lane,
                            "requested_action": "decide whether to ask the user, add follow-up work, or unblock with guidance",
                        },
                    },
                    session_id="trainer:default",
                    dedupe_signature={
                        "trace_kind": "task_trace",
                        "reviewer_tier": "trainer",
                        "task_id": task.task_id,
                        "supervision_reason": "agent_blocked_escalation",
                    },
                )
            except Exception as exc:
                logger.warning("Unable to queue trainer escalation review for blocked agent task %s: %s", task.task_id, exc)
        if not queued_trainer_review:
            _enqueue_blocked_question_once(
                storage,
                task,
                reasoning=resolution_data.reasoning,
                error_text=str(error),
            )
        storage.commit()
