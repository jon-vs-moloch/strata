from __future__ import annotations

from strata.feedback.signals import get_feedback_signal, list_feedback_signals, register_feedback_signal


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


def test_register_surprise_signal_is_urgent():
    storage = DummyStorage()
    signal = register_feedback_signal(
        storage,
        source_type="eval",
        source_id="eval-case-1",
        signal_kind="unexpected_success",
        signal_value="passed unexpectedly",
        source_actor="system",
        source_preview="Model passed a case we expected to fail.",
        expected_outcome="fail",
        observed_outcome="pass",
    )
    assert signal["prioritization"]["priority"] == "urgent"
    assert signal["prioritization"]["reason_family"] == "expectation_violation"
    assert signal["status"] == "queued_attention"


def test_register_response_signal_is_listed_by_session():
    storage = DummyStorage()
    register_feedback_signal(
        storage,
        source_type="session",
        source_id="trainer:default",
        signal_kind="response",
        signal_value="User said this answer format is too verbose.",
        source_actor="user",
        session_id="trainer:default",
        source_preview="Assistant answered with a long explanation.",
    )
    rows = list_feedback_signals(storage, session_id="trainer:default")
    assert len(rows) == 1
    assert rows[0]["prioritization"]["priority"] == "review_soon"


def test_get_feedback_signal_returns_matching_row():
    storage = DummyStorage()
    signal = register_feedback_signal(
        storage,
        source_type="session",
        source_id="session-1",
        signal_kind="surprise",
        signal_value="unexpected dislike",
        source_actor="system",
        session_id="session-1",
        source_preview="User disliked a greeting we expected to land well.",
    )
    loaded = get_feedback_signal(storage, signal["signal_id"])
    assert loaded is not None
    assert loaded["signal_id"] == signal["signal_id"]
