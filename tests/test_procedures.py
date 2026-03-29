from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from strata.procedures.registry import get_procedure, list_procedures, queue_procedure
from strata.storage.models import Base, TaskType
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

    assert any(item["procedure_id"] == "operator_onboarding" for item in procedures)
    assert onboarding["title"] == "Operator Onboarding"
    assert onboarding["checklist"]


def test_queue_procedure_creates_verifiable_task():
    storage = make_storage()

    task = queue_procedure(storage, None, procedure_id="operator_onboarding", lane="agent")

    assert task.type == TaskType.RESEARCH
    assert task.session_id == "agent:default"
    assert task.constraints["procedure_id"] == "operator_onboarding"
    assert task.constraints["verification_required"] is True
    assert task.success_criteria["required_checklist_ids"]
