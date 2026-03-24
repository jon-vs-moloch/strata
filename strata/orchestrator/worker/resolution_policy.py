"""
@module orchestrator.worker.resolution_policy
@purpose Map task failures to structural resolutions.
@owns deterministic failure policy, LLM-based failure analysis
"""

import logging
import asyncio
from typing import Optional, List
from strata.storage.models import TaskModel, AttemptModel, AttemptResolution, TaskState, TaskType
from strata.schemas.core import AttemptResolutionSchema, SubtaskDraft

logger = logging.getLogger(__name__)

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
    prompt = f"""You are a failure analysis agent for the Strata Swarm.
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
    logger.info(f"Applying Resolution: {res.upper()} for task {task.task_id} ({resolution_data.reasoning})")
    
    if res == "reattempt":
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
                    depth=task.depth + 1
                )
                sub.type = TaskType.IMPL
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
                depth=task.depth + 1
            )
            storage.commit()
            await enqueue_fn(failover.task_id)
            task.state = TaskState.WORKING
            
    elif res == "abandon_to_parent":
        task.state = TaskState.ABANDONED
        storage.apply_dependency_cascade()

    elif res == "improve_tooling":
        target = resolution_data.tool_modification_target or "unknown_tool"
        task.state = TaskState.BLOCKED
        repair_task = storage.tasks.create(
            title=f"Tool Repair: {target}",
            description=f"The Orchestrator failed an objective because a tool is inadequate or missing. Target: {target}. Reasoning: {resolution_data.reasoning}.",
            session_id=task.session_id,
            state=TaskState.PENDING,
            type=TaskType.IMPL,
            depth=task.depth + 1,
            priority=100.0 # Maximum priority
        )
        storage.commit()
        storage.tasks.add_dependency(task.task_id, repair_task.task_id)
        storage.commit()
        await enqueue_fn(repair_task.task_id)

    elif res == "blocked":
        import json
        task.state = TaskState.BLOCKED
        task.human_intervention_required = True
        storage.messages.create(
            role="system",
            content=json.dumps({
                "error": "Task Blocked",
                "reasoning": resolution_data.reasoning,
                "task_id": task.task_id,
                "title": task.title
            }),
            session_id=task.session_id or "default",
            is_intervention=True,
            task_id=task.task_id
        )
        storage.commit()
