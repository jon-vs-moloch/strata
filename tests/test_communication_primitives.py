from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from strata.communication.primitives import (
    build_communication_decision,
    deliver_communication,
    deliver_communication_decision,
    route_communication_decision,
)
from strata.messages.metadata import (
    get_message_metadata,
    initialize_message_metadata,
    mark_message_seen_by_system,
    mark_messages_read,
)
from strata.storage.models import Base
from strata.storage.services.main import StorageManager


def make_storage():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    return StorageManager(session=session)


def test_deliver_communication_can_open_new_autonomous_session():
    storage = make_storage()

    result = deliver_communication(
        storage,
        role="assistant",
        content="Autonomous alignment review queued.",
        lane="weak",
        channel="new_session",
        audience="user",
        source_kind="autonomous_alignment",
        source_actor="system_opened",
        opened_reason="idle_alignment_review",
        tags=["autonomous", "alignment"],
        session_title="Alignment Review",
    )
    storage.commit()

    assert result["status"] == "ok"
    assert result["session_id"].startswith("weak:session-")
    metadata = storage.parameters.peek_parameter(f"session_metadata:{result['session_id']}", default_value={})
    assert metadata["opened_by"] == "system_opened"
    assert metadata["opened_reason"] == "idle_alignment_review"
    assert metadata["generated_title"] == "Alignment Review"
    assert "autonomous" in metadata["tags"]


def test_deliver_communication_can_append_to_existing_session():
    storage = make_storage()

    result = deliver_communication(
        storage,
        role="system",
        content="User reacted to assistant message with thumbs up.",
        lane="strong",
        channel="existing_session_message",
        session_id="strong:default",
        source_kind="feedback_event",
        source_actor="system_opened",
        opened_reason="message_feedback",
        tags=["feedback"],
    )
    storage.commit()

    assert result["session_id"] == "strong:default"
    messages = storage.messages.get_all(session_id="strong:default")
    assert len(messages) == 1
    metadata = storage.parameters.peek_parameter("session_metadata:strong:default", default_value={})
    assert metadata["opened_reason"] == "message_feedback"


def test_deliver_communication_decision_records_act_and_response_kind():
    storage = make_storage()

    decision = build_communication_decision(
        role="assistant",
        content="Here is the answer.",
        lane="strong",
        channel="existing_session_message",
        session_id="strong:default",
        communicative_act="response",
        response_kind="answer",
        source_kind="chat_reply",
    )
    result = deliver_communication_decision(storage, decision)
    storage.commit()

    assert result["status"] == "ok"
    metadata = storage.parameters.peek_parameter("session_metadata:strong:default", default_value={})
    assert metadata["last_communicative_act"] == "response"
    assert metadata["last_response_kind"] == "answer"
    assert metadata["last_communication_source_kind"] == "chat_reply"
    message_metadata = get_message_metadata(storage, result["message_id"])
    assert message_metadata["delivery_channel"] == "existing_session_message"
    assert message_metadata["communicative_act"] == "response"
    assert message_metadata["response_kind"] == "answer"
    assert message_metadata["source_kind"] == "chat_reply"
    assert message_metadata["delivery_records"][-1]["recipient"] == "user"


def test_route_communication_decision_keeps_responses_in_existing_session():
    storage = make_storage()

    routed = route_communication_decision(
        storage,
        build_communication_decision(
            role="assistant",
            content="reply",
            lane="strong",
            session_id="strong:default",
            communicative_act="response",
            source_kind="chat_reply",
        ),
    )

    assert routed["channel"] == "existing_session_message"
    assert routed["session_id"] == "strong:default"


def test_route_communication_decision_allows_explicit_new_session_response():
    storage = make_storage()

    routed = route_communication_decision(
        storage,
        build_communication_decision(
            role="assistant",
            content="Starting a fresh thread for that topic.",
            lane="strong",
            channel="new_session",
            session_id="strong:default",
            communicative_act="response",
            response_kind="handoff",
            source_kind="chat_reply",
        ),
    )

    assert routed["channel"] == "new_session"
    assert routed["session_id"] is None


def test_route_communication_decision_opens_new_session_for_autonomous_notice():
    storage = make_storage()

    routed = route_communication_decision(
        storage,
        build_communication_decision(
            role="assistant",
            content="autonomous update",
            lane="weak",
            communicative_act="notification",
            source_kind="autonomous_alignment",
            session_id="weak:default",
        ),
    )

    assert routed["channel"] == "new_session"
    assert routed["session_id"] is None


