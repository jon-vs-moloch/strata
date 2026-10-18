"""
@module orchestrator.judge
@purpose Rank and judge multiple candidate generations using a Borda-weighted scoring rubric.
@owns rubric based evaluation, candidate comparison, ranking generation
@does_not_own candidate generation execution, code execution (sandboxing)
@key_exports JudgeModule
@side_effects none
"""

import os
from typing import List, Dict, Any
from strata.schemas.core import TaskFraming
from strata.orchestrator.worker.telemetry import record_metric
from strata.experimental.variants import classify_pool_pruning, record_ranked_variant_matchups

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
        @summary Ranks candidates based on scorecards with optional LLM tie-breaking.
        """
        from strata.storage.models import TaskModel, CandidateModel, ModelTelemetry
        task = self.storage.session.get(TaskModel, task_id)
        if not task:
            return []
            
        print(f"Judging {len(candidate_ids)} candidates for task: {task_id}...")
        
        scored = []
        for c_id in candidate_ids:
            candidate = self.storage.session.get(CandidateModel, c_id)
            if not candidate:
                continue
                
            # RUN DETERMINISTIC PIPELINE
            scorecard = await self.evaluator.evaluate_candidate(task, candidate)
            
            scored.append({
                "candidate_id": c_id,
                "score": scorecard.score,
                "valid": scorecard.valid,
                "reasoning": scorecard.reasoning,
                "checks_passed": scorecard.checks_passed,
                "checks_failed": scorecard.checks_failed,
                "diff_summary": scorecard.diff_summary
            })
            
            # Record telemetry feedback
            telemetry = ModelTelemetry(
                model_id=candidate.model,
                task_type=task.type.value if hasattr(task.type, 'value') else str(task.type),
                score=scorecard.score
            )
            self.storage.session.add(telemetry)

        # 1. Separate valid from invalid
        valid = [x for x in scored if x["valid"]]
        invalid = [x for x in scored if not x["valid"]]

        # 2. Sort valid candidates by score
        valid.sort(key=lambda x: float(x["score"]), reverse=True)

        # 3. LLM Tie-breaker if scores are close (within 2.0 points)
        if len(valid) >= 2 and abs(float(valid[0]["score"]) - float(valid[1]["score"])) < 2.0:
            record_metric(self.storage, "tie_break_triggered", 1.0, task_id=task.task_id)
            valid = await self.maybe_llm_tiebreak(task, valid)
        else:
            record_metric(self.storage, "tie_break_triggered", 0.0, task_id=task.task_id)

        ranked_variant_ids = []
        for row in valid:
            candidate = self.storage.session.get(CandidateModel, row["candidate_id"])
            variant_id = str(getattr(candidate, "prompt_version", "") or "").strip()
            if variant_id:
                ranked_variant_ids.append(variant_id)
        matchup_snapshots = []
        if len(ranked_variant_ids) >= 2:
            matchup_snapshots = record_ranked_variant_matchups(
                self.storage,
                domain=f"ops:implementation:{task.type.value.lower()}",
                ranked_variant_ids=ranked_variant_ids,
                context={"task_id": task.task_id, "stage": "implementation"},
            )

        pruning = classify_pool_pruning(self.storage, pool_size=len(valid))
        constraints = dict(task.constraints or {})
        constraints["candidate_ranking"] = {
            "valid_count": len(valid),
            "invalid_count": len(invalid),
            "ranked_variant_ids": ranked_variant_ids,
            "matchup_count": len(matchup_snapshots),
            "pruning_policy": pruning,
            "recommended_rejections": pruning.get("drop_count", 0),
        }
        task.constraints = constraints

        self.storage.commit()
        return valid + invalid

    async def maybe_llm_tiebreak(self, task, ranked_candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        @summary LLM-based tie-breaker logic for cases with similar deterministic scores.
        """
        from strata.storage.models import CandidateModel
        print("Scores are close. Invoking LLM tie-breaker...")
        
        # Take the top N candidates that are close
        top_candidates = []
        base_score = ranked_candidates[0]["score"]
        for c in ranked_candidates:
            if abs(c["score"] - base_score) < 2.0:
                top_candidates.append(c)
            else:
                break
        
        if len(top_candidates) < 2:
            return ranked_candidates
            
        # Build prompt for comparison
        comparison_data = []
        for c in top_candidates:
            cand = self.storage.session.get(CandidateModel, c["candidate_id"])
            content = ""
            if cand and os.path.exists(cand.content_path):
                with open(cand.content_path, "r") as f:
                    content = f.read()
            comparison_data.append(f"CANDIDATE: {c['candidate_id']}\nSCORE: {c['score']}\nCHANCE SUMMARY: {c['diff_summary']}\n--- CONTENT START ---\n{content}\n--- CONTENT END ---")
            
        system_prompt = f"""You are a High-Confidence Judge. Multiple implementation candidates have similar deterministic scores.
Compare them and pick the one that is most robust, clean, and exactly follows the task requirements.

TASK: {task.title}
DESCRIPTION: {task.description}

{chr(10).join(comparison_data)}

Respond with ONLY the candidate_id you prefer.
"""
        response = await self.model.chat(messages=[{"role": "user", "content": system_prompt}])
        preferred_id = response.get("content", "").strip()
        
        # Re-sort preferred to the top
        for i, c in enumerate(ranked_candidates):
            if c["candidate_id"] == preferred_id:
                winner = ranked_candidates.pop(i)
                winner["reasoning"] += " (Preferred by LLM Tie-breaker)"
                ranked_candidates.insert(0, winner)
                break
                
        return ranked_candidates
