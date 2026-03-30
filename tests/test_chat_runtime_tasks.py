import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from strata.core.lanes import canonical_session_id_for_lane
from strata.api.chat_runtime import ChatRuntime
from strata.sessions.metadata import ensure_generated_session_title, set_session_metadata
from strata.storage.models import Base, TaskModel, TaskState, TaskType
from strata.storage.services.main import StorageManager
from strata.orchestrator.user_questions import enqueue_user_question, get_active_question, get_question_for_source


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


class DummyWorker:
    def __init__(self):
        self.enqueued = []

    async def enqueue(self, task_id):
        self.enqueued.append(task_id)


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
        session_id="agent:default",
        state=TaskState.PENDING,
        constraints={"lane": "agent"},
    )
    storage.tasks.create(
        title="Strong Task",
        description="lane strong",
        session_id="trainer:default",
        state=TaskState.PENDING,
        constraints={"lane": "trainer"},
    )
    storage.commit()

    weak_tasks = runtime.list_tasks_payload(storage, lane="agent")
    strong_tasks = runtime.list_tasks_payload(storage, lane="trainer")

    assert [task["title"] for task in weak_tasks] == ["Weak Task"]
    assert [task["title"] for task in strong_tasks] == ["Strong Task"]


def test_list_tasks_payload_infers_lane_from_session_when_missing():
    storage = make_storage()
    runtime = make_runtime()

    storage.tasks.create(
        title="Inferred Weak Task",
        description="inferred lane",
        session_id="agent:session-1",
        state=TaskState.PENDING,
        constraints={},
    )
    storage.commit()

    weak_tasks = runtime.list_tasks_payload(storage, lane="agent")

    assert len(weak_tasks) == 1
    assert weak_tasks[0]["lane"] == "agent"


def test_list_tasks_payload_includes_pending_question_metadata():
    storage = make_storage()
    runtime = ChatRuntime(
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
        find_pending_spec_clarification=lambda storage, session_id: None,
        get_active_question=get_active_question,
        get_question_for_source=get_question_for_source,
        mark_question_asked=lambda storage, question_id: None,
        resolve_question=lambda storage, question_id, resolution, response=None: None,
    )
    task = storage.tasks.create(
        title="Blocked task",
        description="Needs clarification.",
        session_id="agent:default",
        state=TaskState.BLOCKED,
        constraints={"lane": "agent"},
    )
    task.human_intervention_required = True
    storage.commit()
    queued = enqueue_user_question(
        storage,
        session_id="agent:session-question",
        question="What should I change before retrying?",
        source_type="task_blocked",
        source_id=task.task_id,
        lane="agent",
    )
    storage.commit()

    tasks = runtime.list_tasks_payload(storage, lane="agent")

    assert tasks[0]["pending_question"]["question_id"] == queued["question_id"]
    assert tasks[0]["pending_question"]["session_id"] == queued["session_id"]
    assert tasks[0]["pending_question"]["question"] == queued["question"]


def test_list_tasks_payload_can_limit_attempts_and_omit_evidence():
    storage = make_storage()
    runtime = make_runtime()
    task = storage.tasks.create(
        title="Task with many attempts",
        description="trim me",
        session_id="agent:default",
        state=TaskState.WORKING,
        constraints={"lane": "agent"},
    )
    for index in range(3):
        attempt = storage.attempts.create(task_id=task.task_id)
        attempt.reason = f"failure {index}"
        attempt.evidence = {"failure_kind": f"kind_{index}"}
    storage.commit()

    tasks = runtime.list_tasks_payload(storage, lane="agent", attempt_limit=2, include_evidence=False)

    assert len(tasks[0]["attempts"]) == 2
    assert tasks[0]["attempts"][0]["evidence"] == {}


def test_task_repository_inherits_lane_from_parent_task():
    storage = make_storage()

    parent = storage.tasks.create(
        title="Parent Strong Task",
        description="parent",
        session_id="agent:session-1",
        state=TaskState.PENDING,
        constraints={"lane": "trainer"},
    )
    child = storage.tasks.create(
        title="Child Task",
        description="child",
        parent_task_id=parent.task_id,
        state=TaskState.PENDING,
        constraints={},
    )
    storage.commit()

    assert child.constraints["lane"] == "trainer"


