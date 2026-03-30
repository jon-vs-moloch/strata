"""
@module eval.job_runner
@purpose Execute queued evaluation jobs through the background worker instead of blocking API requests.
"""

from __future__ import annotations

import time
import os
import json
import asyncio
from datetime import datetime
from typing import Any, Dict

from strata.api.experiment_runtime import (
    canonical_eval_override,
    eval_override_signature,
    get_active_eval_proposal_config,
    summarize_recent_eval_candidates,
)
from strata.eval.benchmark import persist_benchmark_report, run_benchmark
from strata.eval.matrix import run_eval_matrix
from strata.eval.harness_eval import get_active_eval_harness_config
from strata.eval.structured_eval import persist_structured_eval_report, run_structured_eval
from strata.experimental.experiment_runner import ExperimentRunner, iter_experiment_reports
from strata.experimental.artifact_pipeline import (
    append_trace_review_to_session,
    enqueue_review_followups,
    persist_trace_review_artifacts,
)
from strata.experimental.trace_review import (
    append_trace_review_to_task,
    build_trace_summary,
    emit_trace_review_attention_signal,
    review_trace,
)
from strata.knowledge.pages import KnowledgePageStore
from strata.orchestrator.tools_pipeline import ToolsPromotionPipeline
from strata.orchestrator.user_questions import enqueue_user_question, get_question_for_source
from strata.orchestrator.worker.telemetry import record_metric
from strata.storage.models import AttemptOutcome, TaskState


def _trim_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    trimmed = dict(payload)
    if isinstance(trimmed.get("samples"), list):
        trimmed["samples_preview"] = trimmed["samples"][:3]
        trimmed["sample_count"] = len(trimmed["samples"])
        trimmed.pop("samples", None)
    if isinstance(trimmed.get("variants"), list):
        trimmed["variants_preview"] = [
            {
                key: variant.get(key)
                for key in (
                    "variant_id",
                    "mode",
                    "profile",
                    "accuracy",
                    "error_count",
                    "error_rate",
                    "degraded_count",
                    "degraded_rate",
                    "avg_latency_s",
                    "total_tokens",
                    "case_count",
                )
            }
            for variant in trimmed["variants"][:6]
        ]
        trimmed["variant_count"] = len(trimmed["variants"])
        trimmed.pop("variants", None)
    return trimmed


def _review_needs_user_clarification(review: Dict[str, Any]) -> bool:
    assessment = str(review.get("overall_assessment") or "").strip().lower()
    primary_failure_mode = str(review.get("primary_failure_mode") or "").strip().lower()
    if assessment == "review_unavailable":
        return True
    if primary_failure_mode in {"uncorrected_verifier_failures", "trainer_supervision_gap"}:
        return True
    text_bits = [
        str(review.get("summary") or ""),
        " ".join(str((item or {}).get("description") or "") for item in review.get("targeted_interventions") or []),
    ]
    haystack = " ".join(bit.strip().lower() for bit in text_bits if bit.strip())
    return "user-facing clarification" in haystack or "needs user input" in haystack or "clarification" in haystack


def _maybe_enqueue_blocked_task_question_from_review(storage, *, task_id: str, review: Dict[str, Any]) -> None:
    normalized_task_id = str(task_id or "").strip()
    if not normalized_task_id:
        return
    task = storage.tasks.get_by_id(normalized_task_id)
    if not task:
        return
    if task.state != TaskState.BLOCKED or not bool(task.human_intervention_required):
        return
    if get_question_for_source(storage, source_type="task_blocked", source_id=task.task_id):
        return
    if not _review_needs_user_clarification(review):
        return
    summary = str(review.get("summary") or "Trainer supervision could not fully resolve the blocked branch.").strip()
    question = (
        f"I’m still blocked on '{task.title}' and need your guidance before proceeding. "
        f"Trainer review summary: {summary}"
    )
    enqueue_user_question(
        storage,
        session_id=task.session_id or "default",
        question=question,
        source_type="task_blocked",
        source_id=task.task_id,
        context={
            "task_id": task.task_id,
            "title": task.title,
            "reasoning": summary,
            "trace_review_failure_mode": review.get("primary_failure_mode"),
            "trace_review_assessment": review.get("overall_assessment"),
        },
    )


