from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from strata.experimental.trace_review import build_task_trace_summary
from strata.observability.writer import flush_observability_writes
from strata.orchestrator.worker import attempt_runner
from strata.storage.models import AttemptOutcome, Base, TaskModel, TaskState, TaskType
from strata.storage.services.main import StorageManager


class DummyModel:
    last_response = None


def make_storage():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    return StorageManager(session=session)


async def _noop_notify(*_args, **_kwargs):
    return None


async def _noop_enqueue(*_args, **_kwargs):
    return None


def test_run_attempt_upgrades_procedure_item_hints_and_notifies_working(monkeypatch):
    storage = make_storage()
    task = storage.tasks.create(
        title="Procedure Step: Establish the starting verification posture",
        description="Advance exactly one checklist item for onboarding.",
        session_id="agent:default",
        state=TaskState.PENDING,
        type=TaskType.RESEARCH,
        constraints={
            "lane": "agent",
            "procedure_checklist_item": {
                "id": "verification_posture",
                "title": "Establish the starting verification posture",
            },
        },
    )
    storage.commit()

    notifications = []

    async def capture_notify(task_id, state):
        notifications.append((task_id, state))

    async def succeed(task_obj, *_args, **_kwargs):
        hints = dict((task_obj.constraints or {}).get("source_hints") or {})
        assert hints
        assert "strata/experimental/verifier.py" in list(hints.get("preferred_paths") or [])
        assert task_obj.state == TaskState.WORKING

    monkeypatch.setattr(attempt_runner, "_run_research", succeed)

    success, error, attempt = __import__("asyncio").run(
        attempt_runner.run_attempt(task, storage, DummyModel(), capture_notify, _noop_enqueue)
    )

    assert success is True
    assert error is None
    assert attempt.outcome == AttemptOutcome.SUCCEEDED
    updated_task = storage.tasks.get_by_id(task.task_id)
    hints = dict((updated_task.constraints or {}).get("source_hints") or {})
    assert hints
    assert (task.task_id, TaskState.WORKING.value) in notifications


def test_failed_attempt_is_closed_when_task_body_raises(monkeypatch):
    storage = make_storage()
    task = storage.tasks.create(
        title="Research task",
        description="Find the answer.",
        session_id="agent:default",
        state=TaskState.PENDING,
        type=TaskType.RESEARCH,
        constraints={"lane": "agent"},
    )
    storage.commit()

    async def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(attempt_runner, "_run_research", boom)

    success, error, attempt = __import__("asyncio").run(
        attempt_runner.run_attempt(task, storage, DummyModel(), _noop_notify, _noop_enqueue)
    )

    assert success is False
    assert str(error) == "boom"
    assert attempt.outcome == AttemptOutcome.FAILED
    assert attempt.ended_at is not None


def test_failed_attempt_persists_sidecar_autopsy_after_flush(monkeypatch):
    storage = make_storage()
    task = storage.tasks.create(
        title="Research task",
        description="Find the answer.",
        session_id="agent:default",
        state=TaskState.PENDING,
        type=TaskType.RESEARCH,
        constraints={"lane": "agent"},
    )
    storage.commit()

    class AutopsyError(RuntimeError):
        failure_kind = "iteration_budget_exhausted"
        autopsy = {"failure_kind": "iteration_budget_exhausted", "warm_history": [{"role": "user", "content": "hi"}]}

    async def boom(*_args, **_kwargs):
        raise AutopsyError("budget exhausted")

    monkeypatch.setattr(attempt_runner, "_run_research", boom)

    success, error, attempt = __import__("asyncio").run(
        attempt_runner.run_attempt(task, storage, DummyModel(), _noop_notify, _noop_enqueue)
    )
    flushed = flush_observability_writes(lambda: storage)
    summary = build_task_trace_summary(storage, task_id=task.task_id)

    assert success is False
    assert str(error) == "budget exhausted"
    assert attempt.evidence["failure_kind"] == "iteration_budget_exhausted"
    assert flushed is True
    assert summary["observability_artifacts"][0]["artifact_kind"] == "failure_autopsy"
    assert summary["observability_artifacts"][0]["payload"]["evidence"]["autopsy"]["failure_kind"] == "iteration_budget_exhausted"


def test_root_procedure_task_expands_into_children_without_research_turn(monkeypatch):
    storage = make_storage()
    task = storage.tasks.create(
        title="Procedure: Startup Sanity Check",
        description="Run the startup sanity checklist.",
        session_id="agent:default",
        state=TaskState.PENDING,
        type=TaskType.RESEARCH,
        constraints={
            "lane": "agent",
            "procedure_id": "startup_sanity_check",
            "procedure_title": "Startup Sanity Check",
            "procedure_checklist": [
                {"id": "spec_presence", "title": "Confirm the core spec files are present", "verification": "Spec files exist."},
                {"id": "runtime_wiring", "title": "Confirm the split runtime wiring is present", "verification": "Runtime split exists."},
            ],
        },
    )
    storage.commit()

    enqueued = []
    notifications = []

    async def capture_enqueue(task_id, front=False):
        enqueued.append((task_id, front))

    async def capture_notify(task_id, state):
        notifications.append((task_id, state))

    async def should_not_run(*_args, **_kwargs):
        raise AssertionError("root Procedure expansion should happen before any research turn")

    monkeypatch.setattr(attempt_runner, "_run_research", should_not_run)

    success, error, attempt = __import__("asyncio").run(
        attempt_runner.run_attempt(task, storage, DummyModel(), capture_notify, capture_enqueue)
    )

    assert success is True
    assert error is None
    assert attempt.outcome == AttemptOutcome.SUCCEEDED
    updated_task = storage.tasks.get_by_id(task.task_id)
    assert updated_task.state == TaskState.PUSHED
    assert len(list(updated_task.active_child_ids or [])) == 2
    children = storage.session.query(TaskModel).filter(TaskModel.parent_task_id == task.task_id).all()
    assert len(children) == 2
    assert all(child.parent_task_id == task.task_id for child in children)
    assert all(front is True for _, front in enqueued)
    assert (task.task_id, TaskState.WORKING.value) in notifications
    assert (task.task_id, TaskState.PUSHED.value) in notifications
