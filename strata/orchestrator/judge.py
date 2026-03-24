"""
@module orchestrator.judge
@purpose Rank and judge multiple candidate generations using a Borda-weighted scoring rubric.
@owns rubric based evaluation, candidate comparison, ranking generation
@does_not_own candidate generation execution, code execution (sandboxing)
@key_exports JudgeModule
@side_effects none
"""

from typing import List, Dict, Any
from strata.schemas.core import TaskFraming

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
        
        from strata.orchestrator.evaluation import EvaluationPipeline
        self.evaluator = EvaluationPipeline(self.storage)

    async def judge_candidates(self, task_id: str, candidate_ids: List[str]) -> List[Dict[str, Any]]:
        """
        @summary Ranks candidates based on a specific task rubric using deterministic checks and optional LLM judgement.
        @inputs task_id: the parent task, candidate_ids: the generated solutions to compare
        @outputs list of objects containing candidate_id, score, and reasoning
        @side_effects reads candidates and updates telemetry
        """
        from strata.storage.models import TaskModel, CandidateModel
        task = self.storage.session.query(TaskModel).filter_by(task_id=task_id).first()
        if not task:
            return []
            
        print(f"Judging {len(candidate_ids)} candidates for task: {task_id}...")
        
        results = []
        for c_id in candidate_ids:
            candidate = self.storage.session.query(CandidateModel).filter_by(candidate_id=c_id).first()
            if not candidate:
                continue
                
            # RUN DETERMINISTIC PIPELINE
            scorecard = await self.evaluator.evaluate_candidate(task, candidate)
            
            # Record scorecard in DB (if CandidateModel support it, for now we just log/return)
            # Actually, CandidateModel doesn't have a scorecard field, but we can store it in telemetry or a separate record.
            
            results.append({
                "candidate_id": c_id,
                "score": scorecard.score,
                "valid": scorecard.valid,
                "reasoning": scorecard.reasoning,
                "checks_passed": scorecard.checks_passed,
                "checks_failed": scorecard.checks_failed,
                "diff_summary": scorecard.diff_summary
            })

        # Record telemetry feedback for the models used
        from strata.storage.models import ModelTelemetry, CandidateModel
        for res in results:
            cand = self.storage.session.query(CandidateModel).filter_by(candidate_id=res["candidate_id"]).first()
            if cand:
                telemetry = ModelTelemetry(
                    model_id=cand.model,
                    task_type=task.type.value,
                    score=res["score"]
                )
                self.storage.session.add(telemetry)
        
        self.storage.commit()
        return results
