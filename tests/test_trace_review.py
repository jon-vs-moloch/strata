import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from strata.api import main as api_main
from strata.api import message_feedback
from strata.api.runtime_admin import register_runtime_admin_routes
from strata.eval.job_runner import run_eval_job_task
from strata.experimental.audit_registry import get_audit_artifact, get_timeline_artifact
from strata.experimental.artifact_pipeline import append_trace_review_to_session
from strata.feedback.signals import list_feedback_signals, register_feedback_signal
from strata.experimental.trace_review import build_task_trace_summary, list_attempt_observability_artifacts, review_trace
from strata.experimental.trace_review import build_attempt_intelligence, render_attempt_intelligence
from strata.observability.writer import enqueue_attempt_observability_artifact, flush_observability_writes
from strata.storage.models import Base, AttemptOutcome, TaskState, TaskType
from strata.storage.services.main import StorageManager


def make_storage():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    return StorageManager(session=session)


class FakeModelAdapter:
    def __init__(self):
        self.bound_context = None
        self.prompts = []

    def bind_execution_context(self, context):
        self.bound_context = context

    async def chat(self, messages, **_kwargs):
        self.prompts.append(messages)
        return {
            "content": (
                '{"summary":"Task got stuck before using local inspection.","overall_assessment":"needs_intervention",'
                '"primary_failure_mode":"tool_avoidance","evidence":["No repo inspection"],'
                '"targeted_interventions":[{"kind":"tooling","target":"research prompt",'
                '"description":"Require list_directory before claiming missing access.","priority":"high"}],'
                '"telemetry_to_watch":["research_tool_usage"],"confidence":0.82}'
            )
        }

    def extract_structured_object(self, raw_content):
        return __import__("json").loads(raw_content)


def test_build_task_trace_summary_includes_attempts_children_and_messages():
    storage = make_storage()
    task = storage.tasks.create(
        title="Inspect repo",
        description="Find the current system bottleneck.",
        session_id="trace-session",
        state=TaskState.WORKING,
        type=TaskType.RESEARCH,
        constraints={"associated_reports": [{"candidate_change_id": "candidate_a"}]},
    )
    storage.tasks.create(
        title="Child task",
        description="Follow-up",
        session_id="trace-session",
        parent_task_id=task.task_id,
        state=TaskState.PENDING,
        type=TaskType.IMPL,
    )
    attempt = storage.attempts.create(task_id=task.task_id)
    attempt.outcome = AttemptOutcome.FAILED
    attempt.reason = "Said it could not access the repo."
    attempt.artifacts = {"tool_calls": 0}
    storage.messages.create(
        role="assistant",
        content="I cannot see the codebase yet.",
        session_id="trace-session",
        task_id=task.task_id,
    )
    storage.commit()

    summary = build_task_trace_summary(storage, task_id=task.task_id)

    assert summary["task"]["task_id"] == task.task_id
    assert summary["attempt_count"] == 1
    assert summary["attempts"][0]["reason"] == "Said it could not access the repo."
    assert summary["child_tasks"][0]["parent_task_id"] == task.task_id
    assert summary["messages"][0]["associated_task_id"] == task.task_id
    assert summary["associated_reports"][0]["candidate_change_id"] == "candidate_a"
    assert summary["repo_fact_checks"] == []


def test_list_attempt_observability_artifacts_returns_scan_friendly_summary():
    storage = make_storage()
    task = storage.tasks.create(
        title="Inspect repo",
        description="Find the current system bottleneck.",
        session_id="trace-session",
        state=TaskState.WORKING,
        type=TaskType.RESEARCH,
    )
    attempt = storage.attempts.create(task_id=task.task_id)
    storage.commit()
    enqueue_attempt_observability_artifact(
        {
            "task_id": task.task_id,
            "attempt_id": attempt.attempt_id,
            "session_id": task.session_id,
            "artifact_kind": "failure_autopsy",
            "payload": {
                "reason": "Agent iteration limit reached after repeated manual recovery.",
                "evidence": {"failure_kind": "iteration_budget_exhausted"},
            },
        }
    )
    flush_observability_writes(lambda: storage)

    artifacts = list_attempt_observability_artifacts(storage, task_id=task.task_id)

    assert artifacts[0]["artifact_kind"] == "failure_autopsy"
    assert "iteration_budget_exhausted" in artifacts[0]["summary"]


