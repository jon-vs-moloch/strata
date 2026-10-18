"""
@module observability.writer
@purpose Provide a serialized buffered write lane for hot observability events.

This keeps chatty telemetry off the main request/task transaction path and
reduces SQLite write contention by batching append-only observability writes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock
from time import monotonic
from typing import Any, Dict, List

from strata.storage.models import (
    AttemptObservabilityArtifactModel,
    ContextLoadEventModel,
    ProviderTelemetrySnapshotModel,
)


@dataclass
class _ObservabilityBuffer:
    context_events: List[Dict[str, Any]] = field(default_factory=list)
    provider_snapshot: Dict[str, Dict[str, object]] | None = None
    attempt_artifacts: List[Dict[str, Any]] = field(default_factory=list)
    dirty_since: float | None = None


class ObservabilityWriter:
    """
    @summary Serialize and batch append-only observability writes.
    """

    def __init__(self, *, flush_interval_s: float = 2.0, max_context_events: int = 25):
        self._flush_interval_s = max(0.0, float(flush_interval_s))
        self._max_context_events = max(1, int(max_context_events))
        self._lock = Lock()
        self._buffer = _ObservabilityBuffer()

    def enqueue_context_event(self, event: Dict[str, Any]) -> bool:
        should_flush = False
        with self._lock:
            self._buffer.context_events.append(dict(event or {}))
            if self._buffer.dirty_since is None:
                self._buffer.dirty_since = monotonic()
            should_flush = self._should_flush_locked()
        return should_flush

    def enqueue_provider_snapshot(self, snapshot: Dict[str, Dict[str, object]]) -> bool:
        should_flush = False
        with self._lock:
            self._buffer.provider_snapshot = dict(snapshot or {})
            if self._buffer.dirty_since is None:
                self._buffer.dirty_since = monotonic()
            should_flush = self._should_flush_locked()
        return should_flush

    def enqueue_attempt_artifact(self, artifact: Dict[str, Any]) -> bool:
        should_flush = False
        with self._lock:
            self._buffer.attempt_artifacts.append(dict(artifact or {}))
            if self._buffer.dirty_since is None:
                self._buffer.dirty_since = monotonic()
            should_flush = self._should_flush_locked()
        return should_flush

    def _should_flush_locked(self) -> bool:
        if len(self._buffer.context_events) >= self._max_context_events:
            return True
        if len(self._buffer.attempt_artifacts) >= self._max_context_events:
            return True
        if self._buffer.provider_snapshot:
            dirty_since = self._buffer.dirty_since
            if dirty_since is not None and (monotonic() - dirty_since) >= self._flush_interval_s:
                return True
        if self._buffer.context_events:
            dirty_since = self._buffer.dirty_since
            if dirty_since is not None and (monotonic() - dirty_since) >= self._flush_interval_s:
                return True
        if self._buffer.attempt_artifacts:
            dirty_since = self._buffer.dirty_since
            if dirty_since is not None and (monotonic() - dirty_since) >= self._flush_interval_s:
                return True
        return False

    def flush(self, storage_factory=None) -> bool:
        with self._lock:
            if not self._buffer.context_events and not self._buffer.provider_snapshot and not self._buffer.attempt_artifacts:
                return False
            context_events = list(self._buffer.context_events)
            provider_snapshot = dict(self._buffer.provider_snapshot or {})
            attempt_artifacts = list(self._buffer.attempt_artifacts)
            self._buffer = _ObservabilityBuffer()

        owns_storage = storage_factory is not None
        if not owns_storage:
            from strata.storage.services.main import StorageManager

            storage = StorageManager()
        else:
            storage = storage_factory()
        try:
            bind = None
            try:
                bind = storage.session.get_bind()
            except Exception:
                bind = getattr(storage, "engine", None)
            if bind is not None:
                ContextLoadEventModel.__table__.create(bind=bind, checkfirst=True)
                ProviderTelemetrySnapshotModel.__table__.create(bind=bind, checkfirst=True)
                AttemptObservabilityArtifactModel.__table__.create(bind=bind, checkfirst=True)

            for event in context_events:
                loaded_at_raw = event.get("loaded_at")
                loaded_at = None
                if isinstance(loaded_at_raw, str) and loaded_at_raw:
                    try:
                        loaded_at = datetime.fromisoformat(loaded_at_raw.replace("Z", "+00:00"))
                    except Exception:
                        loaded_at = None
                storage.session.add(
                    ContextLoadEventModel(
                        artifact_type=str(event.get("artifact_type") or ""),
                        identifier=str(event.get("identifier") or ""),
                        source=str(event.get("source") or ""),
                        estimated_tokens=int(event.get("estimated_tokens", 0) or 0),
                        event_metadata=dict(event.get("metadata") or {}),
                        loaded_at=loaded_at or datetime.utcnow(),
                    )
                )

            if provider_snapshot:
                storage.session.add(ProviderTelemetrySnapshotModel(snapshot=provider_snapshot))

            for artifact in attempt_artifacts:
                storage.session.add(
                    AttemptObservabilityArtifactModel(
                        task_id=str(artifact.get("task_id") or ""),
                        attempt_id=str(artifact.get("attempt_id") or ""),
                        session_id=str(artifact.get("session_id") or "") or None,
                        artifact_kind=str(artifact.get("artifact_kind") or ""),
                        payload=dict(artifact.get("payload") or {}),
                    )
                )

            if hasattr(storage, "commit"):
                storage.commit()
            return True
        except Exception:
            if hasattr(storage, "rollback"):
                storage.rollback()
            with self._lock:
                self._buffer.context_events = context_events + self._buffer.context_events
                self._buffer.attempt_artifacts = attempt_artifacts + self._buffer.attempt_artifacts
                if provider_snapshot and not self._buffer.provider_snapshot:
                    self._buffer.provider_snapshot = provider_snapshot
                if self._buffer.dirty_since is None:
                    self._buffer.dirty_since = monotonic()
            return False
        finally:
            if not owns_storage and hasattr(storage, "close"):
                storage.close()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "context_events": list(self._buffer.context_events),
                "provider_snapshot": dict(self._buffer.provider_snapshot or {}),
                "attempt_artifacts": list(self._buffer.attempt_artifacts),
                "dirty_since": self._buffer.dirty_since,
            }


_writer = ObservabilityWriter()


def enqueue_context_observability_event(event: Dict[str, Any]) -> bool:
    return _writer.enqueue_context_event(event)


def enqueue_provider_observability_snapshot(snapshot: Dict[str, Dict[str, object]]) -> bool:
    return _writer.enqueue_provider_snapshot(snapshot)


def enqueue_attempt_observability_artifact(artifact: Dict[str, Any]) -> bool:
    return _writer.enqueue_attempt_artifact(artifact)


def flush_observability_writes(storage_factory=None) -> bool:
    return _writer.flush(storage_factory=storage_factory)


def get_pending_observability_snapshot() -> Dict[str, Any]:
    return _writer.snapshot()
