"""
@module orchestrator.implementation
@purpose Execute leaf-level coding tasks and generate implementation candidates.
@owns code generation, local research (file-level), staging of candidate artifacts
@does_not_own task decomposition, synthesis, or evaluation
@key_exports ImplementationModule
@side_effects initiates code writing to temporary worktrees or buffer files
"""

from typing import List, Dict, Any, Optional
from strata.schemas.core import ResearchReport, ResearchReport as LocalResearchReport
import os
import json
import httpx
from datetime import datetime, timezone
from strata.observability.writer import enqueue_attempt_observability_artifact, flush_observability_writes
from strata.orchestrator.step_outcomes import TerminalToolCallOutcome
from strata.storage.models import TaskModel, CandidateModel, AttemptModel, AttemptOutcome
from strata.experimental.variants import build_stage_scope, build_variant_execution_plan, classify_pool_pruning
from strata.orchestrator.research import (
    TaskBoundaryViolationError,
    _build_prompt_snapshot_payload,
    _render_task_graph_prompt_block,
    _task_graph_snapshot,
)

IMPLEMENTATION_META_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_active_tools",
            "description": "Returns the names and descriptions of currently loaded tools available to the orchestrator.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_tool_source",
            "description": "Returns the raw Python file contents of a specific dynamic tool.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string", "description": "The name of the tool (filename without extension)."}
                },
                "required": ["tool_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "upsert_tool_source",
            "description": "Writes new or updated logic to strata/tools/{tool_name}.experimental.py.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string", "description": "The name of the tool."},
                    "source": {"type": "string", "description": "The full Python source code for the tool file."}
                },
                "required": ["tool_name", "source"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_tool_promotion",
            "description": "Gated tool promotion. Validates .experimental.py for syntax, contract compliance, and sandbox tests before promoting it to .py (live). Provides a hard fitness signal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string", "description": "The name of the tool to promote (e.g., 'example_tool')."}
                },
                "required": ["tool_name"]
            }
        }
    }
]