def test_plain_user_chat_does_not_auto_resolve_task_question():
    storage = make_storage()
    worker = DummyWorker()
    runtime = ChatRuntime(
        task_model_cls=TaskModel,
        task_type_cls=TaskType,
        task_state_cls=TaskState,
        model_adapter=None,
        semantic_memory=None,
        worker=worker,
        broadcast_event=None,
        global_settings={},
        knowledge_page_store_cls=lambda storage: None,
        slugify_page_title=lambda value: value,
        load_dynamic_tools=lambda: [],
        load_specs=lambda: {},
        create_spec_proposal=None,
        resubmit_spec_proposal_with_clarification=None,
        find_pending_spec_clarification=lambda storage, session_id: None,
        get_active_question=get_active_question,
        get_question_for_source=lambda storage, source_type, source_id: {},
        mark_question_asked=lambda storage, question_id: None,
        resolve_question=lambda storage, question_id, resolution, response=None: None,
    )
    task = storage.tasks.create(
        title="Blocked task",
        description="Needs clarification.",
        session_id="agent:default",
        state=TaskState.BLOCKED,
        constraints={"lane": "agent"},
    )
    storage.commit()
    queued = enqueue_user_question(
        storage,
        session_id="agent:default",
        question="What should I change before retrying?",
        source_type="task_blocked",
        source_id=task.task_id,
        lane="agent",
    )
    storage.commit()

    result = asyncio.run(
        runtime.handle_explicit_question_answer(
            storage,
            {"role": "user"},
            queued["session_id"],
            "Can you give me more detail?",
        )
    )

    assert result is None
    active = get_active_question(storage, queued["session_id"])
    assert active["question_id"] == queued["question_id"]
    assert worker.enqueued == []


def test_explicit_question_answer_resolves_and_requeues_task():
    storage = make_storage()
    worker = DummyWorker()
    resolved_rows = []
    async def _broadcast_event(*_args, **_kwargs):
        return None

    def _resolve_question(storage, question_id, resolution, response=None):
        resolved_rows.append((question_id, resolution, response))

    runtime = ChatRuntime(
        task_model_cls=TaskModel,
        task_type_cls=TaskType,
        task_state_cls=TaskState,
        model_adapter=None,
        semantic_memory=None,
        worker=worker,
        broadcast_event=_broadcast_event,
        global_settings={},
        knowledge_page_store_cls=lambda storage: None,
        slugify_page_title=lambda value: value,
        load_dynamic_tools=lambda: [],
        load_specs=lambda: {},
        create_spec_proposal=None,
        resubmit_spec_proposal_with_clarification=None,
        find_pending_spec_clarification=lambda storage, session_id: None,
        get_active_question=get_active_question,
        get_question_for_source=lambda storage, source_type, source_id: {},
        mark_question_asked=lambda storage, question_id: None,
        resolve_question=_resolve_question,
    )
    task = storage.tasks.create(
        title="Blocked task",
        description="Needs clarification.",
        session_id="agent:default",
        state=TaskState.BLOCKED,
        constraints={"lane": "agent"},
    )
    storage.commit()
    queued = enqueue_user_question(
        storage,
        session_id="agent:default",
        question="What should I change before retrying?",
        source_type="task_blocked",
        source_id=task.task_id,
        lane="agent",
    )
    storage.commit()

    result = asyncio.run(
        runtime.handle_explicit_question_answer(
            storage,
            {"role": "user", "answer_question_id": queued["question_id"]},
            queued["session_id"],
            "Break the recovery flow into smaller steps.",
        )
    )

    storage.session.expire_all()
    updated_task = storage.tasks.get_by_id(task.task_id)
    assert result["status"] == "ok"
    assert updated_task.state == TaskState.PENDING
    assert "User clarification" in updated_task.description
    assert worker.enqueued == [task.task_id]
    assert resolved_rows[0][0] == queued["question_id"]


def test_build_chat_messages_mentions_open_asked_question_without_auto_resolving():
    storage = make_storage()
    runtime = ChatRuntime(
        task_model_cls=TaskModel,
        task_type_cls=TaskType,
        task_state_cls=TaskState,
        model_adapter=None,
        semantic_memory=type("Memory", (), {"query_memory": lambda self, *_args, **_kwargs: {}})(),
        worker=None,
        broadcast_event=None,
        global_settings={},
        knowledge_page_store_cls=lambda storage: None,
        slugify_page_title=lambda value: value,
        load_dynamic_tools=lambda: [],
        load_specs=lambda: {},
        create_spec_proposal=None,
        resubmit_spec_proposal_with_clarification=None,
        find_pending_spec_clarification=lambda storage, session_id: None,
        get_active_question=get_active_question,
        get_question_for_source=lambda storage, source_type, source_id: {},
        mark_question_asked=lambda storage, question_id: None,
        resolve_question=lambda storage, question_id, resolution, response=None: None,
    )
    queued = enqueue_user_question(
        storage,
        session_id="agent:default",
        question="What should I change before retrying?",
        source_type="task_blocked",
        source_id="task-123",
        lane="agent",
    )
    storage.commit()

    messages, _tools, _pages, _pending = runtime.build_chat_messages(
        storage,
        session_id=queued["session_id"],
        content="Can you give me more detail?",
        pending_question=get_active_question(storage, queued["session_id"]),
    )

    system_messages = [row["content"] for row in messages if row["role"] == "system"]
    assert any("resolve_user_question" in content for content in system_messages)
    assert any("Do not assume every user message is automatically an answer" in content for content in system_messages)