def test_route_communication_decision_reuses_matching_system_session_for_autonomous_notice():
    storage = make_storage()
    deliver_communication(
        storage,
        role="assistant",
        content="Initial autonomous alignment review.",
        lane="weak",
        channel="new_session",
        audience="user",
        source_kind="autonomous_alignment",
        source_actor="system_opened",
        opened_reason="idle_alignment_review",
        tags=["autonomous", "alignment"],
        session_title="Alignment Review",
    )
    storage.commit()

    existing_session_id = storage.messages.get_session_summaries(lane="weak")[0]["session_id"]
    routed = route_communication_decision(
        storage,
        build_communication_decision(
            role="assistant",
            content="follow-up autonomous update",
            lane="weak",
            communicative_act="notification",
            source_kind="autonomous_alignment",
            source_actor="system_opened",
            opened_reason="idle_alignment_review",
            tags=["autonomous", "alignment"],
        ),
    )

    assert routed["channel"] == "existing_session_message"
    assert routed["session_id"] == existing_session_id


def test_route_communication_decision_can_reuse_topical_user_opened_session():
    storage = make_storage()
    deliver_communication(
        storage,
        role="user",
        content="Please audit the alignment plan.",
        lane="weak",
        channel="existing_session_message",
        session_id="weak:default",
        audience="user",
        source_kind="user",
        source_actor="user_opened",
        opened_reason="direct_chat",
        tags=["chat"],
    )
    storage.parameters.set_parameter(
        "session_metadata:weak:default",
        {
            "opened_by": "user_opened",
            "opened_reason": "direct_chat",
            "source_kind": "user",
            "tags": ["chat", "alignment"],
            "topic_summary": "Alignment planning for Project A",
            "generated_title": "Project A Alignment",
        },
        description="test metadata",
    )
    storage.commit()

    routed = route_communication_decision(
        storage,
        build_communication_decision(
            role="assistant",
            content="autonomous alignment update",
            lane="weak",
            communicative_act="notification",
            source_kind="autonomous_alignment",
            source_actor="system_opened",
            opened_reason="idle_alignment_review",
            tags=["autonomous", "alignment"],
            topic_summary="Alignment planning follow-up for Project A",
        ),
    )

    assert routed["channel"] == "existing_session_message"
    assert routed["session_id"] == "weak:default"


def test_route_communication_decision_can_forbid_user_opened_session_reuse():
    storage = make_storage()
    deliver_communication(
        storage,
        role="user",
        content="Please audit the alignment plan.",
        lane="weak",
        channel="existing_session_message",
        session_id="weak:default",
        audience="user",
        source_kind="user",
        source_actor="user_opened",
        opened_reason="direct_chat",
        tags=["chat"],
    )
    storage.parameters.set_parameter(
        "session_metadata:weak:default",
        {
            "opened_by": "user_opened",
            "opened_reason": "direct_chat",
            "source_kind": "user",
            "tags": ["chat", "alignment"],
            "topic_summary": "Alignment planning for Project A",
            "generated_title": "Project A Alignment",
        },
        description="test metadata",
    )
    storage.commit()

    routed = route_communication_decision(
        storage,
        build_communication_decision(
            role="assistant",
            content="autonomous alignment update",
            lane="weak",
            communicative_act="notification",
            source_kind="autonomous_alignment",
            source_actor="system_opened",
            opened_reason="idle_alignment_review",
            tags=["autonomous", "alignment"],
            topic_summary="Alignment planning follow-up for Project A",
            allow_user_opened_reuse=False,
        ),
    )

    assert routed["channel"] == "new_session"
    assert routed["session_id"] is None


def test_message_metadata_can_track_seen_and_read_state():
    storage = make_storage()
    message = storage.messages.create(role="user", content="hello", session_id="strong:default")
    initialize_message_metadata(
        storage,
        message_id=message.message_id,
        audience="system",
        delivery_channel="session_store",
        source_kind="user",
        source_actor="user_opened",
        communicative_act="message",
        tags=["chat", "user"],
    )
    mark_message_seen_by_system(storage, message_id=message.message_id, actor="chat_runtime")
    mark_messages_read(storage, message_ids=[message.message_id], reader="system_audit")
    storage.commit()

    metadata = get_message_metadata(storage, message.message_id)

    assert metadata["audience"] == "system"
    assert metadata["seen_by_system_actor"] == "chat_runtime"
    assert metadata["read_by"] == "system_audit"
    assert metadata["delivery_records"][-1]["recipient"] == "system"
    assert metadata["seen_receipts"][-1]["actor"] == "chat_runtime"
    assert metadata["read_receipts"][-1]["reader"] == "system_audit"
