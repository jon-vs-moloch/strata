from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import asyncio

from strata.orchestrator.background import (
    BackgroundWorker,
    _preflight_lane_model,
    queue_direct_audit_review,
    queue_process_repair_task,
    ensure_blocked_weak_task_review,
    resolution_from_plan_review,
)
from strata.orchestrator.capability_incidents import get_capability_incident, record_capability_incident
from strata.schemas.core import AttemptResolutionSchema
from strata.orchestrator.worker.idle_policy import run_idle_tasks
from strata.orchestrator.worker.attempt_runner import _run_decomposition
from strata.procedures.registry import queue_procedure
from strata.schemas.core import TaskDecomposition, TaskFraming
from strata.storage.models import AttemptOutcome, Base, TaskModel, TaskState, TaskType
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


class DependentDecompModel(DummyModel):
    def extract_structured_object(self, _raw):
        return {
            "framing": {
                "repository_context": "Repo",
                "problem_statement": "Do the thing",
                "constraints": [],
                "success_criteria": [],
            },
            "subtasks": {
                "inspect": {
                    "title": "Inspect current settings",
                    "description": "Inspect the existing runtime posture configuration.",
                    "target_files": ["strata/api/main.py"],
                    "edit_type": "chore",
                    "validator": "pytest",
                    "max_diff_size": 5000,
                    "dependencies": [],
                },
                "persist": {
                    "title": "Persist runtime posture",
                    "description": "Write the confirmed runtime posture into durable settings.",
                    "target_files": ["strata/api/main.py"],
                    "edit_type": "feature",
                    "validator": "pytest",
                    "max_diff_size": 5000,
                    "dependencies": ["inspect"],
                },
            },
            "total_estimated_budget": 0.2,
        }


class ExplodingModel(DummyModel):
    async def chat(self, *_args, **_kwargs):
        raise AssertionError("idle alignment should not call the model before onboarding exists")


class ErrorStatusModel(DummyModel):
    async def chat(self, *_args, **_kwargs):
        return {"status": "error", "message": "local provider unhealthy"}


class HangingModel(DummyModel):
    async def chat(self, *_args, **_kwargs):
        await asyncio.sleep(10)
        return {"status": "success", "content": "ok"}


class LocalCatalogModel(DummyModel):
    endpoint = "http://127.0.0.1:1234/v1/chat/completions"
    active_model = "foo-local-model"


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


def test_background_worker_seeds_per_work_pool_agent_preflight():
    storage_factory = make_storage_factory()
    worker = BackgroundWorker(storage_factory=storage_factory, model_adapter=DummyModel())

    task_id = worker._ensure_agent_preflight_task("remote_agent")

    assert task_id
    assert worker._agent_preflight_health["remote_agent"] == "queued"
    storage = storage_factory()
    try:
        task = storage.tasks.get_by_id(task_id)
        assert task is not None
        assert task.constraints["procedure_id"] == "agent_preflight"
        assert task.constraints["work_pool"] == "remote_agent"
        assert task.constraints["execution_profile"] == "remote_agent"
    finally:
        storage.close()


def test_background_worker_interleaves_shared_backend_turns():
    worker = BackgroundWorker(storage_factory=make_storage, model_adapter=DummyModel())

    worker._work_pool_backend_group_key = lambda pool: "shared-cloud" if pool in {"trainer", "remote_agent"} else None  # type: ignore[method-assign]

    async def scenario():
        order = []
        first_group = await worker._acquire_backend_group_turn("trainer")

        async def wait_remote():
            group = await worker._acquire_backend_group_turn("remote_agent")
            order.append("remote_agent")
            await worker._release_backend_group_turn(group, "remote_agent")

        async def wait_trainer_again():
            group = await worker._acquire_backend_group_turn("trainer")
            order.append("trainer")
            await worker._release_backend_group_turn(group, "trainer")

        remote_task = asyncio.create_task(wait_remote())
        await asyncio.sleep(0.01)
        trainer_task = asyncio.create_task(wait_trainer_again())
        await asyncio.sleep(0.01)
        await worker._release_backend_group_turn(first_group, "trainer")
        await asyncio.gather(remote_task, trainer_task)
        return order

    assert asyncio.run(scenario()) == ["remote_agent", "trainer"]


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


