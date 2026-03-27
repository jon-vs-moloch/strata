import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from strata.api import main as api_main
from strata.eval.job_runner import run_eval_job_task
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
    assert reloaded_job.state == TaskState.COMPLETE
    assert reloaded_job.constraints["system_job_result"]["status"] == "completed"
    assert reloaded_target.constraints["trace_reviews"][0]["primary_failure_mode"] == "tool_avoidance"


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
    assert reloaded_target.constraints["trace_reviews"][0]["overall_assessment"] == "needs_intervention"
