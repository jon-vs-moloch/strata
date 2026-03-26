"""
@module experimental.diagnostics
@purpose Summarize weak-eval traces and ask the strong tier to troubleshoot them explicitly.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from strata.schemas.execution import StrongExecutionContext


def _clip(text: Any, limit: int = 240) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _extract_json_object(raw: str) -> Dict[str, Any]:
    normalized = str(raw or "").strip()
    if normalized.startswith("```"):
        normalized = re.sub(r"^```(?:json)?", "", normalized).strip()
        normalized = re.sub(r"```$", "", normalized).strip()
    try:
        return json.loads(normalized)
    except Exception:
        pass
    match = re.search(r"\{.*\}", normalized, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in diagnostic response.")
    return json.loads(match.group(0))


def build_eval_trace_summary(
    *,
    candidate_change_id: str,
    baseline_change_id: str,
    benchmark_reports: Optional[List[Dict[str, Any]]] = None,
    structured_reports: Optional[List[Dict[str, Any]]] = None,
    suite_name: Optional[str] = None,
) -> Dict[str, Any]:
    benchmark_runs = benchmark_reports or []
    structured_runs = structured_reports or []
    benchmark_samples: List[Dict[str, Any]] = []
    structured_samples: List[Dict[str, Any]] = []

    for report in benchmark_runs[:2]:
        for sample in (report.get("samples") or [])[:3]:
            benchmark_samples.append(
                {
                    "prompt_id": sample.get("prompt_id"),
                    "winner": sample.get("winner"),
                    "baseline_score": sample.get("baseline_score"),
                    "harness_score": sample.get("harness_score"),
                    "baseline_latency_s": sample.get("baseline_latency_s"),
                    "harness_latency_s": sample.get("harness_latency_s"),
                    "rationale": _clip(sample.get("rationale"), 180),
                    "baseline_response": _clip(sample.get("baseline_response")),
                    "harness_response": _clip(sample.get("harness_response")),
                }
            )

    for report in structured_runs[:2]:
        for sample in (report.get("samples") or [])[:4]:
            structured_samples.append(
                {
                    "case_id": sample.get("case_id"),
                    "baseline_correct": sample.get("baseline_correct"),
                    "harness_correct": sample.get("harness_correct"),
                    "baseline_latency_s": sample.get("baseline_latency_s"),
                    "harness_latency_s": sample.get("harness_latency_s"),
                    "baseline_response": _clip(sample.get("baseline_response")),
                    "harness_response": _clip(sample.get("harness_response")),
                }
            )

    return {
        "candidate_change_id": candidate_change_id,
        "baseline_change_id": baseline_change_id,
        "suite_name": suite_name,
        "benchmark_runs": [
            {
                "run_label": report.get("run_label"),
                "baseline_wins": report.get("baseline_wins"),
                "harness_wins": report.get("harness_wins"),
                "ties": report.get("ties"),
                "average_baseline_score": report.get("average_baseline_score"),
                "average_harness_score": report.get("average_harness_score"),
            }
            for report in benchmark_runs[:3]
        ],
        "structured_runs": [
            {
                "run_label": report.get("run_label"),
                "suite_name": report.get("suite_name"),
                "baseline_accuracy": report.get("baseline_accuracy"),
                "harness_accuracy": report.get("harness_accuracy"),
                "baseline_avg_latency_s": report.get("baseline_avg_latency_s"),
                "harness_avg_latency_s": report.get("harness_avg_latency_s"),
            }
            for report in structured_runs[:3]
        ],
        "benchmark_samples": benchmark_samples,
        "structured_samples": structured_samples,
    }


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
    prompt = f"""
You are the strong-tier diagnostic reviewer for Strata.
Your explicit job is to review this weak-model execution trace and troubleshoot it.

Focus on:
- failure modes in the weak model's behavior
- whether the harness is teaching or nudging the wrong thing
- whether tool use, repo exploration, or prompt structure is a bottleneck
- one small, system-side fix that would most likely improve the weak model

Return only JSON with this schema:
{{
  "summary": "one paragraph",
  "primary_failure_mode": "short label",
  "evidence": ["specific observation", "specific observation"],
  "recommended_fix": "one bounded system-side change",
  "why_this_fix": "why it addresses the failure mode",
  "telemetry_to_watch": ["metric or trace to watch"],
  "confidence": 0.0
}}

Weak execution trace summary:
{json.dumps(trace_summary, indent=2)}
""".strip()

    model_adapter.bind_execution_context(StrongExecutionContext(run_id=f"trace_review_{candidate_change_id}"))
    try:
        response = await model_adapter.chat([{"role": "user", "content": prompt}], temperature=0.1)
        raw_content = response.get("content", "")
        review = _extract_json_object(raw_content)
        review["status"] = "ok"
        review["trace_summary"] = trace_summary
        review["raw_response"] = _clip(raw_content, 1600)
        return review
    except Exception as exc:
        return {
            "status": "unavailable",
            "summary": "Strong-tier diagnostic review could not be completed.",
            "primary_failure_mode": "diagnostic_unavailable",
            "evidence": [str(exc)],
            "recommended_fix": "",
            "why_this_fix": "",
            "telemetry_to_watch": [],
            "confidence": 0.0,
            "trace_summary": trace_summary,
            "error": str(exc),
        }
