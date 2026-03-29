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
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

from fastapi import HTTPException

from strata.eval.harness_eval import EVAL_HARNESS_CONFIG_DESCRIPTION, EVAL_HARNESS_CONFIG_KEY
from strata.experimental.experiment_runner import ExperimentRunner, iter_experiment_reports, report_has_weak_gain
from strata.experimental.variants import get_variant_rating_snapshot
from strata.specs.bootstrap import list_spec_proposals
from strata.storage.models import MetricModel
from strata.storage.services.main import StorageManager


MAX_PROMOTION_HISTORY = 200
EVAL_SERIES_LIMIT = 12
EVAL_PROPOSAL_CONFIG_KEY = "eval_proposal_generation_config"
EVAL_PROPOSAL_CONFIG_DESCRIPTION = (
    "Mutable proposal-generation policy for bootstrap mutation authoring, novelty pressure, and inference parameters."
)


def default_eval_proposal_config() -> Dict[str, Any]:
    return {
        "bootstrap": {
            "default_proposer_tiers": ["agent", "trainer"],
            "continuous_proposer_tiers": ["agent", "trainer"],
            "default_run_count": 2,
            "continuous_run_count": 1,
            "recent_report_window": 50,
            "recent_candidate_limit": 6,
        },
        "inference": {
            "trainer": {"temperature": 0.1},
            "agent": {"temperature": 0.2},
            "novelty_retry_count": 1,
            "novelty_temperature_step": 0.15,
            "novelty_max_temperature": 0.35,
        },
        "novelty": {
            "include_recent_candidates_in_prompt": True,
            "require_material_difference": True,
        },
        "resolution": {
            "use_llm_for_ambiguous": True,
            "adjudicator_tier": "trainer",
            "vote_count": 1,
            "near_duplicate_overlap": 0.92,
            "family_overlap": 0.68,
        },
    }


