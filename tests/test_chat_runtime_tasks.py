import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from strata.core.lanes import canonical_session_id_for_lane
from strata.api.chat_runtime import ChatRuntime
from strata.sessions.metadata import ensure_generated_session_title, set_session_metadata
from strata.storage.models import Base, TaskModel, TaskState, TaskType
from strata.storage.services.main import StorageManager


def make_storage():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    return StorageManager(session=session)


def make_runtime():
    return ChatRuntime(
        task_model_cls=TaskModel,
        task_type_cls=TaskType,
        task_state_cls=TaskState,
        model_adapter=None,
        semantic_memory=None,
        worker=None,
        broadcast_event=None,
        global_settings={},
        knowledge_page_store_cls=lambda storage: None,
        slugify_page_title=lambda value: value,
        load_dynamic_tools=lambda: [],
        load_specs=lambda: {},
        create_spec_proposal=None,
        resubmit_spec_proposal_with_clarification=None,
        find_pending_spec_clarification=None,
        get_active_question=lambda storage, session_id: None,
        get_question_for_source=lambda storage, source_type, source_id: {},
        mark_question_asked=lambda storage, question_id: None,
        resolve_question=lambda storage, question_id, resolution, response=None: None,
    )


class DummyTitleModel:
    def __init__(self, title="Profile Help"):
        self._selected_models = {}
        self.title = title

    async def chat(self, *_args, **_kwargs):
        return {"content": self.title}


def test_list_tasks_payload_filters_by_lane_metadata():
    storage = make_storage()
    runtime = make_runtime()

    storage.tasks.create(
        title="Weak Task",
        description="lane weak",
        session_id="weak:default",
        state=TaskState.PENDING,
        constraints={"lane": "weak"},
    )
    storage.tasks.create(
        title="Strong Task",
        description="lane strong",
        session_id="strong:default",
        state=TaskState.PENDING,
        constraints={"lane": "strong"},
    )
    storage.commit()

    weak_tasks = runtime.list_tasks_payload(storage, lane="weak")
    strong_tasks = runtime.list_tasks_payload(storage, lane="strong")

    assert [task["title"] for task in weak_tasks] == ["Weak Task"]
    assert [task["title"] for task in strong_tasks] == ["Strong Task"]


def test_list_tasks_payload_infers_lane_from_session_when_missing():
    storage = make_storage()
    runtime = make_runtime()

    storage.tasks.create(
        title="Inferred Weak Task",
        description="inferred lane",
        session_id="weak:session-1",
        state=TaskState.PENDING,
        constraints={},
    )
    storage.commit()

    weak_tasks = runtime.list_tasks_payload(storage, lane="weak")

    assert len(weak_tasks) == 1
    assert weak_tasks[0]["lane"] == "weak"


def test_task_repository_inherits_lane_from_parent_task():
    storage = make_storage()

    parent = storage.tasks.create(
        title="Parent Strong Task",
        description="parent",
        session_id="weak:session-1",
        state=TaskState.PENDING,
        constraints={"lane": "strong"},
    )
    child = storage.tasks.create(
        title="Child Task",
        description="child",
        parent_task_id=parent.task_id,
        state=TaskState.PENDING,
        constraints={},
    )
    storage.commit()

    assert child.constraints["lane"] == "strong"


def test_session_summaries_filter_by_lane():
    storage = make_storage()

    storage.messages.create(role="user", content="weak lane", session_id="weak:default")
    storage.messages.create(role="user", content="strong lane", session_id="strong:default")
    storage.commit()

    weak_sessions = storage.messages.get_session_summaries(lane="weak")
    strong_sessions = storage.messages.get_session_summaries(lane="strong")

    assert [item["session_id"] for item in weak_sessions] == ["weak:default"]
    assert [item["session_id"] for item in strong_sessions] == ["strong:default"]


def test_canonical_session_id_for_lane_rehomes_unscoped_or_mismatched_sessions():
    assert canonical_session_id_for_lane("weak", None) == "weak:default"
    assert canonical_session_id_for_lane("weak", "default") == "weak:default"
    assert canonical_session_id_for_lane("weak", "session-123") == "weak:session-123"
    assert canonical_session_id_for_lane("strong", "weak:session-123") == "strong:session-123"


def test_ensure_generated_session_title_persists_metadata():
    storage = make_storage()
    storage.messages.create(role="user", content="Help me design the session naming system", session_id="strong:default")
    storage.messages.create(role="assistant", content="Let's make the titles durable and editable.", session_id="strong:default")
    storage.commit()

    metadata = asyncio.run(
        ensure_generated_session_title(
            storage,
            session_id="strong:default",
            model_adapter=DummyTitleModel("Session Naming"),
        )
    )
    storage.commit()

    assert metadata["generated_title"] == "Session Naming"
    assert metadata["recommended_title"] == "Session Naming"


def test_custom_session_title_takes_priority():
    storage = make_storage()
    set_session_metadata(storage, "weak:default", {"generated_title": "Generated"})
    set_session_metadata(storage, "weak:default", {"custom_title": "Operator Renamed"})
    storage.commit()

    metadata = storage.parameters.peek_parameter("session_metadata:weak:default", default_value={})

    assert metadata["custom_title"] == "Operator Renamed"


def test_session_summaries_include_unread_counts_from_assistant_messages():
    storage = make_storage()
    storage.messages.create(role="user", content="hello", session_id="strong:default")
    storage.messages.create(role="assistant", content="reply", session_id="strong:default")
    storage.commit()

    summaries = storage.messages.get_session_summaries(lane="strong")

    assert summaries[0]["unread_count"] == 1
