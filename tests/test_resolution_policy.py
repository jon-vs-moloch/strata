from __future__ import annotations

import asyncio
from types import SimpleNamespace
import sys

from strata.orchestrator.user_questions import enqueue_user_question, get_question_for_source
from strata.orchestrator.worker.resolution_policy import apply_resolution, determine_resolution
from strata.orchestrator.worker.plan_review import generate_plan_review
from strata.orchestrator.research import ResearchIterationLimitError
from strata.schemas.core import AttemptResolutionSchema
from strata.storage.models import AttemptOutcome, TaskState, TaskType


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
        self.active_child_ids = []
        self.human_intervention_required = False


class DummyTaskRepo:
    def __init__(self):
        self.created = []
        self.dependencies = []
        self.by_id = {}

    def create(self, **kwargs):
        task = SimpleNamespace(task_id="repair-1", **kwargs)
        self.created.append(task)
        self.by_id[task.task_id] = task
        return task

    def add_dependency(self, task_id, depends_on_id):
        self.dependencies.append((task_id, depends_on_id))

    def get_by_id(self, task_id):
        return self.by_id.get(task_id)


class DummyAttemptsRepo:
    def __init__(self, by_task_id=None):
        self.by_task_id = by_task_id or {}

    def get_by_task_id(self, task_id):
        return list(self.by_task_id.get(task_id) or [])


class DummyStorage:
    def __init__(self, attempts_by_task=None, policy=None):
        self.tasks = DummyTaskRepo()
        self.attempts = DummyAttemptsRepo(attempts_by_task)
        values = {}
        self.parameters = SimpleNamespace(
            get_parameter=lambda key, default_value, description="": policy or default_value,
            peek_parameter=lambda key, default_value=None: values.get(key, default_value),
            set_parameter=lambda key, value, description="": values.__setitem__(key, value),
        )
        self.commits = 0

    def commit(self):
        self.commits += 1

    def apply_dependency_cascade(self):
        return None


class PromptCapturingModel:
    def __init__(self, content='{"reasoning":"ok","resolution":"reattempt","new_subtasks":[]}'):
        self.prompts = []
        self._content = content

    async def chat(self, messages=None, **_kwargs):
        self.prompts.append(messages or [])
        return {"content": self._content}

    def extract_structured_object(self, raw_content):
        import json
        return json.loads(raw_content)

    def extract_yaml(self, _raw_content):
        return {
            "plan_health": "degraded",
            "recommendation": "internal_replan",
            "confidence": 0.9,
            "rationale": "Observed looping branch.",
        }


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


def test_multistage_task_deterministically_decomposes():
    storage = DummyStorage()
    task = DummyTask()
    task.title = "Inspect, patch, and validate parser bug"
    task.description = "Inspect the failure, patch the parser, then validate with tests."

    resolution = asyncio.run(determine_resolution(task, RuntimeError("generic failure"), PromptCapturingModel(), storage))

    assert resolution.resolution == "decompose"
    assert "task-boundary" in resolution.reasoning.lower() or "oneshottable" in resolution.reasoning.lower()


def test_task_boundary_violation_deterministically_decomposes():
    storage = DummyStorage()
    task = DummyTask()

    class BoundaryError(RuntimeError):
        failure_kind = "task_boundary_violation"

    resolution = asyncio.run(determine_resolution(task, BoundaryError("needs another move"), PromptCapturingModel(), storage))

    assert resolution.resolution == "decompose"
    assert "oneshottable" in resolution.reasoning.lower() or "boundary" in resolution.reasoning.lower()


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


def test_blocked_resolution_promotes_existing_non_blocking_question():
    storage = DummyStorage()
    task = DummyTask(session_id="agent:default")
    enqueue_user_question(
        storage,
        session_id="agent:default",
        question="Do you know where the file lives?",
        source_type="task_blocked",
        source_id=task.task_id,
        lane="agent",
        escalation_mode="non_blocking",
    )
    resolution = AttemptResolutionSchema(
        reasoning="The file cannot be obtained with current capabilities.",
        resolution="blocked",
    )

    asyncio.run(apply_resolution(task, resolution, RuntimeError("boom"), storage, lambda _task_id: None))

    updated = get_question_for_source(storage, source_type="task_blocked", source_id=task.task_id)
    assert updated["escalation_mode"] == "blocking"


