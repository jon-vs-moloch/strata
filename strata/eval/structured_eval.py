"""
@module eval.structured_eval
@purpose Run dataset-style structured eval suites against the baseline weak model and the harness.
@owns suite loading, exact-match grading, structured eval report generation
@does_not_own freeform LLM judging, API orchestration logic
@key_exports run_structured_eval, persist_structured_eval_report
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from strata.eval.benchmark import _load_env_file
from strata.eval.harness_eval import run_harness_response
from strata.models.adapter import ModelAdapter
from strata.orchestrator.worker.telemetry import record_metric
from strata.schemas.execution import AgentExecutionContext


SUITES_DIR = Path("strata/eval/suites")


@dataclass
class StructuredEvalSample:
    case_id: str
    prompt: str
    expected_answer: str
    baseline_response: str
    harness_response: str
    baseline_correct: bool
    harness_correct: bool
    baseline_latency_s: float
    harness_latency_s: float


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _extract_choice_letter(text: str) -> str:
    match = re.search(r"\b([A-D])\b", text.upper())
    return match.group(1) if match else ""


def _grade_response(response: str, expected_answer: str, grading: str) -> bool:
    if grading == "choice":
        return _extract_choice_letter(response) == expected_answer.strip().upper()
    return _normalize_text(response) == _normalize_text(expected_answer)


def load_suite(suite_name: str) -> List[Dict[str, Any]]:
    """
    @summary Load a structured eval suite from JSONL.
    """
    suite_path = SUITES_DIR / f"{suite_name}.jsonl"
    if not suite_path.exists():
        raise FileNotFoundError(f"Structured eval suite not found: {suite_path}")
    cases: List[Dict[str, Any]] = []
    for line in suite_path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw:
            continue
        cases.append(json.loads(raw))
    return cases


async def _run_direct_baseline(adapter: ModelAdapter, prompt: str) -> tuple[str, float]:
    adapter.bind_execution_context(AgentExecutionContext(run_id=f"structured_eval_{int(time.time() * 1000)}"))
    started_at = time.perf_counter()
    response = await adapter.chat([{"role": "user", "content": prompt}], temperature=0.0)
    return response.get("content", ""), time.perf_counter() - started_at


async def run_structured_eval(
    *,
    api_url: str = "http://127.0.0.1:8000",
    suite_name: Optional[str] = None,
    cases: Optional[List[Dict[str, Any]]] = None,
    run_label: Optional[str] = None,
    eval_harness_config_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    @summary Run a structured eval suite and return aggregate accuracy/latency.
    """
    _load_env_file()
    eval_cases = cases or load_suite(suite_name or "bootstrap_mcq_v1")
    run_label = run_label or f"run-{int(time.time() * 1000)}"
    adapter = ModelAdapter()
    samples: List[StructuredEvalSample] = []

    for idx, case in enumerate(eval_cases, start=1):
        case_id = str(case["id"])
        prompt = str(case["prompt"])
        expected = str(case["expected_answer"])
        grading = str(case.get("grading", "exact_match"))

        baseline_response, baseline_latency = await _run_direct_baseline(adapter, prompt)
        harness_response, harness_latency, _ = await run_harness_response(
            prompt,
            run_id=f"structured-{run_label}-{case_id}-{idx}",
            config_override=eval_harness_config_override,
            profile="harness_no_capes",
        )

        samples.append(
            StructuredEvalSample(
                case_id=case_id,
                prompt=prompt,
                expected_answer=expected,
                baseline_response=baseline_response,
                harness_response=harness_response,
                baseline_correct=_grade_response(baseline_response, expected, grading),
                harness_correct=_grade_response(harness_response, expected, grading),
                baseline_latency_s=round(baseline_latency, 2),
                harness_latency_s=round(harness_latency, 2),
            )
        )

    case_count = len(samples)
    baseline_correct = sum(1 for sample in samples if sample.baseline_correct)
    harness_correct = sum(1 for sample in samples if sample.harness_correct)

    return {
        "run_label": run_label,
        "suite_name": suite_name or "inline",
        "case_count": case_count,
        "baseline_accuracy": round(baseline_correct / case_count, 4) if case_count else 0.0,
        "harness_accuracy": round(harness_correct / case_count, 4) if case_count else 0.0,
        "baseline_avg_latency_s": round(sum(sample.baseline_latency_s for sample in samples) / case_count, 2) if case_count else 0.0,
        "harness_avg_latency_s": round(sum(sample.harness_latency_s for sample in samples) / case_count, 2) if case_count else 0.0,
        "samples": [asdict(sample) for sample in samples],
    }


def persist_structured_eval_report(
    storage,
    report: Dict[str, Any],
    *,
    candidate_change_id: Optional[str],
    run_mode: str,
    model_id: str,
    variant_assignment: Optional[Dict[str, Any]] = None,
) -> None:
    """
    @summary Persist structured eval metrics.
    """
    case_count = max(1, int(report.get("case_count", 0) or 0))
    variant_assignment = dict(variant_assignment or {})
    aggregate_metrics = [
        ("structured_eval_baseline_accuracy", float(report.get("baseline_accuracy", 0.0) or 0.0)),
        ("structured_eval_harness_accuracy", float(report.get("harness_accuracy", 0.0) or 0.0)),
        (
            "structured_eval_accuracy_delta",
            float(report.get("harness_accuracy", 0.0) or 0.0) - float(report.get("baseline_accuracy", 0.0) or 0.0),
        ),
        ("structured_eval_baseline_latency_s", float(report.get("baseline_avg_latency_s", 0.0) or 0.0)),
        ("structured_eval_harness_latency_s", float(report.get("harness_avg_latency_s", 0.0) or 0.0)),
    ]
    for metric_name, value in aggregate_metrics:
        record_metric(
            storage,
            metric_name=metric_name,
            value=value,
            model_id=model_id,
            task_type="STRUCTURED_EVAL",
            run_mode=run_mode,
            execution_context="agent",
            candidate_change_id=candidate_change_id,
            details={
                "case_count": case_count,
                "suite_name": report.get("suite_name", "inline"),
                "run_label": report.get("run_label"),
                "variant_assignment": variant_assignment,
            },
        )

    for sample in report.get("samples", []):
        record_metric(
            storage,
            metric_name="structured_eval_sample_delta",
            value=(1.0 if sample.get("harness_correct") else 0.0) - (1.0 if sample.get("baseline_correct") else 0.0),
            model_id=model_id,
            task_type="STRUCTURED_EVAL",
            run_mode=run_mode,
            execution_context="agent",
            candidate_change_id=candidate_change_id,
            details={
                "run_label": report.get("run_label"),
                "case_id": sample.get("case_id"),
                "baseline_correct": sample.get("baseline_correct"),
                "harness_correct": sample.get("harness_correct"),
                "suite_name": report.get("suite_name", "inline"),
                "variant_assignment": variant_assignment,
            },
        )


def main() -> None:
    report = asyncio.run(run_structured_eval())
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