def test_runtime_admin_exposes_attempt_observability_endpoint(tmp_path):
    db_path = tmp_path / "runtime-admin-observability.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    storage = StorageManager(session=session_factory())
    task = storage.tasks.create(
        title="Inspect repo",
        description="Find the current system bottleneck.",
        session_id="trace-session",
        state=TaskState.WORKING,
        type=TaskType.RESEARCH,
    )
    attempt = storage.attempts.create(task_id=task.task_id)
    storage.commit()
    enqueue_attempt_observability_artifact(
        {
            "task_id": task.task_id,
            "attempt_id": attempt.attempt_id,
            "session_id": task.session_id,
            "artifact_kind": "plan_review",
            "payload": {
                "plan_health": "degraded",
                "recommendation": "internal_replan",
                "rationale": "The branch is looping without new evidence.",
            },
        }
    )
    flush_observability_writes(lambda: storage)

    app = FastAPI()

    class DummyWorker:
        _running_task = None
        status = {}

        def pause(self, *_args, **_kwargs):
            return None

        def resume(self, *_args, **_kwargs):
            return None

        def stop_current(self, *_args, **_kwargs):
            return False

        async def enqueue_runnable_tasks(self, *_args, **_kwargs):
            return 0

        async def wait_until_idle(self, timeout=10.0):
            return True

        def clear_queue(self):
            return 0

    class DummyBroadcaster:
        async def subscribe(self):
            return None

        async def unsubscribe(self, queue):
            return None

    class DummyHotReloader:
        def list_experimental(self):
            return []

        async def promote(self, module):
            return type("Result", (), {"success": True, "module": module, "rolled_back": False, "message": "ok", "validation": None})()

        def rollback(self, module):
            return type("Result", (), {"success": True, "module": module, "message": "ok"})()

    register_runtime_admin_routes(
        app,
        get_storage=lambda: StorageManager(session=session_factory()),
        model_adapter=FakeModelAdapter(),
        global_settings={},
        normalized_settings=lambda payload=None: payload or {},
        settings_parameter_key="settings",
        settings_parameter_description="settings",
        worker=DummyWorker(),
        event_broadcaster=DummyBroadcaster(),
        hotreloader=DummyHotReloader(),
        base_dir=".",
    )

    client = TestClient(app)
    response = client.get(f"/admin/observability/attempts?task_id={task.task_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["artifacts"][0]["artifact_kind"] == "plan_review"
    assert payload["artifacts"][0]["payload"]["recommendation"] == "internal_replan"
    storage.close()


def test_attempt_intelligence_summarizes_branch_and_artifacts():
    storage = make_storage()
    parent = storage.tasks.create(
        title="Parent task",
        description="Parent branch.",
        session_id="trace-session",
        state=TaskState.WORKING,
        type=TaskType.RESEARCH,
    )
    child = storage.tasks.create(
        title="Child task",
        description="Child branch.",
        session_id="trace-session",
        state=TaskState.WORKING,
        type=TaskType.RESEARCH,
        parent_task_id=parent.task_id,
    )
    parent_attempt = storage.attempts.create(task_id=parent.task_id)
    parent_attempt.outcome = AttemptOutcome.FAILED
    parent_attempt.reason = "Agent iteration limit reached."
    parent_attempt.evidence = {"failure_kind": "iteration_budget_exhausted"}
    child_attempt = storage.attempts.create(task_id=child.task_id)
    child_attempt.outcome = AttemptOutcome.FAILED
    child_attempt.reason = "Agent iteration limit reached."
    child_attempt.evidence = {
        "failure_kind": "iteration_budget_exhausted",
        "autopsy": {"failure_kind": "iteration_budget_exhausted"},
    }
    storage.commit()
    enqueue_attempt_observability_artifact(
        {
            "task_id": child.task_id,
            "attempt_id": child_attempt.attempt_id,
            "session_id": child.session_id,
            "artifact_kind": "plan_review",
            "payload": {
                "plan_health": "degraded",
                "recommendation": "internal_replan",
                "rationale": "The branch is looping.",
            },
        }
    )
    flush_observability_writes(lambda: storage)

    intelligence = build_attempt_intelligence(storage, task=child, attempt_id=child_attempt.attempt_id)
    rendered = render_attempt_intelligence(intelligence)

    assert intelligence["branch_failure_count"] == 1
    assert intelligence["lineage_iteration_failures"] == 2
    assert intelligence["top_failure_kinds"][0]["failure_kind"] == "iteration_budget_exhausted"
    assert "Attempt Intelligence:" in rendered
    assert "Lineage iteration failures: 2" in rendered


def test_build_task_trace_summary_fact_checks_canonical_spec_paths():
    storage = make_storage()
    task = storage.tasks.create(
        title="Alignment task",
        description="Check whether the canonical spec files exist.",
        session_id="trace-session",
        state=TaskState.BLOCKED,
        type=TaskType.RESEARCH,
        constraints={
            "spec_paths": [".knowledge/specs/constitution.md", ".knowledge/specs/project_spec.md"],
        },
    )
    storage.commit()

    summary = build_task_trace_summary(storage, task_id=task.task_id)

    checks = {item["path"]: item for item in summary["repo_fact_checks"]}
    assert checks[".knowledge/specs/constitution.md"]["exists"] is True
    assert checks[".knowledge/specs/project_spec.md"]["exists"] is True


def test_build_task_trace_summary_includes_attempt_note_excerpt(monkeypatch, tmp_path):
    repo_root = tmp_path
    note_path = repo_root / ".knowledge" / "wip_research_demo.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(
        "Missing canonical spec files at `.knowledge/specs/constitution.md` and "
        "`.knowledge/specs/project_spec.md`.",
        encoding="utf-8",
    )

    import strata.experimental.trace_review as trace_review

    monkeypatch.setattr(trace_review, "_repo_root", lambda: repo_root)

    storage = make_storage()
    task = storage.tasks.create(
        title="Alignment task",
        description="Inspect repo state.",
        session_id="trace-session",
        state=TaskState.BLOCKED,
        type=TaskType.RESEARCH,
    )
    attempt = storage.attempts.create(task_id=task.task_id)
    attempt.outcome = AttemptOutcome.FAILED
    attempt.reason = "Agent iteration limit reached. Partial context saved to durable `.knowledge` library at: ./.knowledge/wip_research_demo.md"
    storage.commit()

    summary = trace_review.build_task_trace_summary(storage, task_id=task.task_id)

    assert summary["attempt_note_excerpts"][0]["path"] == ".knowledge/wip_research_demo.md"
    assert "Missing canonical spec files" in summary["attempt_note_excerpts"][0]["excerpt"]


