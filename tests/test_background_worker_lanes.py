from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import asyncio

from strata.orchestrator.background import (
    BackgroundWorker,
    ensure_blocked_weak_task_review,
    resolution_from_plan_review,
)
from strata.orchestrator.worker.idle_policy import run_idle_tasks
from strata.orchestrator.worker.attempt_runner import _run_decomposition
from strata.procedures.registry import queue_procedure
from strata.schemas.core import TaskDecomposition, TaskFraming
from strata.storage.models import Base, TaskModel, TaskState, TaskType
from strata.storage.services.main import StorageManager
from strata.api.main import GLOBAL_SETTINGS
from sqlalchemy.exc import OperationalError


def make_storage():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    return StorageManager(session=session)


def make_storage_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def factory():
        return StorageManager(session=SessionLocal())

    return factory


class DummyModel:
    async def chat(self, *_args, **_kwargs):
        return {"content": "ok"}

    def bind_execution_context(self, _context):
        return None


class DummyDecompModel(DummyModel):
    def extract_structured_object(self, _raw):
        return {
            "framing": {
                "repository_context": "Repo",
                "problem_statement": "Do the thing",
                "constraints": [],
                "success_criteria": [],
            },
            "subtasks": {},
            "total_estimated_budget": 0.1,
        }


class GenericRecoveryDecompModel(DummyModel):
    def extract_structured_object(self, _raw):
        return {
            "framing": {
                "repository_context": "Repo",
                "problem_statement": "Do the thing",
                "constraints": [],
                "success_criteria": [],
            },
            "subtasks": {
                "recover": {
                    "title": "Error Recover",
                    "description": "Initial decomposition failed. Research manually.",
                    "target_files": [],
                    "edit_type": "chore",
                    "validator": "pytest",
                    "max_diff_size": 5000,
                    "dependencies": [],
                }
            },
            "total_estimated_budget": 0.1,
        }


class ExplodingModel(DummyModel):
    async def chat(self, *_args, **_kwargs):
        raise AssertionError("idle alignment should not call the model before onboarding exists")


def test_background_worker_can_pause_and_resume_individual_lanes():
    worker = BackgroundWorker(storage_factory=make_storage, model_adapter=DummyModel())
    worker._running = True

    worker.pause("agent")

    assert worker.lane_status("agent") == "PAUSED"
    assert worker.lane_status("trainer") == "IDLE"

    worker.resume("agent")

    assert worker.lane_status("agent") == "IDLE"


def test_background_worker_stop_current_respects_lane_scope():
    worker = BackgroundWorker(storage_factory=make_storage, model_adapter=DummyModel())

    class StubProcess:
        def __init__(self):
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

    process = StubProcess()
    worker._current_processes["trainer"] = process
    worker._current_task_ids["trainer"] = "trainer-task"

    assert worker.stop_current("agent") is False
    assert process.cancelled is False
    assert worker.stop_current("trainer") is True
    assert process.cancelled is True


def test_background_worker_task_controls_pause_resume_and_cancel():
    storage_factory = make_storage_factory()
    storage = storage_factory()
    task = storage.tasks.create(
        title="Inspect user profile",
        description="Update profile knowledge.",
        session_id="agent:default",
        state=TaskState.PENDING,
        constraints={"lane": "agent"},
    )
    storage.commit()
    task_id = task.task_id
    storage.close()

    worker = BackgroundWorker(storage_factory=storage_factory, model_adapter=DummyModel())

    assert worker.pause_task(task_id) is True

    paused_storage = storage_factory()
    paused_task = paused_storage.tasks.get_by_id(task_id)
    assert paused_task is not None
    assert paused_task.state == TaskState.PENDING
    assert paused_task.constraints.get("paused") is True
    paused_storage.close()

    assert asyncio.run(worker.resume_task(task_id)) is True

    resumed_storage = storage_factory()
    resumed_task = resumed_storage.tasks.get_by_id(task_id)
    assert resumed_task is not None
    assert resumed_task.state == TaskState.PENDING
    assert resumed_task.constraints.get("paused") is None
    resumed_storage.close()

    assert worker.stop_task(task_id) is True

    cancelled_storage = storage_factory()
    cancelled_task = cancelled_storage.tasks.get_by_id(task_id)
    assert cancelled_task is not None
    assert cancelled_task.state == TaskState.CANCELLED
    assert cancelled_task.constraints.get("paused") is None
    cancelled_storage.close()