def _normalize_inference_params(value: Any, *, fallback: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(fallback)
    if not isinstance(value, dict):
        return params
    for key in ("temperature", "top_p", "max_tokens"):
        raw = value.get(key)
        if raw is None or raw == "":
            continue
        if key == "max_tokens":
            params[key] = max(1, int(raw))
        else:
            params[key] = float(raw)
    return params


def normalize_eval_proposal_config(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    defaults = default_eval_proposal_config()
    payload = payload or {}
    bootstrap_payload = dict(payload.get("bootstrap") or {})
    inference_payload = dict(payload.get("inference") or {})
    novelty_payload = dict(payload.get("novelty") or {})
    resolution_payload = dict(payload.get("resolution") or {})

    def normalize_tiers(raw: Any, fallback: list[str]) -> list[str]:
        if not isinstance(raw, list):
            return list(fallback)
        normalized = [str(item).lower() for item in raw if str(item).lower() in {"agent", "trainer"}]
        return normalized or list(fallback)

    bootstrap_defaults = defaults["bootstrap"]
    inference_defaults = defaults["inference"]
    novelty_defaults = defaults["novelty"]
    resolution_defaults = defaults["resolution"]

    return {
        "bootstrap": {
            "default_proposer_tiers": normalize_tiers(
                bootstrap_payload.get("default_proposer_tiers"),
                bootstrap_defaults["default_proposer_tiers"],
            ),
            "continuous_proposer_tiers": normalize_tiers(
                bootstrap_payload.get("continuous_proposer_tiers"),
                bootstrap_defaults["continuous_proposer_tiers"],
            ),
            "default_run_count": max(1, int(bootstrap_payload.get("default_run_count", bootstrap_defaults["default_run_count"]) or 1)),
            "continuous_run_count": max(
                1,
                int(bootstrap_payload.get("continuous_run_count", bootstrap_defaults["continuous_run_count"]) or 1),
            ),
            "recent_report_window": max(
                1,
                int(bootstrap_payload.get("recent_report_window", bootstrap_defaults["recent_report_window"]) or 1),
            ),
            "recent_candidate_limit": max(
                1,
                int(bootstrap_payload.get("recent_candidate_limit", bootstrap_defaults["recent_candidate_limit"]) or 1),
            ),
        },
        "inference": {
            "trainer": _normalize_inference_params(inference_payload.get("trainer"), fallback=inference_defaults["trainer"]),
            "agent": _normalize_inference_params(inference_payload.get("agent"), fallback=inference_defaults["agent"]),
            "novelty_retry_count": max(
                0,
                int(inference_payload.get("novelty_retry_count", inference_defaults["novelty_retry_count"]) or 0),
            ),
            "novelty_temperature_step": float(
                inference_payload.get("novelty_temperature_step", inference_defaults["novelty_temperature_step"]) or 0.0
            ),
            "novelty_max_temperature": float(
                inference_payload.get("novelty_max_temperature", inference_defaults["novelty_max_temperature"]) or 0.0
            ),
        },
        "novelty": {
            "include_recent_candidates_in_prompt": bool(
                novelty_payload.get(
                    "include_recent_candidates_in_prompt",
                    novelty_defaults["include_recent_candidates_in_prompt"],
                )
            ),
            "require_material_difference": bool(
                novelty_payload.get("require_material_difference", novelty_defaults["require_material_difference"])
            ),
        },
        "resolution": {
            "use_llm_for_ambiguous": bool(
                resolution_payload.get("use_llm_for_ambiguous", resolution_defaults["use_llm_for_ambiguous"])
            ),
            "adjudicator_tier": str(
                resolution_payload.get("adjudicator_tier", resolution_defaults["adjudicator_tier"]) or "trainer"
            ).lower()
            if str(resolution_payload.get("adjudicator_tier", resolution_defaults["adjudicator_tier"]) or "trainer").lower() in {"agent", "trainer"}
            else "trainer",
            "vote_count": max(1, int(resolution_payload.get("vote_count", resolution_defaults["vote_count"]) or 1)),
            "near_duplicate_overlap": float(
                resolution_payload.get("near_duplicate_overlap", resolution_defaults["near_duplicate_overlap"]) or 0.92
            ),
            "family_overlap": float(
                resolution_payload.get("family_overlap", resolution_defaults["family_overlap"]) or 0.68
            ),
        },
    }


def get_active_eval_proposal_config() -> Dict[str, Any]:
    storage = StorageManager()
    try:
        config = storage.parameters.peek_parameter(
            EVAL_PROPOSAL_CONFIG_KEY,
            default_value=default_eval_proposal_config(),
        ) or default_eval_proposal_config()
        return normalize_eval_proposal_config(config)
    finally:
        storage.close()


def summarize_eval_variant_metrics(metric_rows, *, series_limit: int = EVAL_SERIES_LIMIT) -> Dict[str, Any]:
    variants: Dict[str, Dict[str, Any]] = {}
    grouped: Dict[str, Dict[str, list]] = defaultdict(lambda: defaultdict(list))

    for row in metric_rows:
        details = dict(getattr(row, "details", None) or {})
        variant_id = str(details.get("variant_id") or getattr(row, "model_id", "") or "").strip()
        if not variant_id:
            continue
        metric_name = str(getattr(row, "metric_name", "") or "")
        grouped[variant_id][metric_name].append(row)
        current = variants.setdefault(
            variant_id,
            {
                "variant_id": variant_id,
                "mode": details.get("mode"),
                "profile": details.get("profile"),
                "include_context": details.get("include_context"),
                "suite_name": details.get("suite_name"),
                "sampled": bool(details.get("sampled", False)),
            },
        )
        current["mode"] = current.get("mode") or details.get("mode")
        current["profile"] = current.get("profile") or details.get("profile")
        current["include_context"] = (
            details.get("include_context")
            if details.get("include_context") is not None
            else current.get("include_context")
        )
        current["suite_name"] = current.get("suite_name") or details.get("suite_name")
        current["sampled"] = bool(current.get("sampled") or details.get("sampled", False))

    snapshots = []
    for variant_id, metrics_by_name in grouped.items():
        current = variants[variant_id]
        metric_payloads: Dict[str, Any] = {}
        latest_timestamp = None
        for metric_name, rows in metrics_by_name.items():
            ordered = sorted(
                rows,
                key=lambda item: getattr(item, "timestamp", datetime.min.replace(tzinfo=None)),
            )
            series_rows = ordered[-series_limit:]
            values = [round(float(getattr(item, "value", 0.0) or 0.0), 4) for item in series_rows]
            timestamps = [
                item.timestamp.isoformat() if getattr(item, "timestamp", None) is not None else None
                for item in series_rows
            ]
            latest = series_rows[-1] if series_rows else None
            latest_value = round(float(getattr(latest, "value", 0.0) or 0.0), 4) if latest else 0.0
            window_avg = round(sum(values) / len(values), 4) if values else 0.0
            delta = round(values[-1] - values[0], 4) if len(values) >= 2 else 0.0
            metric_payloads[metric_name] = {
                "latest": latest_value,
                "window_avg": window_avg,
                "delta": delta,
                "values": values,
                "timestamps": timestamps,
            }
            if latest is not None and (latest_timestamp is None or latest.timestamp > latest_timestamp):
                latest_timestamp = latest.timestamp
        current["metrics"] = metric_payloads
        current["last_seen"] = latest_timestamp.isoformat() if latest_timestamp is not None else None
        current["latest_accuracy"] = (
            metric_payloads.get("eval_sample_tick_accuracy", {}).get("latest")
            or metric_payloads.get("eval_matrix_accuracy", {}).get("latest")
            or 0.0
        )
        current["latest_error_rate"] = (
            metric_payloads.get("eval_sample_tick_error_rate", {}).get("latest")
            or metric_payloads.get("eval_matrix_error_rate", {}).get("latest")
            or 0.0
        )
        current["latest_degraded_rate"] = (
            metric_payloads.get("eval_sample_tick_degraded_rate", {}).get("latest")
            or metric_payloads.get("eval_matrix_degraded_rate", {}).get("latest")
            or 0.0
        )
        current["latest_latency_s"] = (
            metric_payloads.get("eval_sample_tick_latency_s", {}).get("latest")
            or metric_payloads.get("eval_matrix_latency_s", {}).get("latest")
            or 0.0
        )
        current["latest_total_tokens"] = (
            metric_payloads.get("eval_sample_tick_total_tokens", {}).get("latest")
            or metric_payloads.get("eval_matrix_total_tokens", {}).get("latest")
            or 0.0
        )
        snapshots.append(current)

    snapshots.sort(
        key=lambda item: (
            item.get("mode") != "agent",
            item.get("profile") != "raw_model",
            item.get("profile") != "harness_no_capes",
            item.get("variant_id") or "",
        )
    )
    return {
        "variant_count": len(snapshots),
        "variants": snapshots,
    }


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


def summarize_recent_eval_candidates(recent_reports: Iterable[Dict[str, Any]], *, limit: int = 6) -> list[Dict[str, Any]]:
    summaries: list[Dict[str, Any]] = []
    for report in recent_reports:
        proposal_metadata = dict(report.get("proposal_metadata") or {})
        override = canonical_eval_override(report.get("eval_harness_config_override"))
        if not override.get("system_prompt") and not override.get("context_files"):
            continue
        summaries.append(
            {
                "candidate_change_id": str(report.get("candidate_change_id") or ""),
                "proposer_tier": str(proposal_metadata.get("proposer_tier") or ""),
                "rationale": str(proposal_metadata.get("rationale") or ""),
                "expected_gain": str(proposal_metadata.get("expected_gain") or ""),
                "eval_harness_config_override": override,
            }
        )
        if len(summaries) >= limit:
            break
    return summaries


def _tokenize_prompt(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9_]+", (text or "").lower()) if len(token) >= 4}


def _prompt_tags(text: str) -> list[str]:
    normalized = (text or "").lower()
    tag_map = {
        "proposal_rationale": ["rationale", "expected_gain"],
        "testability": ["testable", "validate", "validated"],
        "idle_quiet": ["quiet testing mode", "remain idle", "background activity"],
        "directness": ["answer directly", "clearly", "concisely"],
        "tooling_restraint": ["unavailable tooling", "do not rely on background work"],
        "context_grounding": ["repository philosophy", "harness intent", "context"],
    }
    tags = [name for name, needles in tag_map.items() if all(needle in normalized for needle in needles)]
    return sorted(tags)


def describe_eval_mutation(current_config: Dict[str, Any], override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    base = canonical_eval_override(current_config)
    candidate = canonical_eval_override(override)
    base_context = set(base.get("context_files") or [])
    candidate_context = set(candidate.get("context_files") or [])
    prompt = candidate.get("system_prompt") or ""
    prompt_tokens = _tokenize_prompt(prompt)
    tags = _prompt_tags(prompt)
    context_added = sorted(candidate_context - base_context)
    context_removed = sorted(base_context - candidate_context)
    axes_changed: list[str] = []
    if context_added or context_removed:
        axes_changed.append("context")
    if prompt.strip() != (base.get("system_prompt") or "").strip():
        axes_changed.append("prompt")
    family_parts: list[str] = []
    if context_added:
        family_parts.append("add_ctx:" + ",".join(context_added[:3]))
    if context_removed:
        family_parts.append("drop_ctx:" + ",".join(context_removed[:3]))
    if tags:
        family_parts.append("tags:" + ",".join(tags[:3]))
    if "prompt" in axes_changed and not tags:
        family_parts.append("prompt:generic")
    if not family_parts:
        family_parts.append("no_change")
    return {
        "axes_changed": axes_changed,
        "context_added": context_added,
        "context_removed": context_removed,
        "prompt_tags": tags,
        "prompt_token_count": len(prompt_tokens),
        "prompt_token_set": prompt_tokens,
        "family": "|".join(family_parts),
        "override": candidate,
    }


def _jaccard_similarity(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def preflight_eval_proposal_relationship(
    current_config: Dict[str, Any],
    proposal_override: Optional[Dict[str, Any]],
    existing_override: Optional[Dict[str, Any]],
    *,
    proposal_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    proposal_config = normalize_eval_proposal_config(proposal_config)
    proposal_desc = describe_eval_mutation(current_config, proposal_override)
    existing_desc = describe_eval_mutation(current_config, existing_override)
    proposal_signature = eval_override_signature(proposal_override)
    existing_signature = eval_override_signature(existing_override)
    token_overlap = _jaccard_similarity(
        set(proposal_desc.get("prompt_token_set") or set()),
        set(existing_desc.get("prompt_token_set") or set()),
    )
    same_family = proposal_desc.get("family") == existing_desc.get("family")
    same_context_delta = (
        proposal_desc.get("context_added") == existing_desc.get("context_added")
        and proposal_desc.get("context_removed") == existing_desc.get("context_removed")
    )
    near_duplicate_overlap = float((proposal_config.get("resolution") or {}).get("near_duplicate_overlap", 0.92) or 0.92)
    family_overlap = float((proposal_config.get("resolution") or {}).get("family_overlap", 0.68) or 0.68)
    if proposal_signature == existing_signature:
        decision = "exact_duplicate"
        score = 1.0
    elif same_context_delta and proposal_desc.get("prompt_tags") == existing_desc.get("prompt_tags") and token_overlap >= near_duplicate_overlap:
        decision = "near_duplicate"
        score = token_overlap
    elif same_family and token_overlap >= family_overlap:
        decision = "same_family_retry"
        score = token_overlap
    elif same_family or same_context_delta:
        decision = "ambiguous"
        score = token_overlap
    else:
        decision = "keep_both_distinct"
        score = token_overlap
    return {
        "decision": decision,
        "score": round(score, 4),
        "same_family": same_family,
        "same_context_delta": same_context_delta,
        "token_overlap": round(token_overlap, 4),
        "proposal_family": proposal_desc.get("family"),
        "existing_family": existing_desc.get("family"),
        "proposal_features": {
            key: value
            for key, value in proposal_desc.items()
            if key != "prompt_token_set"
        },
        "existing_features": {
            key: value
            for key, value in existing_desc.items()
            if key != "prompt_token_set"
        },
    }


async def adjudicate_eval_proposal_relationship(
    proposal: Dict[str, Any],
    existing_candidate: Dict[str, Any],
    current_config: Dict[str, Any],
    *,
    preflight: Dict[str, Any],
    proposal_config: Optional[Dict[str, Any]],
    model_adapter_factory,
) -> Dict[str, Any]:
    proposal_config = normalize_eval_proposal_config(proposal_config)
    resolution_config = dict(proposal_config.get("resolution") or {})
    adjudicator_tier = str(resolution_config.get("adjudicator_tier", "trainer") or "trainer")
    vote_count = max(1, int(resolution_config.get("vote_count", 1) or 1))

    from strata.schemas.execution import TrainerExecutionContext, AgentExecutionContext

    adapter = model_adapter_factory()
    if adjudicator_tier == "agent":
        adapter.bind_execution_context(AgentExecutionContext(run_id=f"proposal_resolution_{int(datetime.now(timezone.utc).timestamp() * 1000)}"))
    else:
        adapter.bind_execution_context(TrainerExecutionContext(run_id=f"proposal_resolution_{int(datetime.now(timezone.utc).timestamp() * 1000)}"))

    adjudication_prompt = f"""
You are resolving whether two Strata harness-mutation proposals are duplicates, mergeable refinements, or distinct.
Return only JSON with this schema:
{{
  "decision": "exact_duplicate|near_duplicate|same_family_retry|merge_with_existing|supersedes_existing|keep_both_distinct",
  "confidence": 0.0,
  "reason": "short explanation",
  "merge_strategy": "optional short merge recommendation"
}}

Current harness config:
{json.dumps(canonical_eval_override(current_config), indent=2)}

New proposal:
{json.dumps({
    "candidate_change_id": proposal.get("candidate_change_id"),
    "rationale": proposal.get("rationale"),
    "expected_gain": proposal.get("expected_gain"),
    "override": canonical_eval_override(proposal.get("eval_harness_config_override")),
}, indent=2)}

Existing candidate:
{json.dumps(existing_candidate, indent=2)}

Deterministic preflight:
{json.dumps(preflight, indent=2)}

Use the deterministic preflight as strong evidence. Prefer merge/same-family outcomes over claiming novelty when the changes are mostly restatements of the same intervention.
""".strip()

    votes: list[Dict[str, Any]] = []
    for _ in range(vote_count):
        response = await adapter.chat(
            [{"role": "user", "content": adjudication_prompt}],
            temperature=0.0,
        )
        try:
            parsed = extract_json_object(response.get("content", ""))
        except Exception:
            continue
        decision = str(parsed.get("decision") or "keep_both_distinct")
        if decision not in {
            "exact_duplicate",
            "near_duplicate",
            "same_family_retry",
            "merge_with_existing",
            "supersedes_existing",
            "keep_both_distinct",
        }:
            decision = "keep_both_distinct"
        votes.append(
            {
                "decision": decision,
                "confidence": float(parsed.get("confidence", 0.5) or 0.5),
                "reason": str(parsed.get("reason") or "").strip(),
                "merge_strategy": str(parsed.get("merge_strategy") or "").strip(),
            }
        )
    if not votes:
        return {
            "decision": "keep_both_distinct",
            "confidence": 0.0,
            "reason": "LLM adjudication failed; defaulting to keep both distinct.",
            "merge_strategy": "",
            "votes": [],
        }
    tallies: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
    for vote in votes:
        tallies[vote["decision"]].append(vote)
    ranked = sorted(
        tallies.items(),
        key=lambda item: (
            len(item[1]),
            sum(v.get("confidence", 0.0) for v in item[1]) / max(1, len(item[1])),
        ),
        reverse=True,
    )
    winner, winner_votes = ranked[0]
    avg_confidence = sum(v.get("confidence", 0.0) for v in winner_votes) / max(1, len(winner_votes))
    return {
        "decision": winner,
        "confidence": round(avg_confidence, 4),
        "reason": next((v.get("reason") for v in winner_votes if v.get("reason")), ""),
        "merge_strategy": next((v.get("merge_strategy") for v in winner_votes if v.get("merge_strategy")), ""),
        "votes": votes,
    }


def _merge_context_files(left: list[str], right: list[str]) -> list[str]:
    merged: list[str] = []
    for path in [*(left or []), *(right or [])]:
        normalized = str(path or "").strip()
        if normalized and normalized not in merged:
            merged.append(normalized)
    return merged


def _deterministic_merged_eval_proposal(
    proposal: Dict[str, Any],
    existing_candidate: Dict[str, Any],
    current_config: Dict[str, Any],
    *,
    merge_strategy: str = "",
) -> Dict[str, Any]:
    proposal_override = canonical_eval_override(proposal.get("eval_harness_config_override"))
    existing_override = canonical_eval_override(existing_candidate.get("eval_harness_config_override"))
    proposal_prompt = str(proposal_override.get("system_prompt") or "")
    existing_prompt = str(existing_override.get("system_prompt") or "")
    merged_prompt = proposal_prompt if len(proposal_prompt) >= len(existing_prompt) else existing_prompt
    merged_context = _merge_context_files(
        existing_override.get("context_files") or current_config.get("context_files") or [],
        proposal_override.get("context_files") or current_config.get("context_files") or [],
    )
    rationale_parts = [
        str(existing_candidate.get("rationale") or "").strip(),
        str(proposal.get("rationale") or "").strip(),
    ]
    expected_gain_parts = [
        str(existing_candidate.get("expected_gain") or "").strip(),
        str(proposal.get("expected_gain") or "").strip(),
    ]
    merged_rationale = " ".join(part for part in rationale_parts if part).strip() or "Merged duplicate-family harness proposals into one synthesized candidate."
    if merge_strategy:
        merged_rationale += f" Merge strategy: {merge_strategy.strip()}"
    merged_expected_gain = " + ".join(part for part in expected_gain_parts if part) or "combine the strongest gains from both related proposals"
    return {
        "candidate_suffix": "merged_eval_mutation",
        "system_prompt": merged_prompt,
        "context_files": merged_context,
        "rationale": merged_rationale,
        "expected_gain": merged_expected_gain,
    }


async def synthesize_merged_eval_proposal(
    proposal: Dict[str, Any],
    existing_candidate: Dict[str, Any],
    current_config: Dict[str, Any],
    *,
    proposal_config: Optional[Dict[str, Any]],
    model_adapter_factory,
    merge_strategy: str = "",
) -> Dict[str, Any]:
    proposal_config = normalize_eval_proposal_config(proposal_config)
    resolution_config = dict(proposal_config.get("resolution") or {})
    adjudicator_tier = str(resolution_config.get("adjudicator_tier", "trainer") or "trainer")

    from strata.schemas.execution import TrainerExecutionContext, AgentExecutionContext

    adapter = model_adapter_factory()
    if adjudicator_tier == "agent":
        adapter.bind_execution_context(AgentExecutionContext(run_id=f"proposal_merge_{int(datetime.now(timezone.utc).timestamp() * 1000)}"))
    else:
        adapter.bind_execution_context(TrainerExecutionContext(run_id=f"proposal_merge_{int(datetime.now(timezone.utc).timestamp() * 1000)}"))

    proposal_override = canonical_eval_override(proposal.get("eval_harness_config_override"))
    existing_override = canonical_eval_override(existing_candidate.get("eval_harness_config_override"))
    deterministic_merge = _deterministic_merged_eval_proposal(
        proposal,
        existing_candidate,
        current_config,
        merge_strategy=merge_strategy,
    )
    merge_prompt = f"""
You are synthesizing a single merged Strata eval-harness mutation from two overlapping proposals.
Return only JSON with this schema:
{{
  "candidate_suffix": "short_slug_like_name",
  "system_prompt": "full replacement system prompt",
  "context_files": [".knowledge/specs/project_spec.md", ".knowledge/specs/constitution.md", "docs/spec/eval-brief.md"],
  "rationale": "why the merged proposal is better than either duplicate alone",
  "expected_gain": "what telemetry should improve"
}}

Current harness config:
{json.dumps(canonical_eval_override(current_config), indent=2)}

Existing candidate:
{json.dumps({
    "candidate_change_id": existing_candidate.get("candidate_change_id"),
    "override": existing_override,
    "rationale": existing_candidate.get("rationale"),
    "expected_gain": existing_candidate.get("expected_gain"),
}, indent=2)}

New proposal:
{json.dumps({
    "candidate_change_id": proposal.get("candidate_change_id"),
    "override": proposal_override,
    "rationale": proposal.get("rationale"),
    "expected_gain": proposal.get("expected_gain"),
}, indent=2)}

Suggested merge strategy:
{merge_strategy or "Unify the overlapping proposals into one stronger candidate without duplicating intent."}

Deterministic fallback merge:
{json.dumps(deterministic_merge, indent=2)}
""".strip()
    response = await adapter.chat([{"role": "user", "content": merge_prompt}], temperature=0.0)
    raw_content = response.get("content", "")
    try:
        merged_payload = extract_json_object(raw_content)
    except Exception:
        merged_payload = deterministic_merge
        merged_payload["parse_error"] = str(raw_content or "")[:2000]
    merged = _normalize_eval_candidate(
        "merged",
        merged_payload,
        current_config,
        metadata={
            "merge_of": [
                existing_candidate.get("candidate_change_id"),
                proposal.get("candidate_change_id"),
            ],
            "merge_strategy": merge_strategy,
            "synthesized": True,
        },
    )
    return merged


async def resolve_eval_proposal_against_history(
    proposal: Dict[str, Any],
    *,
    current_config: Dict[str, Any],
    recent_candidates: list[Dict[str, Any]],
    seen_candidates: Optional[list[Dict[str, Any]]] = None,
    proposal_config: Optional[Dict[str, Any]] = None,
    model_adapter_factory=None,
) -> Dict[str, Any]:
    proposal_config = normalize_eval_proposal_config(proposal_config)
    current_signature = eval_override_signature(current_config)
    proposal_signature = eval_override_signature(proposal.get("eval_harness_config_override"))
    if proposal_signature == current_signature:
        return {"decision": "matches_current_config", "should_evaluate": False, "reason": "matches_current_config"}

    candidates = list(recent_candidates or []) + list(seen_candidates or [])
    best_match = None
    best_preflight = None
    best_score = -1.0
    for candidate in candidates:
        preflight = preflight_eval_proposal_relationship(
            current_config,
            proposal.get("eval_harness_config_override"),
            candidate.get("eval_harness_config_override"),
            proposal_config=proposal_config,
        )
        score = float(preflight.get("score", 0.0) or 0.0)
        if score > best_score:
            best_score = score
            best_match = candidate
            best_preflight = preflight
    if not best_match or not best_preflight:
        return {"decision": "keep_both_distinct", "should_evaluate": True, "reason": "no_similar_candidate"}
    decision = str(best_preflight.get("decision") or "keep_both_distinct")
    if decision in {"exact_duplicate", "near_duplicate", "same_family_retry"}:
        return {
            "decision": decision,
            "should_evaluate": False,
            "reason": "deterministic_preflight",
            "compared_candidate_change_id": best_match.get("candidate_change_id"),
            "preflight": best_preflight,
        }
    if decision == "ambiguous" and bool((proposal_config.get("resolution") or {}).get("use_llm_for_ambiguous", True)) and model_adapter_factory is not None:
        adjudication = await adjudicate_eval_proposal_relationship(
            proposal,
            best_match,
            current_config,
            preflight=best_preflight,
            proposal_config=proposal_config,
            model_adapter_factory=model_adapter_factory,
        )
        final_decision = str(adjudication.get("decision") or "keep_both_distinct")
        merged_proposal = None
        if final_decision == "merge_with_existing":
            merged_proposal = await synthesize_merged_eval_proposal(
                proposal,
                best_match,
                current_config,
                proposal_config=proposal_config,
                model_adapter_factory=model_adapter_factory,
                merge_strategy=str(adjudication.get("merge_strategy") or ""),
            )
        return {
            "decision": final_decision,
            "should_evaluate": final_decision in {"supersedes_existing", "keep_both_distinct", "merge_with_existing"},
            "reason": "llm_adjudicated",
            "compared_candidate_change_id": best_match.get("candidate_change_id"),
            "preflight": best_preflight,
            "adjudication": adjudication,
            "proposal": merged_proposal,
        }
    return {
        "decision": "keep_both_distinct",
        "should_evaluate": True,
        "reason": "deterministic_distinct",
        "compared_candidate_change_id": best_match.get("candidate_change_id"),
        "preflight": best_preflight,
    }


def _proposal_prompt(
    proposer_tier: str,
    current_config: Dict[str, Any],
    *,
    recent_candidates: Optional[list[Dict[str, Any]]] = None,
    retry_reason: Optional[str] = None,
    proposal_config: Optional[Dict[str, Any]] = None,
) -> str:
    proposal_config = normalize_eval_proposal_config(proposal_config)
    recent_candidates = recent_candidates or []
    recent_block = ""
    if recent_candidates and proposal_config["novelty"]["include_recent_candidates_in_prompt"]:
        serialized = json.dumps(recent_candidates, indent=2)
        recent_block = f"""

Recent candidate families already explored:
{serialized}

Novelty requirement:
- Do not repeat or lightly restate one of the recent candidate families above.
- Prefer a different intervention class if possible, such as context selection, evaluation rubric, prompt framing, or proposal-selection criteria.
""".rstrip()

    retry_block = ""
    if retry_reason:
        retry_block = f"""

Your previous draft was rejected before evaluation because it was too similar to the current or recent harness config.
Retry with a materially different idea.
Duplicate reason: {retry_reason}
""".rstrip()

    return f"""
You are proposing one small harness-side change to improve weak-model self-improvement in Strata.
Return only JSON with this schema:
{{
  "candidate_suffix": "short_slug_like_name",
  "system_prompt": "full replacement system prompt",
  "context_files": [".knowledge/specs/project_spec.md", ".knowledge/specs/constitution.md", "docs/spec/eval-brief.md"],
  "rationale": "short explanation of why this should improve weak-model self-improvement",
  "expected_gain": "what telemetry should improve"
}}

Constraints:
- Propose a small, reversible change to the eval harness only.
- The change must be safe to apply to future eval runs from either proposer tier.
- Keep context_files short and repository-local.
- Optimize for the real goal: the weak model proposing and surviving system improvements.
- Avoid duplicates of the current harness config or recent already-tested mutations.{recent_block}{retry_block}

Current eval harness config:
{json.dumps(current_config, indent=2)}
""".strip()


def _normalize_eval_candidate(
    proposer_tier: str,
    proposal: Dict[str, Any],
    current_config: Dict[str, Any],
    *,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    suffix = slugify_candidate_suffix(str(proposal.get("candidate_suffix", proposer_tier)))
    result = {
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
    if metadata:
        result["generation_metadata"] = metadata
    return result


async def generate_eval_candidate_from_tier(
    proposer_tier: str,
    current_config: Dict[str, Any],
    model_adapter_factory,
    *,
    recent_candidates: Optional[list[Dict[str, Any]]] = None,
    recent_signatures: Optional[set[str]] = None,
    current_signature: Optional[str] = None,
    proposal_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    proposal_config = normalize_eval_proposal_config(proposal_config or get_active_eval_proposal_config())
    adapter = model_adapter_factory()
    if proposer_tier == "agent":
        from strata.schemas.execution import AgentExecutionContext

        context = AgentExecutionContext(run_id=f"bootstrap_proposal_{int(datetime.now(timezone.utc).timestamp() * 1000)}")
    else:
        from strata.schemas.execution import TrainerExecutionContext

        context = TrainerExecutionContext(run_id=f"bootstrap_proposal_{int(datetime.now(timezone.utc).timestamp() * 1000)}")
    adapter.bind_execution_context(context)

    recent_signatures = recent_signatures or set()
    current_signature = current_signature or eval_override_signature(current_config)
    base_inference_params = dict((proposal_config.get("inference") or {}).get(proposer_tier) or {})
    base_temperature = float(base_inference_params.get("temperature", 0.2 if proposer_tier == "agent" else 0.1) or 0.0)
    novelty_retry_count = int((proposal_config.get("inference") or {}).get("novelty_retry_count", 1) or 0)
    novelty_temperature_step = float((proposal_config.get("inference") or {}).get("novelty_temperature_step", 0.15) or 0.0)
    novelty_max_temperature = float((proposal_config.get("inference") or {}).get("novelty_max_temperature", 0.35) or 0.0)
    require_material_difference = bool((proposal_config.get("novelty") or {}).get("require_material_difference", True))
    latest_raw_content = ""
    latest_metadata: Dict[str, Any] = {"novelty_retry_count": 0}

    for attempt_index in range(novelty_retry_count + 1):
        retry_reason = None if attempt_index == 0 else "proposal matched current or recent eval override signature"
        proposal_prompt = _proposal_prompt(
            proposer_tier,
            current_config,
            recent_candidates=recent_candidates,
            retry_reason=retry_reason,
            proposal_config=proposal_config,
        )
        inference_params = dict(base_inference_params)
        inference_params["temperature"] = (
            base_temperature
            if attempt_index == 0
            else min(base_temperature + (novelty_temperature_step * attempt_index), novelty_max_temperature)
        )
        response = await adapter.chat(
            [{"role": "user", "content": proposal_prompt}],
            **inference_params,
        )
        latest_raw_content = response.get("content", "")
        try:
            proposal = extract_json_object(latest_raw_content)
        except Exception:
            continue
        normalized = _normalize_eval_candidate(
            proposer_tier,
            proposal,
            current_config,
            metadata={
                **latest_metadata,
                "novelty_retry_count": attempt_index,
                "inference_params": inference_params,
            },
        )
        signature = eval_override_signature(normalized["eval_harness_config_override"])
        if not require_material_difference or (signature != current_signature and signature not in recent_signatures):
            return normalized

    fallback = {
        "candidate_suffix": f"{proposer_tier}_fallback",
        "system_prompt": current_config.get("system_prompt") or "",
        "context_files": current_config.get("context_files") or [],
        "rationale": "Proposal generation returned malformed JSON or repeated a recent harness change; preserving the current config instead of failing the cycle.",
        "expected_gain": "No-op fallback to keep the bootstrap cycle alive while capturing proposer output for debugging.",
        "parse_error": str(latest_raw_content or "")[:2000],
    }
    return _normalize_eval_candidate(
        proposer_tier,
        fallback,
        current_config,
        metadata={"novelty_retry_count": 1, "fallback_reason": "duplicate_or_malformed"},
    )


async def generate_tool_candidate_from_tier(proposer_tier: str, *, tool_name: str, task_description: str, model_adapter_factory) -> Dict[str, Any]:
    adapter = model_adapter_factory()
    if proposer_tier == "agent":
        from strata.schemas.execution import AgentExecutionContext

        context = AgentExecutionContext(run_id=f"tool_bootstrap_{int(datetime.now(timezone.utc).timestamp() * 1000)}")
    else:
        from strata.schemas.execution import TrainerExecutionContext

        context = TrainerExecutionContext(run_id=f"tool_bootstrap_{int(datetime.now(timezone.utc).timestamp() * 1000)}")
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
        temperature=0.15 if proposer_tier == "trainer" else 0.25,
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
            if metadata.get("proposer_tier") == "agent":
                weak_promotions += 1
            elif metadata.get("proposer_tier") == "trainer":
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
        if metadata.get("proposer_tier") == "agent" and current.get("recommendation") == "promote" and weak_gain:
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
            float(item.get("recent_token_share_pct", 0.0) or 0.0),
            float(item.get("token_share_pct", 0.0) or 0.0),
            int(item.get("total_estimated_tokens", 0) or 0),
        ),
        reverse=True,
    )[:5]
    recent_spec_proposals = list_spec_proposals(storage, limit=5)
    eval_metric_rows = (
        storage.session.query(MetricModel)
        .filter(MetricModel.metric_name.in_([
            "eval_matrix_accuracy",
            "eval_matrix_latency_s",
            "eval_matrix_total_tokens",
            "eval_sample_tick_accuracy",
            "eval_sample_tick_latency_s",
            "eval_sample_tick_total_tokens",
        ]))
        .order_by(MetricModel.timestamp.desc())
        .limit(400)
        .all()
    )
    eval_profiles = summarize_eval_variant_metrics(eval_metric_rows)
    variant_registry = get_variant_rating_snapshot(storage)
    return {
        "generated_at": telemetry.get("generated_at"),
        "overview": telemetry.get("overview", {}),
        "ignition": ignition,
        "current_promoted_candidate": promoted_state.get("current"),
        "promotion_counts": {
            "agent": weak_promotions,
            "trainer": strong_promotions,
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
            "recent_estimated_tokens": int(context_telemetry.get("stats", {}).get("totals", {}).get("recent_estimated_tokens", 0) or 0),
            "all_time_estimated_tokens": int(context_telemetry.get("stats", {}).get("totals", {}).get("all_time_estimated_tokens", 0) or 0),
            "top_artifacts": top_context_artifacts,
            "file_scan": context_telemetry.get("file_scan", {}),
        },
        "eval_profiles": eval_profiles,
        "variant_registry": {
            "index_size": int(variant_registry.get("index_size", 0) or 0),
            "top_variants": (variant_registry.get("variants") or [])[-5:],
            "ratings": variant_registry.get("ratings", {}),
            "recent_matchups": (variant_registry.get("recent_matchups") or [])[-10:],
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
