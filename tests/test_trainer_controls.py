from __future__ import annotations

import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from strata.api.chat_tool_executor import ChatToolExecutor
from strata.api.chat_tools import NON_GENERATIVE_TOOLS, filter_chat_tools_for_lane
from strata.orchestrator.trainer_controls import build_branch_state_summary
from strata.storage.models import Base, TaskState, TaskType
from strata.storage.services.main import StorageManager


def make_storage():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    return StorageManager(session=session)


class DummyWorker:
    def __init__(self):
        self.enqueued = []

    async def enqueue(self, task_id):
        self.enqueued.append(task_id)


def make_executor(worker=None, queue_eval_system_job=None):
    return ChatToolExecutor(
        task_state_cls=TaskState,
        task_type_cls=TaskType,
        slugify_page_title=lambda value: value,
        load_specs=lambda storage=None: {},
        create_spec_proposal=lambda *args, **kwargs: {},
        worker=worker or DummyWorker(),
        queue_eval_system_job=queue_eval_system_job,
        get_active_question=lambda storage, session_id: {},
        get_question_for_source=lambda storage, source_type, source_id: {},
        resolve_question=lambda storage, question_id, resolution, response=None: {},
    )


def test_trainer_control_tools_hidden_from_agent_lane():
    tool_names = {
        (tool.get("function") or {}).get("name")
        for tool in filter_chat_tools_for_lane(NON_GENERATIVE_TOOLS, "agent")
    }

    assert "inspect_branch_state" not in tool_names
    assert "rewrite_plan" not in tool_names
    assert "invalidate_premise" not in tool_names
    assert "set_verification_posture" not in tool_names
    assert "request_self_audit" not in tool_names


def test_trainer_control_tools_available_to_trainer_lane():
    tool_names = {
        (tool.get("function") or {}).get("name")
        for tool in filter_chat_tools_for_lane(NON_GENERATIVE_TOOLS, "trainer")
    }

    assert "inspect_branch_state" in tool_names
    assert "rewrite_plan" in tool_names
    assert "invalidate_premise" in tool_names
    assert "set_verification_posture" in tool_names
    assert "request_self_audit" in tool_names


def test_rewrite_plan_tool_updates_task_and_requeues():
    storage = make_storage()
    worker = DummyWorker()
    executor = make_executor(worker=worker)
    task = storage.tasks.create(
        title="Looping branch",
        description="Old plan.",
        session_id="trainer:default",
        state=TaskState.WORKING,
        constraints={"lane": "agent"},
    )
    storage.commit()

    result = asyncio.run(
        executor.execute_tool_call(
            storage,
            call={
                "id": "call-1",
                "function": {
                    "name": "rewrite_plan",
                    "arguments": json.dumps(
                        {
                            "task_id": task.task_id,
                            "plan": "1. Inspect the current assumptions. 2. Ask one bounded user question if needed. 3. Resume with the corrected branch state.",
                            "rationale": "The current branch is looping and needs a smaller corrective plan.",
                        }
                    ),
                },
            },
            session_id="trainer:default",
            content="",
            knowledge_pages=None,
        )
    )

    updated = storage.tasks.get_by_id(task.task_id)
    assert updated is not None
    assert updated.state == TaskState.PENDING
    assert updated.constraints["plan_override"]["rationale"] == "The current branch is looping and needs a smaller corrective plan."
    assert "PLAN OVERRIDE" in updated.description
    assert worker.enqueued == [task.task_id]
    assert result["tool_outputs_generated"] is True


def test_request_self_audit_tool_records_request_and_queues_review():
    storage = make_storage()
    queued = []

    async def fake_queue_eval_system_job(storage_obj, **kwargs):
        queued.append(kwargs)
        return {"status": "queued", "task_id": "review-1"}

    executor = make_executor(queue_eval_system_job=fake_queue_eval_system_job)
    task = storage.tasks.create(
        title="Suspicious branch",
        description="Needs audit.",
        session_id="trainer:default",
        state=TaskState.WORKING,
        constraints={"lane": "agent"},
    )
    storage.commit()

    result = asyncio.run(
        executor.execute_tool_call(
            storage,
            call={
                "id": "call-2",
                "function": {
                    "name": "request_self_audit",
                    "arguments": json.dumps({"task_id": task.task_id, "focus": "check whether the branch is relying on stale assumptions"}),
                },
            },
            session_id="trainer:default",
            content="",
            knowledge_pages=None,
        )
    )

    updated = storage.tasks.get_by_id(task.task_id)
    summary = build_branch_state_summary(storage, task_id=task.task_id)
    assert updated is not None
    assert updated.constraints["self_audit_requests"][-1]["focus"] == "check whether the branch is relying on stale assumptions"
    assert queued[0]["payload"]["reviewer_tier"] == "agent"
    assert summary["self_audit_requests"]
    assert result["tool_outputs_generated"] is True
