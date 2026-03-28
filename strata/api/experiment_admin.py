"""
@module api.experiment_admin
@purpose Register experiment comparison, promotion, and bootstrap-cycle routes.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict

from fastapi import Depends, HTTPException

from strata.eval.harness_eval import (
    EVAL_HARNESS_CONFIG_DESCRIPTION,
    EVAL_HARNESS_CONFIG_KEY,
    default_eval_harness_config,
    get_active_eval_harness_config,
)
from strata.api.experiment_runtime import (
    EVAL_PROPOSAL_CONFIG_DESCRIPTION,
    EVAL_PROPOSAL_CONFIG_KEY,
    canonical_eval_override,
    default_eval_proposal_config,
    get_active_eval_proposal_config,
    normalize_eval_proposal_config,
    summarize_recent_eval_candidates,
)
from strata.experimental.experiment_runner import ExperimentRunner, iter_experiment_reports, report_has_weak_gain
from strata.experimental.trace_review import append_trace_review_to_task, build_trace_summary, review_trace
from strata.experimental.audit_registry import (
    audit_stored_artifact,
    audit_timeline_artifact,
    persist_timeline_artifact,
)
from strata.specs.bootstrap import get_active_spec_record
from strata.experimental.calibration import JUDGE_TRUST_KEY
from strata.experimental.variants import get_variant_rating_snapshot
from strata.storage.models import ParameterModel

def register_experiment_routes(
    app,
    *,
    get_storage,
    model_adapter,
    queue_eval_system_job,
    apply_experiment_promotion,
    generate_eval_candidate_from_tier,
    resolve_eval_proposal_against_history,
    generate_tool_candidate_from_tier,
    eval_override_signature,
) -> Dict[str, Any]:
    exported: Dict[str, Any] = {}

    @app.post("/admin/experiments/benchmark")
    async def run_benchmark_experiment(payload: Dict[str, Any], storage=Depends(get_storage)):
        candidate_change_id = payload.get("candidate_change_id")
        if not candidate_change_id:
            raise HTTPException(status_code=400, detail="candidate_change_id field required")
        if payload.get("queue"):
            queued = await queue_eval_system_job(
                storage,
                kind="benchmark_gate",
                title=f"Benchmark Gate: {candidate_change_id}",
                description=f"Queued benchmark gate for candidate '{candidate_change_id}'.",
                payload=payload,
                session_id=payload.get("session_id"),
                dedupe_signature={"candidate_change_id": candidate_change_id},
            )
            return {"status": "ok", **queued}
        runner = ExperimentRunner(storage, model_adapter)
        result = await runner.run_benchmark_gate(
            candidate_change_id,
            api_url=payload.get("api_url", "http://127.0.0.1:8000"),
            baseline_change_id=payload.get("baseline_change_id", "baseline"),
            run_count=max(1, int(payload.get("run_count", 1) or 1)),
            eval_harness_config_override=payload.get("eval_harness_config_override"),
            proposal_metadata=payload.get("proposal_metadata"),
            source_task_id=payload.get("source_task_id"),
            spawned_task_ids=payload.get("spawned_task_ids"),
            associated_task_ids=payload.get("associated_task_ids"),
        )
        return {"status": "ok", "result": result.model_dump()}

    @app.post("/admin/experiments/full_eval")
    async def run_full_eval_experiment(payload: Dict[str, Any], storage=Depends(get_storage)):
        candidate_change_id = payload.get("candidate_change_id")
        if not candidate_change_id:
            raise HTTPException(status_code=400, detail="candidate_change_id field required")
        if payload.get("queue"):
            queued = await queue_eval_system_job(
                storage,
                kind="full_eval",
                title=f"Full Eval Gate: {candidate_change_id}",
                description=f"Queued full eval gate for candidate '{candidate_change_id}'.",
                payload=payload,
                session_id=payload.get("session_id"),
                dedupe_signature={
                    "candidate_change_id": candidate_change_id,
                    "suite_name": payload.get("suite_name", "bootstrap_mcq_v1"),
                },
            )
            return {"status": "ok", **queued}
        runner = ExperimentRunner(storage, model_adapter)
        result = await runner.run_full_eval_gate(
            candidate_change_id,
            api_url=payload.get("api_url", "http://127.0.0.1:8000"),
            baseline_change_id=payload.get("baseline_change_id", "baseline"),
            suite_name=payload.get("suite_name", "bootstrap_mcq_v1"),
            run_count=max(1, int(payload.get("run_count", 1) or 1)),
            eval_harness_config_override=payload.get("eval_harness_config_override"),
            proposal_metadata=payload.get("proposal_metadata"),
            source_task_id=payload.get("source_task_id"),
            spawned_task_ids=payload.get("spawned_task_ids"),
            associated_task_ids=payload.get("associated_task_ids"),
        )
        return {"status": "ok", "result": result.model_dump()}

    @app.get("/admin/experiments/compare")
    async def compare_experiment_metrics(candidate_change_id: str, baseline_change_id: str = "baseline", storage=Depends(get_storage)):
        runner = ExperimentRunner(storage, model_adapter)
        persisted_report = runner.get_persisted_experiment_report(candidate_change_id)
        if persisted_report and persisted_report.get("baseline_change_id") == baseline_change_id:
            return {"status": "ok", "source": "persisted_report", **persisted_report}
        candidate_metrics = runner._gather_metrics(candidate_change_id)
        baseline_metrics = runner._gather_metrics(baseline_change_id)
        deltas = runner._calculate_deltas(baseline_metrics, candidate_metrics)
        recommendation = runner._decide_benchmark_promotion(deltas)
        return {
            "status": "ok",
            "source": "aggregate_metrics",
            "candidate_change_id": candidate_change_id,
            "baseline_change_id": baseline_change_id,
            "baseline_metrics": baseline_metrics,
            "candidate_metrics": candidate_metrics,
            "deltas": deltas,
            "recommendation": recommendation,
        }

    @app.get("/admin/experiments/report")
    async def get_experiment_report(candidate_change_id: str, storage=Depends(get_storage)):
        runner = ExperimentRunner(storage, model_adapter)
        report = runner.get_persisted_experiment_report(candidate_change_id)
        if not report:
            raise HTTPException(status_code=404, detail="No persisted experiment report found for candidate_change_id")
        return {"status": "ok", "report": report}

    @app.get("/admin/experiments/history")
    async def get_experiment_history(limit: int = 25, storage=Depends(get_storage)):
        safe_limit = max(1, min(limit, 100))
        rows = (
            storage.session.query(ParameterModel)
            .filter(ParameterModel.key.like("experiment_report:%"))
            .order_by(ParameterModel.updated_at.desc())
            .limit(safe_limit)
            .all()
        )
        history = []
        for current in iter_experiment_reports(rows):
            readiness = current.get("promotion_readiness") or {}
            history.append(
                {
                    "candidate_change_id": current.get("candidate_change_id"),
                    "evaluation_kind": current.get("evaluation_kind"),
                    "recommendation": current.get("recommendation"),
                    "recorded_at": current.get("recorded_at"),
                    "proposal_metadata": current.get("proposal_metadata") or {},
                    "promotion_readiness": readiness,
                    "diagnostic_review": {
                        "status": (current.get("diagnostic_review") or {}).get("status"),
                        "primary_failure_mode": (current.get("diagnostic_review") or {}).get("primary_failure_mode"),
                        "recommended_fix": (current.get("diagnostic_review") or {}).get("recommended_fix"),
                        "summary": (current.get("diagnostic_review") or {}).get("summary"),
                    },
                    "prediction_record": {
                        "predicted_outcome": (current.get("prediction_record") or {}).get("predicted_outcome"),
                        "confidence": (current.get("prediction_record") or {}).get("confidence"),
                        "domains_affected": (current.get("prediction_record") or {}).get("domains_affected"),
                    },
                    "calibration_record": {
                        "actual_outcome": (current.get("calibration_record") or {}).get("actual_outcome"),
                        "direction_correct": (current.get("calibration_record") or {}).get("direction_correct"),
                        "calibration_score": (current.get("calibration_record") or {}).get("calibration_score"),
                    },
                    "variant_assignment": current.get("variant_assignment") or {},
                    "variant_rating_snapshot": current.get("variant_rating_snapshot") or {},
                    "has_eval_harness_override": bool(current.get("eval_harness_config_override")),
                    "has_code_validation": bool(current.get("code_validation")),
                    "task_associations": current.get("task_associations") or {},
                }
            )
        promoted_state = storage.parameters.peek_parameter(
            "promoted_eval_candidates",
            default_value={"current": None, "history": []},
        ) or {"current": None, "history": []}
        return {
            "status": "ok",
            "current_promoted_candidate": promoted_state.get("current"),
            "promotion_history": promoted_state.get("history", []),
            "reports": history,
        }

    @app.get("/admin/variants")
    async def get_variant_registry(storage=Depends(get_storage)):
        snapshot = get_variant_rating_snapshot(storage)
        return {"status": "ok", **snapshot}

    @app.get("/admin/variants/ratings")
    async def get_variant_ratings(storage=Depends(get_storage)):
        snapshot = get_variant_rating_snapshot(storage)
        return {
            "status": "ok",
            "ratings": snapshot.get("ratings", {}),
            "recent_matchups": snapshot.get("recent_matchups", []),
        }

    @app.get("/admin/evals/config")
    async def get_eval_harness_config(storage=Depends(get_storage)):
        active_config = storage.parameters.peek_parameter(
            EVAL_HARNESS_CONFIG_KEY,
            default_value=default_eval_harness_config(),
        ) or default_eval_harness_config()
        return {"status": "ok", "config": active_config}

    @app.post("/admin/evals/config")
    async def set_eval_harness_config(payload: Dict[str, Any], storage=Depends(get_storage)):
        system_prompt = payload.get("system_prompt")
        context_files = payload.get("context_files")
        current_config = get_active_eval_harness_config()
        if system_prompt:
            current_config["system_prompt"] = str(system_prompt)
        if context_files:
            current_config["context_files"] = [str(path) for path in context_files]
        storage.parameters.set_parameter(
            EVAL_HARNESS_CONFIG_KEY,
            current_config,
            description=EVAL_HARNESS_CONFIG_DESCRIPTION,
        )
        storage.commit()
        return {"status": "ok", "config": current_config}

    @app.get("/admin/evals/proposal_config")
    async def get_eval_proposal_config(storage=Depends(get_storage)):
        active_config = storage.parameters.peek_parameter(
            EVAL_PROPOSAL_CONFIG_KEY,
            default_value=default_eval_proposal_config(),
        ) or default_eval_proposal_config()
        return {"status": "ok", "config": normalize_eval_proposal_config(active_config)}

    @app.post("/admin/evals/proposal_config")
    async def set_eval_proposal_config(payload: Dict[str, Any], storage=Depends(get_storage)):
        current_config = get_active_eval_proposal_config()
        merged = normalize_eval_proposal_config(
            {
                "bootstrap": {**dict(current_config.get("bootstrap") or {}), **dict(payload.get("bootstrap") or {})},
                "inference": {
                    **dict(current_config.get("inference") or {}),
                    **dict(payload.get("inference") or {}),
                },
                "novelty": {**dict(current_config.get("novelty") or {}), **dict(payload.get("novelty") or {})},
                "resolution": {
                    **dict(current_config.get("resolution") or {}),
                    **dict(payload.get("resolution") or {}),
                },
            }
        )
        storage.parameters.set_parameter(
            EVAL_PROPOSAL_CONFIG_KEY,
            merged,
            description=EVAL_PROPOSAL_CONFIG_DESCRIPTION,
        )
        storage.commit()
        return {"status": "ok", "config": merged}

    @app.post("/admin/experiments/promote")
    async def promote_experiment_candidate(payload: Dict[str, Any], storage=Depends(get_storage)):
        candidate_change_id = payload.get("candidate_change_id")
        if not candidate_change_id:
            raise HTTPException(status_code=400, detail="candidate_change_id field required")
        force = bool(payload.get("force", False))
        result = apply_experiment_promotion(storage, candidate_change_id, force=force)
        return {"status": "ok", **result}

    @app.post("/admin/experiments/bootstrap_cycle")
    async def run_bootstrap_cycle(payload: Dict[str, Any] | None = None, storage=Depends(get_storage)):
        payload = payload or {}
        queue_requested = payload.get("queue")
        wait_for_completion = bool(payload.get("wait", False))
        if queue_requested is None:
            queue_requested = not wait_for_completion
        proposal_config = get_active_eval_proposal_config()
        bootstrap_policy = dict(proposal_config.get("bootstrap") or {})
        proposer_tiers = [
            str(tier).lower()
            for tier in payload.get("proposer_tiers", bootstrap_policy.get("default_proposer_tiers", ["weak", "strong"]))
        ]
        proposer_tiers = [tier for tier in proposer_tiers if tier in {"weak", "strong"}]
        if not proposer_tiers:
            raise HTTPException(status_code=400, detail="At least one proposer tier must be 'weak' or 'strong'")
        auto_promote = bool(payload.get("auto_promote", True))
        suite_name = payload.get("suite_name", "bootstrap_mcq_v1")
        run_count = max(1, int(payload.get("run_count", bootstrap_policy.get("default_run_count", 2)) or 1))
        baseline_change_id = payload.get("baseline_change_id", "baseline")
        normalized_payload = {
            "proposer_tiers": proposer_tiers,
            "auto_promote": auto_promote,
            "suite_name": suite_name,
            "run_count": run_count,
            "baseline_change_id": baseline_change_id,
        }
        if payload.get("api_url"):
            normalized_payload["api_url"] = str(payload.get("api_url"))
        if payload.get("associated_task_ids"):
            normalized_payload["associated_task_ids"] = list(payload.get("associated_task_ids") or [])
        if payload.get("source_task_id"):
            normalized_payload["source_task_id"] = payload.get("source_task_id")
        if queue_requested:
            queued = await queue_eval_system_job(
                storage,
                kind="bootstrap_cycle",
                title="Bootstrap Cycle",
                description="Queued strong-over-weak bootstrap cycle.",
                payload=normalized_payload,
                session_id=payload.get("session_id"),
                dedupe_signature={
                    "suite_name": suite_name,
                    "run_count": run_count,
                    "proposer_tiers": proposer_tiers,
                },
            )
            return {"status": "ok", **queued}
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
        proposals = await asyncio.gather(
            *[
                generate_eval_candidate_from_tier(
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
            proposal_signature = eval_override_signature(proposal["eval_harness_config_override"])
            if proposal_signature in recent_signatures:
                skipped.append({"proposal": proposal, "reason": "recent_duplicate_signature"})
                continue
            resolution = await resolve_eval_proposal_against_history(
                proposal,
                current_config=current_config,
                recent_candidates=recent_candidate_hints,
                seen_candidates=seen_candidates,
                proposal_config=proposal_config,
            )
            if not resolution.get("should_evaluate", False):
                skipped.append({"proposal": proposal, "reason": resolution.get("decision"), "resolution": resolution})
                continue
            seen_signatures.add(proposal_signature)
            seen_candidates.append(
                {
                    "candidate_change_id": proposal.get("candidate_change_id"),
                    "proposer_tier": proposal.get("proposer_tier"),
                    "rationale": proposal.get("rationale"),
                    "expected_gain": proposal.get("expected_gain"),
                    "eval_harness_config_override": canonical_eval_override(proposal.get("eval_harness_config_override")),
                }
            )
            result = await runner.run_full_eval_gate(
                proposal["candidate_change_id"],
                api_url="http://127.0.0.1:8000",
                baseline_change_id=baseline_change_id,
                suite_name=suite_name,
                run_count=run_count,
                eval_harness_config_override=proposal["eval_harness_config_override"],
                proposal_metadata={
                    "proposer_tier": proposal["proposer_tier"],
                    "rationale": proposal["rationale"],
                    "expected_gain": proposal["expected_gain"],
                    "source": "bootstrap_cycle",
                },
                source_task_id=payload.get("source_task_id"),
                associated_task_ids=payload.get("associated_task_ids"),
            )
            evaluated.append({"proposal": proposal, "result": result.model_dump()})
            if auto_promote and result.recommendation == "promote":
                promoted.append(apply_experiment_promotion(storage, proposal["candidate_change_id"], force=False))

        return {
            "status": "ok",
            "current_eval_harness_config": current_config,
            "evaluated": evaluated,
            "promoted": promoted,
            "skipped": skipped,
            "auto_promote": auto_promote,
        }

    @app.post("/admin/experiments/tool_cycle")
    async def run_tool_bootstrap_cycle(payload: Dict[str, Any] | None = None, storage=Depends(get_storage)):
        from strata.orchestrator.tools_pipeline import ToolsPromotionPipeline

        payload = payload or {}
        proposer_tiers = [str(tier).lower() for tier in payload.get("proposer_tiers", ["weak"])]
        proposer_tiers = [tier for tier in proposer_tiers if tier in {"weak", "strong"}]
        if not proposer_tiers:
            raise HTTPException(status_code=400, detail="At least one proposer tier must be 'weak' or 'strong'")
        tool_name = payload.get("tool_name", "bootstrap_history_tool")
        task_description = str(
            payload.get(
                "task_description",
                "Create a read-only dynamic tool that helps operators inspect bootstrap history and promotion readiness.",
            )
        )
        proposals = await asyncio.gather(
            *[generate_tool_candidate_from_tier(tier, tool_name=tool_name, task_description=task_description) for tier in proposer_tiers]
        )
        pipeline = ToolsPromotionPipeline(storage)
        evaluated = []
        for proposal in proposals:
            spec_citations = [str(item).strip() for item in (proposal.get("spec_citations") or []) if str(item).strip()]
            evaluation_plan = str(proposal.get("evaluation_plan") or "").strip()
            if not spec_citations or not evaluation_plan:
                validation = {
                    "tool_name": proposal["tool_name"],
                    "promoted": False,
                    "checks_passed": [],
                    "checks_failed": [
                        "Spec citation and evaluation plan are required for code/tool promotion proposals."
                    ],
                    "details": "Proposal did not include explicit spec alignment evidence and a post-promotion evaluation plan.",
                }
                result = ExperimentRunner(storage, model_adapter).record_tool_promotion_result(
                    candidate_change_id=proposal["candidate_change_id"],
                    validation_result=validation,
                    proposal_metadata={
                        "proposer_tier": proposal["proposer_tier"],
                        "tool_name": proposal["tool_name"],
                        "rationale": proposal["rationale"],
                        "expected_gain": proposal["expected_gain"],
                        "source": "tool_cycle",
                        "spec_citations": spec_citations,
                        "evaluation_plan": evaluation_plan,
                    },
                    source_task_id=payload.get("source_task_id"),
                    associated_task_ids=payload.get("associated_task_ids"),
                )
                evaluated.append({"proposal": proposal, "validation": validation, "result": result.model_dump()})
                continue
            os.makedirs("strata/tools", exist_ok=True)
            os.makedirs("strata/tools/manifests", exist_ok=True)
            os.makedirs("strata/tools/tests", exist_ok=True)
            experimental_path = os.path.join("strata/tools", f"{proposal['tool_name']}.experimental.py")
            manifest_path = os.path.join("strata/tools/manifests", f"{proposal['tool_name']}.json")
            smoke_path = os.path.join("strata/tools/tests", f"test_{proposal['tool_name']}_smoke.py")
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
            validation = await pipeline.validate_and_promote(proposal["tool_name"])
            result = ExperimentRunner(storage, model_adapter).record_tool_promotion_result(
                candidate_change_id=proposal["candidate_change_id"],
                validation_result=validation.model_dump(),
                proposal_metadata={
                    "proposer_tier": proposal["proposer_tier"],
                    "tool_name": proposal["tool_name"],
                    "rationale": proposal["rationale"],
                    "expected_gain": proposal["expected_gain"],
                    "source": "tool_cycle",
                    "spec_citations": spec_citations,
                    "evaluation_plan": evaluation_plan,
                },
                source_task_id=payload.get("source_task_id"),
                associated_task_ids=payload.get("associated_task_ids"),
            )
            evaluated.append({"proposal": proposal, "validation": validation.model_dump(), "result": result.model_dump()})
        return {"status": "ok", "evaluated": evaluated}

    @app.get("/admin/experiments/secondary_ignition")
    async def get_secondary_ignition_status(storage=Depends(get_storage)):
        promoted_state = storage.parameters.peek_parameter(
            "promoted_eval_candidates",
            default_value={"current": None, "history": []},
        ) or {"current": None, "history": []}
        current_candidate = promoted_state.get("current")
        runner = ExperimentRunner(storage, model_adapter)
        report_rows = (
            storage.session.query(ParameterModel)
            .filter(ParameterModel.key.like("experiment_report:%"))
            .order_by(ParameterModel.updated_at.desc())
            .all()
        )
        matching_report = None
        for report in iter_experiment_reports(report_rows):
            proposal_metadata = report.get("proposal_metadata") or {}
            recommendation = report.get("recommendation")
            weak_gain = report_has_weak_gain(report)
            if proposal_metadata.get("proposer_tier") == "weak" and recommendation == "promote" and weak_gain:
                matching_report = report
                break

        current_report = runner.get_persisted_experiment_report(current_candidate) if current_candidate else None
        if matching_report:
            return {
                "status": "ok",
                "detected": True,
                "candidate_change_id": matching_report.get("candidate_change_id"),
                "current_promoted_candidate": current_candidate,
                "recommendation": matching_report.get("recommendation"),
                "proposal_metadata": matching_report.get("proposal_metadata") or {},
                "weak_gain_detected": True,
                "reason": "Weak-originated candidate was promoted after improving weak-tier eval metrics.",
            }
        if not current_candidate:
            return {"status": "ok", "detected": False, "reason": "No promoted eval candidate is currently active."}
        if not current_report:
            return {
                "status": "ok",
                "detected": False,
                "candidate_change_id": current_candidate,
                "reason": "No persisted experiment report found for the current promoted candidate.",
            }
        proposal_metadata = current_report.get("proposal_metadata") or {}
        recommendation = current_report.get("recommendation")
        weak_gain = report_has_weak_gain(current_report)
        return {
            "status": "ok",
            "detected": False,
            "candidate_change_id": current_candidate,
            "recommendation": recommendation,
            "proposal_metadata": proposal_metadata,
            "weak_gain_detected": weak_gain,
            "reason": "Secondary ignition has not been detected yet for the current promoted candidate.",
        }

    @app.post("/admin/traces/review")
    async def review_trace_endpoint(payload: Dict[str, Any], storage=Depends(get_storage)):
        artifact_type = str(payload.get("artifact_type") or "").strip().lower()
        artifact_id = str(payload.get("artifact_id") or "").strip()
        spec_scope = str(payload.get("spec_scope") or "project").strip().lower() or "project"
        if artifact_type and artifact_id:
            active_spec = get_active_spec_record(storage, scope=spec_scope)
            audit_artifact = audit_stored_artifact(
                storage,
                artifact_type=artifact_type,
                artifact_id=artifact_id,
                spec_version_used=active_spec.get("version"),
                rationale=f"Recursive audit requested for stored {artifact_type} artifact.",
            )
            storage.commit()
            return {"status": "ok", "audit_artifact": audit_artifact}
        trace_kind = str(payload.get("trace_kind") or "generic_trace").strip() or "generic_trace"
        reviewer_tier = str(payload.get("reviewer_tier") or "strong").strip().lower() or "strong"
        if payload.get("queue"):
            queued = await queue_eval_system_job(
                storage,
                kind="trace_review",
                title=f"Trace Review: {trace_kind}",
                description=f"Queued {reviewer_tier}-tier review for trace kind '{trace_kind}'.",
                payload=payload,
                session_id=payload.get("session_id"),
                dedupe_signature={
                    "trace_kind": trace_kind,
                    "reviewer_tier": reviewer_tier,
                    "task_id": payload.get("task_id"),
                    "session_id": payload.get("session_id"),
                    "candidate_change_id": payload.get("candidate_change_id"),
                },
            )
            return {"status": "ok", **queued}
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
        active_spec = get_active_spec_record(storage, scope=spec_scope)
        timeline_artifact = persist_timeline_artifact(
            storage,
            trace_kind=trace_kind,
            trace_summary=trace_summary,
            applicable_spec_version=active_spec.get("version"),
            metadata={
                "reviewer_tier": reviewer_tier,
                "candidate_change_id": payload.get("candidate_change_id"),
            },
        )
        audit_artifact = audit_timeline_artifact(
            storage,
            timeline_artifact=timeline_artifact,
            spec_version_used=active_spec.get("version"),
            rationale="Durable trace review artifacts should be auditable against the governing spec.",
            confidence=float(review.get("confidence", 0.7) or 0.7),
            extra_context={
                "reviewer_tier": reviewer_tier,
                "trace_kind": trace_kind,
                "associated_review_status": review.get("status"),
            },
        )
        review["timeline_artifact_id"] = timeline_artifact.get("artifact_id")
        review["audit_artifact_id"] = audit_artifact.get("artifact_id")
        if payload.get("persist_to_task", False):
            target_task_ids = []
            for candidate in [payload.get("task_id"), *(payload.get("associated_task_ids") or [])]:
                task_id = str(candidate or "").strip()
                if task_id and task_id not in target_task_ids:
                    target_task_ids.append(task_id)
            for task_id in target_task_ids:
                append_trace_review_to_task(storage, task_id=task_id, review=review)
            if target_task_ids:
                storage.commit()
        else:
            storage.commit()
        return {
            "status": "ok",
            "review": review,
            "timeline_artifact": timeline_artifact,
            "audit_artifact": audit_artifact,
        }

    @app.post("/admin/traces/review_task")
    async def review_task_trace(payload: Dict[str, Any], storage=Depends(get_storage)):
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            raise HTTPException(status_code=400, detail="task_id field required")
        return await review_trace_endpoint(
            {
                **payload,
                "trace_kind": "task_trace",
                "task_id": task_id,
                "persist_to_task": payload.get("persist_to_task", True),
            },
            storage=storage,
        )

    @app.post("/admin/traces/review_session")
    async def review_session_trace(payload: Dict[str, Any], storage=Depends(get_storage)):
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id field required")
        return await review_trace_endpoint(
            {
                **payload,
                "trace_kind": "session_trace",
                "session_id": session_id,
            },
            storage=storage,
        )

    @app.post("/admin/predictions/run")
    async def run_prediction_review(payload: Dict[str, Any], storage=Depends(get_storage)):
        return await review_trace_endpoint(payload, storage=storage)

    @app.get("/admin/predictions/history")
    async def get_prediction_history(limit: int = 25, storage=Depends(get_storage)):
        safe_limit = max(1, min(limit, 100))
        rows = (
            storage.session.query(ParameterModel)
            .filter(ParameterModel.key.like("experiment_report:%"))
            .order_by(ParameterModel.updated_at.desc())
            .limit(safe_limit)
            .all()
        )
        history = []
        for report in iter_experiment_reports(rows):
            prediction = report.get("prediction_record") or {}
            if not prediction:
                continue
            history.append(
                {
                    "candidate_change_id": report.get("candidate_change_id"),
                    "evaluation_kind": report.get("evaluation_kind"),
                    "recorded_at": report.get("recorded_at"),
                    "prediction_record": prediction,
                    "prediction_outcome": report.get("prediction_outcome") or {},
                    "calibration_record": report.get("calibration_record") or {},
                }
            )
        return {"status": "ok", "history": history}

    @app.get("/admin/predictions/calibration")
    async def get_prediction_calibration(limit: int = 50, storage=Depends(get_storage)):
        safe_limit = max(1, min(limit, 200))
        rows = (
            storage.session.query(ParameterModel)
            .filter(ParameterModel.key.like("experiment_report:%"))
            .order_by(ParameterModel.updated_at.desc())
            .limit(safe_limit)
            .all()
        )
        records = []
        scores = []
        for report in iter_experiment_reports(rows):
            calibration = report.get("calibration_record") or {}
            if not calibration:
                continue
            scores.append(float(calibration.get("calibration_score", 0.0) or 0.0))
            records.append(
                {
                    "candidate_change_id": report.get("candidate_change_id"),
                    "prediction_record": report.get("prediction_record") or {},
                    "calibration_record": calibration,
                }
            )
        average_score = round(sum(scores) / len(scores), 4) if scores else 0.0
        return {"status": "ok", "average_calibration_score": average_score, "records": records}

    @app.get("/admin/predictions/trust")
    async def get_prediction_trust(storage=Depends(get_storage)):
        trust = storage.parameters.peek_parameter(
            JUDGE_TRUST_KEY,
            default_value={"by_tier": {}, "by_domain": {}, "by_failure_family": {}},
        ) or {"by_tier": {}, "by_domain": {}, "by_failure_family": {}}
        return {"status": "ok", "trust": trust}

    exported.update(
        {
            "run_benchmark_experiment": run_benchmark_experiment,
            "run_full_eval_experiment": run_full_eval_experiment,
            "compare_experiment_metrics": compare_experiment_metrics,
            "get_experiment_report": get_experiment_report,
            "get_experiment_history": get_experiment_history,
            "get_eval_harness_config": get_eval_harness_config,
            "set_eval_harness_config": set_eval_harness_config,
            "promote_experiment_candidate": promote_experiment_candidate,
            "run_bootstrap_cycle": run_bootstrap_cycle,
            "run_tool_bootstrap_cycle": run_tool_bootstrap_cycle,
            "get_secondary_ignition_status": get_secondary_ignition_status,
            "review_trace_endpoint": review_trace_endpoint,
            "review_task_trace": review_task_trace,
            "review_session_trace": review_session_trace,
            "run_prediction_review": run_prediction_review,
            "get_prediction_history": get_prediction_history,
            "get_prediction_calibration": get_prediction_calibration,
            "get_prediction_trust": get_prediction_trust,
        }
    )
    return exported
