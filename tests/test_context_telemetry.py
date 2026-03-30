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
from strata.observability.writer import flush_observability_writes, get_pending_observability_snapshot
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


def test_context_load_telemetry_limits_recent_and_warning_views():
    storage = make_storage()
    storage.parameters.set_parameter(
        CONTEXT_LOAD_POLICY_KEY,
        {
            "warning_estimated_tokens": 5,
            "recent_event_limit": 2,
            "recent_warning_limit": 1,
            "file_warning_estimated_tokens": 50,
            "file_scan_result_limit": 20,
        },
        description="test policy",
    )
    storage.commit()

    record_context_load(
        artifact_type="knowledge_page",
        identifier="alpha",
        content="one two three four five six",
        source="test-a",
        storage=storage,
    )
    record_context_load(
        artifact_type="knowledge_page",
        identifier="alpha",
        content="one two",
        source="test-b",
        storage=storage,
    )
    record_context_load(
        artifact_type="knowledge_page",
        identifier="beta",
        content="one two three four five six seven",
        source="test-c",
        storage=storage,
    )

    telemetry = get_context_load_telemetry(storage)

    assert len(telemetry["recent"]) == 2
    assert telemetry["recent"][0]["identifier"] == "beta"
    assert len(telemetry["warnings"]) == 1
    assert telemetry["warnings"][0]["identifier"] == "beta"
    assert telemetry["stats"]["artifacts"]["knowledge_page:alpha"]["load_count"] == 2
    assert telemetry["stats"]["artifacts"]["knowledge_page:alpha"]["last_source"] == "test-b"


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


def test_context_load_can_buffer_before_flush(tmp_path):
    db_path = tmp_path / "context-buffer.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    storage = StorageManager(session=session_factory())
    before_storage = None
    after_storage = None

    try:
        event = record_context_load(
            artifact_type="knowledge_page",
            identifier="buffered",
            content="buffered event content",
            source="test-buffered",
            storage=storage,
        )
        pending = get_pending_observability_snapshot()
        before_storage = StorageManager(session=session_factory())
        before = get_context_load_telemetry(before_storage)
        flushed = flush_observability_writes(lambda: StorageManager(session=session_factory()))
        after_storage = StorageManager(session=session_factory())
        after = get_context_load_telemetry(after_storage)

        assert event["identifier"] == "buffered"
        assert pending["context_events"]
        assert "knowledge_page:buffered" not in before["stats"]["artifacts"]
        assert flushed is True
        assert after["stats"]["artifacts"]["knowledge_page:buffered"]["load_count"] == 1
    finally:
        if before_storage is not None:
            before_storage.close()
        if after_storage is not None:
            after_storage.close()
        storage.close()
