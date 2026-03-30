from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from strata.api.chat_tool_executor import ChatToolExecutor
from strata.orchestrator.tool_health import assess_tool_health, record_tool_execution, should_throttle_tool
from strata.storage.models import Base, TaskState, TaskType
from strata.storage.services.main import StorageManager


def make_storage():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    return StorageManager(session=session)


class DummyWorker:
    async def enqueue(self, _task_id):
        return None


def make_executor():
    return ChatToolExecutor(
        task_state_cls=TaskState,
        task_type_cls=TaskType,
        slugify_page_title=lambda value: value,
        load_specs=lambda storage=None: {},
        create_spec_proposal=lambda *args, **kwargs: {},
        worker=DummyWorker(),
        queue_eval_system_job=None,
        get_active_question=lambda storage, session_id: {},
        get_question_for_source=lambda storage, source_type, source_id: {},
        resolve_question=lambda storage, question_id, resolution, response=None: {},
    )


def test_tool_health_throttles_scope_after_repeated_failures():
    storage = make_storage()
    for index in range(3):
        record_tool_execution(
            storage,
            tool_name="search_web",
            outcome="broken",
            lane="agent",
            task_type="RESEARCH",
            task_id=f"task-{index}",
            session_id="agent:default",
            source="research_module",
            failure_kind="search_failed",
            details={"error": "Search failed: timeout"},
        )
    storage.commit()

    health = assess_tool_health(storage, tool_name="search_web", lane="agent", task_type="RESEARCH")
    throttle = should_throttle_tool(storage, tool_name="search_web", lane="agent", task_type="RESEARCH")

    assert health["status"] == "broken"
    assert throttle["throttle"] is True


def test_tool_health_clears_after_tooling_remediation():
    storage = make_storage()
    event = record_tool_execution(
        storage,
        tool_name="search_web",
        outcome="broken",
        lane="agent",
        task_type="RESEARCH",
        task_id="task-1",
        session_id="agent:default",
        source="research_module",
        failure_kind="search_failed",
        details={"error": "Search failed: timeout"},
    )
    storage.commit()

    repair = storage.tasks.create(
        title="Tool Fix: search_web",
        description="Repair the search_web tool.",
        session_id="trainer:default",
        state=TaskState.PENDING,
        constraints={"target_scope": "tooling", "tool_modification_target": "search_web", "lane": "trainer"},
    )
    repair.type = TaskType.BUG_FIX
    repair.created_at = event.created_at + timedelta(seconds=1)
    storage.commit()

    health = assess_tool_health(storage, tool_name="search_web", lane="agent", task_type="RESEARCH")

    assert health["status"] == "healthy"
    assert health["scope"] == "repaired"


def test_chat_tool_executor_circuit_breaks_degraded_tool():
    storage = make_storage()
    for index in range(2):
        record_tool_execution(
            storage,
            tool_name="imaginary_tool",
            outcome="broken",
            lane="trainer",
            task_type="JUDGE",
            task_id=f"task-{index}",
            session_id="trainer:default",
            source="chat_tool_executor",
            failure_kind="not_implemented",
            details={"error": "Error: Tool 'imaginary_tool' not implemented."},
        )
    storage.commit()

    result = asyncio.run(
        make_executor().execute_tool_call(
            storage,
            call={
                "id": "call-1",
                "function": {"name": "imaginary_tool", "arguments": json.dumps({})},
            },
            session_id="trainer:default",
            content="",
            knowledge_pages=None,
        )
    )

    assert "circuit-broken" in result["tool_message"]["content"]
    assert result["tool_name"] == "imaginary_tool"
