"""
@module orchestrator.eval_jobs
@purpose Execute queued eval/system jobs through the background worker.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict

from strata.eval.benchmark import persist_benchmark_report, run_benchmark
from strata.eval.matrix import run_eval_matrix
from strata.eval.structured_eval import persist_structured_eval_report, run_structured_eval
from strata.experimental.experiment_runner import ExperimentRunner
from strata.orchestrator.worker.telemetry import record_metric


def _stamp_system_job_result(task, result: Dict[str, Any]) -> None:
    constraints = dict(task.constraints or {})
    constraints["system_job_result"] = result
    history = list(constraints.get("generated_reports") or [])
    history.append(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "kind": result.get("kind"),
            "summary": result.get("summary") or {},
        }
    )
    constraints["generated_reports"] = history[-25:]
    task.constraints = constraints


async def run_system_job(task, storage, model_adapter) -> Dict[str, Any]:
    system_job = dict((task.constraints or {}).get("system_job") or {})
    kind = str(system_job.get("kind") or "").strip()
    payload = dict(system_job.get("payload") or {})
    if not kind:
        raise ValueError("System job missing kind")

    if kind == "benchmark":
        candidate_change_id = str(payload.get("candidate_change_id") or "baseline")
        report = await run_benchmark(
            api_url=str(payload.get("api_url") or "http://127.0.0.1:8000"),
            run_label=str(payload.get("run_label") or f"{candidate_change_id}-benchmark-bg"),
            eval_harness_config_override=payload.get("eval_harness_config_override"),
        )
        persist_benchmark_report(
            storage,
            report,
            candidate_change_id=candidate_change_id,
            run_mode="weak_eval" if candidate_change_id != "baseline" else "baseline",
            model_id="benchmark/harness",
        )
        result = {
            "kind": kind,
            "candidate_change_id": candidate_change_id,
            "report": report,
            "summary": {
                "candidate_change_id": candidate_change_id,
                "run_label": report.get("run_label"),
                "harness_wins": report.get("harness_wins"),
                "baseline_wins": report.get("baseline_wins"),
            },
        }
        _stamp_system_job_result(task, result)
        return result

    if kind == "benchmark_gate":
        runner = ExperimentRunner(storage, model_adapter)
        candidate_change_id = str(payload.get("candidate_change_id") or "")
        result_obj = await runner.run_benchmark_gate(
            candidate_change_id,
            api_url=str(payload.get("api_url") or "http://127.0.0.1:8000"),
            baseline_change_id=str(payload.get("baseline_change_id") or "baseline"),
            run_count=max(1, int(payload.get("run_count", 1) or 1)),
            eval_harness_config_override=payload.get("eval_harness_config_override"),
            proposal_metadata=payload.get("proposal_metadata"),
            source_task_id=task.task_id,
            associated_task_ids=[task.task_id, *(payload.get("associated_task_ids") or [])],
        )
        result = {
            "kind": kind,
            "candidate_change_id": candidate_change_id,
            "report": result_obj.model_dump(),
            "summary": {
                "candidate_change_id": candidate_change_id,
                "recommendation": result_obj.recommendation,
            },
        }
        _stamp_system_job_result(task, result)
        return result

    if kind == "structured_eval":
        candidate_change_id = str(payload.get("candidate_change_id") or "baseline")
        report = await run_structured_eval(
            api_url=str(payload.get("api_url") or "http://127.0.0.1:8000"),
            suite_name=str(payload.get("suite_name") or "bootstrap_mcq_v1"),
            cases=payload.get("cases"),
            run_label=str(payload.get("run_label") or f"{candidate_change_id}-structured-bg"),
            eval_harness_config_override=payload.get("eval_harness_config_override"),
        )
        persist_structured_eval_report(
            storage,
            report,
            candidate_change_id=candidate_change_id,
            run_mode="weak_eval" if candidate_change_id != "baseline" else "baseline",
            model_id="structured_eval/harness",
        )
        result = {
            "kind": kind,
            "candidate_change_id": candidate_change_id,
            "report": report,
            "summary": {
                "candidate_change_id": candidate_change_id,
                "suite_name": report.get("suite_name"),
                "harness_accuracy": report.get("harness_accuracy"),
            },
        }
        _stamp_system_job_result(task, result)
        return result

    if kind == "eval_matrix":
        suite_name = str(payload.get("suite_name") or "mmlu_mini_v1")
        sampled = bool(payload.get("sampled", False))
        report = await run_eval_matrix(
            suite_name=suite_name,
            include_context=bool(payload.get("include_context", not sampled)),
            include_strong=bool(payload.get("include_strong", True)),
            include_weak=bool(payload.get("include_weak", True)),
            profiles=payload.get("profiles"),
            sample_size=int(payload.get("sample_size")) if payload.get("sample_size") is not None else (2 if sampled else None),
            random_seed=int(payload.get("random_seed")) if payload.get("random_seed") is not None else (int(time.time()) if sampled else None),
        )
        for variant in report.get("variants", []):
            details = {
                "suite_name": suite_name,
                "variant_id": variant.get("variant_id"),
                "mode": variant.get("mode"),
                "profile": variant.get("profile"),
                "include_context": report.get("include_context"),
                "case_count": report.get("case_count"),
                "sampled": sampled,
                "task_id": task.task_id,
            }
            metric_prefix = "eval_sample_tick" if sampled else "eval_matrix"
            task_type = "EVAL_SAMPLE_TICK" if sampled else "EVAL_MATRIX"
            for metric_name, value in (
                (f"{metric_prefix}_accuracy", float(variant.get("accuracy", 0.0) or 0.0)),
                (f"{metric_prefix}_latency_s", float(variant.get("avg_latency_s", 0.0) or 0.0)),
                (f"{metric_prefix}_prompt_tokens", float(variant.get("prompt_tokens", 0.0) or 0.0)),
                (f"{metric_prefix}_completion_tokens", float(variant.get("completion_tokens", 0.0) or 0.0)),
                (f"{metric_prefix}_total_tokens", float(variant.get("total_tokens", 0.0) or 0.0)),
            ):
                record_metric(
                    storage,
                    metric_name=metric_name,
                    value=value,
                    model_id=variant.get("variant_id"),
                    task_type=task_type,
                    run_mode=metric_prefix,
                    execution_context=variant.get("mode"),
                    task_id=task.task_id,
                    details=details,
                )
        result = {
            "kind": kind,
            "report": report,
            "summary": {
                "suite_name": suite_name,
                "variant_count": len(report.get("variants") or []),
                "sampled": sampled,
            },
        }
        _stamp_system_job_result(task, result)
        return result

    if kind == "full_eval":
        runner = ExperimentRunner(storage, model_adapter)
        candidate_change_id = str(payload.get("candidate_change_id") or "")
        result_obj = await runner.run_full_eval_gate(
            candidate_change_id,
            api_url=str(payload.get("api_url") or "http://127.0.0.1:8000"),
            baseline_change_id=str(payload.get("baseline_change_id") or "baseline"),
            suite_name=str(payload.get("suite_name") or "bootstrap_mcq_v1"),
            run_count=max(1, int(payload.get("run_count", 1) or 1)),
            eval_harness_config_override=payload.get("eval_harness_config_override"),
            proposal_metadata=payload.get("proposal_metadata"),
            source_task_id=task.task_id,
            associated_task_ids=[task.task_id, *(payload.get("associated_task_ids") or [])],
        )
        result = {
            "kind": kind,
            "candidate_change_id": candidate_change_id,
            "report": result_obj.model_dump(),
            "summary": {
                "candidate_change_id": candidate_change_id,
                "recommendation": result_obj.recommendation,
            },
        }
        _stamp_system_job_result(task, result)
        return result

    raise NotImplementedError(f"Unsupported system job kind: {kind}")