def test_review_trace_supports_weak_reviewer_tier():
    adapter = FakeModelAdapter()

    result = asyncio.run(
        review_trace(
            adapter,
            trace_kind="generic_trace",
            trace_summary={"foo": "bar"},
            reviewer_tier="agent",
            candidate_change_id="candidate_x",
        )
    )

    assert result["status"] == "ok"
    assert result["reviewer_tier"] == "agent"
    assert adapter.bound_context.mode == "agent"
    assert adapter.bound_context.evaluation_run is True


def test_review_trace_overrides_stale_missing_file_premise_with_repo_fact_check():
    adapter = FakeModelAdapter()

    result = asyncio.run(
        review_trace(
            adapter,
            trace_kind="task_trace",
            trace_summary={
                "task": {
                    "title": "Alignment task",
                    "description": "Investigate whether `.knowledge/specs/project_spec.md` is missing.",
                },
                "attempts": [
                    {
                        "reason": (
                            "Observed repository contains neither `.knowledge/specs/constitution.md` "
                            "nor `.knowledge/specs/project_spec.md`."
                        )
                    }
                ],
                "messages": [],
                "repo_fact_checks": [
                    {"path": ".knowledge/specs/constitution.md", "exists": True, "is_file": True},
                    {"path": ".knowledge/specs/project_spec.md", "exists": True, "is_file": True},
                ],
            },
            reviewer_tier="trainer",
        )
    )

    assert result["overall_assessment"] == "needs_intervention"
    assert result["primary_failure_mode"] == "repo_fact_miss"
    assert any("Deterministic repo fact-check" in item for item in result["evidence"])
    assert "repo_fact_mismatch_rate" in result["telemetry_to_watch"]