def test_reattempt_demotes_existing_blocking_question_when_new_path_exists():
    storage = DummyStorage(attempts_by_task={"root": []})
    task = DummyTask(task_id="root", session_id="agent:default")
    enqueue_user_question(
        storage,
        session_id="agent:default",
        question="What should I do?",
        source_type="task_blocked",
        source_id=task.task_id,
        lane="agent",
        escalation_mode="blocking",
    )
    resolution = AttemptResolutionSchema(
        reasoning="Retry with the updated plan.",
        resolution="reattempt",
    )
    queued = []

    async def enqueue_fn(task_id):
        queued.append(task_id)

    asyncio.run(apply_resolution(task, resolution, RuntimeError("temporary timeout"), storage, enqueue_fn))

    updated = get_question_for_source(storage, source_type="task_blocked", source_id=task.task_id)
    assert updated["escalation_mode"] == "non_blocking"
    assert queued == ["root"]


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


def test_clarification_research_iteration_limit_blocks_instead_of_decompose():
    task = DummyTask()
    task.type = TaskType.RESEARCH
    task.title = "Procedure: Operator Onboarding"
    task.description = (
        "Confirm unresolved onboarding items. If an item cannot be completed directly, "
        "surface it as an explicit pending question or attention item instead of silently skipping it."
    )

    resolution = asyncio.run(
        determine_resolution(
            task,
            RuntimeError("Agent iteration limit reached. Partial context saved."),
            model_adapter=None,
            storage=None,
        )
    )

    assert resolution.resolution == "blocked"


def test_failed_decomposition_prefers_internal_replan():
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

    assert resolution.resolution == "internal_replan"


def test_recovery_shell_iteration_limit_prefers_internal_replan():
    task = DummyTask(task_id="recover-1")
    task.title = "Error Recover"
    task.description = "Initial decomposition failed. Research manually."
    storage = DummyStorage(policy={"lineage_iteration_limit": 4})

    resolution = asyncio.run(
        determine_resolution(
            task,
            ResearchIterationLimitError(
                public_message="Agent iteration limit reached. Partial context saved.",
                autopsy={"archived_transcript": {"path": "./.knowledge/wip_research_demo.md"}},
            ),
            model_adapter=None,
            storage=storage,
        )
    )

    assert resolution.resolution == "internal_replan"
    assert "captured autopsy" in resolution.reasoning


def test_recovery_shell_iteration_limit_abandons_after_lineage_cap():
    task = DummyTask(task_id="recover-2")
    task.title = "Error Recover"
    task.description = "Initial decomposition failed. Research manually."
    task.parent_task_id = "parent-1"
    storage = DummyStorage(
        attempts_by_task={
            "recover-2": [
                SimpleNamespace(
                    outcome=AttemptOutcome.FAILED,
                    reason="Agent iteration limit reached.",
                    evidence={"failure_kind": "iteration_budget_exhausted"},
                )
            ],
            "parent-1": [
                SimpleNamespace(
                    outcome=AttemptOutcome.FAILED,
                    reason="Agent iteration limit reached.",
                    evidence={"failure_kind": "iteration_budget_exhausted"},
                ),
                SimpleNamespace(
                    outcome=AttemptOutcome.FAILED,
                    reason="Agent iteration limit reached.",
                    evidence={"failure_kind": "iteration_budget_exhausted"},
                ),
                SimpleNamespace(
                    outcome=AttemptOutcome.FAILED,
                    reason="Agent iteration limit reached.",
                    evidence={"failure_kind": "iteration_budget_exhausted"},
                ),
            ]
        },
        policy={"lineage_iteration_limit": 4},
    )
    storage.tasks.by_id["parent-1"] = SimpleNamespace(task_id="parent-1", parent_task_id=None)

    resolution = asyncio.run(
        determine_resolution(
            task,
            ResearchIterationLimitError(
                public_message="Agent iteration limit reached. Partial context saved.",
                autopsy={},
            ),
            model_adapter=None,
            storage=storage,
        )
    )

    assert resolution.resolution == "abandon_to_parent"


