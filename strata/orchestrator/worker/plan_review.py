"""
@module orchestrator.worker.plan_review
@purpose Evaluate if the current plan still makes sense after an attempt.
"""

import logging
from strata.experimental.trace_review import build_attempt_intelligence, render_attempt_intelligence
from strata.storage.models import TaskModel, AttemptModel

logger = logging.getLogger(__name__)

async def generate_plan_review(task: TaskModel, attempt: AttemptModel, model_adapter, storage) -> dict:
    """
    @summary Use the LLM to review the current plan health and recommend next steps.
    @outputs dict containing plan_health, recommendation, confidence, rationale
    """
    logger.info(f"Generating plan review for task {task.task_id}")
    
    # Gather context
    outcome_str = attempt.outcome.value if attempt.outcome else "unknown"
    reason_str = attempt.reason or "No specific reason provided."
    attempt_intelligence = render_attempt_intelligence(
        build_attempt_intelligence(
            storage,
            task=task,
            attempt_id=getattr(attempt, "attempt_id", None),
        )
    )
    
    prompt = f"""You are a senior technical project manager reviewing an agent's progress.
An 'Attempt' has just finished for the following task:
Title: {task.title}
Description: {task.description}

Attempt Outcome: {outcome_str}
Attempt Reason/Error: {reason_str}
{attempt_intelligence}

Evaluate if the current plan (pursuing this task and its subtasks) still makes sense or if it needs structural adjustment.

Output MUST be a YAML block with these fields:
plan_health: healthy | uncertain | degraded | invalid
recommendation: continue | reattempt | decompose | internal_replan | abandon_to_parent
confidence: <float 0.0 to 1.0>
rationale: <short explanation>

Rules:
- Even if the attempt SUCCEEDED, you may recommend 'decompose' or 'internal_replan' if the approach seems unsustainable.
- Even if the attempt FAILED, you may recommend 'continue' (if it was a transient error) or 'reattempt'.
- 'healthy' means the plan is working and on track.
- 'uncertain' means progress is slower than expected or small obstacles appeared.
- 'degraded' means significant issues occurred, but the goal is still viable.
- 'invalid' means this branch of the plan is no longer viable.
"""
    try:
        messages = [{"role": "system", "content": prompt}]
        response = await model_adapter.chat(messages)
        content = response.get("content", "")
        
        review = model_adapter.extract_yaml(content)
        
        # Basic validation/normalization
        defaults = {
            "plan_health": "healthy" if outcome_str == "succeeded" else "uncertain",
            "recommendation": "continue" if outcome_str == "succeeded" else "reattempt",
            "confidence": 0.8,
            "rationale": "Automated fallback"
        }
        
        if not isinstance(review, dict) or "plan_health" not in review:
            logger.warning("LLM produced invalid plan review YAML, using default.")
            return defaults
            
        return {
            "plan_health": review.get("plan_health", defaults["plan_health"]),
            "recommendation": review.get("recommendation", defaults["recommendation"]),
            "confidence": float(review.get("confidence", defaults["confidence"])),
            "rationale": review.get("rationale", defaults["rationale"])
        }
        
    except Exception as e:
        logger.error(f"Error generating LLM plan review: {e}")
        return {
            "plan_health": "uncertain",
            "recommendation": "reattempt",
            "confidence": 0.5,
            "rationale": f"Review system error: {str(e)}"
        }