def test_review_trace_uses_attempt_note_excerpt_for_repo_fact_override():
    adapter = FakeModelAdapter()

    result = asyncio.run(
        review_trace(
            adapter,
            trace_kind="task_trace",
            trace_summary={
                "task": {
                    "title": "Alignment task",
                    "description": "Inspect repo state.",
                },
                "attempts": [
                    {
                        "reason": "Agent iteration limit reached. Partial context saved to durable `.knowledge` library at: ./.knowledge/wip_research_demo.md"
                    }
                ],
                "messages": [],
                "attempt_note_excerpts": [
                    {
                        "path": ".knowledge/wip_research_demo.md",
                        "excerpt": (
                            "Missing canonical spec files at `.knowledge/specs/constitution.md` and "
                            "`.knowledge/specs/project_spec.md`."
                        ),
                    }
                ],
                "repo_fact_checks": [
                    {"path": ".knowledge/specs/constitution.md", "exists": True, "is_file": True},
                    {"path": ".knowledge/specs/project_spec.md", "exists": True, "is_file": True},
                ],
            },
            reviewer_tier="trainer",
        )
    )

    assert result["primary_failure_mode"] == "repo_fact_miss"


def test_review_trace_escalates_repeated_verifier_failures_into_supervision_gap():
    adapter = FakeModelAdapter()

    result = asyncio.run(
        review_trace(
            adapter,
            trace_kind="task_trace",
            trace_summary={
                "task": {
                    "title": "Procedure: Operator Onboarding",
                    "constraints": {
                        "verifier_reviews": [
                            {
                                "verdict": "uncertain",
                                "recommended_action": "verify_more",
                            }
                        ],
                        "trace_reviews": [
                            {
                                "overall_assessment": "review_unavailable",
                            }
                        ],
                    },
                },
                "attempts": [
                    {
                        "reason": "Agent iteration limit reached.",
                        "artifacts": {
                            "verifier": {
                                "verdict": "flawed",
                                "recommended_action": "revise",
                            }
                        },
                    }
                ],
                "messages": [],
                "repo_fact_checks": [],
                "attempt_note_excerpts": [],
            },
            reviewer_tier="trainer",
        )
    )

    assert result["overall_assessment"] == "needs_intervention"
    assert result["primary_failure_mode"] == "uncorrected_verifier_failures"
    assert any("Verifier flagged" in item for item in result["evidence"])
    assert "verifier_issue_rate" in result["telemetry_to_watch"]
    assert "trainer_correction_latency" in result["telemetry_to_watch"]


def test_session_trace_summary_includes_feedback_events():
    storage = make_storage()
    storage.messages.create(
        role="assistant",
        content="Here is an answer.",
        session_id="trace-session",
    )
    storage.commit()
    message = storage.messages.get_all(session_id="trace-session")[-1]
    message_feedback.toggle_message_reaction(
        storage,
        message=message,
        reaction="confused",
        session_id="trace-session",
    )
    storage.commit()

    from strata.experimental.trace_review import build_session_trace_summary

    summary = build_session_trace_summary(storage, session_id="trace-session")
    assert summary["feedback_event_count"] == 1
    assert summary["feedback_events"][0]["reaction"] == "confused"
    assert "confused" in summary["feedback_summaries"][0]


def test_append_trace_review_to_session_persists_slim_reviews():
    storage = make_storage()
    payload = append_trace_review_to_session(
        storage,
        session_id="trace-session",
        review={
            "recorded_at": "2026-03-28T00:00:00+00:00",
            "trace_kind": "session_trace",
            "reviewer_tier": "trainer",
            "overall_assessment": "needs_intervention",
            "primary_failure_mode": "missed_feedback",
            "recommended_title": "Feedback Repair",
            "summary": "The assistant missed an explicit correction signal.",
            "targeted_interventions": [{"kind": "spec", "description": "Tighten feedback handling."}],
            "telemetry_to_watch": ["chat_feedback_negative_rate"],
            "timeline_artifact_id": "timeline_demo",
            "audit_artifact_id": "audit_demo",
        },
    )
    assert payload["session_id"] == "trace-session"
    assert payload["reviews"][0]["primary_failure_mode"] == "missed_feedback"
    stored = storage.parameters.peek_parameter("session_trace_review:trace-session", default_value={})
    assert stored["reviews"][0]["timeline_artifact_id"] == "timeline_demo"
    session_metadata = storage.parameters.peek_parameter("session_metadata:trace-session", default_value={})
    assert session_metadata["recommended_title"] == "Feedback Repair"