def test_enqueue_with_priority_skips_duplicate_queued_task():
    worker = BackgroundWorker(storage_factory=make_storage, model_adapter=DummyModel())
    queue = worker._lane_queue("agent")
    queue.put_nowait("task-123")

    asyncio.run(worker.enqueue_with_priority("task-123"))

    queued = list(queue._queue)
    assert queued == ["task-123"]


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
    assert GLOBAL_SETTINGS["replay_pending_tasks_on_startup"] is False


def test_queue_direct_audit_review_latches_open_incident(monkeypatch):
    storage = make_storage()
    task = storage.tasks.create(
        title="Review branch output",
        description="Verify current task output.",
        session_id="agent:default",
        state=TaskState.WORKING,
        constraints={"lane": "agent"},
    )
    storage.commit()
    incident = record_capability_incident(
        storage,
        capability_kind="task_output",
        capability_name=task.task_id,
        status="degraded",
        reason="Verifier requested audit.",
        task_id=task.task_id,
        session_id=task.session_id,
    )
    storage.commit()

    queued = []

    async def fake_queue_eval_system_job(*_args, **_kwargs):
        queued.append("audit")
        return {"status": "queued", "task_id": "audit-task-1"}

    monkeypatch.setattr("strata.api.main._queue_eval_system_job", fake_queue_eval_system_job)

    verification = {
        "attempt_id": "attempt-1",
        "verdict": "uncertain",
        "recommended_action": "audit",
        "incident_id": incident["incident_id"],
    }

    first = asyncio.run(queue_direct_audit_review(storage, task=task, verification=verification))
    second = asyncio.run(queue_direct_audit_review(storage, task=task, verification=verification))

    incident_after = get_capability_incident(storage, incident_id=incident["incident_id"])

    assert first is not None
    assert first["task_id"] == "audit-task-1"
    assert second is None
    assert queued == ["audit"]
    assert incident_after is not None
    assert incident_after["metadata"]["audit_task_id"] == "audit-task-1"


def test_queue_process_repair_task_latches_open_incident(monkeypatch):
    storage = make_storage()
    task = storage.tasks.create(
        title="Verifier branch",
        description="A verifier failure needs repair.",
        session_id="agent:default",
        state=TaskState.WORKING,
        constraints={"lane": "agent"},
    )
    storage.commit()
    incident = record_capability_incident(
        storage,
        capability_kind="process",
        capability_name="verification_process",
        status="degraded",
        reason="Repeated verifier machinery failures.",
        task_id=task.task_id,
        session_id=task.session_id,
    )
    storage.commit()

    enqueued = []

    async def fake_enqueue(task_id: str):
        enqueued.append(task_id)

    first = asyncio.run(
        queue_process_repair_task(
            storage,
            task=task,
            process_name="verification_process",
            reason="Repeated verifier machinery failures.",
            enqueue_fn=fake_enqueue,
            incident_id=incident["incident_id"],
        )
    )
    storage.commit()

    assert first is not None
    first.state = TaskState.COMPLETE
    storage.commit()

    second = asyncio.run(
        queue_process_repair_task(
            storage,
            task=task,
            process_name="verification_process",
            reason="Repeated verifier machinery failures.",
            enqueue_fn=fake_enqueue,
            incident_id=incident["incident_id"],
        )
    )

    incident_after = get_capability_incident(storage, incident_id=incident["incident_id"])

    assert second is None
    assert enqueued == [first.task_id]
    assert incident_after is not None
    assert incident_after["metadata"]["repair_task_id"] == first.task_id
    assert first.constraints["procedure_id"] == "verification_review"
    assert first.constraints["procedure_title"] == "Verification Review"
    assert len(first.constraints["procedure_checklist"]) == 3


def test_preflight_lane_model_treats_error_status_as_failure():
    ok, reason = asyncio.run(
        _preflight_lane_model("agent", ErrorStatusModel(), object(), timeout_s=0.2)
    )

    assert ok is False
    assert "unhealthy" in reason


def test_preflight_lane_model_times_out_hard():
    ok, reason = asyncio.run(
        _preflight_lane_model("agent", HangingModel(), object(), timeout_s=0.05)
    )

    assert ok is False
    assert "timed out" in reason


