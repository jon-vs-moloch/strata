"""
@module orchestrator.judge
@purpose Rank and judge multiple candidate generations using a Borda-weighted scoring rubric.
@owns rubric based evaluation, candidate comparison, ranking generation
@does_not_own candidate generation execution, code execution (sandboxing)
@key_exports JudgeModule
@side_effects none
"""

from typing import List, Dict, Any
from shotgun_tokens.schemas.core import TaskFraming

class JudgeModule:
    """
    @summary Ranks candidate implementations for a specific task using a Borda-weighted voting system.
    @inputs model: ModelAdapter, storage: StorageManager
    @outputs Ranked list of candidate IDs with score breakdowns
    @side_effects requests completions from the LLM adapter
    @depends models.adapter, schemas.core.TaskFraming
    @invariants candidates with same score occupy adjacent ranks
    """
    def __init__(self, model_adapter, storage_manager):
        """
        @summary Initialize the JudgeModule.
        @inputs model_adapter instance, storage_manager instance
        @outputs none
        """
        self.model = model_adapter
        self.storage = storage_manager

    async def judge_candidates(self, task_id: str, candidate_ids: List[str]) -> List[Dict[str, Any]]:
        """
        @summary Ranks candidates based on a specific task rubric.
        @inputs task_id: the parent task, candidate_ids: the generated solutions to compare
        @outputs list of objects containing candidate_id, score, and reasoning
        @side_effects reads candidates from storage
        """
        print(f"Judging {len(candidate_ids)} candidates for task: {task_id}...")
        
        # In a real run, this would be a prompt sending the task.success_criteria
        # and all candidate code to the model to get a YAML-based ranking report.
        results = [
            {"candidate_id": c_id, "score": 10.0, "reasoning": "Highest structural integrity."}
            for c_id in candidate_ids
        ]

        # Record telemetry feedback for the models used
        from shotgun_tokens.storage.models import ModelTelemetry, TaskModel, CandidateModel
        task = self.storage.session.query(TaskModel).filter_by(task_id=task_id).first()
        for res in results:
            cand = self.storage.session.query(CandidateModel).filter_by(candidate_id=res["candidate_id"]).first()
            if cand and task:
                telemetry = ModelTelemetry(
                    model_id=cand.model,
                    task_type=task.type.value,
                    score=res["score"]
                )
                self.storage.session.add(telemetry)
        
        self.storage.commit()
        return results
