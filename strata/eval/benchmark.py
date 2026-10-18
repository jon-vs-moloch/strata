"""
@module eval.benchmark
@purpose Compare direct weak-model responses against harness-mediated responses.
@owns prompt-set execution, paired judging, benchmark report generation
@does_not_own provider transport internals, API orchestration logic
@key_exports run_benchmark
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from strata.eval.harness_eval import run_harness_response
from strata.models.adapter import ModelAdapter
from strata.schemas.execution import TrainerExecutionContext, AgentExecutionContext
from strata.orchestrator.worker.telemetry import record_metric


DEFAULT_PROMPTS = [
    {
        "id": "next_experiment",
        "prompt": (
            "You are evaluating this harness in quiet testing mode. "
            "Based on the project philosophy and current configuration, identify the single "
            "highest-leverage next experiment to improve weak-model self-improvement. "
            "Respond concisely with exactly three numbered items: 1. bottleneck 2. experiment "
            "3. success telemetry."
        ),
    },
    {
        "id": "repo_orientation",
        "prompt": (
            "Explain this repository to a new contributor in three short bullets: "
            "what it is trying to do, why the repo is structured this way, and what success "
            "looks like for the weak model."
        ),
    },
    {
        "id": "quiet_mode_policy",
        "prompt": (
            "In quiet testing mode with no active tasks, what should the system do next? "
            "Answer in three short bullets and avoid background work."
        ),
    },
    {
        "id": "self_improvement_candidate",
        "prompt": (
            "Propose one small system-side change that would help the weak model improve itself. "
            "Respond with exactly three numbered items: 1. change 2. why it helps self-improvement "
            "3. how Strata should verify the gain before promotion."
        ),
    },
]


@dataclass
class BenchmarkSample:
    prompt_id: str
    prompt: str
    baseline_response: str
    harness_response: str
    baseline_latency_s: float
    harness_latency_s: float
    winner: str
    baseline_score: float
    harness_score: float
    rationale: str


def _load_env_file() -> None:
    env_path = Path(".env.local")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _extract_json_object(raw: str) -> Dict[str, Any]:
    try:
        return json.loads(raw)
    except Exception:
        pass

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in judge response.")
    return json.loads(match.group(0))


async def _run_direct_baseline(adapter: ModelAdapter, prompt: str) -> tuple[str, float]:
    adapter.bind_execution_context(AgentExecutionContext(run_id=f"baseline_{int(time.time() * 1000)}"))
    started_at = time.perf_counter()
    response = await adapter.chat(
        [{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    latency_s = time.perf_counter() - started_at
    return response.get("content", ""), latency_s


async def _judge_pair(adapter: ModelAdapter, prompt: str, baseline: str, harness: str) -> Dict[str, Any]:
    adapter.bind_execution_context(TrainerExecutionContext(run_id=f"judge_{int(time.time() * 1000)}"))
    judge_prompt = f"""
You are judging two answers to the same benchmark prompt for the Strata harness.
Evaluate which answer is better for usefulness, alignment with the user's request,
actionability, clarity, and project-awareness. Penalize hallucination, unnecessary
tool obsession, refusal when enough context is available, and failure to answer directly.

Use this rubric:
- 9-10: excellent, directly useful, accurate, well-aligned
- 7-8: good, minor issues
- 4-6: mixed, partially useful or partially aligned
- 1-3: poor, evasive, or substantially unhelpful

Do not give both answers a score of 0 unless both are completely empty.
Prefer a winner instead of a tie when one answer is even slightly more useful.

Return only JSON with this schema:
{{
  "winner": "baseline" | "harness" | "tie",
  "baseline_score": 1-10,
  "harness_score": 1-10,
  "rationale": "short explanation"
}}

Prompt:
{prompt}

Baseline answer:
{baseline}

