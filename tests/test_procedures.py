from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from strata.procedures.registry import (
    ensure_onboarding_task,
    ensure_startup_smoke_task,
    get_onboarding_status,
    get_procedure,
    get_startup_smoke_status,
    list_procedures,
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
