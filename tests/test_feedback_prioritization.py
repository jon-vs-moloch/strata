from __future__ import annotations

from types import SimpleNamespace

from strata.prioritization.feedback import classify_feedback_priority


def test_confused_reaction_is_urgent_and_surprising():
    message = SimpleNamespace(content="Good morning. Ready when you are.")
    result = classify_feedback_priority(
        message=message,
        reaction="confused",
        action="added",
        recent_events=[],
    )
    assert result["priority"] == "urgent"
    assert result["should_interrupt"] is True
    assert result["needs_clarification"] is True
    assert result["surprise_score"] >= 0.9


def test_negative_reaction_on_greeting_is_treated_as_user_model_mismatch():
    message = SimpleNamespace(content="Good morning!")
    result = classify_feedback_priority(
        message=message,
        reaction="thumbs_down",
        action="added",
        recent_events=[],
    )
    assert result["priority"] == "urgent"
    assert result["target_surface"] == "user_profile"
    assert result["reason_family"] == "unexpected_negative_on_greeting"


def test_positive_reaction_on_greeting_is_low_priority():
    message = SimpleNamespace(content="Hello there!")
    result = classify_feedback_priority(
        message=message,
        reaction="thumbs_up",
        action="added",
        recent_events=[],
    )
    assert result["priority"] == "ignore"
    assert result["should_batch"] is True


def test_repeated_emphasis_escalates_to_review_soon():
    message = SimpleNamespace(content="Here is a longer answer about the system's alignment policy and expected behavior.")
    recent_events = [
        {"action": "added", "reaction": "emphasis"},
        {"action": "added", "reaction": "emphasis"},
    ]
    result = classify_feedback_priority(
        message=message,
        reaction="emphasis",
        action="added",
        recent_events=recent_events,
    )
    assert result["priority"] == "review_soon"
    assert result["reaction_cluster_score"] >= 0.66
