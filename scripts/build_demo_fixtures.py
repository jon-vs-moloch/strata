"""
Build the workbench's demo-mode fixtures by seeding a throwaway database with a
small, invented sample run and recording what the real API returns for it.

Why record instead of hand-writing JSON: every derived number the UI shows in
demo mode (dashboard rollups, verifier cadence, context-cost totals, variant
ratings, ignition status) is computed by Strata's own code, so the demo can't
drift from the API's real response shapes.

Everything seeded here is sample data. The UI labels it "Demo data" wherever
it is shown. Nothing in this script calls a model.

Usage (from the repository root):

    PYTHONPATH=. ./venv/bin/python scripts/build_demo_fixtures.py

Writes strata_ui/src/demo/recorded.json.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "strata_ui" / "src" / "demo" / "recorded.json"

# The sample run is anchored to the moment it was recorded (helpers such as the
# rating and context-load recorders stamp "now" themselves). The UI shifts every
# timestamp by (viewer's now - ANCHOR) so relative times ("4m ago") stay meaningful.
ANCHOR = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)

_tmp_dir = tempfile.mkdtemp(prefix="strata-demo-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_dir}/strata-demo.db"
os.chdir(REPO_ROOT)
sys.path.insert(0, str(REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from strata.api import main as api_main  # noqa: E402
from strata.experimental.calibration import update_judge_trust  # noqa: E402
from strata.experimental.variants import record_variant_matchup  # noqa: E402
from strata.experimental.verifier import (  # noqa: E402
    VERIFIER_REGISTRY_KEY,
    _apply_registry_artifact,
    _default_registry,
)
from strata.messages.metadata import initialize_message_metadata  # noqa: E402
from strata.observability.context import record_context_load  # noqa: E402
from strata.sessions.metadata import ensure_session_metadata, set_session_metadata  # noqa: E402
from strata.storage.models import (  # noqa: E402
    AttemptModel,
    AttemptOutcome,
    AttemptResolution,
    Base,
    MessageModel,
    MetricModel,
    ParameterModel,
    ProviderTelemetrySnapshotModel,
    TaskModel,
    TaskState,
    TaskType,
)
from strata.storage.services.main import StorageManager, _engine  # noqa: E402


def at(minutes_ago: float) -> datetime:
    return ANCHOR - timedelta(minutes=minutes_ago)


def iso(minutes_ago: float) -> str:
    return at(minutes_ago).replace(tzinfo=timezone.utc).isoformat()


LANE_POOL = {"agent": "local_agent", "trainer": "trainer", "remote": "remote_agent"}


def add_task(storage, *, task_id, title, description, type_, state, lane, created, parent=None, constraints=None, depth=0):
    pool = LANE_POOL[lane]
    lane_key = "agent" if lane == "remote" else lane
    task = TaskModel(
        task_id=task_id,
        parent_task_id=parent,
        title=title,
        description=description,
        type=type_,
        state=state,
        depth=depth,
        session_id=f"{lane_key}:default",
        constraints={"lane": lane_key, "work_pool": pool, **(constraints or {})},
        created_at=at(created),
        updated_at=at(max(0.0, created - 6)),
    )
    storage.session.add(task)
    return task


def add_attempt(storage, task_id, *, started, minutes, outcome=None, reason=None, resolution=None, step=None):
    attempt = AttemptModel(
        task_id=task_id,
        started_at=at(started),
        ended_at=None if outcome is None else at(started - minutes),
        outcome=outcome,
        reason=reason,
        resolution=resolution,
        evidence={},
        artifacts={"step": step} if step else {},
    )
    storage.session.add(attempt)
    return attempt


def seed_tasks(storage) -> None:
    S, F = AttemptOutcome.SUCCEEDED, AttemptOutcome.FAILED

    # A live task tree on the local agent lane.
    add_task(storage, task_id="t-web-root", title="Make web_search resilient to rate limits",
             description="Search calls fail outright on HTTP 429. Add bounded retries and prove they help.",
             type_=TaskType.IMPL, state=TaskState.PUSHED, lane="agent", created=95)
    add_task(storage, task_id="t-web-survey", parent="t-web-root", depth=1,
             title="Survey failure modes in recent web_search traces",
             description="Classify the last 50 failed search calls by cause.",
             type_=TaskType.RESEARCH, state=TaskState.COMPLETE, lane="agent", created=92)
    add_attempt(storage, "t-web-survey", started=91, minutes=7, outcome=S,
                reason="Found 31 rate-limit failures, 12 timeouts, 7 parse errors.")
    add_task(storage, task_id="t-web-retry", parent="t-web-root", depth=1,
             title="Add bounded retry with jittered backoff",
             description="Retry 429 and timeouts up to 3 times; honour Retry-After.",
             type_=TaskType.IMPL, state=TaskState.WORKING, lane="agent", created=83)
    add_attempt(storage, "t-web-retry", started=82, minutes=11, outcome=F,
                reason="Verifier flagged the patch: retry loop ignored the Retry-After header.",
                resolution=AttemptResolution.REATTEMPT)
    add_attempt(storage, "t-web-retry", started=3, minutes=0, step="implementation")
    add_task(storage, task_id="t-web-judge", parent="t-web-root", depth=1,
             title="Verify retry behaviour against recorded 429 traces",
             description="Replay the surveyed failures and compare success rate before and after.",
             type_=TaskType.JUDGE, state=TaskState.PENDING, lane="agent", created=80)

    # Remote agent and trainer work.
    add_task(storage, task_id="t-split-dispatch", title="Split chat tool dispatch out of chat_runtime",
             description="chat_runtime.py loads 9k tokens of context per turn; move tool dispatch into its own module.",
             type_=TaskType.REFACTOR, state=TaskState.WORKING, lane="remote", created=40)
    add_attempt(storage, "t-split-dispatch", started=2, minutes=0, step="decomposition")
    add_task(storage, task_id="t-bootstrap", title="Bootstrap cycle: propose eval-harness improvements",
             description="Both tiers propose a harness change; score each against the baseline.",
             type_=TaskType.JUDGE, state=TaskState.WORKING, lane="trainer", created=22,
             constraints={"system_job": {"kind": "bootstrap_cycle"}})
    add_attempt(storage, "t-bootstrap", started=4, minutes=0, step="evaluation")
    add_task(storage, task_id="t-audit-abandon", title="Audit: agent abandoned 3 tasks citing missing context",
             description="Trace review of recent abandon_to_parent resolutions.",
             type_=TaskType.RESEARCH, state=TaskState.COMPLETE, lane="trainer", created=260)
    add_attempt(storage, "t-audit-abandon", started=258, minutes=14, outcome=S,
                reason="Root cause: the agent never inspected the repo before concluding context was missing.")
    question = add_task(storage, task_id="t-question", title="Confirm retention policy for archived chat sessions",
             description="Should archived sessions keep full text or only summaries after 30 days?",
             type_=TaskType.RESEARCH, state=TaskState.BLOCKED, lane="agent", created=55)
    question.human_intervention_required = True
    add_attempt(storage, "t-question", started=54, minutes=3, outcome=F,
                reason="Needs an operator decision: the constitution doesn't cover archive retention.",
                resolution=AttemptResolution.BLOCKED)
    add_task(storage, task_id="t-slug", title="Fix knowledge page slug collision for context-management",
             description="Two pages resolve to the same slug, so one silently shadows the other.",
             type_=TaskType.BUG_FIX, state=TaskState.COMPLETE, lane="agent", created=180)
    add_attempt(storage, "t-slug", started=178, minutes=9, outcome=F,
                reason="Patch renamed the page but left the old slug in provenance_index.",
                resolution=AttemptResolution.REATTEMPT)
    add_attempt(storage, "t-slug", started=166, minutes=8, outcome=S, reason="Slugs are unique; index rebuilt.")

    # History so throughput, verifier annealing and the attempt log have depth.
    history = [
        ("Summarise eval drift for bootstrap_mcq_v1", S), ("Classify judge disagreements from last cycle", S),
        ("Draft knowledge page: task-attempt ontology", S), ("Re-run sample tick on bootstrap_mcq", S),
        ("Extract repeated verifier findings into a signal", S), ("Compact context for chat_runtime", F),
        ("Check tool health for fetch_url", S), ("Answer: why did c-02 get rejected?", S),
        ("Trace review: slow decomposition on deep trees", S), ("Index new spec proposal", S),
        ("Rewrite stale codemap entries", S), ("Sample tick: bootstrap_mcq (with context)", F),
        ("Retention compaction dry-run", S), ("Prune duplicated knowledge sources", S),
        ("Diagnose parse errors in judge output", S), ("Sample tick: bootstrap_mcq", S),
    ]
    for idx, (title, outcome) in enumerate(history):
        task_id = f"t-hist-{idx:02d}"
        start = 300 + idx * 95
        lane = "trainer" if idx % 2 == 0 else "agent"
        add_task(storage, task_id=task_id, title=title, description=title + ".",
                 type_=TaskType.RESEARCH if idx % 3 else TaskType.JUDGE,
                 state=TaskState.COMPLETE if outcome == S else TaskState.ABANDONED,
                 lane=lane, created=start + 3)
        add_attempt(storage, task_id, started=start, minutes=4 + (idx % 5) * 2.5, outcome=outcome,
                    reason=None if outcome == S else "Output failed verification twice; handed back to parent.")
    storage.commit()


def seed_messages(storage) -> None:
    convo = {
        "trainer:default": (
            "Bootstrap supervision",
            [
                ("user", "How did the last bootstrap cycle go?", 34),
                ("assistant",
                 "Two candidates were scored against the baseline on `bootstrap_mcq_v1` over 3 runs each.\n\n"
                 "- **c-03 (trainer)** few-shot exemplars drawn from failed items: **+2.1 pt**, promoted.\n"
                 "- **c-04 (agent)** answer-letter-first prompting: +0.6 pt, *insufficient signal*, not promoted.\n\n"
                 "Secondary ignition is still not detected: no agent-proposed change has been promoted with a measured agent-tier gain yet.",
                 33),
                ("user", "Queue another cycle when the agent lane is idle.", 31),
                ("assistant", "Queued. It will start after the current retry task finishes verification.", 31),
            ],
        ),
        "agent:default": (
            "Tool reliability",
            [
                ("user", "web_search keeps failing when DuckDuckGo rate-limits us. Can you fix it?", 97),
                ("assistant",
                 "I'll break this into three steps: survey recent failures, add a bounded retry with backoff, "
                 "then verify against the recorded 429 traces. Tracking it as **Make web_search resilient to rate limits**.",
                 96),
                ("assistant",
                 "First attempt was rejected by the verifier: the retry loop ignored `Retry-After`. Reattempting with the header honoured.",
                 70),
            ],
        ),
    }
    for session_id, (title, rows) in convo.items():
        ensure_session_metadata(storage, session_id=session_id, opened_by="user", source_kind="chat")
        set_session_metadata(storage, session_id, {"title": title, "created_at": iso(rows[0][2])})
        for role, content, minutes_ago in rows:
            msg = MessageModel(session_id=session_id, role=role, content=content, created_at=at(minutes_ago))
            storage.session.add(msg)
            storage.session.flush()
            initialize_message_metadata(
                storage, message_id=msg.message_id, audience="user", delivery_channel="chat",
                source_kind="chat" if role == "user" else "assistant_reply",
                source_actor="user" if role == "user" else session_id.split(":")[0],
                communicative_act="request" if role == "user" else "response",
                sent_at=iso(minutes_ago),
            )
    storage.commit()


def seed_experiments(storage) -> None:
    suite = "bootstrap_mcq_v1"
    domain = f"eval_harness_full_eval:{suite}"
    reports = [
        ("c-01", "trainer", "Add a self-check step before the final answer", "promote", 0.038, 0.027, 640),
        ("c-02", "agent", "Shuffle answer options to expose position bias", "reject", -0.012, -0.008, 520),
        ("c-05", "trainer", "Prepend a constitution summary to every eval prompt", "reject", -0.004, -0.006, 300),
        ("c-03", "trainer", "Few-shot exemplars drawn from previously failed items", "promote", 0.021, 0.018, 45),
        ("c-04", "agent", "Ask for the answer letter first, then the justification", "insufficient_signal", 0.006, 0.002, 38),
    ]
    history = []
    for cid, tier, rationale, rec, structured, bench, minutes_ago in reports:
        report = {
            "candidate_change_id": cid,
            "baseline_change_id": "baseline",
            "evaluation_kind": "full_eval",
            "run_count": 3,
            "suite_name": suite,
            "recommendation": rec,
            "notes": rationale,
            "success": True,
            "valid": True,
            "baseline_metrics": {"structured_eval_harness_accuracy": 0.612},
            "candidate_metrics": {"structured_eval_harness_accuracy": round(0.612 + structured, 4)},
            "deltas": {"structured_eval_harness_accuracy": structured, "benchmark_harness_score": bench},
            "proposal_metadata": {"proposer_tier": tier, "rationale": rationale},
            "promotion_readiness": {"ready_for_promotion": rec == "promote"},
            "diagnostic_review": {
                "status": "ok",
                "primary_failure_mode": "position_bias" if cid == "c-02" else "shallow_reasoning",
                "recommended_fix": rationale,
                "summary": f"{tier} proposal: {rationale.lower()}.",
            },
            "prediction_record": {"predicted_outcome": "improve", "confidence": 0.7 if tier == "trainer" else 0.55,
                                  "domains_affected": ["eval"]},
            "calibration_record": {
                "actual_outcome": "improve" if structured > 0.01 else ("regress" if structured < 0 else "neutral"),
                "direction_correct": 1.0 if structured > 0.01 else 0.0,
                "calibration_score": 0.86 if structured > 0.01 else 0.35,
            },
            "variant_assignment": {"family": "eval_harness", "baseline_variant_id": "harness:baseline",
                                   "candidate_variant_id": f"harness:{cid}"},
            "variant_rating_snapshot": {"domain": domain},
            "task_associations": {"source_task_id": "t-bootstrap" if minutes_ago < 60 else None},
            "recorded_at": iso(minutes_ago),
        }
        storage.parameters.set_parameter(key=f"experiment_report:{cid}", value=report, description="Demo experiment report")
        storage.session.flush()
        row = storage.session.get(ParameterModel, f"experiment_report:{cid}")
        row.updated_at = at(minutes_ago)
        if rec == "promote":
            history.append({"candidate_change_id": cid, "recommendation": rec, "recorded_at": iso(minutes_ago),
                            "promoted_at": iso(minutes_ago - 1), "proposal_metadata": {"proposer_tier": tier}})
        record_variant_matchup(storage, domain=domain, left_variant_id=f"harness:{cid}",
                               right_variant_id="harness:baseline", left_score=1.0 if structured > 0.005 else (0.5 if structured >= 0 else 0.0),
                               context={"candidate_change_id": cid})
        update_judge_trust(
            storage, judge_tier="trainer",
            prediction={"predicted_outcome": "improve", "confidence": 0.7, "domains_affected": ["eval"]},
            calibration_record=report["calibration_record"],
        )
    # A few head-to-heads between promoted candidates.
    for left, right, score in [("c-03", "c-01", 0.5), ("c-01", "c-02", 1.0), ("c-03", "c-04", 1.0), ("c-04", "c-02", 1.0)]:
        record_variant_matchup(storage, domain=domain, left_variant_id=f"harness:{left}",
                               right_variant_id=f"harness:{right}", left_score=score)
    storage.parameters.set_parameter(
        key="promoted_eval_candidates",
        value={"current": "c-03", "history": history, "archived_count": 0},
        description="Accepted eval-harness candidates and their promotion history.",
    )

    # Accuracy series for the eval snapshot sparklines.
    series = {
        "agent:local:ctx": ("agent", True, [0.58, 0.59, 0.61, 0.6, 0.62, 0.63, 0.63, 0.64, 0.65, 0.64, 0.66, 0.67]),
        "agent:local:no-ctx": ("agent", False, [0.55, 0.55, 0.57, 0.56, 0.56, 0.58, 0.57, 0.59, 0.58, 0.59, 0.6, 0.6]),
        "trainer:cloud:ctx": ("trainer", True, [0.81, 0.82, 0.82, 0.83, 0.84, 0.83, 0.85, 0.85, 0.86, 0.86, 0.87, 0.87]),
    }
    for variant_id, (mode, ctx, values) in series.items():
        for idx, value in enumerate(values):
            minutes_ago = (len(values) - idx) * 55
            details = {"variant_id": variant_id, "mode": mode, "include_context": ctx, "suite_name": suite,
                       "profile": "sample_tick", "sampled": True}
            storage.session.add(MetricModel(metric_name="eval_sample_tick_accuracy", value=value, timestamp=at(minutes_ago),
                                            run_mode="weak_eval" if mode == "agent" else "strong_supervisor",
                                            execution_context=mode, details=details))
            storage.session.add(MetricModel(metric_name="eval_sample_tick_latency_s",
                                            value=(2.4 if mode == "agent" else 1.1) + (idx % 3) * 0.2,
                                            timestamp=at(minutes_ago), execution_context=mode, details=details))
    storage.commit()


def seed_verifier(storage) -> None:
    registry = _default_registry()
    for mode, verdicts in {
        "agent": ["verified"] * 11 + ["flawed"] * 2 + ["uncertain"],
        "trainer": ["verified"] * 6,
    }.items():
        for verdict in verdicts:
            registry = _apply_registry_artifact(registry, mode=mode, artifact={"verdict": verdict})
    storage.parameters.set_parameter(VERIFIER_REGISTRY_KEY, registry, description="Demo verifier registry")
    storage.commit()


def seed_knowledge() -> list:
    """Compile the repository's durable docs into knowledge pages the way the
    compactor does, without letting the page store mirror them back to disk."""
    import importlib.util

    from strata.knowledge.pages import KnowledgePageStore

    spec = importlib.util.spec_from_file_location("compact_knowledge", REPO_ROOT / "scripts" / "compact_knowledge.py")
    compactor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compactor)
    KnowledgePageStore._mirror_page_to_disk = lambda self, page: None
    slugs = []
    for page in compactor._iter_blueprint_pages(iso(600)):
        compactor._persist_page_with_retry(page)
        slugs.append(page["slug"])
    return slugs


def seed_context_and_transport(storage) -> None:
    loads = [
        ("spec", ".knowledge/specs/constitution.md", 1450, "chat_runtime", 6),
        ("spec", ".knowledge/specs/project_spec.md", 2380, "chat_runtime", 6),
        ("knowledge_page", "task-attempt-ontology", 1120, "research", 4),
        ("knowledge_page", "context-management", 860, "research", 3),
        ("file", "strata/api/chat_runtime.py", 9120, "implementation", 3),
        ("file", "strata/orchestrator/worker/attempt_runner.py", 4210, "implementation", 2),
        ("task_history", "t-web-retry", 1980, "attempt_runner", 5),
        ("memory", "semantic:web_search failures", 640, "research", 4),
    ]
    for artifact_type, identifier, tokens, source, repeats in loads:
        for _ in range(repeats):
            record_context_load(artifact_type=artifact_type, identifier=identifier, content="tok " * tokens,
                                source=source, metadata={"demo": True})
    snapshot = {
        "local|http://127.0.0.1:1234/v1/chat/completions": {
            "request_count": 412, "success_count": 398, "error_count": 14, "throttled_count": 37, "rate_limit_hits": 0,
            "avg_wait_ms": 420.5, "avg_latency_ms": 2380.2, "adaptive_min_interval_ms": 595.0, "effective_min_interval_ms": 595.0,
        },
        "cloud|https://generativelanguage.googleapis.com": {
            "request_count": 96, "success_count": 95, "error_count": 1, "throttled_count": 12, "rate_limit_hits": 2,
            "avg_wait_ms": 1310.0, "avg_latency_ms": 1105.7, "adaptive_min_interval_ms": 0.0, "effective_min_interval_ms": 4000.0,
        },
        "cloud|https://openrouter.ai/api/v1/chat/completions": {
            "request_count": 41, "success_count": 41, "error_count": 0, "throttled_count": 3, "rate_limit_hits": 0,
            "avg_wait_ms": 210.0, "avg_latency_ms": 1820.4, "adaptive_min_interval_ms": 0.0, "effective_min_interval_ms": 1000.0,
        },
    }
    storage.session.add(ProviderTelemetrySnapshotModel(snapshot=snapshot, recorded_at=at(1)))
    storage.commit()


GET_ENDPOINTS = [
    "/tasks", "/sessions", "/messages?session_id=trainer:default", "/messages?session_id=agent:default",
    "/admin/telemetry", "/admin/providers/telemetry", "/admin/dashboard", "/admin/context/loaded",
    "/admin/routing", "/admin/specs", "/admin/spec_proposals", "/admin/knowledge/pages", "/admin/knowledge/sources",
    "/admin/storage/retention", "/admin/variants/ratings", "/admin/predictions/trust", "/admin/evals/proposal_config",
    "/admin/evals/jobs", "/admin/health", "/admin/settings", "/admin/registry", "/admin/registry/presets",
    "/admin/registry/catalog", "/admin/procedures", "/admin/experiments/history", "/admin/experiments/secondary_ignition",
    "/admin/context/telemetry", "/admin/verifier/policy", "/admin/evals/suites", "/admin/evals/config", "/admin/test",
    "/admin/logs", "/models", "/admin/feedback/signals", "/admin/knowledge/maintenance", "/admin/host/telemetry",
    "/admin/predictions/calibration",
]


def scrub(value):
    """Drop machine-specific absolute paths from recorded responses."""
    root = str(REPO_ROOT)
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        value = value.replace(root + "/", "").replace(root, ".").replace(_tmp_dir, "<demo-db>")
        return re.sub(r"/(?:home|Users)/[^\s\"']+", "<local-path>", value)
    return value


def main() -> None:
    Base.metadata.create_all(_engine)
    storage = StorageManager()
    try:
        seed_tasks(storage)
        seed_messages(storage)
        seed_experiments(storage)
        seed_verifier(storage)
        seed_context_and_transport(storage)
    finally:
        storage.close()
    knowledge_slugs = seed_knowledge()

    # No context manager: lifespan (and the worker) never starts. The base URL is
    # the API's real loopback address, because the API refuses other Host headers.
    client = TestClient(api_main.app, base_url="http://127.0.0.1:8000")
    recorded = {}
    for path in GET_ENDPOINTS:
        response = client.get(path)
        if response.status_code != 200:
            print(f"skip {path}: HTTP {response.status_code}", file=sys.stderr)
            continue
        recorded[path] = scrub(response.json())
    for path in ["/admin/knowledge/pages?limit=6&audience=operator", "/admin/knowledge/pages?audience=operator"]:
        response = client.get(path)
        if response.status_code == 200:
            recorded[path] = scrub(response.json())
    for slug in knowledge_slugs:
        response = client.get(f"/admin/knowledge/pages/{slug}", params={"audience": "operator"})
        if response.status_code == 200:
            recorded[f"/admin/knowledge/pages/{slug}"] = scrub(response.json())
    for task_id in ["t-web-retry", "t-web-root", "t-bootstrap", "t-slug", "t-question", "t-split-dispatch"]:
        response = client.get(f"/admin/tasks/{task_id}/detail")
        if response.status_code == 200:
            recorded[f"/admin/tasks/{task_id}/detail"] = scrub(response.json())

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_about": "Sample data for the workbench's demo mode. Seeded by scripts/build_demo_fixtures.py and recorded from the real API. Not a real run.",
        "anchor": ANCHOR.replace(tzinfo=timezone.utc).isoformat(),
        "responses": recorded,
    }
    OUTPUT.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)} ({len(recorded)} responses, {OUTPUT.stat().st_size // 1024} KiB)")


if __name__ == "__main__":
    main()
