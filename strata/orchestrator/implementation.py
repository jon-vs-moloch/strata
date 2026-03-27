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
from strata.storage.models import TaskModel, CandidateModel, AttemptModel, AttemptOutcome
from strata.experimental.variants import build_stage_scope, build_variant_execution_plan, classify_pool_pruning

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

    async def implement_task(self, task_id: str, global_research: Optional[ResearchReport] = None) -> List[str]:
        """
        @summary Execute a coding task with a two-pass research strategy.
        """
        # Fetch task details from DB
        from strata.storage.models import TaskModel, CandidateModel
        task = self.storage.session.query(TaskModel).filter_by(task_id=task_id).first()
        if not task:
            return []
            
        print(f"Implementing leaf task: {task.title}...")
        
        # Pass 2: Local Research focused on the Files
        local_research: ResearchReport = await self.researcher.conduct_research(
            task_description=f"Analyze files {task.constraints.get('target_files', [])} to implement: {task.description}",
            repo_path=task.repo_path
        )

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
            iteration = 0
            max_iters = 5
            content = ""
            response = {}
            while iteration < max_iters:
                response = await self.model.chat(variant_messages, tools=IMPLEMENTATION_META_TOOLS)
                content = response.get("content", "")
                tool_calls = response.get("tool_calls", None)
                
                if not tool_calls:
                    break
                    
                variant_messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
                
                for call in tool_calls:
                    func_name = call["function"]["name"]
                    args = json.loads(call["function"]["arguments"])
                    tool_result = ""
                    
                    try:
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
                            success, message = await self.tools_pipeline.validate_and_promote(name)
                            tool_result = message
                                
                        variant_messages.append({
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "name": func_name,
                            "content": tool_result
                        })
                    except Exception as e:
                        variant_messages.append({
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "name": func_name,
                            "content": f"Error: {str(e)}"
                        })
                iteration += 1

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