def test_background_worker_enqueue_runnable_tasks_respects_lane_and_paused_state():
    storage_factory = make_storage_factory()
    storage = storage_factory()
    trainer_task = storage.tasks.create(
        title="Trainer task",
        description="Do trainer work.",
        session_id="trainer:default",
        state=TaskState.PENDING,
        constraints={"lane": "trainer"},
    )
    agent_task = storage.tasks.create(
        title="Agent task",
        description="Do agent work.",
        session_id="agent:default",
        state=TaskState.PENDING,
        constraints={"lane": "agent", "paused": True},
    )
    storage.commit()
    trainer_task_id = trainer_task.task_id
    agent_task_id = agent_task.task_id
    storage.close()

    worker = BackgroundWorker(storage_factory=storage_factory, model_adapter=DummyModel())

    enqueued = asyncio.run(worker.enqueue_runnable_tasks("trainer"))
    assert enqueued == 1

    queued = []
    trainer_queue = worker._lane_queue("trainer")
    while not trainer_queue.empty():
        queued.append(trainer_queue.get_nowait())

    assert trainer_task_id in queued
    assert agent_task_id not in queued


def test_worker_status_includes_lane_runtime_details():
    storage_factory = make_storage_factory()
    worker = BackgroundWorker(storage_factory=storage_factory, model_adapter=DummyModel())
    worker._running = True
    worker._tier_health["agent"] = "ok"
    task_id = "task-123"
    worker._current_task_ids["agent"] = task_id
    worker._current_processes["agent"] = object()
    worker._lane_started_at["agent"] = datetime.now(timezone.utc)
    worker._lane_last_activity_at["agent"] = datetime.now(timezone.utc)

    status = worker.status
    agent_detail = status["lane_details"]["agent"]

    assert agent_detail["activity_mode"] == "GENERATING"
    assert agent_detail["current_task_id"] == task_id
    assert agent_detail["current_task_started_at"] is not None
    assert agent_detail["heartbeat_age_s"] is not None


def test_worker_status_exposes_live_step_details():
    storage_factory = make_storage_factory()
    worker = BackgroundWorker(storage_factory=storage_factory, model_adapter=DummyModel())
    worker._running = True
    worker._tier_health["agent"] = "ok"
    worker._current_task_ids["agent"] = "task-123"
    worker._current_processes["agent"] = object()
    worker._lane_started_at["agent"] = datetime.now(timezone.utc)
    worker._lane_last_activity_at["agent"] = datetime.now(timezone.utc)
    worker._mark_lane_progress(
        "agent",
        step="tool_execution",
        label="Executing research tool",
        detail="read_file",
        task_id="task-123",
        task_title="Procedure: Operator Onboarding",
        attempt_id="attempt-1",
        progress_label="tool read_file",
    )

    agent_detail = worker.status["lane_details"]["agent"]

    assert agent_detail["step"] == "tool_execution"
    assert agent_detail["step_label"] == "Executing research tool"
    assert agent_detail["step_detail"] == "read_file"
    assert agent_detail["active_attempt_id"] == "attempt-1"
    assert agent_detail["recent_steps"][-1]["label"] == "Executing research tool"
    assert "Executing research tool: read_file" in agent_detail["ticker_items"][-1]


def test_task_and_attempt_creation_retry_after_sqlite_lock(monkeypatch):
    storage = make_storage()
    try:
        from strata.storage import repositories as repositories_pkg
        import strata.storage.repositories.tasks as task_repo_module
        import strata.storage.repositories.attempts as attempt_repo_module

        original_task_flush = task_repo_module.flush_with_write_lock
        original_attempt_flush = attempt_repo_module.flush_with_write_lock
        task_calls = {"count": 0}
        attempt_calls = {"count": 0}

        def flaky_task_flush(session, *, enabled):
            task_calls["count"] += 1
            if task_calls["count"] == 1:
                raise OperationalError("insert", {}, Exception("database is locked"))
            return original_task_flush(session, enabled=enabled)

        def flaky_attempt_flush(session, *, enabled):
            attempt_calls["count"] += 1
            if attempt_calls["count"] == 1:
                raise OperationalError("insert", {}, Exception("database is locked"))
            return original_attempt_flush(session, enabled=enabled)

        monkeypatch.setattr(task_repo_module, "flush_with_write_lock", flaky_task_flush)
        monkeypatch.setattr(attempt_repo_module, "flush_with_write_lock", flaky_attempt_flush)

        task = storage.tasks.create(
            title="Retryable task",
            description="Should survive a transient sqlite lock.",
            session_id="agent:default",
            state=TaskState.PENDING,
        )
        attempt = storage.attempts.create(task_id=task.task_id)

        assert task.task_id
        assert attempt.attempt_id
        assert task_calls["count"] >= 2
        assert attempt_calls["count"] >= 2
    finally:
        storage.close()


