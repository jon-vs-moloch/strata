"""
@module eval.matrix
@purpose Run structured eval suites across multiple model/scaffold variants.
@owns per-variant answer collection, exact grading, matrix report generation
@key_exports run_eval_matrix
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from strata.eval.harness_eval import EVAL_PROFILES, run_harness_response
from strata.eval.structured_eval import _grade_response, load_suite
from strata.models.adapter import ModelAdapter
from strata.schemas.execution import StrongExecutionContext, WeakExecutionContext


@dataclass
class EvalMatrixSample:
    case_id: str
    prompt: str
    expected_answer: str
    grading: str
    response: str
    correct: bool
    latency_s: float
    usage: Dict[str, Any]
    status: str
    error_message: str


def _build_context(mode: str, run_id: str):
    if mode == "strong":
        return StrongExecutionContext(run_id=run_id)
    return WeakExecutionContext(run_id=run_id)


async def _run_direct_response(mode: str, prompt: str, run_id: str) -> tuple[str, float, Dict[str, Any]]:
    adapter = ModelAdapter()
    adapter.bind_execution_context(_build_context(mode, run_id))
    started_at = time.perf_counter()
    response = await adapter.chat([{"role": "user", "content": prompt}], temperature=0.0)
    latency_s = time.perf_counter() - started_at
    return response.get("content", ""), latency_s, response.get("usage") or {}


async def _run_scaffold_response(
    mode: str,
    prompt: str,
    run_id: str,
    profile: str,
    context_files: Optional[List[str]] = None,
    system_prompt: Optional[str] = None,
) -> tuple[str, float, Dict[str, Any]]:
    return await run_harness_response(
        prompt,
        run_id=run_id,
        config_override={
            "context_files": context_files if context_files is not None else None,
            "system_prompt": system_prompt,
        },
        mode=mode,
        profile=profile,
    )


async def _run_variant(
    *,
    variant_id: str,
    mode: str,
    profile: str,
    cases: List[Dict[str, Any]],
    context_files: Optional[List[str]] = None,
    system_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    samples: List[EvalMatrixSample] = []
    for idx, case in enumerate(cases, start=1):
        case_id = str(case["id"])
        prompt = str(case["prompt"])
        expected = str(case["expected_answer"])
        grading = str(case.get("grading", "exact_match"))
        run_id = f"{variant_id}-{case_id}-{idx}-{int(time.time() * 1000)}"
        if profile == "raw_model":
            response, latency_s, usage = await _run_direct_response(mode, prompt, run_id)
        else:
            response, latency_s, usage = await _run_scaffold_response(
                mode,
                prompt,
                run_id,
                profile=profile,
                context_files=context_files,
                system_prompt=system_prompt,
            )
        samples.append(
            EvalMatrixSample(
                case_id=case_id,
                prompt=prompt,
                expected_answer=expected,
                grading=grading,
                response=response,
                correct=_grade_response(response, expected, grading),
                latency_s=round(latency_s, 2),
                usage=usage,
                status=str(usage.get("status") or "success"),
                error_message=str(usage.get("error_message") or ""),
            )
        )
    case_count = len(samples)
    correct = sum(1 for sample in samples if sample.correct)
    error_count = sum(1 for sample in samples if sample.status == "error")
    degraded_count = sum(1 for sample in samples if sample.status == "degraded")
    total_prompt_tokens = sum(int(sample.usage.get("prompt_tokens") or 0) for sample in samples)
    total_completion_tokens = sum(int(sample.usage.get("completion_tokens") or 0) for sample in samples)
    total_tokens = sum(int(sample.usage.get("total_tokens") or 0) for sample in samples)
    return {
        "variant_id": variant_id,
        "mode": mode,
        "profile": profile,
        "case_count": case_count,
        "accuracy": round(correct / case_count, 4) if case_count else 0.0,
        "error_count": error_count,
        "error_rate": round(error_count / case_count, 4) if case_count else 0.0,
        "degraded_count": degraded_count,
        "degraded_rate": round(degraded_count / case_count, 4) if case_count else 0.0,
        "avg_latency_s": round(sum(sample.latency_s for sample in samples) / case_count, 2) if case_count else 0.0,
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "total_tokens": total_tokens,
        "samples": [asdict(sample) for sample in samples],
    }


async def run_eval_matrix(
    *,
    suite_name: str,
    include_context: bool = True,
    include_strong: bool = True,
    include_weak: bool = True,
    profiles: Optional[List[str]] = None,
    sample_size: Optional[int] = None,
    random_seed: Optional[int] = None,
) -> Dict[str, Any]:
    cases = load_suite(suite_name)
    if sample_size is not None and 0 < sample_size < len(cases):
        rng = random.Random(random_seed)
        cases = rng.sample(cases, sample_size)
    selected_profiles = [profile for profile in (profiles or list(EVAL_PROFILES.keys())) if profile in EVAL_PROFILES]
    variants: List[Dict[str, Any]] = []
    if include_weak:
        for profile in selected_profiles:
            variants.append(
                {
                    "variant_id": f"weak_{profile}",
                    "mode": "weak",
                    "profile": profile,
                    "context_files": None if include_context else [],
                }
            )
    if include_strong:
        for profile in selected_profiles:
            variants.append(
                {
                    "variant_id": f"strong_{profile}",
                    "mode": "strong",
                    "profile": profile,
                    "context_files": None if include_context else [],
                }
            )
    results = []
    for variant in variants:
        results.append(await _run_variant(cases=cases, **variant))
    return {
        "suite_name": suite_name,
        "include_context": include_context,
        "profiles": selected_profiles,
        "case_count": len(cases),
        "variants": results,
    }


def main() -> None:
    import json
    report = asyncio.run(run_eval_matrix(suite_name="mmlu_mini_v1"))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
