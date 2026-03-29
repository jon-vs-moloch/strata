from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import asyncio

from strata.orchestrator.background import BackgroundWorker, resolution_from_plan_review
from strata.storage.models import Base, TaskState
from strata.storage.services.main import StorageManager


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
    worker._current_process = process
    worker._current_task_lane = "trainer"

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
    while not worker._queue.empty():
        queued.append(worker._queue.get_nowait())

    assert trainer_task_id in queued
    assert agent_task_id not in queued


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
