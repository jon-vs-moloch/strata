"""
@module orchestrator.worker.resolution_policy
@purpose Map task failures to structural resolutions.
@owns deterministic failure policy, LLM-based failure analysis
"""

import logging
import asyncio
from typing import Optional, List
from strata.core.lanes import infer_lane_from_task
from strata.storage.models import TaskModel, AttemptModel, AttemptResolution, AttemptOutcome, TaskState, TaskType
from strata.schemas.core import AttemptResolutionSchema, SubtaskDraft
from strata.core.policy import requires_validator
from strata.orchestrator.user_questions import enqueue_user_question

logger = logging.getLogger(__name__)
MAX_RESEARCH_REATTEMPTS = 2
MAX_DEFAULT_REATTEMPTS = 3


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

async def determine_resolution(task: TaskModel, error: Exception, model_adapter, storage) -> AttemptResolutionSchema:
    """
    @summary Choose a resolution strategy for a failed task.
    @inputs task: the failed task, error: the exception that occurred
    @outputs AttemptResolutionSchema
    """
    
    # 1. DETERMINISTIC MAPPING (Priority)
    err_str = str(error).lower()
    
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
        
    # C. Missing Dependency -> blocked
    if any(x in err_str for x in ["not found", "missing", "no such file"]):
        return AttemptResolutionSchema(
            reasoning=f"Physical resource missing: {err_str}. Marking as blocked.",
            resolution="blocked"
        )

    # 2. LLM-BASED ANALYSIS (Fallback/Override)
    logger.info("Falling back to LLM for complex failure analysis.")
    prompt = f"""You are a failure analysis agent for Strata.
A background task has failed. Evaluate the error and determine the structural fix.

TASK: {task.title}
DESCRIPTION: {task.description}
ERROR: {str(error)}

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

Respond with structured reasoning first, then the resolution choice.
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
        import json
        raw_content = response.get("content", "{}")
        # Handle potential markdown fence
        if "```json" in raw_content:
            raw_content = raw_content.split("```json")[1].split("```")[0]
        
        data = json.loads(raw_content)
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
    
    if res == "reattempt":
        attempts = storage.attempts.get_by_task_id(task.task_id)
        failed_attempts = [attempt for attempt in attempts if attempt.outcome == AttemptOutcome.FAILED]
        error_text = str(error).lower()
        max_reattempts = MAX_RESEARCH_REATTEMPTS if task.type == TaskType.RESEARCH else MAX_DEFAULT_REATTEMPTS
        repeated_iteration_limit = "iteration limit reached" in error_text
        if len(failed_attempts) >= max_reattempts and repeated_iteration_limit:
            task.state = TaskState.BLOCKED
            task.human_intervention_required = True
            enqueue_user_question(
                storage,
                session_id=task.session_id or "default",
                question=(
                    f"Task '{task.title}' hit repeated autonomous iteration limits after "
                    f"{len(failed_attempts)} failed attempts. What should I change or clarify before retrying?"
                ),
                source_type="task_blocked",
                source_id=task.task_id,
                lane=task_lane,
                context={"reasoning": str(error), "title": task.title, "lane": task_lane},
            )
            storage.commit()
            return
        task.state = TaskState.PENDING
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
            # Fallback to generic decomposition task
            failover = storage.tasks.create(
                title=f"Recovery Plan for {task.title}",
                description=f"Automated recovery from failover analysis: {resolution_data.reasoning}. Original error: {error}",
                parent_task_id=task.task_id,
                type=TaskType.DECOMP,
                state=TaskState.PENDING,
                depth=task.depth + 1,
                constraints={"lane": task_lane} if task_lane else None,
            )
            storage.commit()
            await enqueue_fn(failover.task_id)
            task.state = TaskState.WORKING
            
    elif res == "abandon_to_parent":
        task.state = TaskState.ABANDONED
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
        await enqueue_fn(repair_task.task_id)

    elif res == "blocked":
        task.state = TaskState.BLOCKED
        task.human_intervention_required = True
        enqueue_user_question(
            storage,
            session_id=task.session_id or "default",
            question=(
                f"I’m blocked on task '{task.title}'. "
                f"What should I know or change to proceed? Reason: {resolution_data.reasoning}"
            ),
            source_type="task_blocked",
            source_id=task.task_id,
            context={
                "reasoning": resolution_data.reasoning,
                "title": task.title,
                "task_id": task.task_id,
                "lane": task_lane,
            },
            lane=task_lane,
        )
        storage.commit()
