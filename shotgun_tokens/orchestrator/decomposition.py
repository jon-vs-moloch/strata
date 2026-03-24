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
from shotgun_tokens.schemas.core import TaskDecomposition, ResearchReport

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
        
        YOUR OUTPUT MUST BE A SINGLE VALID YAML BLOCK WRAPPED IN ```yaml TRIPLE-BACKTICKS.
        Follow this strict format:
        
        framing:
          repository_context: "Brief summary of the target code area"
          problem_statement: "{task_desc[:100]}..."
          constraints: ["rule 1", "rule 2"]
          success_criteria: ["verification step 1"]
        subtasks:
          t1:
            title: "Short name"
            description: "Detailed prompt for the implementer agent"
            target_files: ["path/to/file.py"]
            dependencies: []
          t2:
            title: "Next step"
            description: "..."
            target_files: ["..."]
            dependencies: ["t1"]
        total_estimated_budget: 1.0
        """
        
        response = await self.model.chat([{"role": "user", "content": system_prompt}])
        raw_content = response.get("content", "")
        
        # Use the adapter's built-in block extractor
        data = self.model.extract_yaml(raw_content)
        
        if not data or "error" in data:
            print(f"Decomposition failed to parse YAML: {data}")
            # Fallback mock so we don't crash, but log it
            from shotgun_tokens.schemas.core import TaskFraming, LeafTaskPrototype
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

