"""
@module experimental.calibration
@purpose Score predictive judge outputs against downstream outcomes and maintain rolling trust.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List


JUDGE_TRUST_KEY = "judge_trust_registry"
DEFAULT_CONFIDENCE = 0.5
METRIC_WEIGHTS = {
    "structured_eval_harness_accuracy": 4.0,
    "benchmark_harness_win_rate": 3.0,
    "benchmark_score_delta": 2.0,
    "task_failure": 3.0,
    "structured_eval_harness_latency_s": 1.0,
    "latency_s": 1.0,
}


def normalize_prediction(review: Dict[str, Any]) -> Dict[str, Any]:
    risk = review.get("risk") if isinstance(review.get("risk"), dict) else {}
    predicted_delta = review.get("predicted_delta") if isinstance(review.get("predicted_delta"), dict) else {}
    return {
        "predicted_outcome": str(review.get("predicted_outcome") or "uncertain").strip().lower() or "uncertain",
        "confidence": max(0.0, min(1.0, float(review.get("confidence", DEFAULT_CONFIDENCE) or DEFAULT_CONFIDENCE))),
        "expected_value": float(review.get("expected_value", 0.0) or 0.0),
        "risk": {str(k): float(v or 0.0) for k, v in risk.items()},
        "predicted_delta": {str(k): float(v or 0.0) for k, v in predicted_delta.items()},
        "domains_affected": [str(item) for item in (review.get("domains_affected") or []) if str(item).strip()],
        "rationale": str(review.get("rationale") or review.get("summary") or "").strip(),
    }


def infer_actual_outcome(actual_delta: Dict[str, Any]) -> str:
    positive = 0.0
    negative = 0.0
    for key, raw_value in (actual_delta or {}).items():
        value = float(raw_value or 0.0)
        if key in {"task_failure", "latency_s", "structured_eval_harness_latency_s"}:
            if value < 0:
                positive += abs(value)
            elif value > 0:
                negative += abs(value)
        else:
            if value > 0:
                positive += value
            elif value < 0:
                negative += abs(value)
    if positive > negative + 0.01:
        return "improve"
    if negative > positive + 0.01:
        return "regress"
    return "neutral"


def _direction_probability(predicted_outcome: str, confidence: float) -> float:
    if predicted_outcome == "improve":
        return confidence
    if predicted_outcome == "regress":
        return 1.0 - confidence
    if predicted_outcome == "neutral":
        return 0.5
    return 0.5


def _magnitude_accuracy(predicted_delta: Dict[str, float], actual_delta: Dict[str, Any]) -> float:
    keys = set(predicted_delta.keys()) | set((actual_delta or {}).keys())
    if not keys:
        return 0.5
    weighted_error = 0.0
    total_weight = 0.0
    for key in keys:
        weight = float(METRIC_WEIGHTS.get(key, 1.0))
        predicted = float(predicted_delta.get(key, 0.0) or 0.0)
        actual = float((actual_delta or {}).get(key, 0.0) or 0.0)
        weighted_error += weight * abs(predicted - actual)
        total_weight += weight
    mae = weighted_error / total_weight if total_weight else 0.0
    return max(0.0, 1.0 - min(1.0, mae))


def score_prediction_against_outcome(
    prediction: Dict[str, Any],
    *,
    actual_delta: Dict[str, Any],
    promotion_result: str,
    observed_domains: Iterable[str],
    run_count: int,
) -> Dict[str, Any]:
    predicted_outcome = str(prediction.get("predicted_outcome") or "uncertain").lower()
    confidence = max(0.0, min(1.0, float(prediction.get("confidence", DEFAULT_CONFIDENCE) or DEFAULT_CONFIDENCE)))
    actual_outcome = infer_actual_outcome(actual_delta)
    direction_correct = 1.0 if predicted_outcome == actual_outcome else 0.0
    y = 1.0 if actual_outcome == "improve" else 0.0
    probability = _direction_probability(predicted_outcome, confidence)
    brier_loss = (probability - y) ** 2
    confidence_score = max(0.0, 1.0 - brier_loss)
    magnitude_accuracy = _magnitude_accuracy(prediction.get("predicted_delta") or {}, actual_delta)
    promoted = str(promotion_result or "").lower() == "promote"
    utility_alignment = 0.5
    if predicted_outcome == "improve" and promoted:
        utility_alignment = 1.0 if actual_outcome == "improve" else 0.0
    elif predicted_outcome == "regress" and not promoted:
        utility_alignment = 1.0 if actual_outcome != "improve" else 0.0
    elif predicted_outcome == "neutral":
        utility_alignment = 0.75 if actual_outcome == "neutral" else 0.25
    calibration_score = (
        0.35 * direction_correct
        + 0.30 * confidence_score
        + 0.20 * utility_alignment
        + 0.15 * magnitude_accuracy
    )
    return {
        "actual_outcome": actual_outcome,
        "direction_correct": direction_correct,
        "confidence_score": round(confidence_score, 4),
        "magnitude_error": round(1.0 - magnitude_accuracy, 4),
        "utility_alignment": round(utility_alignment, 4),
        "calibration_score": round(calibration_score, 4),
        "promotion_result": promotion_result,
        "run_count": int(run_count or 0),
        "observed_domains": [str(item) for item in observed_domains if str(item).strip()],
        "actual_delta": {str(k): float(v or 0.0) for k, v in (actual_delta or {}).items()},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def update_judge_trust(storage, *, judge_tier: str, prediction: Dict[str, Any], calibration_record: Dict[str, Any]) -> Dict[str, Any]:
    registry = storage.parameters.peek_parameter(
        JUDGE_TRUST_KEY,
        default_value={"by_tier": {}, "by_domain": {}, "by_failure_family": {}},
    ) or {"by_tier": {}, "by_domain": {}, "by_failure_family": {}}
    registry = dict(registry)
    registry.setdefault("by_tier", {})
    registry.setdefault("by_domain", {})
    registry.setdefault("by_failure_family", {})

    score = float(calibration_record.get("calibration_score", 0.0) or 0.0)

    def _roll(bucket: Dict[str, Any], key: str) -> Dict[str, Any]:
        current = dict(bucket.get(key) or {})
        count = int(current.get("count", 0) or 0)
        avg = float(current.get("trust", 0.5) or 0.5)
        new_avg = ((avg * count) + score) / (count + 1) if count >= 0 else score
        updated = {"trust": round(new_avg, 4), "count": count + 1}
        bucket[key] = updated
        return updated

    tier_snapshot = _roll(registry["by_tier"], str(judge_tier or "unknown"))
    domain_snapshots = {}
    for domain in prediction.get("domains_affected") or []:
        domain_key = f"{judge_tier}:{domain}"
        domain_snapshots[domain] = _roll(registry["by_domain"], domain_key)

    failure_family = str(prediction.get("failure_family") or "").strip()
    failure_snapshot = None
    if failure_family:
        failure_snapshot = _roll(registry["by_failure_family"], f"{judge_tier}:{failure_family}")

    storage.parameters.set_parameter(
        JUDGE_TRUST_KEY,
        registry,
        description="Rolling trust for predictive judges by tier, domain, and failure family.",
    )
    return {
        "tier": tier_snapshot,
        "domains": domain_snapshots,
        "failure_family": failure_snapshot,
    }