def test_lane_idle_policies_seed_strong_supervision_independently(monkeypatch):
    storage_factory = make_storage_factory()
    worker = BackgroundWorker(storage_factory=storage_factory, model_adapter=DummyModel())
    worker._tier_health["trainer"] = "ok"

    calls = []

    async def fake_supervision(*_args, **kwargs):
        calls.append("trainer")
        return {"status": "queued", "task_id": "bootstrap-job"}

    async def fake_idle_tasks(*_args, **_kwargs):
        calls.append("agent")

    monkeypatch.setattr("strata.orchestrator.background.ensure_continuous_supervision_job", fake_supervision)
    monkeypatch.setattr("strata.orchestrator.background.run_idle_tasks", fake_idle_tasks)

    asyncio.run(worker._ensure_lane_idle_policies({"automatic_task_generation": False, "testing_mode": False}))

    assert calls == ["trainer"]


def test_lane_idle_policies_can_seed_weak_even_when_other_lane_is_busy(monkeypatch):
    storage_factory = make_storage_factory()
    storage = storage_factory()
    strong_task = storage.tasks.create(
        title="Bootstrap Cycle",
        description="Trainer supervision task.",
        session_id="trainer:default",
        state=TaskState.WORKING,
        constraints={"lane": "trainer"},
    )
    storage.commit()
    assert strong_task.task_id

    storage.close()

    worker = BackgroundWorker(storage_factory=storage_factory, model_adapter=DummyModel())
    worker._tier_health["trainer"] = "ok"

    calls = []

    async def fake_supervision(*_args, **kwargs):
        calls.append("trainer")
        return None

    async def fake_idle_tasks(*_args, **_kwargs):
        calls.append("agent")

    monkeypatch.setattr("strata.orchestrator.background.ensure_continuous_supervision_job", fake_supervision)
    monkeypatch.setattr("strata.orchestrator.background.run_idle_tasks", fake_idle_tasks)

    asyncio.run(worker._ensure_lane_idle_policies({"automatic_task_generation": True, "testing_mode": False}))

    assert calls == ["agent"]


def test_runtime_defaults_replay_pending_tasks_on_startup():
    assert GLOBAL_SETTINGS["replay_pending_tasks_on_startup"] is True


def test_run_idle_tasks_seeds_onboarding_before_alignment(monkeypatch):
    storage_factory = make_storage_factory()
    queue = asyncio.Queue()

    monkeypatch.setattr("strata.orchestrator.worker.idle_policy.deliver_communication", lambda *args, **kwargs: None)

    asyncio.run(run_idle_tasks(storage_factory, ExplodingModel(), queue))

    queued_task_id = queue.get_nowait()
    storage = storage_factory()
    try:
        queued_task = storage.tasks.get_by_id(queued_task_id)
        all_tasks = storage.session.query(TaskModel).all()
        assert queued_task is not None
        assert queued_task.title == "Procedure: Operator Onboarding"
        assert queued_task.constraints["procedure_id"] == "operator_onboarding"
        assert not any(str(task.title).startswith("Alignment:") for task in all_tasks)
    finally:
        storage.close()


def test_run_idle_tasks_allows_alignment_after_onboarding_completes(monkeypatch):
    storage_factory = make_storage_factory()
    storage = storage_factory()
    onboarding = queue_procedure(storage, None, procedure_id="operator_onboarding", lane="agent")
    onboarding.state = TaskState.COMPLETE
    storage.commit()
    storage.close()

    queue = asyncio.Queue()

    async def fake_verify_artifact(*_args, **_kwargs):
        return {"verdict": "sound", "failure_modes": []}

    monkeypatch.setattr("strata.orchestrator.worker.idle_policy.deliver_communication", lambda *args, **kwargs: None)
    monkeypatch.setattr("strata.orchestrator.worker.idle_policy.verify_artifact", fake_verify_artifact)

    asyncio.run(run_idle_tasks(storage_factory, DummyModel(), queue))

    queued_task_id = queue.get_nowait()
    reloaded = storage_factory()
    try:
        queued_task = reloaded.tasks.get_by_id(queued_task_id)
        assert queued_task is not None
        assert queued_task.title.startswith("Alignment:")
        assert queued_task.constraints["alignment_source"] == "idle_policy"
    finally:
        reloaded.close()


def test_resolution_from_plan_review_honors_structural_recommendation():
    resolution = resolution_from_plan_review(
        {
            "plan_health": "degraded",
            "recommendation": "decompose",
            "confidence": 0.92,
            "rationale": "The task should be broken into smaller steps.",
        }
    )

    assert resolution is not None
    assert resolution.resolution == "decompose"
    assert resolution.reasoning == "The task should be broken into smaller steps."


