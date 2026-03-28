import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from strata.api import main as api_main
from strata.api import message_feedback
from strata.eval.job_runner import run_eval_job_task
from strata.experimental.audit_registry import get_audit_artifact, get_timeline_artifact
from strata.experimental.artifact_pipeline import append_trace_review_to_session
from strata.feedback.signals import list_feedback_signals
from strata.experimental.trace_review import build_task_trace_summary, review_trace
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


def test_review_trace_supports_weak_reviewer_tier():
    adapter = FakeModelAdapter()

    result = asyncio.run(
        review_trace(
            adapter,
            trace_kind="generic_trace",
            trace_summary={"foo": "bar"},
            reviewer_tier="weak",
            candidate_change_id="candidate_x",
        )
    )

    assert result["status"] == "ok"
    assert result["reviewer_tier"] == "weak"
    assert adapter.bound_context.mode == "weak"
    assert adapter.bound_context.evaluation_run is True


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
            "reviewer_tier": "strong",
            "overall_assessment": "needs_intervention",
            "primary_failure_mode": "missed_feedback",
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
                    "reviewer_tier": "strong",
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
                payload={"task_id": target.task_id, "reviewer_tier": "weak", "persist_to_task": True},
                storage=storage,
            )
        )
    finally:
        experiment_admin.review_trace = original_review_trace

    storage.session.expire_all()
    reloaded_target = storage.tasks.get_by_id(target.task_id)
    assert result["status"] == "ok"
    assert result["review"]["reviewer_tier"] == "weak"
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
                payload={"task_id": target.task_id, "reviewer_tier": "weak", "persist_to_task": False},
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