async def run_eval_job_task(task, storage, model_adapter, progress_fn=None) -> Dict[str, Any]:
    system_job = dict((task.constraints or {}).get("system_job") or {})
    kind = str(system_job.get("kind") or "").strip()
    payload = dict(system_job.get("payload") or {})
    if not kind:
        raise ValueError("Task is missing system_job.kind")

    for prior_attempt in storage.attempts.get_by_task_id(task.task_id):
        if prior_attempt.outcome is None and prior_attempt.ended_at is None:
            storage.attempts.update_outcome(
                prior_attempt.attempt_id,
                AttemptOutcome.CANCELLED,
                reason="Superseded by a newer eval-job attempt.",
            )

    attempt = storage.attempts.create(task_id=task.task_id)
    task.state = TaskState.WORKING
    storage.commit()
    started_at = time.perf_counter()

    def _progress(*, step: str, label: str, detail: str = "", progress_label: str = "") -> None:
        if progress_fn:
            progress_fn(
                step=str(step or "").strip().lower() or "system_job",
                label=str(label or "").strip() or "Working",
                detail=str(detail or "").strip(),
                progress_label=str(progress_label or "").strip(),
                attempt_id=attempt.attempt_id,
            )

    _progress(step="attempt", label="Attempt running", detail=f"{kind} attempt started", progress_label="attempt active")

    try:
        if kind == "benchmark":
            _progress(step="system_job", label="Running benchmark", detail=str(payload.get("run_label") or "benchmark harness"), progress_label="benchmark")
            candidate_change_id = payload.get("candidate_change_id", "baseline")
            report = await run_benchmark(
                api_url=payload.get("api_url", "http://127.0.0.1:8000"),
                run_label=payload.get("run_label") or f"{candidate_change_id}-queued",
                eval_harness_config_override=payload.get("eval_harness_config_override"),
            )
            persist_benchmark_report(
                storage,
                report,
                candidate_change_id=candidate_change_id,
                run_mode="weak_eval" if candidate_change_id != "baseline" else "baseline",
                model_id="benchmark/harness",
            )
            result = report
        elif kind == "structured_eval":
            _progress(step="system_job", label="Running structured eval", detail=str(payload.get("suite_name") or "bootstrap_mcq_v1"), progress_label="structured eval")
            candidate_change_id = payload.get("candidate_change_id", "baseline")
            report = await run_structured_eval(
                api_url=payload.get("api_url", "http://127.0.0.1:8000"),
                suite_name=payload.get("suite_name", "bootstrap_mcq_v1"),
                cases=payload.get("cases"),
                run_label=payload.get("run_label") or f"{candidate_change_id}-queued",
                eval_harness_config_override=payload.get("eval_harness_config_override"),
            )
            persist_structured_eval_report(
                storage,
                report,
                candidate_change_id=candidate_change_id,
                run_mode="weak_eval" if candidate_change_id != "baseline" else "baseline",
                model_id="structured_eval/harness",
            )
            result = report
        elif kind == "full_eval":
            _progress(step="system_job", label="Running full eval", detail=str(payload.get("candidate_change_id") or "candidate"), progress_label="full eval")
            runner = ExperimentRunner(storage, model_adapter)
            experiment_result = await runner.run_full_eval_gate(
                payload["candidate_change_id"],
                api_url=payload.get("api_url", "http://127.0.0.1:8000"),
                baseline_change_id=payload.get("baseline_change_id", "baseline"),
                suite_name=payload.get("suite_name", "bootstrap_mcq_v1"),
                run_count=max(1, int(payload.get("run_count", 1) or 1)),
                eval_harness_config_override=payload.get("eval_harness_config_override"),
                proposal_metadata=payload.get("proposal_metadata"),
                source_task_id=task.task_id,
                spawned_task_ids=payload.get("spawned_task_ids"),
                associated_task_ids=payload.get("associated_task_ids"),
            )
            result = experiment_result.model_dump()
        elif kind == "benchmark_gate":
            _progress(step="system_job", label="Running benchmark gate", detail=str(payload.get("candidate_change_id") or "candidate"), progress_label="benchmark gate")
            runner = ExperimentRunner(storage, model_adapter)
            experiment_result = await runner.run_benchmark_gate(
                payload["candidate_change_id"],
                api_url=payload.get("api_url", "http://127.0.0.1:8000"),
                baseline_change_id=payload.get("baseline_change_id", "baseline"),
                run_count=max(1, int(payload.get("run_count", 1) or 1)),
                eval_harness_config_override=payload.get("eval_harness_config_override"),
                proposal_metadata=payload.get("proposal_metadata"),
                source_task_id=task.task_id,
                spawned_task_ids=payload.get("spawned_task_ids"),
                associated_task_ids=payload.get("associated_task_ids"),
            )
            result = experiment_result.model_dump()
        elif kind == "bootstrap_cycle":
            from strata.api.main import (
                _apply_experiment_promotion,
                _generate_eval_candidate_from_tier,
                _resolve_eval_proposal_against_history,
            )
            from strata.storage.models import ParameterModel

            proposal_config = get_active_eval_proposal_config()
            bootstrap_policy = dict(proposal_config.get("bootstrap") or {})
            proposer_tiers = [
                str(tier).lower()
                for tier in payload.get("proposer_tiers", bootstrap_policy.get("default_proposer_tiers", ["agent", "trainer"]))
            ]
            proposer_tiers = [tier for tier in proposer_tiers if tier in {"agent", "trainer"}]
            if not proposer_tiers:
                raise ValueError("bootstrap_cycle requires at least one proposer tier")

            auto_promote = bool(payload.get("auto_promote", True))
            suite_name = str(payload.get("suite_name") or "bootstrap_mcq_v1")
            run_count = max(1, int(payload.get("run_count", bootstrap_policy.get("default_run_count", 2)) or 1))
            baseline_change_id = str(payload.get("baseline_change_id") or "baseline")
            recent_reports = (
                storage.session.query(ParameterModel)
                .filter(ParameterModel.key.like("experiment_report:%"))
                .order_by(ParameterModel.updated_at.desc())
                .limit(int(bootstrap_policy.get("recent_report_window", 50) or 50))
                .all()
            )
            recent_report_payloads = list(iter_experiment_reports(recent_reports))
            recent_signatures = {
                eval_override_signature(report.get("eval_harness_config_override"))
                for report in recent_report_payloads
                if report.get("eval_harness_config_override")
            }
            recent_candidate_hints = summarize_recent_eval_candidates(
                recent_report_payloads,
                limit=int(bootstrap_policy.get("recent_candidate_limit", 6) or 6),
            )
            current_config = get_active_eval_harness_config()
            current_signature = eval_override_signature(current_config)
            _progress(
                step="system_job",
                label="Generating proposals",
                detail=f"{len(proposer_tiers)} proposer tier(s), suite {suite_name}",
                progress_label="proposal generation",
            )
            proposals = await asyncio.gather(
                *[
                    _generate_eval_candidate_from_tier(
                        tier,
                        current_config,
                        recent_candidates=recent_candidate_hints,
                        recent_signatures=recent_signatures,
                        current_signature=current_signature,
                        proposal_config=proposal_config,
                    )
                    for tier in proposer_tiers
                ]
            )
            seen_signatures = set()
            seen_candidates: list[Dict[str, Any]] = []

            runner = ExperimentRunner(storage, model_adapter)
            evaluated = []
            promoted = []
            skipped = []

            for proposal in proposals:
                _progress(
                    step="system_job",
                    label="Reviewing proposal",
                    detail=str(proposal.get("candidate_change_id") or proposal.get("proposer_tier") or "candidate"),
                    progress_label="proposal review",
                )
                proposal_signature = eval_override_signature(proposal["eval_harness_config_override"])
                if proposal_signature in recent_signatures:
                    skipped.append({"proposal": proposal, "reason": "recent_duplicate_signature"})
                    continue
                resolution = await _resolve_eval_proposal_against_history(
                    proposal,
                    current_config=current_config,
                    recent_candidates=recent_candidate_hints,
                    seen_candidates=seen_candidates,
                    proposal_config=proposal_config,
                )
                if not resolution.get("should_evaluate", False):
                    skipped.append({"proposal": proposal, "reason": resolution.get("decision"), "resolution": resolution})
                    continue
                proposal_to_evaluate = dict(resolution.get("proposal") or proposal)
                proposal_signature = eval_override_signature(proposal_to_evaluate["eval_harness_config_override"])
                if proposal_signature in recent_signatures or proposal_signature in seen_signatures:
                    skipped.append({"proposal": proposal_to_evaluate, "reason": "merged_duplicate_signature", "resolution": resolution})
                    continue
                seen_signatures.add(proposal_signature)
                seen_candidates.append(
                    {
                        "candidate_change_id": proposal_to_evaluate.get("candidate_change_id"),
                        "proposer_tier": proposal_to_evaluate.get("proposer_tier"),
                        "rationale": proposal_to_evaluate.get("rationale"),
                        "expected_gain": proposal_to_evaluate.get("expected_gain"),
                        "eval_harness_config_override": canonical_eval_override(proposal_to_evaluate.get("eval_harness_config_override")),
                    }
                )
                _progress(
                    step="system_job",
                    label="Running eval gate",
                    detail=str(proposal_to_evaluate.get("candidate_change_id") or "candidate"),
                    progress_label="eval gate",
                )
                experiment_result = await runner.run_full_eval_gate(
                    proposal_to_evaluate["candidate_change_id"],
                    api_url=str(payload.get("api_url") or "http://127.0.0.1:8000"),
                    baseline_change_id=baseline_change_id,
                    suite_name=suite_name,
                    run_count=run_count,
                    eval_harness_config_override=proposal_to_evaluate["eval_harness_config_override"],
                    proposal_metadata={
                        "proposer_tier": proposal_to_evaluate["proposer_tier"],
                        "rationale": proposal_to_evaluate["rationale"],
                        "expected_gain": proposal_to_evaluate["expected_gain"],
                        "source": "bootstrap_cycle_queue",
                        "resolution": resolution,
                    },
                    source_task_id=task.task_id,
                    associated_task_ids=[task.task_id, *(payload.get("associated_task_ids") or [])],
                )
                evaluated.append({"proposal": proposal_to_evaluate, "result": experiment_result.model_dump(), "resolution": resolution})
                if auto_promote and experiment_result.recommendation == "promote":
                    _progress(
                        step="system_job",
                        label="Applying promotion",
                        detail=str(proposal_to_evaluate.get("candidate_change_id") or "candidate"),
                        progress_label="promotion",
                    )
                    promoted.append(_apply_experiment_promotion(storage, proposal_to_evaluate["candidate_change_id"], force=False))

            result = {
                "current_eval_harness_config": current_config,
                "evaluated": evaluated,
                "promoted": promoted,
                "skipped": skipped,
                "auto_promote": auto_promote,
            }
        elif kind == "eval_matrix":
            _progress(step="system_job", label="Running eval matrix", detail=str(payload.get("suite_name") or "mmlu_mini_v1"), progress_label="eval matrix")
            report = await run_eval_matrix(
                suite_name=payload.get("suite_name", "mmlu_mini_v1"),
                include_context=bool(payload.get("include_context", True)),
                include_strong=bool(payload.get("include_strong", True)),
                include_weak=bool(payload.get("include_weak", True)),
                profiles=payload.get("profiles"),
                sample_size=int(payload["sample_size"]) if payload.get("sample_size") is not None else None,
                random_seed=int(payload["random_seed"]) if payload.get("random_seed") is not None else None,
            )
            metric_name_prefix = "eval_sample_tick" if payload.get("sampled") else "eval_matrix"
            task_type = "EVAL_SAMPLE_TICK" if payload.get("sampled") else "EVAL_MATRIX"
            for variant in report.get("variants", []):
                details = {
                    "suite_name": report.get("suite_name"),
                    "variant_id": variant.get("variant_id"),
                    "mode": variant.get("mode"),
                    "profile": variant.get("profile"),
                    "include_context": report.get("include_context"),
                    "case_count": report.get("case_count"),
                    "sampled": bool(payload.get("sampled", False)),
                    "task_id": task.task_id,
                }
                for metric_name, value in (
                    (f"{metric_name_prefix}_accuracy", float(variant.get("accuracy", 0.0) or 0.0)),
                    (f"{metric_name_prefix}_error_rate", float(variant.get("error_rate", 0.0) or 0.0)),
                    (f"{metric_name_prefix}_degraded_rate", float(variant.get("degraded_rate", 0.0) or 0.0)),
                    (f"{metric_name_prefix}_latency_s", float(variant.get("avg_latency_s", 0.0) or 0.0)),
                    (f"{metric_name_prefix}_total_tokens", float(variant.get("total_tokens", 0.0) or 0.0)),
                ):
                    record_metric(
                        storage,
                        metric_name=metric_name,
                        value=value,
                        model_id=variant.get("variant_id"),
                        task_type=task_type,
                        run_mode="eval_sample_tick" if payload.get("sampled") else "eval_matrix",
                        execution_context=variant.get("mode"),
                        task_id=task.task_id,
                        details=details,
                    )
            result = report
        elif kind == "tool_cycle":
            _progress(step="system_job", label="Drafting tool candidate", detail=str(payload.get("tool_name") or "tool"), progress_label="tool proposal")
            tool_name = str(payload.get("tool_name") or "").strip()
            if not tool_name:
                raise ValueError("tool_cycle requires tool_name")
            proposer_tier = str(payload.get("proposer_tier") or "agent")
            from strata.api.main import _generate_tool_candidate_from_tier
            proposal = await _generate_tool_candidate_from_tier(
                proposer_tier,
                tool_name=tool_name,
                task_description=str(payload.get("task_description") or "Create a safe dynamic tool."),
            )
            os.makedirs("strata/tools", exist_ok=True)
            os.makedirs("strata/tools/manifests", exist_ok=True)
            os.makedirs("strata/tools/tests", exist_ok=True)
            experimental_path = os.path.join("strata/tools", f"{proposal['tool_name']}.experimental.py")
            manifest_path = os.path.join("strata/tools", "manifests", f"{proposal['tool_name']}.json")
            smoke_path = os.path.join("strata/tools", "tests", f"test_{proposal['tool_name']}_smoke.py")
            with open(experimental_path, "w", encoding="utf-8") as handle:
                handle.write(proposal["source"])
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "validator": (proposal["manifest"] or {}).get("validator", "python_import_only"),
                        "smoke_test": (proposal["manifest"] or {}).get("smoke_test", smoke_path),
                        "proposer_tier": proposal["proposer_tier"],
                    },
                    handle,
                    indent=2,
                )
            with open(smoke_path, "w", encoding="utf-8") as handle:
                handle.write(proposal["smoke_test"])
            validation = await ToolsPromotionPipeline(storage).validate_and_promote(proposal["tool_name"])
            _progress(step="system_job", label="Validating tool candidate", detail=str(proposal["tool_name"]), progress_label="tool validation")
            experiment_result = ExperimentRunner(storage, model_adapter).record_tool_promotion_result(
                candidate_change_id=proposal["candidate_change_id"],
                validation_result=validation.model_dump(),
                proposal_metadata={
                    "proposer_tier": proposal["proposer_tier"],
                    "tool_name": proposal["tool_name"],
                    "rationale": proposal["rationale"],
                    "expected_gain": proposal["expected_gain"],
                    "source": "tool_cycle_queue",
                },
                source_task_id=task.task_id,
                associated_task_ids=[task.task_id, *(payload.get("associated_task_ids") or [])],
            )
            result = {
                "proposal": proposal,
                "validation": validation.model_dump(),
                "result": experiment_result.model_dump(),
            }
        elif kind == "trace_review":
            _progress(step="system_job", label="Building trace summary", detail=str(payload.get("trace_kind") or "generic_trace"), progress_label="trace summary")
            trace_kind = str(payload.get("trace_kind") or "generic_trace")
            reviewer_tier = str(payload.get("reviewer_tier") or "trainer")
            spec_scope = str(payload.get("spec_scope") or "project")
            trace_summary = build_trace_summary(
                trace_kind=trace_kind,
                storage=storage,
                trace_payload=payload.get("trace_payload"),
                task_id=payload.get("task_id"),
                session_id=payload.get("session_id"),
                candidate_change_id=payload.get("candidate_change_id"),
                baseline_change_id=payload.get("baseline_change_id"),
                benchmark_reports=payload.get("benchmark_reports"),
                structured_reports=payload.get("structured_reports"),
                suite_name=payload.get("suite_name"),
                include_session_messages=bool(payload.get("include_session_messages", True)),
            )
            review = await review_trace(
                model_adapter,
                trace_kind=trace_kind,
                trace_summary=trace_summary,
                reviewer_tier=reviewer_tier,
                candidate_change_id=payload.get("candidate_change_id"),
            )
            _progress(step="system_job", label="Generating review", detail=str(trace_kind), progress_label="review generation")
            artifacts = persist_trace_review_artifacts(
                storage,
                trace_kind=trace_kind,
                trace_summary=trace_summary,
                review=review,
                spec_scope=spec_scope,
                reviewer_tier=reviewer_tier,
                candidate_change_id=payload.get("candidate_change_id"),
            )
            review["timeline_artifact_id"] = artifacts["timeline_artifact"].get("artifact_id")
            review["audit_artifact_id"] = artifacts["audit_artifact"].get("artifact_id")
            target_task_ids = []
            if payload.get("persist_to_task", True):
                for candidate in [payload.get("task_id"), *(payload.get("associated_task_ids") or [])]:
                    task_id = str(candidate or "").strip()
                    if task_id and task_id not in target_task_ids:
                        target_task_ids.append(task_id)
                for task_id in target_task_ids:
                    append_trace_review_to_task(storage, task_id=task_id, review=review)
                    _maybe_enqueue_blocked_task_question_from_review(storage, task_id=task_id, review=review)
            session_review = None
            session_id = str(payload.get("session_id") or "").strip()
            derived_session_id = session_id or str((trace_summary.get("task") or {}).get("session_id") or "").strip()
            if trace_kind == "session_trace" and session_id:
                session_review = append_trace_review_to_session(storage, session_id=session_id, review=review)
            attention_signal = emit_trace_review_attention_signal(
                storage,
                trace_kind=trace_kind,
                trace_summary=trace_summary,
                review=review,
                reviewer_tier=review.get("reviewer_tier") or reviewer_tier,
                session_id=derived_session_id or None,
                task_id=payload.get("task_id"),
            )
            queued_followups = []
            if bool(payload.get("emit_followups", True)):
                _progress(step="system_job", label="Queuing followups", detail=str(trace_kind), progress_label="followups")
                queued_followups = enqueue_review_followups(
                    storage,
                    trace_kind=trace_kind,
                    trace_summary=trace_summary,
                    review=review,
                    session_id=session_id or None,
                    knowledge_page_store_cls=KnowledgePageStore,
                )
                if queued_followups:
                    from strata.api.main import _worker
                for followup_task_id in queued_followups:
                    await _worker.enqueue(followup_task_id)
            result = {
                "trace_kind": trace_kind,
                "reviewer_tier": review.get("reviewer_tier"),
                "review": review,
                "associated_task_ids": target_task_ids,
                "timeline_artifact": artifacts["timeline_artifact"],
                "audit_artifact": artifacts["audit_artifact"],
                "attention_signal": attention_signal,
                "session_review": session_review,
                "queued_followup_task_ids": queued_followups,
            }
        else:
            raise ValueError(f"Unsupported system eval job kind: {kind}")

        duration_s = time.perf_counter() - started_at
        _progress(step="verification", label="Persisting result", detail=str(kind), progress_label="persist result")
        attempt.outcome = AttemptOutcome.SUCCEEDED
        attempt.ended_at = datetime.utcnow()
        attempt.artifacts = {
            "job_kind": kind,
            "duration_s": duration_s,
            "result_summary": _trim_result(result),
        }
        task.state = TaskState.COMPLETE
        constraints = dict(task.constraints or {})
        constraints["system_job_result"] = {
            "status": "completed",
            "kind": kind,
            "completed_at": time.time(),
            "result": _trim_result(result),
        }
        task.constraints = constraints
        storage.commit()
        return result
    except Exception as exc:
        _progress(step="resolution", label="System job failed", detail=str(exc), progress_label="failure")
        attempt.outcome = AttemptOutcome.FAILED
        attempt.reason = str(exc)
        attempt.artifacts = {
            "job_kind": kind,
            "duration_s": time.perf_counter() - started_at,
            "error": str(exc),
        }
        task.state = TaskState.BLOCKED
        constraints = dict(task.constraints or {})
        constraints["system_job_result"] = {
            "status": "failed",
            "kind": kind,
            "error": str(exc),
        }
        task.constraints = constraints
        storage.commit()
        raise
