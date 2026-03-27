from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from strata.observability.context import (
    CONTEXT_FILE_SCAN_KEY,
    CONTEXT_LOAD_POLICY_KEY,
    estimate_text_tokens,
    get_context_load_telemetry,
    record_context_load,
    scan_codebase_context_pressure,
)
from strata.storage.models import Base
from strata.storage.services.main import StorageManager


def make_storage():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    return StorageManager(session=session)


def test_record_context_load_tracks_artifact_usage():
    storage = make_storage()

    event = record_context_load(
        artifact_type="knowledge_page",
        identifier="seahorses",
        content="Seahorses are fish.",
        source="test",
        metadata={"audience": "agent"},
        storage=storage,
    )

    telemetry = get_context_load_telemetry(storage)
    stats = telemetry["stats"]["artifacts"]["knowledge_page:seahorses"]

    assert event["artifact_type"] == "knowledge_page"
    assert stats["load_count"] == 1
    assert stats["last_source"] == "test"
    assert stats["avg_estimated_tokens"] >= 4
    assert stats["token_share_pct"] == 100.0
    assert stats["recent_token_share_pct"] == 100.0
    assert "estimated_token_stddev" in stats


def test_context_pressure_scan_flags_large_files(tmp_path):
    storage = make_storage()
    storage.parameters.set_parameter(
        CONTEXT_LOAD_POLICY_KEY,
        {
            "warning_estimated_tokens": 50,
            "recent_event_limit": 50,
            "recent_warning_limit": 50,
            "file_warning_estimated_tokens": 50,
            "file_scan_result_limit": 20,
        },
        description="test policy",
    )
    storage.commit()

    small = tmp_path / "small.py"
    large = tmp_path / "large.md"
    ignored = tmp_path / "runtime" / "ignored.py"
    ignored.parent.mkdir(parents=True, exist_ok=True)

    small.write_text("print('hi')\n", encoding="utf-8")
    large.write_text("# Large\n\n" + ("token " * 120), encoding="utf-8")
    ignored.write_text("x = 1\n", encoding="utf-8")

    result = scan_codebase_context_pressure(storage, base_dir=str(tmp_path))
    persisted = storage.parameters.peek_parameter(CONTEXT_FILE_SCAN_KEY, default_value={})

    assert result["scanned_file_count"] == 2
    assert persisted["warnings"][0]["path"] == "large.md"
    assert all(entry["path"] != str(Path("runtime") / "ignored.py") for entry in persisted["largest_files"])


def test_estimate_text_tokens_counts_words_and_punctuation():
    assert estimate_text_tokens("Hello, world!") == 4
