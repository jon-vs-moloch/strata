from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from strata.sessions.metadata import get_session_metadata, set_session_metadata
from strata.storage.models import Base
from strata.storage.services.main import StorageManager


def make_storage():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    return StorageManager(session=session)


def test_session_metadata_normalizes_legacy_trainer_participant_name():
    storage = make_storage()
    metadata = set_session_metadata(
        storage,
        "trainer:session-1",
        {
            "participant_names": {
                "user": "You",
                "trainer": "Trainer-agent",
                "agent": "Agent",
                "system": "System",
            }
        },
    )
    storage.commit()

    assert metadata["participant_names"]["trainer"] == "Trainer"
    assert get_session_metadata(storage, "trainer:session-1")["participant_names"]["trainer"] == "Trainer"


def test_session_metadata_supplies_default_participant_names():
    storage = make_storage()

    metadata = get_session_metadata(storage, "agent:session-1")

    assert metadata["participant_names"] == {
        "user": "You",
        "trainer": "Trainer",
        "agent": "Agent",
        "system": "System",
    }
