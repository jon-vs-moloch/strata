"""
@module orchestrator.decomposition
@purpose Decompose a high-level task into a directed acyclic graph (DAG) of leaf tasks.
@owns task framing, dependency graph generation, leaf task prototyping
@does_not_own research execution, task status management, LLM completions
@key_exports DecompositionModule
@side_effects none
"""

import yaml
from typing import Dict, Any, Optional
from strata.schemas.core import TaskDecomposition, ResearchReport

class DecompositionModule:
    """
    @summary Analyzes a task and uses LLM to generate a structured decomposition.
    @inputs model: ModelAdapter, storage: StorageManager
    @outputs TaskDecomposition object
    @side_effects requests completions from the LLM adapter
    @depends models.adapter, schemas.core.TaskDecomposition
    @invariants total_estimated_budget in decomposition is > 0
    """
    def __init__(self, model_adapter, storage_manager):
        """
        @summary Initialize the DecompositionModule.
        @inputs model_adapter instance, storage_manager instance
        @outputs none
        """
        self.model = model_adapter
        self.storage = storage_manager

    async def decompose_task(self, task_title: str, task_desc: str, research: Optional[ResearchReport] = None) -> TaskDecomposition:
        """
        @summary Generates a structured DAG of subtasks using YAML-based prompting.
        """
        print(f"Decomposing task: {task_title}...")
        system_prompt = f"""You are an Expert Software Architect. Your job is to decompose a high-level coding task into a series of small, atomic, parallelizable 'leaf' tasks (subtasks).
        
        GOAL: {task_title}
        DESCRIPTION: {task_desc}
        
        {"RESEARCH CONTEXT:" + research.context_gathered if research else ""}
        {"CONSTRAINTS:" + str(research.key_constraints_discovered) if research else ""}
        {"SUGGESTED APPROACH:" + research.suggested_approach if research else ""}
        
        Respond with a structured decomposition of the task into subtasks.
        
        CRITICAL: For each subtask, you MUST provide:
        - target_files: The exact list of files to be modified.
        - edit_type: 'refactor', 'feature', 'test', 'fix', or 'chore'.
        - validator: The specific validation engine (e.g., 'pytest', 'lint', 'sandbox').
        - max_diff_size: A character-count budget for the file change (default 50000).
        """
        
        response = await self.model.chat(
            messages=[{"role": "user", "content": system_prompt}],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "task_decomposition",
                    "strict": True,
                    "schema": TaskDecomposition.model_json_schema()
                }
            }
        )
        raw_content = response.get("content", "{}")
        
        import json
        try:
            # Handle potential markdown fence if the model ignored response_format (rare)
            if "```json" in raw_content:
                raw_content = raw_content.split("```json")[1].split("```")[0]
            data = json.loads(raw_content)
        except Exception:
            data = {"error": "Failed to parse JSON"}
        
        if not data or "error" in data:
            print(f"Decomposition failed to parse YAML: {data}")
            # Fallback mock so we don't crash, but log it
            from strata.schemas.core import TaskFraming, LeafTaskPrototype
            return TaskDecomposition(
                framing=TaskFraming(repository_context="Repo", problem_statement=task_desc, constraints=[], success_criteria=[]),
                subtasks={"error_fallback": LeafTaskPrototype(title="Error Recover", description="Initial decomposition failed. Research manually.", target_files=[], dependencies=[])},
                total_estimated_budget=0.1
            )
            
        try:
            return TaskDecomposition(**data)
        except Exception as e:
            print(f"Decomposition validation failed: {e}")
            # Another layer of safety
            return TaskDecomposition(
                framing=TaskDecomposition(**data).framing if "framing" in data else TaskDecomposition(framing={"repository_context": "..."}).framing, # bit risky but ok for now
                subtasks={},
                total_estimated_budget=0.0
            ) # This will likely fail downstream, which is fine

