from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from strata.orchestrator.user_questions import (
    ensure_question_escalation_for_source,
    enqueue_user_question,
    get_active_question,
    mark_question_asked,
    resolve_question,
    set_question_escalation_mode,
)
from strata.storage.models import Base, TaskState
from strata.storage.services.main import StorageManager


class DummyParameterRepo:
    def __init__(self):
        self.values = {}

    def peek_parameter(self, key, default_value=None):
        return self.values.get(key, default_value)

    def set_parameter(self, key, value, description=""):
        self.values[key] = value


class DummyStorage:
    def __init__(self):
        self.parameters = DummyParameterRepo()


def make_real_storage():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    return StorageManager(session=session)


def test_question_queue_lifecycle():
    storage = DummyStorage()
    queued = enqueue_user_question(
        storage,
        session_id="demo",
        question="What should I change before retrying?",
        source_type="spec_clarification",
        source_id="task-123",
        context={"reason": "missing env var"},
    )
    assert queued["status"] == "pending"

    active = get_active_question(storage, queued["session_id"])
    assert active["question_id"] == queued["question_id"]

    asked = mark_question_asked(storage, queued["question_id"])
    assert asked["status"] == "asked"

    resolved = resolve_question(storage, queued["question_id"], resolution="resolved", response="Use the smaller model.")
    assert resolved["status"] == "resolved"
    assert resolved["response"] == "Use the smaller model."

    assert get_active_question(storage, queued["session_id"]) == {}


def test_non_blocking_question_persists_escalation_mode():
    storage = DummyStorage()
    queued = enqueue_user_question(
        storage,
        session_id="demo",
        question="Do you know where this document lives?",
        source_type="task_blocked",
        source_id="task-123",
        escalation_mode="non_blocking",
    )

    assert queued["escalation_mode"] == "non_blocking"


def test_question_can_promote_from_non_blocking_to_blocking_and_block_task():
    storage = make_real_storage()
    task = storage.tasks.create(
        title="Investigate document location",
        description="Search while asking the user for a shortcut.",
        session_id="agent:default",
        state=TaskState.WORKING,
        constraints={"lane": "agent"},
    )
    storage.commit()
    queued = enqueue_user_question(
        storage,
        session_id="agent:default",
        question="Do you know where this document is?",
        source_type="task_blocked",
        source_id=task.task_id,
        lane="agent",
        escalation_mode="non_blocking",
    )

    updated = set_question_escalation_mode(
        storage,
        queued["question_id"],
        escalation_mode="blocking",
        rationale="Current capabilities cannot obtain the information autonomously.",
    )
    storage.commit()
    storage.session.expire_all()
    reloaded_task = storage.tasks.get_by_id(task.task_id)

    assert updated["escalation_mode"] == "blocking"
    assert updated["escalation_history"][-1]["to_mode"] == "blocking"
    assert reloaded_task.human_intervention_required is True
    assert reloaded_task.state == TaskState.BLOCKED


def test_question_can_demote_from_blocking_to_non_blocking_and_resume_task():
    storage = make_real_storage()
    task = storage.tasks.create(
        title="Need operator clarification",
        description="Currently blocked.",
        session_id="agent:default",
        state=TaskState.BLOCKED,
        constraints={"lane": "agent"},
    )
    task.human_intervention_required = True
    storage.commit()
    queued = enqueue_user_question(
        storage,
        session_id="agent:default",
        question="What should I change before retrying?",
        source_type="task_blocked",
        source_id=task.task_id,
        lane="agent",
        escalation_mode="blocking",
    )

    updated = set_question_escalation_mode(
        storage,
        queued["question_id"],
        escalation_mode="non_blocking",
        rationale="New tooling makes continued investigation possible.",
    )
    storage.commit()
    storage.session.expire_all()
    reloaded_task = storage.tasks.get_by_id(task.task_id)

    assert updated["escalation_mode"] == "non_blocking"
    assert updated["escalation_history"][-1]["to_mode"] == "non_blocking"
    assert reloaded_task.human_intervention_required is False
    assert reloaded_task.state == TaskState.PENDING


def test_question_escalation_can_be_updated_by_source_lookup():
    storage = make_real_storage()
    task = storage.tasks.create(
        title="Need operator clarification",
        description="Currently blocked.",
        session_id="agent:default",
        state=TaskState.WORKING,
        constraints={"lane": "agent"},
    )
    storage.commit()
    queued = enqueue_user_question(
        storage,
        session_id="agent:default",
        question="Where is the missing document?",
        source_type="task_blocked",
        source_id=task.task_id,
        lane="agent",
        escalation_mode="non_blocking",
    )

    updated = ensure_question_escalation_for_source(
        storage,
        source_type="task_blocked",
        source_id=task.task_id,
        escalation_mode="blocking",
        rationale="Autonomous search exhausted current capabilities.",
    )
    storage.commit()
    storage.session.expire_all()
    reloaded_task = storage.tasks.get_by_id(task.task_id)

    assert updated["question_id"] == queued["question_id"]
    assert updated["escalation_mode"] == "blocking"
    assert reloaded_task.human_intervention_required is True
    assert reloaded_task.state == TaskState.BLOCKED


def test_terminal_question_history_is_bounded(monkeypatch):
    monkeypatch.setattr("strata.orchestrator.user_questions.MAX_TERMINAL_QUESTIONS", 3)
    storage = DummyStorage()
    for idx in range(5):
        queued = enqueue_user_question(
            storage,
            session_id="demo",
            question=f"q{idx}",
            source_type="task_blocked",
            source_id=f"task-{idx}",
        )
        resolve_question(storage, queued["question_id"], resolution="resolved", response="done")

    rows = storage.parameters.values["user_questions:index"]
    assert len(rows) == 3


def test_brief_question_is_derived_for_long_prompts():
    storage = DummyStorage()
    queued = enqueue_user_question(
        storage,
        session_id="demo",
        question=(
            "1. What durable principle or constraint should be added?\n"
            "2. What should replace or modify existing guidance?\n"
            "3. What triggered this update?"
        ),
        source_type="spec_clarification",
        source_id="spec-1",
    )

    assert queued["brief_question"] == "What durable principle or constraint should be added?"


def test_task_blocked_question_opens_durable_dedicated_session():
    storage = make_real_storage()

    queued = enqueue_user_question(
        storage,
        session_id="agent:default",
        question="Task 'Alignment gap review' is blocked. What should I change before retrying?",
        source_type="task_blocked",
        source_id="task-123",
        lane="agent",
    )
    storage.commit()

    assert queued["status"] == "asked"
    assert queued["session_id"].startswith("agent:session-")

    active = get_active_question(storage, queued["session_id"])
    assert active["question_id"] == queued["question_id"]

    summaries = storage.messages.get_session_summaries(lane="agent")
    matching = [row for row in summaries if row["session_id"] == queued["session_id"]]
    assert matching