def test_internal_replan_failover_preserves_original_task_focus():
    root = DummyTask(task_id="root-task", session_id="agent:default")
    root.title = "Procedure: Operator Onboarding"
    root.description = "Confirm operator preferences and unresolved onboarding questions."
    root.type = TaskType.RESEARCH

    task = DummyTask(task_id="recover-3", session_id="agent:default")
    task.title = "Error Recover"
    task.description = "Initial decomposition failed. Research manually."
    task.parent_task_id = root.task_id

    storage = DummyStorage()
    storage.tasks.by_id[root.task_id] = root
    resolution = AttemptResolutionSchema(
        reasoning="This branch needs a bounded replacement plan.",
        resolution="internal_replan",
        new_subtasks=[],
    )
    queued = []

    async def enqueue_fn(task_id):
        queued.append(task_id)

    asyncio.run(apply_resolution(task, resolution, RuntimeError("boom"), storage, enqueue_fn))

    created = storage.tasks.created[0]
    assert created.title == "Recovery Plan for Procedure: Operator Onboarding"
    assert created.session_id == "agent:default"
    assert "Do not produce generic 'Error Recover' shells" in created.description
    assert created.constraints["recovery_focus_task_id"] == root.task_id
    assert task.state == TaskState.PUSHED
    assert task.active_child_ids == ["repair-1"]
    assert queued == ["repair-1"]


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


def test_failed_decomposition_prefers_internal_replan_over_abandon():
    storage = DummyStorage()
    task = DummyTask()
    task.type = TaskType.DECOMP

    resolution = asyncio.run(
        determine_resolution(
            task,
            RuntimeError("Decomposition produced no actionable subtasks."),
            PromptCapturingModel(),
            storage,
        )
    )

    assert resolution.resolution == "internal_replan"
    assert "recoverable planning failure" in resolution.reasoning.lower()


def test_determine_resolution_prompt_includes_attempt_intelligence():
    task = DummyTask(task_id="recover-3")
    task.parent_task_id = "parent-2"
    storage = DummyStorage(
        attempts_by_task={
            "recover-3": [
                SimpleNamespace(
                    attempt_id="attempt-1",
                    outcome=AttemptOutcome.FAILED,
                    resolution=None,
                    reason="Agent iteration limit reached.",
                    evidence={"failure_kind": "iteration_budget_exhausted"},
                )
            ],
            "parent-2": [
                SimpleNamespace(
                    attempt_id="attempt-parent",
                    outcome=AttemptOutcome.FAILED,
                    resolution=None,
                    reason="Agent iteration limit reached.",
                    evidence={"failure_kind": "iteration_budget_exhausted"},
                ),
            ],
        },
    )
    storage.tasks.by_id["parent-2"] = SimpleNamespace(task_id="parent-2", parent_task_id=None)
    model = PromptCapturingModel()

    resolution = asyncio.run(
        determine_resolution(
            task,
            RuntimeError("ambiguous failure"),
            model_adapter=model,
            storage=storage,
        )
    )

    prompt = model.prompts[0][0]["content"]
    assert resolution.resolution == "reattempt"
    assert "Attempt Intelligence:" in prompt
    assert "Lineage iteration failures: 2" in prompt


def test_generate_plan_review_prompt_includes_attempt_intelligence():
    task = DummyTask(task_id="review-1")
    storage = DummyStorage(
        attempts_by_task={
            "review-1": [
                SimpleNamespace(
                    attempt_id="attempt-1",
                    outcome=AttemptOutcome.FAILED,
                    resolution=None,
                    reason="Repeated timeout while retrying.",
                    evidence={"failure_kind": "network_timeout"},
                )
            ]
        }
    )
    attempt = SimpleNamespace(
        attempt_id="attempt-1",
        outcome=AttemptOutcome.FAILED,
        reason="Repeated timeout while retrying.",
    )
    model = PromptCapturingModel(content="plan_health: degraded\nrecommendation: internal_replan\nconfidence: 0.9\nrationale: Observed looping branch.")

    review = asyncio.run(generate_plan_review(task, attempt, model, storage))

    prompt = model.prompts[0][0]["content"]
    assert review["recommendation"] == "internal_replan"
    assert "Attempt Intelligence:" in prompt
    assert "network_timeout" in prompt