def test_run_decomposition_raises_when_no_actionable_subtasks():
    storage = make_storage()
    task = storage.tasks.create(
        title="Recovery Plan for Error Recover",
        description="Try to recover from a failed decomposition.",
        session_id="agent:default",
        state=TaskState.PENDING,
        constraints={"lane": "agent"},
    )
    task.type = TaskType.DECOMP
    storage.commit()

    async def enqueue_fn(_task_id):
        raise AssertionError("No child tasks should be enqueued for an empty decomposition")

    try:
        asyncio.run(_run_decomposition(task, storage, DummyDecompModel(), enqueue_fn))
        raise AssertionError("Expected empty decomposition to raise")
    except RuntimeError as exc:
        assert "no actionable subtasks" in str(exc).lower()
        assert "recoverable planning failure" in str(exc).lower()


def test_run_decomposition_rejects_generic_recovery_shell_subtasks():
    storage = make_storage()
    task = storage.tasks.create(
        title="Recovery Plan for Operator Onboarding",
        description="Create a bounded recovery plan.",
        session_id="agent:default",
        state=TaskState.PENDING,
        constraints={"lane": "agent"},
    )
    task.type = TaskType.DECOMP
    storage.commit()

    async def enqueue_fn(_task_id):
        raise AssertionError("Generic recovery placeholders should not be enqueued")

    try:
        asyncio.run(_run_decomposition(task, storage, GenericRecoveryDecompModel(), enqueue_fn))
        raise AssertionError("Expected generic recovery placeholder to be rejected")
    except RuntimeError as exc:
        assert "recoverable planning failure" in str(exc).lower()


def test_run_decomposition_falls_back_to_procedure_checklist_subtasks():
    storage = make_storage()
    root = storage.tasks.create(
        title="Procedure: Operator Onboarding",
        description="Run onboarding.",
        session_id="agent:default",
        state=TaskState.WORKING,
        constraints={
            "lane": "agent",
            "procedure_id": "operator_onboarding",
            "procedure_title": "Operator Onboarding",
            "procedure_checklist": [
                {
                    "id": "agent_name",
                    "title": "Choose or confirm the agent name",
                    "verification": "Session metadata contains participant names.",
                },
                {
                    "id": "runtime_posture",
                    "title": "Confirm runtime posture",
                    "verification": "Runtime posture is durably recorded or queued.",
                },
            ],
        },
    )
    decomp_task = storage.tasks.create(
        title="Recovery Plan for Procedure: Operator Onboarding",
        description="Create a bounded recovery plan.",
        session_id="agent:default",
        parent_task_id=root.task_id,
        state=TaskState.PENDING,
        constraints={"lane": "agent", "recovery_focus_task_id": root.task_id},
    )
    decomp_task.type = TaskType.DECOMP
    storage.commit()

    queued = []

    async def enqueue_fn(task_id):
        queued.append(task_id)

    asyncio.run(_run_decomposition(decomp_task, storage, DummyDecompModel(), enqueue_fn))

    created = [task for task in storage.session.query(TaskModel).all() if task.parent_task_id == decomp_task.task_id]
    assert len(created) == 2
    assert all(task.type == TaskType.RESEARCH for task in created)
    assert all("procedure_checklist_item" in dict(task.constraints or {}) for task in created)
    assert len(queued) == 2


def test_blocked_weak_task_review_skips_when_no_new_evidence_after_review():
    storage_factory = make_storage_factory()
    storage = storage_factory()
    task = storage.tasks.create(
        title="Blocked task",
        description="Need guidance.",
        session_id="agent:default",
        state=TaskState.BLOCKED,
        constraints={
            "lane": "agent",
            "trace_reviews": [
                {
                    "trace_kind": "task_trace",
                    "reviewer_tier": "trainer",
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                }
            ],
        },
    )
    task.human_intervention_required = True
    attempt = storage.attempts.create(task_id=task.task_id)
    attempt.started_at = datetime.now(timezone.utc) - timedelta(minutes=15)
    attempt.ended_at = datetime.now(timezone.utc) - timedelta(minutes=14)
    storage.commit()
    storage.close()

    async def fake_queue_system_job(*_args, **_kwargs):
        raise AssertionError("Should not queue review without new evidence")

    result = asyncio.run(
        ensure_blocked_weak_task_review(
            storage_factory,
            queue_system_job=fake_queue_system_job,
        )
    )

    assert result is None
