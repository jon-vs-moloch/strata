"""
@module experimental.diagnostics
@purpose Backward-compatible eval-specific wrappers over the generic trace-review engine.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from strata.experimental.trace_review import build_eval_trace_summary, review_trace


async def review_eval_trace(
    model_adapter,
    *,
    candidate_change_id: str,
    baseline_change_id: str,
    benchmark_reports: Optional[List[Dict[str, Any]]] = None,
    structured_reports: Optional[List[Dict[str, Any]]] = None,
    suite_name: Optional[str] = None,
) -> Dict[str, Any]:
    trace_summary = build_eval_trace_summary(
        candidate_change_id=candidate_change_id,
        baseline_change_id=baseline_change_id,
        benchmark_reports=benchmark_reports,
        structured_reports=structured_reports,
        suite_name=suite_name,
    )
    return await review_trace(
        model_adapter,
        trace_kind="eval_trace",
        trace_summary=trace_summary,
        reviewer_tier="trainer",
        candidate_change_id=candidate_change_id,
    )
