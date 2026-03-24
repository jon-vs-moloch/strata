"""
@module orchestrator.synthesis
@purpose Fuses multiple subtask patches into a unified parent task solution.
@owns patch merging, conflict resolution logic, final artifact generation
@does_not_own code execution (sandboxing), candidate generation
@key_exports SynthesisModule
@side_effects none
"""

from typing import List, Dict, Any
from strata.schemas.core import TaskDecomposition

class SynthesisModule:
    """
    @summary Consolidates multiple child task artifacts into a single coherent patch.
    @inputs model: ModelAdapter, storage: StorageManager
    @outputs Resulting synthesis patch or file
    @side_effects requests completions from the LLM adapter
    @depends models.adapter, schemas.core.TaskDecomposition
    @invariants does not produce a patch that breaks existing tests
    """
    def __init__(self, model_adapter, storage_manager):
        """
        @summary Initialize the SynthesisModule.
        @inputs model_adapter instance, storage_manager instance
        @outputs none
        """
        self.model = model_adapter
        self.storage = storage_manager

    async def synthesize_subtasks(self, task_id: str, subtask_patches: Dict[str, str]) -> str:
        """
        @summary Harmonizes overlapping subtask patches into a final parent solution.
        @inputs task_id: parent task to fulfill, subtask_patches: map of task_id to its patch
        @outputs consolidated patch string
        @side_effects uses LLM to handle complex conflict resolution
        """
        # 3. Construct a prompt array for the model.chat() method
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an expert code integration engine. Your job is to take multiple "
                    "non-conflicting code patches/snippets and merge them into a single, cohesive "
                    "file or patch. If you detect overlapping changes, harmonize them logically."
                )
            },
            {
                "role": "user",
                "content": (
                    f"Please synthesize the following subtask patches for parent task '{task_id}':\n\n" +
                    "\n\n".join([f"### Subtask {tid}:\n```\n{patch}\n```" for tid, patch in subtask_patches.items()])
                )
            }
        ]
        
        # 4. Execute the LLM call
        response = await self.model.chat(messages)
        
        # 5. Extract and return the content
        return response.get("content", "# Synthesis failed or returned empty.")
