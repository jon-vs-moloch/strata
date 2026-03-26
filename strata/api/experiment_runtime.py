"""
@module api.experiment_runtime
@purpose Shared helper logic for experiment generation, promotion, and dashboard snapshots.

These helpers are used by multiple API surfaces and queued jobs. Keeping them
out of `api.main` avoids treating the top-level app assembly as the hidden
dependency root for the experiment subsystem.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException

from strata.eval.harness_eval import EVAL_HARNESS_CONFIG_DESCRIPTION, EVAL_HARNESS_CONFIG_KEY
from strata.experimental.experiment_runner import ExperimentRunner, iter_experiment_reports, report_has_weak_gain
from strata.specs.bootstrap import list_spec_proposals


MAX_PROMOTION_HISTORY = 200


def slugify_candidate_suffix(raw: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    return slug[:48] or "candidate"


def canonical_eval_override(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    config = config or {}
    return {
        "system_prompt": str(config.get("system_prompt") or "").strip(),
        "context_files": [str(path).strip() for path in (config.get("context_files") or []) if str(path).strip()],
    }


def eval_override_signature(config: Optional[Dict[str, Any]]) -> str:
    canonical = canonical_eval_override(config)
    return json.dumps(canonical, sort_keys=True)


def extract_json_object(raw: str) -> Dict[str, Any]:
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
        raise ValueError("No JSON object found in model output.")
    return json.loads(match.group(0))


async def generate_eval_candidate_from_tier(proposer_tier: str, current_config: Dict[str, Any], model_adapter_factory) -> Dict[str, Any]:
    adapter = model_adapter_factory()
    if proposer_tier == "weak":
        from strata.schemas.execution import WeakExecutionContext

        context = WeakExecutionContext(run_id=f"bootstrap_proposal_{int(datetime.now(timezone.utc).timestamp() * 1000)}")
    else:
        from strata.schemas.execution import StrongExecutionContext

        context = StrongExecutionContext(run_id=f"bootstrap_proposal_{int(datetime.now(timezone.utc).timestamp() * 1000)}")
    adapter.bind_execution_context(context)

    proposal_prompt = f"""
You are proposing one small harness-side change to improve weak-model self-improvement in Strata.
Return only JSON with this schema:
{{
  "candidate_suffix": "short_slug_like_name",
  "system_prompt": "full replacement system prompt",
  "context_files": [".knowledge/specs/project_spec.md", "docs/spec/eval-brief.md"],
  "rationale": "short explanation of why this should improve weak-model self-improvement",
  "expected_gain": "what telemetry should improve"
}}

Constraints:
- Propose a small, reversible change to the eval harness only.
- The change must be safe to apply to future eval runs from either proposer tier.
- Keep context_files short and repository-local.
- Optimize for the real goal: the weak model proposing and surviving system improvements.

Current eval harness config:
{json.dumps(current_config, indent=2)}
""".strip()

    response = await adapter.chat(
        [{"role": "user", "content": proposal_prompt}],
        temperature=0.2 if proposer_tier == "weak" else 0.1,
    )
    raw_content = response.get("content", "")
    try:
        proposal = extract_json_object(raw_content)
    except Exception:
        proposal = {
            "candidate_suffix": f"{proposer_tier}_fallback",
            "system_prompt": current_config.get("system_prompt") or "",
            "context_files": current_config.get("context_files") or [],
            "rationale": "Proposal generation returned malformed JSON; preserving the current config instead of failing the cycle.",
            "expected_gain": "No-op fallback to keep the bootstrap cycle alive while capturing malformed proposer output.",
            "parse_error": str(raw_content or "")[:2000],
        }
    suffix = slugify_candidate_suffix(str(proposal.get("candidate_suffix", proposer_tier)))
    return {
        "proposer_tier": proposer_tier,
        "candidate_change_id": f"{proposer_tier}_{suffix}_{int(datetime.now(timezone.utc).timestamp())}",
        "eval_harness_config_override": {
            "system_prompt": str(proposal.get("system_prompt") or current_config.get("system_prompt") or ""),
            "context_files": [str(path) for path in proposal.get("context_files") or current_config.get("context_files") or []],
        },
        "rationale": str(proposal.get("rationale") or ""),
        "expected_gain": str(proposal.get("expected_gain") or ""),
        "raw_proposal": proposal,
    }


async def generate_tool_candidate_from_tier(proposer_tier: str, *, tool_name: str, task_description: str, model_adapter_factory) -> Dict[str, Any]:
    adapter = model_adapter_factory()
    if proposer_tier == "weak":
        from strata.schemas.execution import WeakExecutionContext

        context = WeakExecutionContext(run_id=f"tool_bootstrap_{int(datetime.now(timezone.utc).timestamp() * 1000)}")
    else:
        from strata.schemas.execution import StrongExecutionContext

        context = StrongExecutionContext(run_id=f"tool_bootstrap_{int(datetime.now(timezone.utc).timestamp() * 1000)}")
    adapter.bind_execution_context(context)
    proposal_prompt = f"""
