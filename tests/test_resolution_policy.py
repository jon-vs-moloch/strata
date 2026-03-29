from __future__ import annotations

import asyncio
from types import SimpleNamespace
import sys

from strata.orchestrator.worker.resolution_policy import apply_resolution, determine_resolution
from strata.schemas.core import AttemptResolutionSchema
from strata.storage.models import TaskState, TaskType


class DummyTask:
    def __init__(self, task_id="root", priority=17.0, session_id="demo"):
        self.task_id = task_id
        self.title = "Parent Task"
        self.description = "Do the thing."
        self.session_id = session_id
        self.state = TaskState.WORKING
        self.type = TaskType.IMPL
        self.depth = 0
        self.priority = priority
        self.constraints = {}
        self.human_intervention_required = False


class DummyTaskRepo:
    def __init__(self):
        self.created = []
        self.dependencies = []

    def create(self, **kwargs):
        task = SimpleNamespace(task_id="repair-1", **kwargs)
        self.created.append(task)
        return task

    def add_dependency(self, task_id, depends_on_id):
        self.dependencies.append((task_id, depends_on_id))


class DummyAttemptsRepo:
    def get_by_task_id(self, task_id):
        return []


class DummyStorage:
    def __init__(self):
        self.tasks = DummyTaskRepo()
        self.attempts = DummyAttemptsRepo()
        self.commits = 0

    def commit(self):
        self.commits += 1

    def apply_dependency_cascade(self):
        return None


async def _run_apply_resolution(task_priority=17.0, reason="tool_broken"):
    storage = DummyStorage()
    task = DummyTask(priority=task_priority)
    resolution = AttemptResolutionSchema(
        reasoning="The tool returned malformed output and should not be trusted until fixed.",
        resolution="improve_tooling",
        tool_modification_target="search_web",
        tool_improvement_reason=reason,
    )
    queued = []

    async def enqueue_fn(task_id):
        queued.append(task_id)

    await apply_resolution(task, resolution, RuntimeError("boom"), storage, enqueue_fn)
    return task, storage, queued


def test_improve_tooling_inherits_parent_priority():
    task, storage, queued = asyncio.run(_run_apply_resolution(task_priority=23.0, reason="tool_too_weak"))
    repair = storage.tasks.created[0]
    assert task.state == TaskState.BLOCKED
    assert repair.priority == 23.0
    assert repair.constraints["source_task_priority"] == 23.0
    assert repair.constraints["tool_improvement_reason"] == "tool_too_weak"
    assert queued == ["repair-1"]


def test_improve_tooling_marks_broken_tools_as_bug_fix():
    _, storage, _ = asyncio.run(_run_apply_resolution(task_priority=5.0, reason="tool_broken"))
    repair = storage.tasks.created[0]
    assert repair.title.startswith("Tool Fix:")
    assert repair.type == TaskType.BUG_FIX
    assert repair.constraints["tool_modification_target"] == "search_web"


def test_blocked_weak_task_queues_strong_escalation_review(monkeypatch):
    storage = DummyStorage()
    task = DummyTask(session_id="agent:default")
    resolution = AttemptResolutionSchema(
        reasoning="Need higher-level judgment on whether this requires user clarification.",
        resolution="blocked",
    )
    queued = []
    queued_review_payloads = []

    async def enqueue_fn(task_id):
        queued.append(task_id)

    async def fake_queue_eval_system_job(storage_obj, **kwargs):
        queued_review_payloads.append(kwargs)
        return {"status": "queued", "task_id": "review-1"}

    api_main = sys.modules.get("strata.api.main")
    original_queue = getattr(api_main, "_queue_eval_system_job", None) if api_main else None
    if api_main is None:
        api_main = SimpleNamespace()
        sys.modules["strata.api.main"] = api_main
    api_main._queue_eval_system_job = fake_queue_eval_system_job
    try:
        asyncio.run(apply_resolution(task, resolution, RuntimeError("boom"), storage, enqueue_fn))
    finally:
        if original_queue is None:
            del sys.modules["strata.api.main"]
        else:
            api_main._queue_eval_system_job = original_queue

    assert task.state == TaskState.BLOCKED
    assert task.human_intervention_required is True
    assert queued == []
    assert len(queued_review_payloads) == 1
    payload = queued_review_payloads[0]
    assert payload["kind"] == "trace_review"
    assert payload["payload"]["supervision_reason"] == "agent_blocked_escalation"
    assert payload["payload"]["reviewer_tier"] == "trainer"


def test_research_iteration_limit_prefers_decompose():
    task = DummyTask()
    task.type = TaskType.RESEARCH

    resolution = asyncio.run(
        determine_resolution(
            task,
            RuntimeError("Agent iteration limit reached. Partial context saved."),
            model_adapter=None,
            storage=None,
        )
    )

    assert resolution.resolution == "decompose"


def test_failed_decomposition_prefers_abandon_to_parent():
    task = DummyTask()
    task.type = TaskType.DECOMP
    task.title = "Recovery Plan for Error Recover"

    resolution = asyncio.run(
        determine_resolution(
            task,
            RuntimeError("Decomposition produced no actionable subtasks. Escalate for trainer intervention instead of spawning generic recovery work."),
            model_adapter=None,
            storage=None,
        )
    )

    assert resolution.resolution == "abandon_to_parent"


def test_abandon_to_parent_agent_task_queues_trainer_intervention():
    storage = DummyStorage()
    task = DummyTask(session_id="agent:default")
    resolution = AttemptResolutionSchema(
        reasoning="Decomposition failed repeatedly and should be replaced by a bounded trainer intervention.",
        resolution="abandon_to_parent",
    )
    queued_review_payloads = []

    async def enqueue_fn(_task_id):
        raise AssertionError("abandon_to_parent should not enqueue new child work directly")

    async def fake_queue_eval_system_job(storage_obj, **kwargs):
        queued_review_payloads.append(kwargs)
        return {"status": "queued", "task_id": "review-2"}

    api_main = sys.modules.get("strata.api.main")
    original_queue = getattr(api_main, "_queue_eval_system_job", None) if api_main else None
    if api_main is None:
        api_main = SimpleNamespace()
        sys.modules["strata.api.main"] = api_main
    api_main._queue_eval_system_job = fake_queue_eval_system_job
    try:
        asyncio.run(apply_resolution(task, resolution, RuntimeError("boom"), storage, enqueue_fn))
    finally:
        if original_queue is None:
            del sys.modules["strata.api.main"]
        else:
            api_main._queue_eval_system_job = original_queue

    assert task.state == TaskState.ABANDONED
    assert len(queued_review_payloads) == 1
    payload = queued_review_payloads[0]
    assert payload["kind"] == "trace_review"
    assert payload["payload"]["supervision_reason"] == "abandon_to_parent_recovery_loop"