def test_preflight_lane_model_uses_local_catalog_for_local_endpoints(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"id": "foo-local-model"}]}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            calls.append(url)
            return FakeResponse()

    monkeypatch.setattr("strata.orchestrator.background.httpx.AsyncClient", FakeClient)

    ok, reason = asyncio.run(
        _preflight_lane_model("agent", LocalCatalogModel(), object(), timeout_s=0.2)
    )

    assert ok is True
    assert reason == ""
    assert calls == ["http://127.0.0.1:1234/v1/models"]


def test_run_task_cycle_returns_immediately_after_decompose_handoff(monkeypatch):
    storage_factory = make_storage_factory()
    storage = storage_factory()
    task = storage.tasks.create(
        title="Smoke child",
        description="Confirm the core spec files are present.",
        session_id="agent:default",
        state=TaskState.PENDING,
        type=TaskType.RESEARCH,
        constraints={"lane": "agent"},
    )
    storage.commit()
    task_id = task.task_id
    storage.close()

    worker = BackgroundWorker(storage_factory=storage_factory, model_adapter=DummyModel())

    async def fail_attempt(*_args, **_kwargs):
        storage = _args[1]
        task = _args[0]
        attempt = storage.attempts.create(task_id=task.task_id)
        storage.attempts.update_outcome(attempt.attempt_id, AttemptOutcome.FAILED, reason="boom")
        storage.commit()
        return False, RuntimeError("boom"), attempt

    async def fake_determine_resolution(*_args, **_kwargs):
        return AttemptResolutionSchema(reasoning="decompose it", resolution="decompose", new_subtasks=[])

    async def fake_apply_resolution(task, _resolution, _error, storage, enqueue_fn):
        child = storage.tasks.create(
            title="Recovery child",
            description="child",
            session_id=task.session_id,
            parent_task_id=task.task_id,
            state=TaskState.PENDING,
            constraints={"lane": "agent"},
        )
        task.active_child_ids = [child.task_id]
        task.state = TaskState.PUSHED
        storage.commit()
        await enqueue_fn(child.task_id)

    async def should_not_verify(*_args, **_kwargs):
        raise AssertionError("verification should be skipped after decompose handoff")

    async def should_not_review(*_args, **_kwargs):
        raise AssertionError("review should be skipped after decompose handoff")

    monkeypatch.setattr("strata.orchestrator.background.run_attempt", fail_attempt)
    monkeypatch.setattr("strata.orchestrator.background.determine_resolution", fake_determine_resolution)
    monkeypatch.setattr("strata.orchestrator.background.apply_resolution", fake_apply_resolution)
    monkeypatch.setattr("strata.orchestrator.background.verify_task_output", should_not_verify)
    monkeypatch.setattr("strata.orchestrator.background.generate_plan_review", should_not_review)

    asyncio.run(worker._run_task_cycle(task_id, lane="agent"))

    reloaded = storage_factory()
    try:
        parent = reloaded.tasks.get_by_id(task_id)
        assert parent is not None
        assert parent.state == TaskState.PUSHED
        assert list(parent.active_child_ids or [])
        queued = list(worker._lane_queue("agent")._queue)
        assert queued
    finally:
        reloaded.close()


def test_run_idle_tasks_seeds_preflight_before_onboarding(monkeypatch):
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
        assert queued_task.title == "Procedure: Preflight"
        assert queued_task.constraints["procedure_id"] == "preflight"
        assert not any(str(task.title).startswith("Alignment:") for task in all_tasks)
    finally:
        storage.close()


