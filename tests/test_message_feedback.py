from __future__ import annotations

from types import SimpleNamespace

from strata.api import message_feedback


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


def test_toggle_message_reaction_records_counts_and_events():
    storage = DummyStorage()
    message = SimpleNamespace(
        message_id="message-1",
        role="assistant",
        content="Hello from Strata.",
    )

    added = message_feedback.toggle_message_reaction(
        storage,
        message=message,
        reaction="thumbs_up",
        session_id="trainer:default",
    )

    assert added["action"] == "added"
    assert added["feedback"]["counts"] == {"thumbs_up": 1}
    assert added["feedback"]["viewer_reactions"] == ["thumbs_up"]

    removed = message_feedback.toggle_message_reaction(
        storage,
        message=message,
        reaction="thumbs_up",
        session_id="trainer:default",
    )

    assert removed["action"] == "removed"
    assert removed["feedback"]["counts"] == {}
    assert removed["feedback"]["viewer_reactions"] == []

    events = message_feedback.list_message_feedback_events(storage, session_id="trainer:default")
    assert len(events) == 2
    assert events[-1]["action"] == "removed"
    assert events[-1]["distillation_status"] == "pending"


def test_build_feedback_event_message_is_human_readable():
    text = message_feedback.build_feedback_event_message(
        action="added",
        reaction="thumbs_down",
        message_preview="This answer missed the point.",
    )
    assert "thumbs down" in text
    assert "This answer missed the point." in text