class ImplementationModule:
    """
    @summary Manages the leaf-level execution of a code transformation task.
    @inputs model: ModelAdapter, storage: StorageManager, researcher: ResearchModule
    @outputs List of candidate IDs
    @side_effects requests completions from the LLM adapter, writes candidates to storage
    @depends orchestrator.research.ResearchModule, models.adapter
    @invariants does not mutate the main branch (uses candidates/worktrees)
    """
    def __init__(self, model_adapter, storage_manager, research_module):
        """
        @summary Initialize the ImplementationModule.
        @inputs model_adapter instance, storage_manager instance, research_module instance
        @outputs none
        """
        self.model = model_adapter
        self.storage = storage_manager
        self.researcher = research_module
        
        from strata.orchestrator.tools_pipeline import ToolsPromotionPipeline
        self.tools_pipeline = ToolsPromotionPipeline(self.storage)

    def _build_task_boundary_autopsy(
        self,
        *,
        task: TaskModel,
        response: Dict[str, Any],
        variant: Dict[str, Any],
        tool_call: Optional[Dict[str, Any]] = None,
        tool_result_preview: str = "",
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "failure_kind": "task_boundary_violation",
            "task_id": task.task_id,
            "task_title": str(task.title or ""),
            "task_description": str(task.description or ""),
            "variant_id": str(variant.get("variant_id") or ""),
            "single_turn_contract": (
                "An implementation task attempt must complete within one variance-bearing invocation plus bounded deterministic fallout."
            ),
            "model_response": {
                "content": str(response.get("content") or "")[:1600],
                "tool_call_count": len(response.get("tool_calls") or []),
            },
        }
        if tool_call:
            payload["tool_call"] = {
                "id": str(tool_call.get("id") or ""),
                "name": str((tool_call.get("function") or {}).get("name") or ""),
                "arguments": str((tool_call.get("function") or {}).get("arguments") or "")[:800],
            }
        if tool_result_preview:
            payload["tool_result_preview"] = str(tool_result_preview)[:1200]
        return payload

    def _resolve_generation_variants(self, task: TaskModel) -> List[Dict[str, Any]]:
        constraints = dict(task.constraints or {})
        stage_scope = str(
            constraints.get("variant_scope")
            or build_stage_scope(component="implementation", process=str(task.type.value).lower(), step="default")
        )
        execution_plan = build_variant_execution_plan(
            self.storage,
            family="implementation_prompt",
            stage_scope=stage_scope,
            domain=f"ops:{stage_scope}",
            safe_mode=bool(constraints.get("safe_mode", False)),
        )
        selected = list(execution_plan.get("selected_variants") or [])
        if selected:
            task_constraints = dict(task.constraints or {})
            task_constraints["variant_execution_plan"] = {
                "mode": execution_plan.get("mode"),
                "stage_scope": stage_scope,
                "default_variant_id": (execution_plan.get("default") or {}).get("variant_id"),
                "exploit_variant_ids": [item.get("variant_id") for item in (execution_plan.get("exploit_pool") or [])],
                "explore_variant_ids": [item.get("variant_id") for item in (execution_plan.get("explore_pair") or [])],
            }
            task.constraints = task_constraints
            self.storage.commit()
            return selected
        return [
            {
                "variant_id": "implementation_prompt.generic",
                "label": "implementation_prompt.generic",
                "payload": {},
                "metadata": {"stage_scope": stage_scope},
            }
        ]

    async def implement_task(
        self,
        task_id: str,
        global_research: Optional[ResearchReport] = None,
        *,
        progress_fn=None,
        attempt_id: Optional[str] = None,
        task_context: Optional[Dict[str, Any]] = None,
    ) -> List[str] | TerminalToolCallOutcome:
        """
        @summary Execute a coding task with a two-pass research strategy.
        """
        # Fetch task details from DB
        from strata.storage.models import TaskModel, CandidateModel
        task = self.storage.session.query(TaskModel).filter_by(task_id=task_id).first()
        if not task:
            return []
            
        print(f"Implementing leaf task: {task.title}...")
        task_context = dict(task_context or {})
        task_graph_context = dict(task_context.get("task_trajectory") or {}) or _task_graph_snapshot(
            self.storage,
            task_context={
                "task_id": task.task_id,
                "session_id": task.session_id,
            },
        )
        task_graph_block = _render_task_graph_prompt_block(task_graph_context)
        
        # Pass 2: Local Research focused on the Files
        local_research = await self.researcher.conduct_research(
            task_description=f"Analyze files {task.constraints.get('target_files', [])} to implement: {task.description}",
            repo_path=task.repo_path,
            context_hints={**dict(task.constraints or {}), "task_trajectory": task_graph_context},
            task_context={
                "task_id": task.task_id,
                "parent_task_id": task.parent_task_id,
                "title": task.title,
                "type": getattr(task.type, "value", str(task.type)),
                "state": getattr(task.state, "value", str(task.state)),
                "session_id": task.session_id,
                "task_trajectory": task_graph_context,
            },
            progress_fn=progress_fn,
            attempt_id=attempt_id,
        )
        if isinstance(local_research, TerminalToolCallOutcome):
            return local_research

        # Get past failures to avoid infinite loops
        from strata.storage.models import AttemptModel, AttemptOutcome
        failed_attempts = self.storage.session.query(AttemptModel).filter(
            AttemptModel.task_id == task_id,
            AttemptModel.outcome == AttemptOutcome.FAILED
        ).order_by(AttemptModel.started_at.asc()).all()
        
        failure_log = "None."
        if failed_attempts:
            failure_log = "\n".join([
                f"- Attempt {i+1} Failed: {a.reason or 'Unknown error'}. Resolution: {a.resolution.value if a.resolution else 'None'}."
                for i, a in enumerate(failed_attempts)
            ])

        system_prompt = f"""You are an Senior Implementation Engineer. 
        Your goal is to write code that satisfies the following task:
        
        TITLE: {task.title}
        DESCRIPTION: {task.description}
        
        GLOBAL ARCHITECTURAL CONTEXT:
        {global_research.context_gathered if global_research else "None provided."}
        
        LOCAL IMPLEMENTATION DETAILS:
        {local_research.context_gathered}
        CONSTRAINTS: {local_research.key_constraints_discovered}
        
        PAST ATTEMPTS TO AVOID:
        {failure_log}

        {task_graph_block}
        
        YOU MUST OUTPUT THE ENTIRE UPDATED FILE CONTENT OR A NEW FILE CONTENT.
        Output format:
        ```python (or other language)
        [CODE HERE]
        ```
        """
        
        messages = [{"role": "system", "content": system_prompt}]
        stage_variants = self._resolve_generation_variants(task)
        candidate_ids: List[str] = []
        os.makedirs("strata/experimental/candidates", exist_ok=True)

        for variant in stage_variants:
            variant_payload = dict(variant.get("payload") or {})
            variant_messages = list(messages)
            instruction = str(variant_payload.get("instruction_suffix") or "").strip()
            if instruction:
                variant_messages[0] = {
                    "role": "system",
                    "content": f"{system_prompt}\n\nVariant Instruction:\n{instruction}",
                }
            content = ""
            response = {}
            if progress_fn:
                progress_fn(
                    step="model_turn",
                    label="Generating implementation",
                    detail=str(variant.get("variant_id") or "implementation_prompt.generic"),
                    attempt_id=attempt_id,
                    progress_label="model generating",
                )

            # Phase 3.3: Attempt Context Snapshot
            if attempt_id:
                prompt_snapshot = _build_prompt_snapshot_payload(
                    prompt_kind="implementation_prompt",
                    prompt_version=str(variant.get("variant_id") or "generic"),
                    system_prompt=variant_messages[0]["content"],
                    user_message="",
                    tools=IMPLEMENTATION_META_TOOLS,
                    task_description=task.description,
                    target_scope="implementation",
                    handoff_context=dict(task.constraints.get("handoff_context") or {}),
                    task_graph_context=task_graph_context,
                    spec_paths=[],
                    preferred_start_paths=list(task.constraints.get("target_files") or []),
                    focused_guidance="Implement the task against the target files while respecting the active branch trajectory.",
                    repo_snapshot="",
                )
                should_flush = enqueue_attempt_observability_artifact({
                    "task_id": task_id,
                    "attempt_id": attempt_id,
                    "session_id": task.session_id,
                    "artifact_kind": "context_snapshot",
                    "payload": {
                        **prompt_snapshot,
                        "variant_id": str(variant.get("variant_id") or "generic"),
                        "tool_names": [str(((item or {}).get("function") or {}).get("name") or "") for item in IMPLEMENTATION_META_TOOLS],
                        "local_research": {
                            "gathered": str(local_research.context_gathered or "")[:1200],
                            "constraints": list(local_research.key_constraints_discovered or []),
                        },
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                })
                if should_flush:
                    flush_observability_writes()

            response = await self.model.chat(variant_messages, tools=IMPLEMENTATION_META_TOOLS)
            if attempt_id:
                should_flush = enqueue_attempt_observability_artifact(
                    {
                        "task_id": task_id,
                        "attempt_id": attempt_id,
                        "session_id": task.session_id,
                        "artifact_kind": "model_turn_snapshot",
                        "payload": {
                            "prompt_lineage_id": prompt_snapshot.get("prompt_lineage_id"),
                            "prompt_template_ref": prompt_snapshot.get("prompt_template_ref"),
                            "variant_id": str(variant.get("variant_id") or "generic"),
                            "response_status": str(response.get("status") or "").strip(),
                            "model": str(response.get("model") or ""),
                            "provider": str(response.get("provider") or ""),
                            "usage": dict(response.get("usage") or {}),
                            "content_preview": str(response.get("content") or "")[:1600],
                            "tool_calls": list(response.get("tool_calls") or []),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    }
                )
                if should_flush:
                    flush_observability_writes()
            content = response.get("content", "")
            tool_calls = response.get("tool_calls") or []

            if len(tool_calls) > 1:
                raise TaskBoundaryViolationError(
                    public_message=(
                        "Implementation task attempted multiple tool calls in a single variance-bearing invocation. "
                        "Decompose it into smaller oneshottable tasks."
                    ),
                    autopsy=self._build_task_boundary_autopsy(
                        task=task,
                        response=response,
                        variant=variant,
                    ),
                )

            if tool_calls:
                call = tool_calls[0]
                func_name = call["function"]["name"]
                args = json.loads(call["function"]["arguments"])
                tool_result = ""
                if progress_fn:
                    progress_fn(
                        step="tool_execution",
                        label="Executing implementation tool",
                        detail=func_name,
                        attempt_id=attempt_id,
                        progress_label=f"tool {func_name}",
                    )

                if func_name == "list_active_tools":
                    tools_dir = "strata/tools"
                    os.makedirs(tools_dir, exist_ok=True)
                    files = [f for f in os.listdir(tools_dir) if f.endswith(".py") or f.endswith(".experimental.py")]
                    tool_result = f"Dynamic files in tools/: {files}"

                elif func_name == "read_tool_source":
                    name = args["tool_name"]
                    path = f"strata/tools/{name}.py"
                    if not os.path.exists(path):
                        path = f"strata/tools/{name}.experimental.py"

                    if os.path.exists(path):
                        with open(path, "r") as f:
                            tool_result = f.read()
                    else:
                        tool_result = f"Tool {name} not found."

                elif func_name == "upsert_tool_source":
                    name = args["tool_name"]
                    source = args["source"]
                    os.makedirs("strata/tools", exist_ok=True)
                    path = f"strata/tools/{name}.experimental.py"
                    with open(path, "w") as f:
                        f.write(source)
                    tool_result = f"Successfully wrote to {path}. You must call trigger_tool_promotion to make it live."

                elif func_name == "trigger_tool_promotion":
                    name = args["tool_name"]
                    _success, message = await self.tools_pipeline.validate_and_promote(name)
                    tool_result = message

                else:
                    tool_result = f"Unsupported implementation tool call: {func_name}"

                return TerminalToolCallOutcome(
                    tool_name=str(func_name or ""),
                    tool_arguments=dict(args or {}),
                    tool_result_preview=str(tool_result or "")[:1200],
                    tool_result_full=str(tool_result if len(str(tool_result or "")) <= 12000 else ""),
                    next_step_hint=(
                        "Consume the inherited tool result in a new explicit implementation step. "
                        "Do not implicitly continue in the same model turn."
                    ),
                    source_module="implementation",
                    metadata={
                        "task_id": task_id,
                        "variant_id": str(variant.get("variant_id") or "generic"),
                    },
                    continuation_title=f"Continue implementation after {str(func_name or 'tool')}",
                    continuation_description=(
                        f"Continue implementation after the prior step executed `{str(func_name or 'tool')}`. "
                        "Read the inherited tool result first, then either produce the implementation artifact or take one new bounded tool action."
                    ),
                    continuation_task_type="IMPL",
                    continuation_constraints={
                        "terminal_tool_step": True,
                        "allow_inherited_tool_result": True,
                        "step_role": "consume_tool_result",
                    },
                )

            from uuid import uuid4
            candidate_id = str(uuid4())
            candidate = CandidateModel(
                candidate_id=candidate_id,
                task_id=task_id,
                stage="impl",
                prompt_version=str(variant.get("variant_id") or "v1"),
                model=f"{response.get('provider')}/{response.get('model')}",
                artifact_type="python_file",
                content_path=f"strata/experimental/candidates/{candidate_id}.py",
                summary=f"Implementation for {task.title}",
                proposed_files=task.constraints.get("target_files", [])
            )
            self.storage.session.add(candidate)
            self.storage.commit()
            with open(candidate.content_path, "w", encoding="utf-8") as f:
                f.write(content)
            candidate_ids.append(candidate_id)

        pruning = classify_pool_pruning(self.storage, pool_size=len(candidate_ids))
        task_constraints = dict(task.constraints or {})
        task_constraints["candidate_generation"] = {
            "stage_scope": str(stage_variants[0].get("metadata", {}).get("stage_scope") or ""),
            "generated_count": len(candidate_ids),
            "pruning_policy": pruning,
            "variant_ids": [str(item.get("variant_id") or "") for item in stage_variants],
        }
        task.constraints = task_constraints
        self.storage.commit()
        return candidate_ids
