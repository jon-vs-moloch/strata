"""
@module scripts.bench_refinery
@purpose Deterministic benchmark wrapper: run run_eval_matrix repeatedly on the WEAK (local agent) tier and publish the numbers.
@owns repetition orchestration, aggregation, JSON/Markdown result publishing
@does_not_own grading (correctness comes only from the `correct` field of run_eval_matrix samples), scaffold/model transport, cloud endpoints
@key_exports main

Usage:
    PYTHONPATH=. python scripts/bench_refinery.py \
        --endpoint-url http://127.0.0.1:1234/v1/chat/completions \
        --reps 2 \
        --conditions "model X, quant Y, hardware Z, server build N"
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from strata.eval.matrix import run_eval_matrix
from strata.eval.structured_eval import SUITES_DIR, load_suite
from strata.eval.harness_eval import EVAL_PROFILES, get_active_eval_harness_config
from strata.models.providers import GenericOpenAICompatibleProvider
from strata.models.registry import DEFAULT_CONFIG, registry


MARK_BEGIN = "<!-- bench_refinery:begin -->"
MARK_END = "<!-- bench_refinery:end -->"

DEFAULT_ENDPOINT_URL = DEFAULT_CONFIG["local_agent"]["endpoints"][0]["endpoint_url"]


def _split_csv(raw: str) -> List[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_commit() -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10, check=True
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def _models_endpoint_url(endpoint_url: str) -> str:
    # Everything from "/v1/" onward is stripped, then "/v1/models" is appended.
    base = endpoint_url.split("/v1/")[0] if "/v1/" in endpoint_url else endpoint_url.rstrip("/")
    return f"{base}/v1/models"


def _probe_served_models(endpoint_url: str) -> List[Any]:
    try:
        response = httpx.get(_models_endpoint_url(endpoint_url), timeout=10.0)
        response.raise_for_status()
        payload = response.json()
        return [entry.get("id") for entry in (payload.get("data") or []) if entry.get("id")]
    except Exception as exc:
        return [f"error: {exc}"]


def _apply_endpoint_override(endpoint_url: Optional[str], model: Optional[str]) -> None:
    """
    @summary Point the local_agent pool at the requested endpoint/model.
    @note This is the same config-replacement path strata/api/runtime_admin.py uses
          (POST /admin/registry -> registry._load_config(payload)); we never touch
          the trainer or remote pools and never call cloud endpoints.
    """
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["local_agent"]["allow_cloud"] = False
    cfg["local_agent"]["allow_local"] = True
    endpoint = cfg["local_agent"]["endpoints"][0]
    if endpoint_url:
        endpoint["endpoint_url"] = endpoint_url
    if model:
        endpoint["model"] = model
    registry._load_config(cfg)


def _suite_metadata(suite: str, cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    categories: Dict[str, int] = {}
    for case in cases:
        category = str(case.get("category") or suite)
        categories[category] = categories.get(category, 0) + 1
    suite_path = SUITES_DIR / f"{suite}.jsonl"
    return {
        "file_sha256": _sha256_bytes(suite_path.read_bytes()) if suite_path.exists() else None,
        "case_count": len(cases),
        "categories": categories,
    }


def _category_map(suite: str, cases: List[Dict[str, Any]]) -> Dict[str, str]:
    return {str(case["id"]): str(case.get("category") or suite) for case in cases}


def _run_suite_reps(suite: str, profiles: List[str], reps: int, categories: Dict[str, str]) -> Dict[str, Any]:
    rep_entries: List[Dict[str, Any]] = []
    for rep in range(1, reps + 1):
        started_at = time.perf_counter()
        report = asyncio.run(
            run_eval_matrix(
                suite_name=suite,
                include_strong=False,
                include_weak=True,
                include_context=True,
                profiles=profiles,
            )
        )
        wall_time_s = round(time.perf_counter() - started_at, 2)
        by_variant = {variant["variant_id"]: variant for variant in report.get("variants", [])}
        profile_entries: Dict[str, Any] = {}
        for profile in profiles:
            variant = by_variant.get(f"weak_{profile}", {})
            samples = [
                {
                    "case_id": sample.get("case_id"),
                    "correct": bool(sample.get("correct")),
                    "response": sample.get("response"),
                    "latency_s": sample.get("latency_s"),
                    "usage": sample.get("usage"),
                    "status": sample.get("status"),
                    "error_message": sample.get("error_message"),
                }
                for sample in variant.get("samples", [])
            ]
            profile_entries[profile] = {
                "variant_id": variant.get("variant_id", f"weak_{profile}"),
                "accuracy": variant.get("accuracy"),
                "error_count": variant.get("error_count"),
                "avg_latency_s": variant.get("avg_latency_s"),
                "total_tokens": variant.get("total_tokens"),
                # An empty answer usually means a reasoning model spent its whole
                # output budget thinking; it is graded wrong, and counted here so
                # it is visible rather than silently folded into accuracy.
                "empty_responses": sum(1 for s in samples if not str(s.get("response") or "").strip()),
                "samples": samples,
            }
        rep_entries.append({"rep": rep, "wall_time_s": wall_time_s, "profiles": profile_entries})
        print(
            f"[bench_refinery] suite={suite} rep={rep}/{reps} wall={wall_time_s:.1f}s",
            file=sys.stderr,
        )
    return {"suite": suite, "reps": rep_entries}


def _reps_stats(values: List[float]) -> Dict[str, Any]:
    if not values:
        return {"mean": None, "min": None, "max": None, "stdev": None}
    return {
        "mean": statistics.fmean(values),
        "min": min(values),
        "max": max(values),
        "stdev": statistics.stdev(values) if len(values) >= 2 else None,
    }


def _reps_values(rep_entries: List[Dict[str, Any]], profile: str, selector) -> List[float]:
    values: List[float] = []
    for rep_entry in rep_entries:
        entry = rep_entry["profiles"].get(profile) or {}
        value = selector(entry)
        if value is not None:
            values.append(float(value))
    return values


def _aggregate_suite(
    suite_entry: Dict[str, Any],
    profiles: List[str],
    categories: Dict[str, str],
) -> Dict[str, Any]:
    rep_entries = suite_entry["reps"]
    case_order = list(dict.fromkeys(
        sample["case_id"]
        for rep in rep_entries
        for sample in (rep["profiles"].get(profiles[0], {}).get("samples") or [])
    ))

    profile_aggregates: Dict[str, Any] = {}
    for profile in profiles:
        overall = _reps_stats(_reps_values(rep_entries, profile, lambda p: p.get("accuracy")))
        per_category: Dict[str, Any] = {}
        for category in dict.fromkeys(categories.values()):
            per_rep: List[float] = []
            for rep_entry in rep_entries:
                samples = rep_entry["profiles"].get(profile, {}).get("samples") or []
                pool = [s for s in samples if categories.get(str(s["case_id"])) == category]
                if pool:
                    per_rep.append(sum(1 for s in pool if s["correct"]) / len(pool))
            per_category[category] = _reps_stats(per_rep)
        profile_aggregates[profile] = {
            "overall": overall,
            "categories": per_category,
            "avg_latency_s_mean": (
                statistics.fmean(_reps_values(rep_entries, profile, lambda p: p.get("avg_latency_s")))
            ),
            "total_tokens_mean": (
                statistics.fmean(_reps_values(rep_entries, profile, lambda p: p.get("total_tokens")))
            ),
            "errors_sum": int(sum(_reps_values(rep_entries, profile, lambda p: p.get("error_count") or 0))),
            "empty_responses_sum": int(sum(_reps_values(rep_entries, profile, lambda p: p.get("empty_responses") or 0))),
        }

    paired: Dict[str, Any] = {}
    if len(profiles) >= 2:
        first, second = profiles[0], profiles[1]
        cases: List[Dict[str, Any]] = []
        for case_id in case_order:
            counts: Dict[str, int] = {}
            for profile in (first, second):
                correct_reps = 0
                for rep_entry in rep_entries:
                    samples = rep_entry["profiles"].get(profile, {}).get("samples") or []
                    match = next((s for s in samples if str(s["case_id"]) == case_id), None)
                    if match is not None and match["correct"]:
                        correct_reps += 1
                counts[profile] = correct_reps
            if counts[first] != counts[second]:
                cases.append(
                    {
                        "case_id": case_id,
                        "category": categories.get(case_id, suite_entry["suite"]),
                        "correct_count": counts,
                    }
                )
        paired = {"profiles": [first, second], "differing_cases": cases}

    return {"profiles": profile_aggregates, "paired_comparison": paired}


def _pct(value: Optional[float]) -> str:
    return f"{100.0 * value:.1f}%" if value is not None else "n/a"


def _render_accuracy_table(aggregate: Dict[str, Any], suite_meta: Dict[str, Dict[str, Any]], profiles: List[str]) -> List[str]:
    lines: List[str] = []
    header = ["Suite", "Category", "Items"] + [f"{p} mean (min-max)" for p in profiles]
    if len(profiles) >= 2:
        header.append(f"Delta (mean, {profiles[1]} - {profiles[0]})")
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * len(header))
    for suite, agg in aggregate.items():
        meta = suite_meta[suite]
        rows = [("all", meta["case_count"])]
        for category, count in meta["categories"].items():
            rows.append((category, count))
        for name, items in rows:
            row = [suite, name, str(items)]
            if name == "all":
                values = [agg["profiles"][p]["overall"] for p in profiles]
            else:
                values = [agg["profiles"][p]["categories"][name] for p in profiles]
            row += [
                f"{_pct(v['mean'])} ({_pct(v['min'])}-{_pct(v['max'])})" for v in values
            ]
            if len(profiles) >= 2:
                delta = (values[1]["mean"] - values[0]["mean"]) if values[1]["mean"] is not None and values[0]["mean"] is not None else None
                row.append(f"{100.0 * delta:+.1f}" if delta is not None else "n/a")
            lines.append("| " + " | ".join(row) + " |")
    return lines


def _render_markdown(
    aggregate: Dict[str, Any],
    suite_meta: Dict[str, Dict[str, Any]],
    reps: int,
    profiles: List[str],
    endpoint_url: str,
    conditions: str,
    served_models: List[Any],
    generated_at: str,
    git_commit: Optional[str],
    comfort_profile: str,
) -> str:
    lines: List[str] = [MARK_BEGIN, "## Refinery bench", "", "### Conditions", ""]
    lines.append(f"- Generated (UTC): {generated_at}")
    lines.append(f"- Git commit: {git_commit or 'unavailable'}")
    lines.append(f"- Conditions: {conditions or 'unspecified'}")
    lines.append(f"- Endpoint: {endpoint_url}")
    lines.append(f"- Served models: {', '.join(str(item) for item in served_models) or 'unknown'}")
    lines.append(f"- Reps: {reps}")
    lines.append(f"- Profiles: {', '.join(profiles)}")
    lines.append(f"- Temperature: 0.0")
    lines.append(f"- Pacing profile: {comfort_profile} (affects wall time only)")
    for suite, meta in suite_meta.items():
        digest = (meta["file_sha256"] or "")[:12]
        lines.append(f"- Suite {suite}: {meta['case_count']} cases (file sha256 {digest})")

    lines += ["", "### Accuracy", ""]
    lines += _render_accuracy_table(aggregate, suite_meta, profiles)

    lines += ["", "### Cost", "", "| Suite | Profile | Avg latency s (mean of reps) | Total tokens (mean of reps) | Errors (sum over reps) | Empty answers (sum over reps) |",
              "|---|---|---|---|---|---|"]
    for suite, agg in aggregate.items():
        for profile in profiles:
            entry = agg["profiles"][profile]
            lines.append(
                f"| {suite} | {profile} | {entry['avg_latency_s_mean']:.2f} | "
                f"{entry['total_tokens_mean']:.1f} | {entry['errors_sum']} | {entry['empty_responses_sum']} |"
            )

    reps_count = reps
    lines += ["", "### Cases where the profiles disagree", ""]
    disagreements = False
    for suite, agg in aggregate.items():
        paired = agg.get("paired_comparison") or {}
        names = paired.get("profiles") or []
        for case in paired.get("differing_cases") or []:
            disagreements = True
            counts = case["correct_count"]
            parts = ", ".join(f"{p} {counts[p]}/{reps_count}" for p in names)
            lines.append(f"- `{case['case_id']}` ({case['category']}): {parts}")
    if not disagreements:
        lines.append("- None recorded.")

    section = "\n".join(lines)
    section += f"\n{MARK_END}\n"
    return section


def _write_markdown_section(path: Path, section: str) -> None:
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if MARK_BEGIN in text and MARK_END in text:
            prefix = text.split(MARK_BEGIN, 1)[0]
            suffix = text.rsplit(MARK_END, 1)[1]
            path.write_text(prefix + section + suffix, encoding="utf-8")
        else:
            if text and not text.endswith("\n"):
                text += "\n"
            path.write_text(text + "\n" + section, encoding="utf-8")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Results\n\n" + section, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic WEAK-tier benchmark wrapper around run_eval_matrix.")
    parser.add_argument("--suites", default="refinery_mixed_v1,bootstrap_mcq_v1",
                        help="Comma-separated suite names.")
    parser.add_argument("--profiles", default="raw_model,harness_no_capes",
                        help="Comma-separated eval profile names.")
    parser.add_argument("--reps", type=int, default=2)
    parser.add_argument("--endpoint-url", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--conditions", default="", help="Free text describing model, quantisation, hardware, server build (stored verbatim).")
    parser.add_argument(
        "--comfort-profile",
        choices=["quiet", "balanced", "aggressive"],
        default="quiet",
        help="Provider pacing profile (operator_comfort.profile). Changes only the idle gap between "
        "requests, never the prompts or decoding. 'quiet' is the app default; a dedicated bench host can use 'aggressive'.",
    )
    parser.add_argument("--out-json", default="docs/results/refinery-bench.json")
    parser.add_argument("--out-md", default="docs/results.md")
    args = parser.parse_args()

    # Suites (strata/eval/suites) and harness context files are repo-relative paths.
    os.chdir(Path(__file__).resolve().parents[1])

    suites = _split_csv(args.suites)
    profiles = _split_csv(args.profiles)
    for profile in profiles:
        if profile not in EVAL_PROFILES:
            parser.error(
                f"Unknown eval profile '{profile}'. Valid profiles: {', '.join(sorted(EVAL_PROFILES))}"
            )
    if not profiles:
        parser.error("At least one profile is required.")
    if args.reps < 1:
        parser.error("--reps must be >= 1")

    endpoint_url = args.endpoint_url or DEFAULT_ENDPOINT_URL
    if args.endpoint_url or args.model:
        _apply_endpoint_override(args.endpoint_url, args.model)

    # Same in-process setter the API uses at startup (strata/api/main.py).
    GenericOpenAICompatibleProvider.set_runtime_policy({"operator_comfort": {"profile": args.comfort_profile}})
    served_models = _probe_served_models(endpoint_url)
    # Same idempotent table creation the API runs at startup (strata/api/main.py lifespan),
    # so the bench also works on a fresh clone whose database has never been initialised.
    from strata.storage.models import Base
    from strata.storage.services.main import _engine

    Base.metadata.create_all(_engine)
    # Captured before the runs: this is the scaffold the harness profiles actually used.
    harness_config = get_active_eval_harness_config()

    suite_cases = {suite: load_suite(suite) for suite in suites}
    suite_meta = {suite: _suite_metadata(suite, cases) for suite, cases in suite_cases.items()}
    category_maps = {suite: _category_map(suite, cases) for suite, cases in suite_cases.items()}

    results = []
    aggregate: Dict[str, Any] = {}
    for suite in suites:
        suite_entry = _run_suite_reps(suite, profiles, args.reps, category_maps[suite])
        results.append(suite_entry)
        aggregate[suite] = _aggregate_suite(suite_entry, profiles, category_maps[suite])

    generated_at = datetime.now(timezone.utc).isoformat()
    report = {
        "generated_at_utc": generated_at,
        "git_commit": _git_commit(),
        "conditions": args.conditions,
        "endpoint_url": endpoint_url,
        "served_models": served_models,
        "decoding": {
            "temperature": 0.0,
            "note": "run_eval_matrix calls the model with temperature 0.0; no sampling seed is used",
        },
        "pacing": {"comfort_profile": args.comfort_profile},
        "active_harness_config": {
            "system_prompt_sha256": _sha256_bytes(harness_config.get("system_prompt", "").encode("utf-8")),
            "context_files": harness_config.get("context_files"),
        },
        "suites": suite_meta,
        "reps": args.reps,
        "profiles": profiles,
        "results": results,
        "aggregate": aggregate,
    }

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    section = _render_markdown(
        aggregate,
        suite_meta,
        args.reps,
        profiles,
        endpoint_url,
        args.conditions,
        served_models,
        generated_at,
        report["git_commit"],
        args.comfort_profile,
    )
    out_md = Path(args.out_md)
    _write_markdown_section(out_md, section)

    print(str(out_json))
    print(str(out_md))
    sys.exit(0)


if __name__ == "__main__":
    main()