def test_trace_review_job_persists_result_and_attaches_to_task():
    storage = make_storage()
    target = storage.tasks.create(
        title="Research weakness",
        description="Review a failing research task.",
        session_id="trace-session",
        state=TaskState.WORKING,
        type=TaskType.RESEARCH,
    )
    storage.messages.create(
        role="assistant",
        content="I cannot access the repo.",
        session_id="trace-session",
        task_id=target.task_id,
    )
    job = storage.tasks.create(
        title="Trace review",
        description="Queued trace review",
        session_id="trace-session",
        state=TaskState.PENDING,
        type=TaskType.JUDGE,
        constraints={
            "system_job": {
                "kind": "trace_review",
                "payload": {
                    "trace_kind": "task_trace",
                    "task_id": target.task_id,
                    "reviewer_tier": "trainer",
                    "persist_to_task": True,
                },
            }
        },
    )
    storage.commit()

    result = asyncio.run(run_eval_job_task(job, storage, model_adapter=FakeModelAdapter()))
    storage.session.expire_all()
    reloaded_job = storage.tasks.get_by_id(job.task_id)
    reloaded_target = storage.tasks.get_by_id(target.task_id)

    assert result["trace_kind"] == "task_trace"
    assert result["review"]["primary_failure_mode"] == "tool_avoidance"
    assert result["attention_signal"]["signal_kind"] == "surprise"
    assert reloaded_job.state == TaskState.COMPLETE
    assert reloaded_job.constraints["system_job_result"]["status"] == "completed"
    assert reloaded_target.constraints["trace_reviews"][0]["primary_failure_mode"] == "tool_avoidance"
    signals = list_feedback_signals(storage, session_id="trace-session")
    assert signals[-1]["source_type"] == "task_review"
    assert signals[-1]["metadata"]["primary_failure_mode"] == "tool_avoidance"


def test_review_task_trace_endpoint_persists_inline_review():
    storage = make_storage()
    target = storage.tasks.create(
        title="Inline trace review target",
        description="Needs review",
        session_id="trace-session",
        state=TaskState.WORKING,
        type=TaskType.RESEARCH,
    )
    storage.commit()

    async def fake_review_trace(*_args, **kwargs):
        return {
            "status": "ok",
            "trace_kind": "task_trace",
            "reviewer_tier": kwargs.get("reviewer_tier"),
            "recorded_at": "2026-03-26T00:00:00+00:00",
            "summary": "Inline review summary.",
            "overall_assessment": "needs_intervention",
            "primary_failure_mode": "tool_avoidance",
            "targeted_interventions": [],
            "telemetry_to_watch": [],
        }

    import strata.api.experiment_admin as experiment_admin

    original_review_trace = experiment_admin.review_trace
    experiment_admin.review_trace = fake_review_trace
    try:
        result = asyncio.run(
            api_main.review_task_trace(
                payload={"task_id": target.task_id, "reviewer_tier": "agent", "persist_to_task": True},
                storage=storage,
            )
        )
    finally:
        experiment_admin.review_trace = original_review_trace

    storage.session.expire_all()
    reloaded_target = storage.tasks.get_by_id(target.task_id)
    assert result["status"] == "ok"
    assert result["review"]["reviewer_tier"] == "agent"
    assert result["review"]["timeline_artifact_id"]
    assert result["review"]["audit_artifact_id"]
    assert result["attention_signal"]["signal_kind"] == "surprise"
    assert get_timeline_artifact(storage, result["review"]["timeline_artifact_id"])["artifact_type"] == "timeline_artifact"
    assert get_audit_artifact(storage, result["review"]["audit_artifact_id"])["artifact_type"] == "audit_artifact"
    assert reloaded_target.constraints["trace_reviews"][0]["overall_assessment"] == "needs_intervention"


