from __future__ import annotations

from strata.orchestrator.user_questions import (
    enqueue_user_question,
    get_active_question,
    mark_question_asked,
    resolve_question,
)


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


def test_question_queue_lifecycle():
    storage = DummyStorage()
    queued = enqueue_user_question(
        storage,
        session_id="demo",
        question="What should I change before retrying?",
        source_type="task_blocked",
        source_id="task-123",
        context={"reason": "missing env var"},
    )
    assert queued["status"] == "pending"

    active = get_active_question(storage, "demo")
    assert active["question_id"] == queued["question_id"]

    asked = mark_question_asked(storage, queued["question_id"])
    assert asked["status"] == "asked"

    resolved = resolve_question(storage, queued["question_id"], resolution="resolved", response="Use the smaller model.")
    assert resolved["status"] == "resolved"
    assert resolved["response"] == "Use the smaller model."

    assert get_active_question(storage, "demo") == {}
