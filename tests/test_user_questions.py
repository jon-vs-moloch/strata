from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from strata.orchestrator.user_questions import (
    enqueue_user_question,
    get_active_question,
    mark_question_asked,
    resolve_question,
)
from strata.storage.models import Base
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