def test_session_summaries_filter_by_lane():
    storage = make_storage()

    storage.messages.create(role="user", content="weak lane", session_id="agent:default")
    storage.messages.create(role="user", content="strong lane", session_id="trainer:default")
    storage.commit()

    weak_sessions = storage.messages.get_session_summaries(lane="agent")
    strong_sessions = storage.messages.get_session_summaries(lane="trainer")

    assert [item["session_id"] for item in weak_sessions] == ["agent:default"]
    assert [item["session_id"] for item in strong_sessions] == ["trainer:default"]


def test_canonical_session_id_for_lane_rehomes_unscoped_or_mismatched_sessions():
    assert canonical_session_id_for_lane("agent", None) == "agent:default"
    assert canonical_session_id_for_lane("agent", "default") == "agent:default"
    assert canonical_session_id_for_lane("agent", "session-123") == "agent:session-123"
    assert canonical_session_id_for_lane("trainer", "agent:session-123") == "trainer:session-123"


def test_ensure_generated_session_title_persists_metadata():
    storage = make_storage()
    storage.messages.create(role="user", content="Help me design the session naming system", session_id="trainer:default")
    storage.messages.create(role="assistant", content="Let's make the titles durable and editable.", session_id="trainer:default")
    storage.commit()

    metadata = asyncio.run(
        ensure_generated_session_title(
            storage,
            session_id="trainer:default",
            model_adapter=DummyTitleModel("Session Naming"),
        )
    )
    storage.commit()

    assert metadata["generated_title"] == "Session Naming"
    assert metadata["recommended_title"] == "Session Naming"


def test_custom_session_title_takes_priority():
    storage = make_storage()
    set_session_metadata(storage, "agent:default", {"generated_title": "Generated"})
    set_session_metadata(storage, "agent:default", {"custom_title": "Operator Renamed"})
    storage.commit()

    metadata = storage.parameters.peek_parameter("session_metadata:agent:default", default_value={})

    assert metadata["custom_title"] == "Operator Renamed"


def test_session_summaries_include_unread_counts_from_assistant_messages():
    storage = make_storage()
    storage.messages.create(role="user", content="hello", session_id="trainer:default")
    storage.messages.create(role="assistant", content="reply", session_id="trainer:default")
    storage.commit()

    summaries = storage.messages.get_session_summaries(lane="trainer")

    assert summaries[0]["unread_count"] == 1


def test_list_tasks_payload_slims_large_system_job_payloads():
    storage = make_storage()
    runtime = make_runtime()

    task = storage.tasks.create(
        title="Bootstrap Cycle",
        description="Queued strong-over-weak bootstrap cycle.",
        session_id="trainer:default",
        state=TaskState.COMPLETE,
        constraints={
            "lane": "trainer",
            "system_job": {"kind": "bootstrap_cycle", "payload": {"run_count": 3}},
            "system_job_result": {
                "status": "completed",
                "result": {
                    "evaluated": [{"candidate_change_id": "a"}, {"candidate_change_id": "b"}, {"candidate_change_id": "c"}, {"candidate_change_id": "d"}],
                    "promoted": [{"candidate_change_id": "p1"}, {"candidate_change_id": "p2"}, {"candidate_change_id": "p3"}, {"candidate_change_id": "p4"}],
                    "skipped": [{"reason": "r1"}, {"reason": "r2"}, {"reason": "r3"}, {"reason": "r4"}],
                },
            },
        },
    )
    storage.commit()

    payload = runtime.list_tasks_payload(storage, lane="trainer")
    result = payload[0]["system_job_result"]["result"]

    assert result["summary"] == {"evaluated_count": 4, "promoted_count": 4, "skipped_count": 4}
    assert len(result["evaluated"]) == 3
    assert len(result["promoted"]) == 3
    assert len(result["skipped"]) == 3
