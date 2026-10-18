"""
@module experimental.promotion_policy
@purpose Keep bootstrap promotion heuristics small, explicit, and reusable.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


DEFAULT_PROMOTION_POLICY = {
    "min_benchmark_wins": 2,
    "min_structured_wins": 1,
    "min_code_wins": 1,
}


def get_promotion_policy(storage) -> Dict[str, int]:
    policy = storage.parameters.peek_parameter(
        "bootstrap_promotion_policy",
        default_value=DEFAULT_PROMOTION_POLICY,
    ) or DEFAULT_PROMOTION_POLICY
    merged = dict(DEFAULT_PROMOTION_POLICY)
    if isinstance(policy, dict):
        merged.update({k: int(v) for k, v in policy.items() if str(v).isdigit() or isinstance(v, int)})
    return merged


def benchmark_improved(report: Dict[str, Any]) -> bool:
    harness_wins = int(report.get("harness_wins", 0) or 0)
    baseline_wins = int(report.get("baseline_wins", 0) or 0)
    harness_score = float(report.get("average_harness_score", 0.0) or 0.0)
    baseline_score = float(report.get("average_baseline_score", 0.0) or 0.0)
    return harness_wins > baseline_wins or harness_score > baseline_score


def structured_improved(report: Dict[str, Any]) -> bool:
    harness_accuracy = float(report.get("harness_accuracy", 0.0) or 0.0)
    baseline_accuracy = float(report.get("baseline_accuracy", 0.0) or 0.0)
    return harness_accuracy > baseline_accuracy


def build_promotion_readiness(
    storage,
    *,
    evaluation_kind: str,
    benchmark_reports: Optional[List[Dict[str, Any]]] = None,
    structured_reports: Optional[List[Dict[str, Any]]] = None,
    code_validation: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    policy = get_promotion_policy(storage)
    benchmark_reports = benchmark_reports or []
    structured_reports = structured_reports or []
    benchmark_wins = sum(1 for report in benchmark_reports if benchmark_improved(report))
    structured_wins = sum(1 for report in structured_reports if structured_improved(report))
    code_wins = 1 if code_validation and code_validation.get("promoted") else 0

    ready = False
    if evaluation_kind == "benchmark":
        ready = benchmark_wins >= policy["min_benchmark_wins"]
    elif evaluation_kind == "full_eval":
        ready = (
            benchmark_wins >= policy["min_benchmark_wins"]
            and structured_wins >= policy["min_structured_wins"]
        )
    elif evaluation_kind == "tool_promotion":
        ready = code_wins >= policy["min_code_wins"]

    return {
        "policy": policy,
        "benchmark_win_runs": benchmark_wins,
        "benchmark_total_runs": len(benchmark_reports),
        "structured_win_runs": structured_wins,
        "structured_total_runs": len(structured_reports),
        "code_win_runs": code_wins,
        "ready_for_promotion": ready,
    }


def calculate_deltas(baseline: Dict[str, float], candidate: Dict[str, float]) -> Dict[str, float]:
    deltas: Dict[str, float] = {}
    all_keys = set(baseline.keys()) | set(candidate.keys())
    for key in all_keys:
        deltas[key] = float(candidate.get(key, 0.0) or 0.0) - float(baseline.get(key, 0.0) or 0.0)
    return deltas


def decide_promotion(deltas: Dict[str, float]) -> str:
    vcr_delta = deltas.get("valid_candidate_rate", 0.0)
    failure_delta = deltas.get("task_failure", 0.0)
    if vcr_delta > 0.05 and failure_delta <= 0:
        return "promote"
    if vcr_delta < -0.05:
        return "reject"
    return "insufficient_signal"


def decide_benchmark_promotion(
    deltas: Dict[str, float],
    promotion_readiness: Optional[Dict[str, Any]] = None,
) -> str:
    if promotion_readiness and not promotion_readiness.get("ready_for_promotion", False):
        return "insufficient_signal"
    score_delta = deltas.get("benchmark_score_delta", 0.0)
    harness_win_delta = deltas.get("benchmark_harness_win_rate", 0.0)
    structured_accuracy_delta = deltas.get("structured_eval_harness_accuracy", 0.0)
    structured_latency_delta = deltas.get("structured_eval_harness_latency_s", 0.0)
    if structured_accuracy_delta > 0.0:
        return "promote"
    if structured_accuracy_delta < 0.0:
        return "reject"
    if score_delta > 0.5 or harness_win_delta > 0.15:
        return "promote"
    if score_delta < -0.5:
        return "reject"
    if structured_latency_delta < -5.0:
        return "promote"
    return "insufficient_signal"
