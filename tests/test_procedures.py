from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from strata.procedures.registry import (
    ensure_draft_procedure_for_task,
    ensure_onboarding_task,
    ensure_startup_smoke_task,
    get_onboarding_status,
    get_procedure,
    get_startup_smoke_status,
    list_procedures,
    record_procedure_run,
    queue_procedure,
)
from strata.orchestrator.user_questions import get_question_for_source
from strata.storage.models import Base, TaskState, TaskType
from strata.storage.services.main import StorageManager


def make_storage():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    return StorageManager(session=session)


def test_default_onboarding_procedure_is_available():
    storage = make_storage()

    procedures = list_procedures(storage)
    onboarding = get_procedure(storage, "operator_onboarding")
    smoke = get_procedure(storage, "startup_sanity_check")

    assert any(item["procedure_id"] == "operator_onboarding" for item in procedures)
    assert any(item["procedure_id"] == "startup_sanity_check" for item in procedures)
    assert onboarding["title"] == "Operator Onboarding"
    assert onboarding["lifecycle_state"] == "vetted"
    assert onboarding["checklist"]
    assert smoke["checklist"]


def test_queue_procedure_creates_verifiable_task():
    storage = make_storage()

    task = queue_procedure(storage, None, procedure_id="operator_onboarding", lane="agent")

    assert task.type == TaskType.RESEARCH
    assert task.session_id == "agent:default"
    assert task.constraints["procedure_id"] == "operator_onboarding"
    assert task.constraints["verification_required"] is True
    assert task.success_criteria["required_checklist_ids"]
    question = get_question_for_source(
        storage,
        source_type="procedure_onboarding_intro",
        source_id="operator_onboarding",
    )
    assert question
    assert question["escalation_mode"] == "non_blocking"


def test_onboarding_status_and_ensure_task_are_idempotent():
    storage = make_storage()

    initial = get_onboarding_status(storage)
    assert initial["needs_queue"] is True
    assert initial["has_completed"] is False

    smoke = ensure_startup_smoke_task(storage, None)
    assert smoke is not None
    smoke.state = TaskState.COMPLETE
    storage.commit()

    task = ensure_onboarding_task(storage, None)
    assert task is not None

    active = get_onboarding_status(storage)
    assert active["has_active"] is True
    assert active["needs_queue"] is False
    assert ensure_onboarding_task(storage, None) is None

    task.state = TaskState.COMPLETE
    storage.commit()

    completed = get_onboarding_status(storage)
    assert completed["has_completed"] is True
    assert completed["needs_queue"] is False


def test_startup_smoke_status_and_ensure_task_are_idempotent():
    storage = make_storage()

    initial = get_startup_smoke_status(storage)
    assert initial["needs_queue"] is True
    assert initial["has_completed"] is False

    task = ensure_startup_smoke_task(storage, None)
    assert task is not None
    assert task.constraints["procedure_id"] == "startup_sanity_check"

    active = get_startup_smoke_status(storage)
    assert active["has_active"] is True
    assert active["needs_queue"] is False
    assert ensure_startup_smoke_task(storage, None) is None

    task.state = TaskState.COMPLETE
    storage.commit()

    completed = get_startup_smoke_status(storage)
    assert completed["has_completed"] is True
    assert completed["needs_queue"] is False


def test_novel_task_can_seed_draft_procedure():
    storage = make_storage()
    task = storage.tasks.create(
        title="Figure out the launch checklist",
        description="Discover and retain a reusable launch workflow.",
        state=TaskState.PENDING,
        constraints={"lane": "agent"},
        success_criteria={"deliverables": ["launch checklist"]},
    )
    storage.commit()

    procedure = ensure_draft_procedure_for_task(
        storage,
        task,
        checklist=[
            {"id": "inspect", "title": "Inspect launch surfaces", "description": "Look at the launch entrypoints."},
            {"id": "verify", "title": "Verify launch health", "description": "Confirm the system comes up."},
        ],
    )

    assert procedure["lifecycle_state"] == "draft"
    assert procedure["procedure_id"].startswith("draft_")
    assert len(procedure["checklist"]) == 2
    reloaded = storage.tasks.get_by_id(task.task_id)
    assert reloaded.constraints["procedure_id"] == procedure["procedure_id"]
    assert reloaded.constraints["procedure_lifecycle_state"] == "draft"


def test_draft_procedure_promotes_to_tested_after_success():
    storage = make_storage()
    task = storage.tasks.create(
        title="Learn runtime smoke flow",
        description="Capture a reusable smoke workflow.",
        state=TaskState.PENDING,
        constraints={"lane": "agent"},
        success_criteria={},
    )
    storage.commit()

    procedure = ensure_draft_procedure_for_task(storage, task)
    updated = record_procedure_run(storage, procedure["procedure_id"], outcome="succeeded", source_task_id=task.task_id)
    assert updated["lifecycle_state"] == "tested"
    assert updated["stats"]["run_count"] == 1
    assert updated["stats"]["success_count"] == 1
    assert updated["stats"]["tested_at"]
