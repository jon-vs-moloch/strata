from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from strata.feedback.signals import list_feedback_signals
from strata.orchestrator.background import emit_task_execution_attention_signal
from strata.schemas.execution import WeakExecutionContext
from strata.storage.models import Base, AttemptOutcome, TaskState, TaskType
from strata.storage.services.main import StorageManager


def make_storage():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    return StorageManager(session=session)


def test_first_failed_attempt_emits_unexpected_failure_signal():
    storage = make_storage()
    task = storage.tasks.create(
        title="Investigate issue",
        description="Figure out why the worker is stuck.",
        session_id="trace-session",
        state=TaskState.WORKING,
        type=TaskType.RESEARCH,
    )
    attempt = storage.attempts.create(task_id=task.task_id)
    attempt.outcome = AttemptOutcome.FAILED
    attempt.artifacts = {"provider": "local", "model": "weak-model"}
    storage.commit()

    signal = emit_task_execution_attention_signal(
        storage,
        task=task,
        attempt=attempt,
        context=WeakExecutionContext(run_id="test"),
        plan_review={"plan_health": "uncertain", "recommendation": "reattempt"},
        error=RuntimeError("tool timed out"),
    )

    assert signal is not None
    assert signal["signal_kind"] == "unexpected_failure"
    assert list_feedback_signals(storage, session_id="trace-session")[-1]["signal_id"] == signal["signal_id"]


def test_success_after_failures_emits_unexpected_success_signal():
    storage = make_storage()
    task = storage.tasks.create(
        title="Recover task",
        description="Get the task unstuck.",
        session_id="trace-session",
        state=TaskState.WORKING,
        type=TaskType.IMPL,
    )
    prior = storage.attempts.create(task_id=task.task_id)
    prior.outcome = AttemptOutcome.FAILED
    current = storage.attempts.create(task_id=task.task_id)
    current.outcome = AttemptOutcome.SUCCEEDED
    current.artifacts = {"provider": "local", "model": "weak-model"}
    storage.commit()

    signal = emit_task_execution_attention_signal(
        storage,
        task=task,
        attempt=current,
        context=WeakExecutionContext(run_id="test"),
        plan_review={"plan_health": "healthy", "recommendation": "continue"},
    )

    assert signal is not None
    assert signal["signal_kind"] == "unexpected_success"


def test_success_with_degraded_plan_emits_surprise_signal():
    storage = make_storage()
    task = storage.tasks.create(
        title="Fragile success",
        description="Succeeded but in a concerning way.",
        session_id="trace-session",
        state=TaskState.WORKING,
        type=TaskType.IMPL,
    )
    attempt = storage.attempts.create(task_id=task.task_id)
    attempt.outcome = AttemptOutcome.SUCCEEDED
    attempt.artifacts = {"provider": "local", "model": "weak-model"}
    storage.commit()

    signal = emit_task_execution_attention_signal(
        storage,
        task=task,
        attempt=attempt,
        context=WeakExecutionContext(run_id="test"),
        plan_review={"plan_health": "degraded", "recommendation": "internal_replan"},
    )

    assert signal is not None
    assert signal["signal_kind"] == "surprise"
