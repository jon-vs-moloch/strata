"""
@module api.eval_routes
@purpose Register eval execution and telemetry routes.

These routes cover running evals and exposing eval-adjacent telemetry. They are
separate from experiment promotion and proposal governance so small models can
inspect the eval surface without also loading the self-improvement control loop.
"""

from __future__ import annotations

import time
import json
from pathlib import Path
from typing import Any, Dict

from fastapi import Depends, HTTPException

from strata.eval.benchmark import persist_benchmark_report, run_benchmark
from strata.eval.structured_eval import persist_structured_eval_report, run_structured_eval
from strata.eval.harness_eval import get_active_eval_harness_config
from strata.eval.matrix import run_eval_matrix
from strata.experimental.promotion_policy import get_promotion_policy
from strata.experimental.variants import ensure_variant
from strata.orchestrator.worker.telemetry import record_metric


def register_eval_routes(
    app,
    *,
    get_storage,
    queue_eval_system_job,
    build_dashboard_snapshot,
    get_provider_telemetry_snapshot,
) -> Dict[str, Any]:
    exported: Dict[str, Any] = {}
    suites_dir = Path("strata/eval/suites")

    def _resolve_runtime_variant_assignment(storage, *, candidate_change_id: str, eval_harness_config_override: Dict[str, Any] | None = None) -> Dict[str, Any]:
        eval_harness_payload = dict(eval_harness_config_override or get_active_eval_harness_config())
        promotion_policy_payload = get_promotion_policy(storage)
        eval_harness_variant = ensure_variant(
            storage,
            kind="eval_harness_bundle",
            payload=eval_harness_payload,
            label=candidate_change_id,
            family="eval_harness",
            metadata={"source": "eval_routes"},
        )
        promotion_policy_variant = ensure_variant(
            storage,
            kind="promotion_policy_bundle",
            payload=promotion_policy_payload,
            label=f"{candidate_change_id}_promotion_policy",
            family="promotion_policy",
            metadata={"source": "eval_routes"},
        )
        return {
            "candidate_variant_id": eval_harness_variant.get("variant_id"),
            "candidate_promotion_policy_variant_id": promotion_policy_variant.get("variant_id"),
            "candidate_promotion_policy_payload": promotion_policy_payload,
        }

    def _list_eval_suites() -> list[Dict[str, Any]]:
        suites = []
        for path in sorted(suites_dir.glob("*.jsonl")):
            try:
                lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            except Exception:
                continue
            first = json.loads(lines[0]) if lines else {}
            suites.append(
                {
                    "suite_name": path.stem,
                    "case_count": len(lines),
                    "grading": first.get("grading", "unknown"),
                    "benchmark": first.get("benchmark"),
                    "source_dataset": first.get("source_dataset"),
                }
            )
        return suites

    @app.get("/admin/telemetry")
    async def get_telemetry(limit: int = 25, storage=Depends(get_storage)):
        safe_limit = max(1, min(limit, 100))
        from strata.orchestrator.worker.telemetry import build_telemetry_snapshot

        return {"status": "ok", "telemetry": build_telemetry_snapshot(storage, limit=safe_limit)}

    @app.get("/admin/dashboard")
    async def get_dashboard(limit: int = 10, storage=Depends(get_storage)):
        safe_limit = max(1, min(limit, 50))
        return {"status": "ok", "dashboard": build_dashboard_snapshot(storage, limit=safe_limit)}

    @app.get("/admin/providers/telemetry")
    async def get_provider_telemetry(storage=Depends(get_storage)):
        providers = get_provider_telemetry_snapshot()
        if providers:
            storage.parameters.set_parameter(
                key="provider_transport_telemetry_snapshot",
                value=providers,
                description="Last persisted provider transport telemetry snapshot.",
            )
            storage.commit()
            return {"status": "ok", "providers": providers, "source": "live"}

        persisted = storage.parameters.get_parameter(
            key="provider_transport_telemetry_snapshot",
            default_value={},
            description="Last persisted provider transport telemetry snapshot.",
        ) or {}
        return {"status": "ok", "providers": persisted, "source": "persisted"}

    @app.get("/admin/evals/suites")
    async def list_eval_suites():
        return {"status": "ok", "suites": _list_eval_suites()}

    @app.post("/admin/benchmark/run")
    async def run_benchmark_suite(payload: Dict[str, Any] | None = None, storage=Depends(get_storage)):
        payload = payload or {}
        candidate_change_id = payload.get("candidate_change_id", "baseline")
        if payload.get("queue"):
            queued = await queue_eval_system_job(
                storage,
                kind="benchmark",
                title=f"Benchmark Eval: {candidate_change_id}",
                description=f"Queued benchmark run for candidate '{candidate_change_id}'.",
                payload=payload,
                session_id=payload.get("session_id"),
                dedupe_signature={"candidate_change_id": candidate_change_id},
            )
            return {"status": "ok", **queued}
        api_url = payload.get("api_url", "http://127.0.0.1:8000")
        run_count = max(1, int(payload.get("run_count", 1) or 1))
        eval_harness_config_override = payload.get("eval_harness_config_override")
        variant_assignment = _resolve_runtime_variant_assignment(
            storage,
            candidate_change_id=candidate_change_id,
            eval_harness_config_override=eval_harness_config_override,
        )
        reports = []
        for run_index in range(run_count):
            report = await run_benchmark(
                api_url=api_url,
                run_label=f"{candidate_change_id}-benchmark-{run_index + 1}",
                eval_harness_config_override=eval_harness_config_override,
            )
            persist_benchmark_report(
                storage,
                report,
                candidate_change_id=candidate_change_id,
                run_mode="weak_eval" if candidate_change_id != "baseline" else "baseline",
                model_id="benchmark/harness",
                variant_assignment=variant_assignment,
            )
            reports.append(report)
        return {"status": "ok", "reports": reports, "candidate_change_id": candidate_change_id, "run_count": run_count}

    @app.post("/admin/evals/run")
    async def run_structured_eval_suite(payload: Dict[str, Any] | None = None, storage=Depends(get_storage)):
        payload = payload or {}
        candidate_change_id = payload.get("candidate_change_id", "baseline")
        if payload.get("queue"):
            queued = await queue_eval_system_job(
                storage,
                kind="structured_eval",
                title=f"Structured Eval: {candidate_change_id}",
                description=f"Queued structured eval run for candidate '{candidate_change_id}'.",
                payload=payload,
                session_id=payload.get("session_id"),
                dedupe_signature={
                    "candidate_change_id": candidate_change_id,
                    "suite_name": payload.get("suite_name", "bootstrap_mcq_v1"),
                },
            )
            return {"status": "ok", **queued}
        suite_name = payload.get("suite_name", "bootstrap_mcq_v1")
        api_url = payload.get("api_url", "http://127.0.0.1:8000")
        cases = payload.get("cases")
        run_count = max(1, int(payload.get("run_count", 1) or 1))
        eval_harness_config_override = payload.get("eval_harness_config_override")
        variant_assignment = _resolve_runtime_variant_assignment(
            storage,
            candidate_change_id=candidate_change_id,
            eval_harness_config_override=eval_harness_config_override,
        )
        reports = []
        for run_index in range(run_count):
            report = await run_structured_eval(
                api_url=api_url,
                suite_name=suite_name,
                cases=cases,
                run_label=f"{candidate_change_id}-structured-{run_index + 1}",
                eval_harness_config_override=eval_harness_config_override,
            )
            persist_structured_eval_report(
                storage,
                report,
                candidate_change_id=candidate_change_id,
                run_mode="weak_eval" if candidate_change_id != "baseline" else "baseline",
                model_id="structured_eval/harness",
                variant_assignment=variant_assignment,
            )
            reports.append(report)
        return {"status": "ok", "reports": reports, "candidate_change_id": candidate_change_id, "run_count": run_count}

    @app.post("/admin/evals/matrix")
    async def run_eval_matrix_suite(payload: Dict[str, Any] | None = None, storage=Depends(get_storage)):
        payload = payload or {}
        if payload.get("queue"):
            queued = await queue_eval_system_job(
                storage,
                kind="eval_matrix",
                title=f"Eval Matrix: {payload.get('suite_name', 'mmlu_mini_v1')}",
                description="Queued eval matrix run.",
                payload={**payload, "sampled": False},
                session_id=payload.get("session_id"),
                dedupe_signature={
                    "suite_name": payload.get("suite_name", "mmlu_mini_v1"),
                    "include_context": bool(payload.get("include_context", True)),
                    "include_strong": bool(payload.get("include_strong", True)),
                    "include_weak": bool(payload.get("include_weak", True)),
                },
            )
            return {"status": "ok", **queued}
        suite_name = payload.get("suite_name", "mmlu_mini_v1")
        include_context = bool(payload.get("include_context", True))
        include_strong = bool(payload.get("include_strong", True))
        include_weak = bool(payload.get("include_weak", True))
        profiles = payload.get("profiles")
        sample_size = payload.get("sample_size")
        random_seed = payload.get("random_seed")
        report = await run_eval_matrix(
            suite_name=suite_name,
            include_context=include_context,
            include_strong=include_strong,
            include_weak=include_weak,
            profiles=profiles,
            sample_size=int(sample_size) if sample_size is not None else None,
            random_seed=int(random_seed) if random_seed is not None else None,
        )
        current_eval_variant = _resolve_runtime_variant_assignment(
            storage,
            candidate_change_id=f"matrix_{suite_name}",
            eval_harness_config_override=None,
        )
        for variant in report.get("variants", []):
            details = {
                "suite_name": suite_name,
                "variant_id": variant.get("variant_id"),
                "mode": variant.get("mode"),
                "profile": variant.get("profile"),
                "include_context": include_context,
                "case_count": report.get("case_count"),
                "variant_assignment": current_eval_variant if str(variant.get("profile")) != "raw_model" else {},
            }
            for metric_name, value in (
                ("eval_matrix_accuracy", float(variant.get("accuracy", 0.0) or 0.0)),
                ("eval_matrix_latency_s", float(variant.get("avg_latency_s", 0.0) or 0.0)),
                ("eval_matrix_prompt_tokens", float(variant.get("prompt_tokens", 0) or 0.0)),
                ("eval_matrix_completion_tokens", float(variant.get("completion_tokens", 0) or 0.0)),
                ("eval_matrix_total_tokens", float(variant.get("total_tokens", 0) or 0.0)),
            ):
                record_metric(
                    storage,
                    metric_name=metric_name,
                    value=value,
                    model_id=variant.get("variant_id"),
                    task_type="EVAL_MATRIX",
                    run_mode="eval_matrix",
                    execution_context=variant.get("mode"),
                    details=details,
                )
        storage.commit()
        return {"status": "ok", "report": report}

    @app.post("/admin/evals/sample_tick")
    async def run_sampled_eval_tick(payload: Dict[str, Any] | None = None, storage=Depends(get_storage)):
        payload = payload or {}
        if payload.get("queue"):
            queued = await queue_eval_system_job(
                storage,
                kind="eval_matrix",
                title=f"Sampled Eval Tick: {payload.get('suite_name', 'mmlu_mini_v1')}",
                description="Queued sampled eval tick.",
                payload={**payload, "sampled": True},
                session_id=payload.get("session_id"),
                dedupe_signature={"suite_name": payload.get("suite_name", "mmlu_mini_v1"), "sampled": True},
            )
            return {"status": "ok", **queued}
        suite_name = payload.get("suite_name", "mmlu_mini_v1")
        sample_size = max(1, int(payload.get("sample_size", 2) or 2))
        include_context = bool(payload.get("include_context", False))
        profiles = payload.get(
            "profiles",
            ["raw_model", "harness_no_capes", "harness_tools_no_web", "harness_web_no_tools", "harness_tools_web"],
        )
        report = await run_eval_matrix(
            suite_name=suite_name,
            include_context=include_context,
            include_strong=bool(payload.get("include_strong", True)),
            include_weak=bool(payload.get("include_weak", True)),
            profiles=profiles,
            sample_size=sample_size,
            random_seed=int(time.time()),
        )
        current_eval_variant = _resolve_runtime_variant_assignment(
            storage,
            candidate_change_id=f"sample_tick_{suite_name}",
            eval_harness_config_override=None,
        )
        for variant in report.get("variants", []):
            details = {
                "suite_name": suite_name,
                "variant_id": variant.get("variant_id"),
                "mode": variant.get("mode"),
                "profile": variant.get("profile"),
                "include_context": include_context,
                "case_count": report.get("case_count"),
                "sampled": True,
                "variant_assignment": current_eval_variant if str(variant.get("profile")) != "raw_model" else {},
            }
            record_metric(
                storage,
                metric_name="eval_sample_tick_accuracy",
                value=float(variant.get("accuracy", 0.0) or 0.0),
                model_id=variant.get("variant_id"),
                task_type="EVAL_SAMPLE_TICK",
                run_mode="eval_sample_tick",
                execution_context=variant.get("mode"),
                details=details,
            )
            record_metric(
                storage,
                metric_name="eval_sample_tick_latency_s",
                value=float(variant.get("avg_latency_s", 0.0) or 0.0),
                model_id=variant.get("variant_id"),
                task_type="EVAL_SAMPLE_TICK",
                run_mode="eval_sample_tick",
                execution_context=variant.get("mode"),
                details=details,
            )
            record_metric(
                storage,
                metric_name="eval_sample_tick_total_tokens",
                value=float(variant.get("total_tokens", 0.0) or 0.0),
                model_id=variant.get("variant_id"),
                task_type="EVAL_SAMPLE_TICK",
                run_mode="eval_sample_tick",
                execution_context=variant.get("mode"),
                details=details,
            )
        storage.commit()
        return {"status": "ok", "report": report}

    @app.get("/admin/evals/jobs/{task_id}")
    async def get_eval_job(task_id: str, storage=Depends(get_storage)):
        task = storage.tasks.get_by_id(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        system_job = dict((task.constraints or {}).get("system_job") or {})
        if not system_job:
            raise HTTPException(status_code=400, detail="Task is not an eval/system job")
        return {
            "status": "ok",
            "task": {
                "task_id": task.task_id,
                "title": task.title,
                "state": task.state.value,
                "type": task.type.value if hasattr(task.type, "value") else str(task.type),
                "system_job": system_job,
                "system_job_result": (task.constraints or {}).get("system_job_result"),
            },
        }

    exported.update(
        {
            "get_telemetry": get_telemetry,
            "get_dashboard": get_dashboard,
            "get_provider_telemetry": get_provider_telemetry,
            "list_eval_suites": list_eval_suites,
            "run_benchmark_suite": run_benchmark_suite,
            "run_structured_eval_suite": run_structured_eval_suite,
            "run_eval_matrix_suite": run_eval_matrix_suite,
            "run_sampled_eval_tick": run_sampled_eval_tick,
            "get_eval_job": get_eval_job,
        }
    )
    return exported
