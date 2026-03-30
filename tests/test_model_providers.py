from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from strata.models.providers import LocalProvider
from strata.models.providers import (
    GenericOpenAICompatibleProvider,
    get_latest_persisted_provider_telemetry,
    persist_provider_telemetry_snapshot,
)
from strata.observability.writer import flush_observability_writes
from strata.storage.models import Base
from strata.storage.services.main import StorageManager


def make_storage():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    return StorageManager(session=session)


def test_provider_normalizes_nested_usage_payloads():
    provider = LocalProvider(
        model_id="demo-model",
        provider_id="demo",
        endpoint_url="http://127.0.0.1:1234/v1/chat/completions",
    )

    normalized = provider._normalize_usage(
        {
            "prompt_tokens": 10,
            "completion_tokens_details": {"reasoning_tokens": 167, "audio_tokens": None},
            "unsupported": object(),
        }
    )

    assert normalized["prompt_tokens"] == 10
    assert normalized["completion_tokens_details"] == {"reasoning_tokens": 167, "audio_tokens": None}
    assert "unsupported" not in normalized


def test_local_provider_adapts_cooldown_to_observed_latency_and_errors():
    provider = LocalProvider(
        model_id="demo-model",
        provider_id="lmstudio",
        endpoint_url="http://127.0.0.1:1234/v1/chat/completions",
        min_interval_ms=500,
    )
    telemetry = provider._get_telemetry_state()
    telemetry.request_count = 6
    telemetry.success_count = 4
    telemetry.error_count = 2
    telemetry.total_latency_s = 32.0

    effective = provider._effective_min_interval_ms(telemetry)

    assert effective > 500


def test_provider_telemetry_snapshot_persists_to_typed_table():
    storage = make_storage()
    GenericOpenAICompatibleProvider._telemetry_states = {}
    GenericOpenAICompatibleProvider._telemetry_dirty = False
    GenericOpenAICompatibleProvider._telemetry_revision = 0
    GenericOpenAICompatibleProvider._last_persisted_revision = 0
    GenericOpenAICompatibleProvider._last_persisted_at = 0.0

    provider = LocalProvider(
        model_id="demo-model",
        provider_id="lmstudio",
        endpoint_url="http://127.0.0.1:1234/v1/chat/completions",
    )
    telemetry = provider._get_telemetry_state()
    telemetry.request_count = 3
    telemetry.success_count = 2
    telemetry.error_count = 1
    provider._mark_telemetry_dirty()

    persisted = persist_provider_telemetry_snapshot(storage, force=True, commit=True)
    snapshot = get_latest_persisted_provider_telemetry(storage)

    assert persisted is True
    assert snapshot
    only_entry = next(iter(snapshot.values()))
    assert only_entry["request_count"] == 3
    assert only_entry["success_count"] == 2


def test_provider_telemetry_snapshot_can_buffer_before_flush(tmp_path):
    db_path = tmp_path / "provider-buffer.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    storage = StorageManager(session=session_factory())
    GenericOpenAICompatibleProvider._telemetry_states = {}
    GenericOpenAICompatibleProvider._telemetry_dirty = False
    GenericOpenAICompatibleProvider._telemetry_revision = 0
    GenericOpenAICompatibleProvider._last_persisted_revision = 0
    GenericOpenAICompatibleProvider._last_persisted_at = 0.0

    provider = LocalProvider(
        model_id="demo-model",
        provider_id="lmstudio",
        endpoint_url="http://127.0.0.1:1234/v1/chat/completions",
    )
    telemetry = provider._get_telemetry_state()
    telemetry.request_count = 4
    telemetry.success_count = 3
    GenericOpenAICompatibleProvider._telemetry_states[provider._throttle_key()] = telemetry
    provider._mark_telemetry_dirty()
    after_storage = None

    try:
        persisted = persist_provider_telemetry_snapshot()
        before = get_latest_persisted_provider_telemetry(storage)
        flushed = flush_observability_writes(lambda: StorageManager(session=session_factory()))
        after_storage = StorageManager(session=session_factory())
        after = get_latest_persisted_provider_telemetry(after_storage)

        assert persisted is False
        assert before == {}
        assert flushed is True
        only_entry = next(iter(after.values()))
        assert only_entry["request_count"] == 4
    finally:
        if after_storage is not None:
            after_storage.close()
        storage.close()