Create a small, safe Strata dynamic tool.
Return only JSON with this schema:
{{
  "source": "full python source for strata/tools/{tool_name}.experimental.py",
  "manifest": {{
    "validator": "python_import_only",
    "smoke_test": "strata/tools/tests/test_{tool_name}_smoke.py"
  }},
  "smoke_test": "full python smoke test source",
  "spec_citations": ["quote or summarize the exact spec constraint this tool serves"],
  "evaluation_plan": "how success will be measured after promotion",
  "rationale": "why this tool helps bootstrap progress",
  "expected_gain": "what operator-visible gain this tool should unlock"
}}

Requirements:
- The tool must define a valid TOOL_SCHEMA.
- The implementation must be read-only or narrowly scoped.
- The smoke test should pass with a plain `python` invocation.
- Task: {task_description}
""".strip()
    response = await adapter.chat(
        [{"role": "user", "content": proposal_prompt}],
        temperature=0.15 if proposer_tier == "strong" else 0.25,
    )
    raw_content = response.get("content", "")
    try:
        proposal = extract_json_object(raw_content)
    except Exception:
        proposal = {
            "source": "",
            "manifest": {},
            "smoke_test": "",
            "spec_citations": [],
            "evaluation_plan": "",
            "rationale": "Tool proposal generation returned malformed JSON; treating the proposal as invalid instead of failing the whole cycle.",
            "expected_gain": "No-op fallback that keeps the tool cycle alive while preserving the bad proposer output for debugging.",
            "parse_error": str(raw_content or "")[:2000],
        }
    return {
        "proposer_tier": proposer_tier,
        "candidate_change_id": f"{proposer_tier}_{tool_name}_{int(datetime.now(timezone.utc).timestamp())}",
        "tool_name": tool_name,
        "source": str(proposal.get("source") or ""),
        "manifest": proposal.get("manifest") or {},
        "smoke_test": str(proposal.get("smoke_test") or ""),
        "spec_citations": [str(item).strip() for item in (proposal.get("spec_citations") or []) if str(item).strip()],
        "evaluation_plan": str(proposal.get("evaluation_plan") or "").strip(),
        "rationale": str(proposal.get("rationale") or ""),
        "expected_gain": str(proposal.get("expected_gain") or ""),
        "raw_proposal": proposal,
    }


def apply_experiment_promotion(storage, candidate_change_id: str, *, force: bool, model_adapter) -> Dict[str, Any]:
    runner = ExperimentRunner(storage, model_adapter)
    report = runner.get_persisted_experiment_report(candidate_change_id)
    if not report:
        raise HTTPException(status_code=404, detail="No persisted experiment report found for candidate_change_id")
    if report.get("recommendation") != "promote" and not force:
        raise HTTPException(status_code=400, detail="Experiment report does not recommend promotion")

    promotion_state = storage.parameters.peek_parameter(
        key="promoted_eval_candidates",
        default_value={"current": None, "history": []},
    ) or {"current": None, "history": []}
    history = list(promotion_state.get("history", []))
    history.append(
        {
            "candidate_change_id": candidate_change_id,
            "recommendation": report.get("recommendation"),
            "recorded_at": report.get("recorded_at"),
            "promoted_at": datetime.now(timezone.utc).isoformat(),
            "proposal_metadata": report.get("proposal_metadata") or {},
        }
    )
    archived_count = int(promotion_state.get("archived_count", 0) or 0)
    if len(history) > MAX_PROMOTION_HISTORY:
        archived_count += len(history) - MAX_PROMOTION_HISTORY
        history = history[-MAX_PROMOTION_HISTORY:]
    promotion_state["current"] = candidate_change_id
    promotion_state["history"] = history
    promotion_state["archived_count"] = archived_count
    storage.parameters.set_parameter(
        key="promoted_eval_candidates",
        value=promotion_state,
        description="Accepted eval-harness candidates and their promotion history.",
    )

    applied_config = None
    if report.get("eval_harness_config_override"):
        applied_config = report["eval_harness_config_override"]
        storage.parameters.set_parameter(
            EVAL_HARNESS_CONFIG_KEY,
            applied_config,
            description=EVAL_HARNESS_CONFIG_DESCRIPTION,
        )

    storage.commit()
    return {
        "candidate_change_id": candidate_change_id,
        "recommendation": report.get("recommendation"),
        "applied_eval_harness_config": applied_config,
        "proposal_metadata": report.get("proposal_metadata") or {},
    }


def build_dashboard_snapshot(
    storage,
    *,
    limit: int,
    build_telemetry_snapshot,
    get_provider_telemetry_snapshot,
    get_retention_runtime,
    get_context_load_telemetry,
) -> Dict[str, Any]:
    telemetry = build_telemetry_snapshot(storage, limit=limit)
    provider_telemetry = get_provider_telemetry_snapshot() or (
        storage.parameters.peek_parameter("provider_transport_telemetry_snapshot", default_value={}) or {}
    )
    context_telemetry = get_context_load_telemetry(storage)
    promoted_state = storage.parameters.peek_parameter(
        "promoted_eval_candidates",
        default_value={"current": None, "history": []},
    ) or {"current": None, "history": []}
    from strata.storage.models import ParameterModel

    report_rows = (
        storage.session.query(ParameterModel)
        .filter(ParameterModel.key.like("experiment_report:%"))
        .order_by(ParameterModel.updated_at.desc())
        .limit(limit)
        .all()
    )
    normalized_reports = iter_experiment_reports(report_rows)
    reports = []
    weak_promotions = 0
    strong_promotions = 0
    for current in normalized_reports:
        metadata = current.get("proposal_metadata") or {}
        if current.get("recommendation") == "promote":
            if metadata.get("proposer_tier") == "weak":
                weak_promotions += 1
            elif metadata.get("proposer_tier") == "strong":
                strong_promotions += 1
        reports.append(
            {
                "candidate_change_id": current.get("candidate_change_id"),
                "evaluation_kind": current.get("evaluation_kind"),
                "recommendation": current.get("recommendation"),
                "recorded_at": current.get("recorded_at"),
                "proposal_metadata": metadata,
                "promotion_readiness": current.get("promotion_readiness") or {},
                "task_associations": current.get("task_associations") or {},
            }
        )
    recent_failures = [metric for metric in telemetry.get("recent_metrics", []) if metric.get("metric_name") == "task_failure"]
    research_failures = [metric for metric in recent_failures if metric.get("task_type") == "RESEARCH"]
    ignition = None
    for current in normalized_reports:
        metadata = current.get("proposal_metadata") or {}
        weak_gain = report_has_weak_gain(current)
        if metadata.get("proposer_tier") == "weak" and current.get("recommendation") == "promote" and weak_gain:
            ignition = {
                "detected": True,
                "candidate_change_id": current.get("candidate_change_id"),
                "proposal_metadata": metadata,
                "recorded_at": current.get("recorded_at"),
            }
            break
    if ignition is None:
        ignition = {"detected": False}
    top_context_artifacts = sorted(
        list((context_telemetry.get("stats", {}).get("artifacts") or {}).values()),
        key=lambda item: (
            int(item.get("load_count", 0) or 0),
            int(item.get("total_estimated_tokens", 0) or 0),
        ),
        reverse=True,
    )[:5]
    recent_spec_proposals = list_spec_proposals(storage, limit=5)
    return {
        "generated_at": telemetry.get("generated_at"),
        "overview": telemetry.get("overview", {}),
        "ignition": ignition,
        "current_promoted_candidate": promoted_state.get("current"),
        "promotion_counts": {
            "weak": weak_promotions,
            "strong": strong_promotions,
            "total_history": len(promoted_state.get("history", [])),
            "archived_history": int(promoted_state.get("archived_count", 0) or 0),
        },
        "failure_pressure": {
            "recent_failures": len(recent_failures),
            "recent_research_failures": len(research_failures),
        },
        "context_pressure": {
            "warning_count": len(context_telemetry.get("warnings", [])),
            "recent_load_count": len(context_telemetry.get("recent", [])),
            "top_artifacts": top_context_artifacts,
            "file_scan": context_telemetry.get("file_scan", {}),
        },
        "spec_governance": {
            "recent_proposals": recent_spec_proposals,
            "pending_count": sum(1 for row in recent_spec_proposals if str(row.get("status") or "") == "pending_review"),
            "clarification_count": sum(1 for row in recent_spec_proposals if str(row.get("status") or "") == "needs_clarification"),
        },
        "reports": reports,
        "provider_telemetry": provider_telemetry,
        "retention": get_retention_runtime(storage),
    }
