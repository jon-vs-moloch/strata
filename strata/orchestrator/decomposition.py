"""
@module orchestrator.decomposition
@purpose Decompose a high-level task into a directed acyclic graph (DAG) of leaf tasks.
@owns task framing, dependency graph generation, leaf task prototyping
@does_not_own research execution, task status management, LLM completions
@key_exports DecompositionModule
@side_effects none
"""

from typing import Optional
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
        system_prompt = f"""You are an Expert Software Architect. Your job is to decompose a high-level coding task into a series of small, atomic leaf tasks (subtasks).

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
        - dependencies: zero or more sibling subtask IDs that must complete before this subtask should run.
        - each leaf task must be oneshottable: one variance-bearing invocation should plausibly complete it.

        ONESHOTTABLE TASK RULES:
        - If work naturally requires progressive stages like inspect, then patch, then validate, those are separate subtasks.
        - Do not collapse multiple progressive stages into one leaf task.
        - If a subtask would need another semantically different model step after completion, it is still too large and should be split again.
        - Use dependencies to represent serial work.
        - Use no dependencies for work that can run in parallel.
        - Prefer mixed DAGs over pretending everything is either fully serial or fully parallel.

        OUTPUT CONTRACT:
        - Return only one structured object matching the requested schema.
        - Do not add prose before or after the object.
        - Do not wrap the object in markdown fences.
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
        data = self.model.extract_structured_object(raw_content)
        
        if not data or "error" in data:
            print(f"Decomposition failed to parse structured output: {data}")
            # Preserve framing, but do not fabricate fake recovery work.
            from strata.schemas.core import TaskFraming
            return TaskDecomposition(
                framing=TaskFraming(repository_context="Repo", problem_statement=task_desc, constraints=[], success_criteria=[]),
                subtasks={},
                total_estimated_budget=0.1
            )
            
        try:
            return TaskDecomposition(**data)
        except Exception as e:
            print(f"Decomposition validation failed: {e}")
            # Another layer of safety
            from strata.schemas.core import TaskFraming
            return TaskDecomposition(
                framing=TaskFraming(
                    repository_context=str((data.get("framing") or {}).get("repository_context") or "Repository context unavailable."),
                    problem_statement=str((data.get("framing") or {}).get("problem_statement") or task_desc),
                    constraints=list((data.get("framing") or {}).get("constraints") or []),
                    success_criteria=list((data.get("framing") or {}).get("success_criteria") or []),
                ),
                subtasks={},
                total_estimated_budget=float(data.get("total_estimated_budget") or 0.0),
            )
