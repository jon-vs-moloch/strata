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
from strata.experimental.variants import build_stage_scope, build_variant_execution_plan, record_ranked_variant_matchups

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

    async def _rank_synthesis_outputs(self, task_id: str, outputs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if len(outputs) < 2:
            return outputs
        prompt = (
            f"You are ranking synthesis outputs for task '{task_id}'. "
            "Choose the best merged result for coherence, correctness, completeness, and minimal conflict. "
            "Return only JSON like {\"preferred_variant_id\":\"...\"}.\n\n"
        )
        for item in outputs:
            prompt += f"Variant {item['variant_id']}:\n```\n{item['content']}\n```\n\n"
        response = await self.model.chat([{"role": "user", "content": prompt}])
        preferred = str(response.get("content", "") or "").strip()
        if preferred.startswith("{") and "preferred_variant_id" in preferred:
            import json
            try:
                preferred = str(json.loads(preferred).get("preferred_variant_id") or "").strip()
            except Exception:
                preferred = ""
        ranked = list(outputs)
        for idx, item in enumerate(ranked):
            if item["variant_id"] == preferred:
                ranked.insert(0, ranked.pop(idx))
                break
        return ranked

    async def synthesize_subtasks(self, task_id: str, subtask_patches: Dict[str, str]) -> str:
        """
        @summary Harmonizes overlapping subtask patches into a final parent solution.
        @inputs task_id: parent task to fulfill, subtask_patches: map of task_id to its patch
        @outputs consolidated patch string
        @side_effects uses LLM to handle complex conflict resolution
        """
        # 3. Construct a prompt array for the model.chat() method
        stage_scope = build_stage_scope(component="synthesis", process="subtasks", step="default")
        execution_plan = build_variant_execution_plan(
            self.storage,
            family="synthesis_prompt",
            stage_scope=stage_scope,
            domain=f"ops:{stage_scope}",
            safe_mode=False,
        )
        variants = list(execution_plan.get("selected_variants") or []) or [
            {
                "variant_id": "synthesis_prompt.generic",
                "payload": {},
                "metadata": {"stage_scope": stage_scope},
            }
        ]
        outputs: List[Dict[str, Any]] = []
        for variant in variants:
            instruction = str((variant.get("payload") or {}).get("instruction_suffix") or "").strip()
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are an expert code integration engine. Your job is to take multiple "
                        "non-conflicting code patches/snippets and merge them into a single, cohesive "
                        "file or patch. If you detect overlapping changes, harmonize them logically."
                        + (f"\n\nVariant Instruction:\n{instruction}" if instruction else "")
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
            response = await self.model.chat(messages)
            outputs.append(
                {
                    "variant_id": str(variant.get("variant_id") or "synthesis_prompt.generic"),
                    "content": response.get("content", "# Synthesis failed or returned empty."),
                }
            )
        ranked_outputs = await self._rank_synthesis_outputs(task_id, outputs)
        ranked_variant_ids = [item["variant_id"] for item in ranked_outputs if item.get("variant_id")]
        if len(ranked_variant_ids) >= 2:
            record_ranked_variant_matchups(
                self.storage,
                domain=f"ops:{stage_scope}",
                ranked_variant_ids=ranked_variant_ids,
                context={"task_id": task_id, "stage": "synthesis"},
            )
            self.storage.commit()
        return ranked_outputs[0]["content"] if ranked_outputs else "# Synthesis failed or returned empty."