Harness answer:
{harness}
""".strip()
    response = await adapter.chat(
        [{"role": "user", "content": judge_prompt}],
        temperature=0.0,
    )
    return _extract_json_object(response.get("content", ""))


async def run_benchmark(
    api_url: str = "http://127.0.0.1:8000",
    prompts: List[Dict[str, str]] | None = None,
    run_label: Optional[str] = None,
    eval_harness_config_override: Optional[Dict[str, Any]] = None,
    progress_fn=None,
) -> Dict[str, Any]:
    """
    @summary Execute the benchmark suite and return a structured report.
    @inputs api_url: running Strata API base URL, prompts: optional prompt definitions
    @returns benchmark report with paired samples and aggregate metrics
    @side_effects issues live model requests to the weak tier, harness API, and strong judge
    """
    _load_env_file()
    prompts = prompts or DEFAULT_PROMPTS
    run_label = run_label or f"run-{int(time.time() * 1000)}"
    baseline_adapter = ModelAdapter()
    judge_adapter = ModelAdapter()

    samples: List[BenchmarkSample] = []
    harness_wins = 0
    baseline_wins = 0
    ties = 0

    def _progress(label: str, detail: str = "", progress_label: str = "benchmark") -> None:
        if progress_fn:
            progress_fn(
                step="system_job",
                label=label,
                detail=detail,
                progress_label=progress_label,
            )

    for idx, prompt_def in enumerate(prompts, start=1):
        prompt = prompt_def["prompt"]
        prompt_id = prompt_def["id"]
        sample_label = f"{idx}/{len(prompts)} · {prompt_id}"
        try:
            _progress("Running benchmark sample", f"{sample_label} · baseline", f"sample {idx}/{len(prompts)}")
            baseline_response, baseline_latency = await _run_direct_baseline(baseline_adapter, prompt)
            _progress("Running benchmark sample", f"{sample_label} · harness", f"sample {idx}/{len(prompts)}")
            harness_response, harness_latency, _ = await run_harness_response(
                prompt,
                run_id=f"benchmark-{run_label}-{prompt_id}-{idx}",
                config_override=eval_harness_config_override,
                profile="harness_no_capes",
            )
            _progress("Running benchmark sample", f"{sample_label} · judge", f"sample {idx}/{len(prompts)}")
            judgment = await _judge_pair(judge_adapter, prompt, baseline_response, harness_response)
        except Exception as exc:
            raise RuntimeError(f"Benchmark sample failed at {sample_label}: {exc}") from exc

        winner = judgment.get("winner", "tie")
        if winner == "harness":
            harness_wins += 1
        elif winner == "baseline":
            baseline_wins += 1
        else:
            ties += 1

        samples.append(
            BenchmarkSample(
                prompt_id=prompt_id,
                prompt=prompt,
                baseline_response=baseline_response,
                harness_response=harness_response,
                baseline_latency_s=round(baseline_latency, 2),
                harness_latency_s=round(harness_latency, 2),
                winner=winner,
                baseline_score=float(judgment.get("baseline_score", 0.0)),
                harness_score=float(judgment.get("harness_score", 0.0)),
                rationale=str(judgment.get("rationale", "")),
            )
        )
        _progress(
            "Completed benchmark sample",
            f"{sample_label} · winner={winner}",
            f"sample {idx}/{len(prompts)}",
        )

    average_baseline_score = sum(sample.baseline_score for sample in samples) / len(samples)
    average_harness_score = sum(sample.harness_score for sample in samples) / len(samples)

    return {
        "run_label": run_label,
        "prompt_count": len(samples),
        "baseline_wins": baseline_wins,
        "harness_wins": harness_wins,
        "ties": ties,
        "average_baseline_score": round(average_baseline_score, 2),
        "average_harness_score": round(average_harness_score, 2),
        "samples": [asdict(sample) for sample in samples],
    }


def persist_benchmark_report(
    storage,
    report: Dict[str, Any],
    *,
    candidate_change_id: Optional[str],
    run_mode: str,
    model_id: str,
    variant_assignment: Optional[Dict[str, Any]] = None,
) -> None:
    """
    @summary Persist benchmark aggregates and per-sample scores as structured metrics.
    @inputs storage: StorageManager, report: benchmark report dict
    @returns none
    @side_effects writes benchmark metrics into the metrics table
    """
    prompt_count = max(1, int(report.get("prompt_count", 0) or 0))
    baseline_wins = int(report.get("baseline_wins", 0) or 0)
    harness_wins = int(report.get("harness_wins", 0) or 0)
    ties = int(report.get("ties", 0) or 0)
    avg_baseline = float(report.get("average_baseline_score", 0.0) or 0.0)
    avg_harness = float(report.get("average_harness_score", 0.0) or 0.0)

    aggregate_metrics = [
        ("benchmark_baseline_score", avg_baseline, {"scope": "aggregate"}),
        ("benchmark_harness_score", avg_harness, {"scope": "aggregate"}),
        ("benchmark_baseline_win_rate", baseline_wins / prompt_count, {"scope": "aggregate", "wins": baseline_wins}),
        ("benchmark_harness_win_rate", harness_wins / prompt_count, {"scope": "aggregate", "wins": harness_wins}),
        ("benchmark_tie_rate", ties / prompt_count, {"scope": "aggregate", "ties": ties}),
        ("benchmark_score_delta", avg_harness - avg_baseline, {"scope": "aggregate"}),
    ]
    variant_assignment = dict(variant_assignment or {})

    for metric_name, value, details in aggregate_metrics:
        record_metric(
            storage,
            metric_name=metric_name,
            value=float(value),
            model_id=model_id,
            task_type="BENCHMARK",
            run_mode=run_mode,
            execution_context="agent",
            candidate_change_id=candidate_change_id,
            details={**details, "variant_assignment": variant_assignment},
        )

    for sample in report.get("samples", []):
        winner = sample.get("winner", "tie")
        details = {
            "scope": "sample",
            "run_label": report.get("run_label"),
            "prompt_id": sample.get("prompt_id"),
            "winner": winner,
            "baseline_latency_s": sample.get("baseline_latency_s"),
            "harness_latency_s": sample.get("harness_latency_s"),
            "rationale": sample.get("rationale", ""),
            "variant_assignment": variant_assignment,
        }
        record_metric(
            storage,
            metric_name="benchmark_sample_delta",
            value=float(sample.get("harness_score", 0.0) or 0.0) - float(sample.get("baseline_score", 0.0) or 0.0),
            model_id=model_id,
            task_type="BENCHMARK",
            run_mode=run_mode,
            execution_context="agent",
            candidate_change_id=candidate_change_id,
            details=details,
        )


def main() -> None:
    report = asyncio.run(run_benchmark())
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
