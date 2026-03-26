from __future__ import annotations

import asyncio
from types import SimpleNamespace

from strata.orchestrator.worker.resolution_policy import apply_resolution
from strata.schemas.core import AttemptResolutionSchema
from strata.storage.models import TaskState, TaskType


class DummyTask:
    def __init__(self, task_id="root", priority=17.0):
        self.task_id = task_id
        self.title = "Parent Task"
        self.description = "Do the thing."
        self.session_id = "demo"
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