def test_run_idle_tasks_allows_alignment_after_onboarding_completes(monkeypatch):
    storage_factory = make_storage_factory()
    storage = storage_factory()
    smoke = queue_procedure(storage, None, procedure_id="preflight", lane="agent")
    smoke.state = TaskState.COMPLETE
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

    async def enqueue_fn(task_id, front=False):
        queued.append((task_id, front))

    asyncio.run(_run_decomposition(decomp_task, storage, DummyDecompModel(), enqueue_fn))

    created = [task for task in storage.session.query(TaskModel).all() if task.parent_task_id == decomp_task.task_id]
    process_children = [
        child for child in created if dict(child.constraints or {}).get("inline_process_kind") == "decomposition_phase"
    ]
    spawned_children = [child for child in created if child not in process_children]
    assert len(spawned_children) == 2
    assert all(task.type == TaskType.RESEARCH for task in spawned_children)
    assert all("procedure_checklist_item" in dict(task.constraints or {}) for task in spawned_children)
    runtime_posture_task = next(task for task in spawned_children if "runtime posture" in task.title.lower())
    runtime_hints = dict((runtime_posture_task.constraints or {}).get("source_hints") or {})
    assert "strata/api/main.py" in list(runtime_hints.get("preferred_paths") or [])
    assert runtime_posture_task.constraints.get("disallow_broad_repo_scan") is None
    assert len(queued) == 2
    assert decomp_task.state == TaskState.PUSHED
    assert sorted(decomp_task.active_child_ids) == sorted(task.task_id for task in spawned_children)
    assert all(front is True for _, front in queued)
    assert [child.title for child in process_children] == [
        "Decomposition Step: Frame task",
        "Decomposition Step: Emit leaf tasks",
        "Decomposition Step: Preserve workflow",
    ]


def test_run_decomposition_persists_dependency_edges():
    storage = make_storage()
    task = storage.tasks.create(
        title="Recovery Plan for Runtime Posture",
        description="Decompose runtime posture work.",
        session_id="agent:default",
        state=TaskState.PENDING,
        constraints={"lane": "agent"},
    )
    task.type = TaskType.DECOMP
    storage.commit()

    queued = []

    async def enqueue_fn(task_id, front=False):
        queued.append((task_id, front))

    asyncio.run(_run_decomposition(task, storage, DependentDecompModel(), enqueue_fn))

    created = [child for child in storage.session.query(TaskModel).all() if child.parent_task_id == task.task_id]
    process_children = [
        child for child in created if dict(child.constraints or {}).get("inline_process_kind") == "decomposition_phase"
    ]
    spawned_children = [child for child in created if child not in process_children]
    assert len(spawned_children) == 2
    by_title = {child.title: child for child in spawned_children}
    inspect_task = by_title["Inspect current settings"]
    persist_task = by_title["Persist runtime posture"]

    assert inspect_task.task_id in [task_id for task_id, _ in queued]
    assert persist_task.task_id in [task_id for task_id, _ in queued]
    assert inspect_task in persist_task.dependencies
    assert task.state == TaskState.PUSHED
    assert sorted(task.active_child_ids) == sorted(child.task_id for child in spawned_children)
    assert all(front is True for _, front in queued)

    phase_children = [
        child
        for child in created
        if dict(child.constraints or {}).get("inline_process_kind") == "decomposition_phase"
    ]
    assert [child.title for child in phase_children] == [
        "Decomposition Step: Frame task",
        "Decomposition Step: Emit leaf tasks",
        "Decomposition Step: Preserve workflow",
    ]
    assert all(child.type == TaskType.DECOMP for child in phase_children)
    emit_phase = next(child for child in phase_children if child.title == "Decomposition Step: Emit leaf tasks")
    emit_summary = dict((emit_phase.constraints or {}).get("decomposition_summary") or {})
    assert emit_summary.get("actionable_subtask_count") == 2
    preserve_phase = next(child for child in phase_children if child.title == "Decomposition Step: Preserve workflow")
    preserve_summary = dict((preserve_phase.constraints or {}).get("decomposition_summary") or {})
    assert preserve_summary.get("preservation_mode") == "draft_procedure"