def test_review_endpoint_supports_recursive_audit_of_prior_audit():
    storage = make_storage()
    target = storage.tasks.create(
        title="Recursive audit target",
        description="Needs review",
        session_id="trace-session",
        state=TaskState.WORKING,
        type=TaskType.RESEARCH,
    )
    storage.commit()

    async def fake_review_trace(*_args, **kwargs):
        return {
            "status": "ok",
            "trace_kind": "task_trace",
            "reviewer_tier": kwargs.get("reviewer_tier"),
            "recorded_at": "2026-03-26T00:00:00+00:00",
            "summary": "Inline review summary.",
            "overall_assessment": "needs_intervention",
            "primary_failure_mode": "tool_avoidance",
            "targeted_interventions": [],
            "telemetry_to_watch": [],
        }

    import strata.api.experiment_admin as experiment_admin

    original_review_trace = experiment_admin.review_trace
    experiment_admin.review_trace = fake_review_trace
    try:
        first = asyncio.run(
            api_main.review_task_trace(
                payload={"task_id": target.task_id, "reviewer_tier": "agent", "persist_to_task": False},
                storage=storage,
            )
        )
        second = asyncio.run(
            api_main.review_trace_endpoint(
                payload={
                    "artifact_type": "audit",
                    "artifact_id": first["review"]["audit_artifact_id"],
                },
                storage=storage,
            )
        )
    finally:
        experiment_admin.review_trace = original_review_trace

    assert second["status"] == "ok"
    assert second["audit_artifact"]["audit_target_type"] == "timeline"
    assert second["audit_artifact"]["metadata"]["recursive_target_type"] == "audit"


def test_review_endpoint_emits_attention_signal_for_warn_recursive_audit():
    storage = make_storage()

    import strata.api.experiment_admin as experiment_admin

    original_audit_stored_artifact = experiment_admin.audit_stored_artifact
    experiment_admin.audit_stored_artifact = lambda *args, **kwargs: {
        "artifact_id": "audit_warn_demo",
        "artifact_type": "audit_artifact",
        "summary_verdict": {"status": "warn", "judgment_count": 1},
        "rationale": "Interpretability warning found during recursive audit.",
    }
    try:
        result = asyncio.run(
            api_main.review_trace_endpoint(
                payload={"artifact_type": "audit", "artifact_id": "audit_prior_demo"},
                storage=storage,
            )
        )
    finally:
        experiment_admin.audit_stored_artifact = original_audit_stored_artifact

    assert result["status"] == "ok"
    assert result["attention_signal"]["signal_kind"] == "surprise"
    assert result["attention_signal"]["source_type"] == "audit"


def test_review_signal_endpoint_treats_signal_as_auditable_trace():
    storage = make_storage()
    signal = register_feedback_signal(
        storage,
        source_type="message",
        source_id="msg_1",
        signal_kind="surprise",
        signal_value="unexpected thumbs_down",
        source_actor="system",
        session_id="trace-session",
        source_preview="User disliked a greeting that the system expected to be welcome.",
        expected_outcome="positive",
        observed_outcome="negative",
    )

    async def fake_review_trace(*_args, **kwargs):
        assert kwargs["trace_kind"] == "feedback_signal_trace"
        assert kwargs["trace_summary"]["signal_id"] == signal["signal_id"]
        return {
            "status": "ok",
            "trace_kind": "feedback_signal_trace",
            "reviewer_tier": kwargs.get("reviewer_tier"),
            "recorded_at": "2026-03-28T00:00:00+00:00",
            "summary": "The system should inspect why the surprise fired.",
            "overall_assessment": "needs_intervention",
            "primary_failure_mode": "attention_miscalibration",
            "targeted_interventions": [{"kind": "telemetry", "description": "Track greeting preference confidence."}],
            "telemetry_to_watch": ["attention_false_positive_rate"],
            "domains_affected": ["attention", "user_model"],
            "confidence": 0.81,
        }

    import strata.api.experiment_admin as experiment_admin

    original_review_trace = experiment_admin.review_trace
    experiment_admin.review_trace = fake_review_trace
    try:
        result = asyncio.run(
            api_main.review_trace_endpoint(
                payload={
                    "trace_kind": "feedback_signal_trace",
                    "signal_id": signal["signal_id"],
                    "reviewer_tier": "trainer",
                },
                storage=storage,
            )
        )
    finally:
        experiment_admin.review_trace = original_review_trace

    assert result["status"] == "ok"
    assert result["review"]["primary_failure_mode"] == "attention_miscalibration"
    assert result["attention_signal"]["source_type"] == "trace_review"