def test_run_decomposition_falls_back_to_procedure_item_recovery_chain():
    storage = make_storage()
    focus = storage.tasks.create(
        title="Procedure Step: Confirm runtime posture",
        description="Advance the runtime posture item.",
        session_id="agent:default",
        state=TaskState.WORKING,
        constraints={
            "lane": "agent",
            "procedure_id": "operator_onboarding",
            "procedure_title": "Operator Onboarding",
            "procedure_parent_task_id": "root-procedure",
            "procedure_checklist_item": {
                "id": "runtime_posture",
                "title": "Confirm local/cloud and quiet-hardware preferences",
                "verification": "Comfort constraints are durably recorded or turned into clarification.",
            },
            "source_hints": {
                "preferred_paths": [
                    ".knowledge/specs/project_spec.md",
                    "strata/api/main.py",
                    "strata/models/providers.py",
                ],
                "guidance": "Inspect runtime settings and comfort-throttle codepaths before asking for clarification.",
            },
        },
    )
    decomp_task = storage.tasks.create(
        title="Recovery Plan for Procedure Step: Confirm local/cloud and quiet-hardware preferences",
        description="Create a bounded recovery plan.",
        session_id="agent:default",
        parent_task_id=focus.task_id,
        state=TaskState.PENDING,
        constraints={"lane": "agent", "recovery_focus_task_id": focus.task_id},
    )
    decomp_task.type = TaskType.DECOMP
    attempt = storage.attempts.create(task_id=focus.task_id)
    attempt.evidence = {
        "failure_kind": "task_boundary_violation",
        "autopsy": {
            "failure_kind": "task_boundary_violation",
            "tool_call": {"name": "list_directory", "arguments": '{"path":"."}'},
            "tool_result_preview": "README.md\nstrata/\n.knowledge/",
            "next_step_hint": "Read the hinted runtime posture files directly.",
        },
    }
    storage.attempts.update_outcome(attempt.attempt_id, AttemptOutcome.FAILED, reason="needs decomposition")
    storage.commit()

    queued = []

    async def enqueue_fn(task_id, front=False):
        queued.append((task_id, front))

    asyncio.run(_run_decomposition(decomp_task, storage, DummyDecompModel(), enqueue_fn))

    created = [child for child in storage.session.query(TaskModel).all() if child.parent_task_id == decomp_task.task_id]
    phase_children = [
        child for child in created if dict(child.constraints or {}).get("inline_process_kind") == "decomposition_phase"
    ]
    spawned_children = [child for child in created if child not in phase_children]
    assert len(spawned_children) == 5
    by_title = {child.title: child for child in spawned_children}
    inspect_task = by_title["Inspect .knowledge/specs/project_spec.md for Confirm local/cloud and quiet-hardware preferences"]
    second_inspect = by_title["Inspect strata/api/main.py for Confirm local/cloud and quiet-hardware preferences"]
    third_inspect = by_title["Inspect strata/models/providers.py for Confirm local/cloud and quiet-hardware preferences"]
    decide_task = by_title["Decide status for Confirm local/cloud and quiet-hardware preferences"]
    cash_out_task = by_title["Cash out Confirm local/cloud and quiet-hardware preferences"]

    assert all(child.task_id in [task_id for task_id, _ in queued] for child in spawned_children)
    assert inspect_task in decide_task.dependencies
    assert second_inspect in decide_task.dependencies
    assert third_inspect in decide_task.dependencies
    assert decide_task in cash_out_task.dependencies
    assert decide_task.constraints.get("disallow_broad_repo_scan") is True
    assert "strata/api/main.py" in list(decide_task.constraints.get("preferred_start_paths") or [])
    handoff = dict(decide_task.constraints.get("handoff_context") or {})
    assert handoff.get("tool_call", {}).get("name") == "list_directory"
    assert "runtime posture files directly" in str(handoff.get("next_step_hint") or "")
    assert handoff.get("avoid_repeating_first_tool", {}).get("name") == "list_directory"
    assert inspect_task.constraints.get("inspect_target_path") == ".knowledge/specs/project_spec.md"
    assert decomp_task.state == TaskState.PUSHED
    assert sorted(decomp_task.active_child_ids) == sorted(child.task_id for child in spawned_children)
    assert all(front is True for _, front in queued)

    phase_children = [
        child
        for child in created
        if dict(child.constraints or {}).get("inline_process_kind") == "decomposition_phase"
    ]
    assert [child.title for child in phase_children] == [
        "Decomposition Step: Frame task",
        "Decomposition Step: Emit leaf tasks",
        "Decomposition Step: Preserve workflow",
    ]
    preserve_phase = next(child for child in phase_children if child.title == "Decomposition Step: Preserve workflow")
    preserve_summary = dict((preserve_phase.constraints or {}).get("decomposition_summary") or {})
    assert preserve_summary.get("preservation_mode") == "procedure_item_recovery_chain"
    assert len(list(preserve_summary.get("spawned_recovery_subtasks") or [])) == 5


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
