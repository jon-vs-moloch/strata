"""
@module observability.context
@purpose Track what artifacts get loaded into model context and how expensive they are.

If Strata wants small models to succeed, it needs to know which files, pages,
specs, and summaries are actually being loaded and whether they are pushing the
context budget too hard. This module records that usage and emits warnings when
an artifact crosses a configurable estimated-token threshold.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from datetime import datetime, timezone
import math
from typing import Any, Dict, Iterable, Optional

from sqlalchemy import func
from sqlalchemy.exc import OperationalError

from strata.storage.services.main import StorageManager
from strata.storage.models import ContextLoadEventModel
from strata.observability.writer import enqueue_context_observability_event, flush_observability_writes


logger = logging.getLogger(__name__)

CONTEXT_LOAD_STATS_KEY = "context_load_stats"
CONTEXT_LOAD_RECENT_KEY = "context_load_recent"
CONTEXT_LOAD_WARNINGS_KEY = "context_load_warnings"
CONTEXT_LOAD_POLICY_KEY = "context_load_policy"
CONTEXT_FILE_SCAN_KEY = "context_file_scan"

DEFAULT_CONTEXT_LOAD_POLICY: Dict[str, Any] = {
    "warning_estimated_tokens": 1800,
    "recent_event_limit": 200,
    "recent_warning_limit": 100,
    "file_warning_estimated_tokens": 2200,
    "file_scan_result_limit": 200,
}

DEFAULT_FILE_SCAN_EXTENSIONS = {
    ".py", ".md", ".json", ".toml", ".yaml", ".yml", ".jsx", ".js", ".ts", ".tsx"
}
DEFAULT_FILE_SCAN_EXCLUDED_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", "venv", ".venv", "venv_new", "memory", "runtime", "attic", "dist",
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    # Lightweight tokenizer approximation that counts words and punctuation as separate units.
    return len(re.findall(r"\w+|[^\w\s]", text, flags=re.UNICODE))


def get_context_load_policy(storage) -> Dict[str, Any]:
    raw = storage.parameters.peek_parameter(
        CONTEXT_LOAD_POLICY_KEY,
        default_value=DEFAULT_CONTEXT_LOAD_POLICY,
    ) or DEFAULT_CONTEXT_LOAD_POLICY
    policy = dict(DEFAULT_CONTEXT_LOAD_POLICY)
    if isinstance(raw, dict):
        policy.update(raw)
    try:
        policy["warning_estimated_tokens"] = int(policy.get("warning_estimated_tokens", DEFAULT_CONTEXT_LOAD_POLICY["warning_estimated_tokens"]))
    except Exception:
        policy["warning_estimated_tokens"] = DEFAULT_CONTEXT_LOAD_POLICY["warning_estimated_tokens"]
    try:
        policy["recent_event_limit"] = int(policy.get("recent_event_limit", DEFAULT_CONTEXT_LOAD_POLICY["recent_event_limit"]))
    except Exception:
        policy["recent_event_limit"] = DEFAULT_CONTEXT_LOAD_POLICY["recent_event_limit"]
    try:
        policy["recent_warning_limit"] = int(policy.get("recent_warning_limit", DEFAULT_CONTEXT_LOAD_POLICY["recent_warning_limit"]))
    except Exception:
        policy["recent_warning_limit"] = DEFAULT_CONTEXT_LOAD_POLICY["recent_warning_limit"]
    try:
        policy["file_warning_estimated_tokens"] = int(
            policy.get("file_warning_estimated_tokens", DEFAULT_CONTEXT_LOAD_POLICY["file_warning_estimated_tokens"])
        )
    except Exception:
        policy["file_warning_estimated_tokens"] = DEFAULT_CONTEXT_LOAD_POLICY["file_warning_estimated_tokens"]
    try:
        policy["file_scan_result_limit"] = int(
            policy.get("file_scan_result_limit", DEFAULT_CONTEXT_LOAD_POLICY["file_scan_result_limit"])
        )
    except Exception:
        policy["file_scan_result_limit"] = DEFAULT_CONTEXT_LOAD_POLICY["file_scan_result_limit"]
    return policy


def _event_to_payload(event: ContextLoadEventModel) -> Dict[str, Any]:
    return {
        "artifact_type": str(event.artifact_type or ""),
        "identifier": str(event.identifier or ""),
        "source": str(event.source or ""),
        "estimated_tokens": int(event.estimated_tokens or 0),
        "loaded_at": _utcnow() if not event.loaded_at else event.loaded_at.replace(tzinfo=timezone.utc).isoformat(),
        "metadata": dict(event.event_metadata or {}),
    }


def _storage_bind(storage):
    try:
        bind = storage.session.get_bind()
        if bind is not None:
            return bind
    except Exception:
        pass
    return getattr(storage, "engine", None)


def _legacy_context_load_telemetry(storage) -> Dict[str, Any]:
    stats = storage.parameters.peek_parameter(CONTEXT_LOAD_STATS_KEY, default_value={"artifacts": {}}) or {"artifacts": {}}
    recent = storage.parameters.peek_parameter(CONTEXT_LOAD_RECENT_KEY, default_value=[]) or []
    warnings = storage.parameters.peek_parameter(CONTEXT_LOAD_WARNINGS_KEY, default_value=[]) or []
    file_scan = storage.parameters.peek_parameter(CONTEXT_FILE_SCAN_KEY, default_value={}) or {}
    artifacts = dict(stats.get("artifacts") or {}) if isinstance(stats, dict) else {}
    total_loads = sum(int(item.get("load_count", 0) or 0) for item in artifacts.values())
    total_tokens = sum(int(item.get("total_estimated_tokens", 0) or 0) for item in artifacts.values())

    recent_events = recent if isinstance(recent, list) else []
    recent_total_loads = len(recent_events)
    recent_total_tokens = sum(int(item.get("estimated_tokens", 0) or 0) for item in recent_events)
    recent_counts: Dict[str, int] = {}
    recent_tokens: Dict[str, int] = {}
    for event in recent_events:
        artifact_key = f"{event.get('artifact_type')}:{event.get('identifier')}"
        recent_counts[artifact_key] = recent_counts.get(artifact_key, 0) + 1
        recent_tokens[artifact_key] = recent_tokens.get(artifact_key, 0) + int(event.get("estimated_tokens", 0) or 0)

    enriched_artifacts: Dict[str, Any] = {}
    for key, item in artifacts.items():
        enriched = dict(item or {})
        load_count = int(enriched.get("load_count", 0) or 0)
        total_estimated_tokens = int(enriched.get("total_estimated_tokens", 0) or 0)
        total_estimated_tokens_sq = float(enriched.get("total_estimated_tokens_sq", 0.0) or 0.0)
        avg_estimated_tokens = float(enriched.get("avg_estimated_tokens", 0.0) or 0.0)
        variance = 0.0
        if load_count > 0:
            variance = max(0.0, (total_estimated_tokens_sq / load_count) - (avg_estimated_tokens ** 2))
        stddev = math.sqrt(variance)
        max_estimated_tokens = int(enriched.get("max_estimated_tokens", 0) or 0)
        peak_sigma = 0.0
        if stddev > 0:
            peak_sigma = round((max_estimated_tokens - avg_estimated_tokens) / stddev, 2)

        recent_count = recent_counts.get(key, 0)
        recent_token_total = recent_tokens.get(key, 0)
        enriched["load_share_pct"] = round((load_count / total_loads) * 100, 2) if total_loads else 0.0
        enriched["token_share_pct"] = round((total_estimated_tokens / total_tokens) * 100, 2) if total_tokens else 0.0
        enriched["recent_load_share_pct"] = round((recent_count / recent_total_loads) * 100, 2) if recent_total_loads else 0.0
        enriched["recent_token_share_pct"] = round((recent_token_total / recent_total_tokens) * 100, 2) if recent_total_tokens else 0.0
        enriched["estimated_token_stddev"] = round(stddev, 2)
        enriched["peak_sigma"] = peak_sigma
        enriched["recent_load_count"] = recent_count
        enriched["recent_total_estimated_tokens"] = recent_token_total
        enriched_artifacts[key] = enriched

    totals = {
        "all_time_load_count": total_loads,
        "all_time_estimated_tokens": total_tokens,
        "recent_load_count": recent_total_loads,
        "recent_estimated_tokens": recent_total_tokens,
    }
    return {
        "policy": get_context_load_policy(storage),
        "stats": {"artifacts": enriched_artifacts, "totals": totals},
        "recent": recent_events,
        "warnings": warnings if isinstance(warnings, list) else [],
        "file_scan": file_scan if isinstance(file_scan, dict) else {},
    }


def get_context_load_telemetry(storage) -> Dict[str, Any]:
    policy = get_context_load_policy(storage)
    file_scan = storage.parameters.peek_parameter(CONTEXT_FILE_SCAN_KEY, default_value={}) or {}
    recent_limit = max(1, int(policy["recent_event_limit"]))
    warning_limit = max(1, int(policy["recent_warning_limit"]))
    warning_threshold = max(1, int(policy["warning_estimated_tokens"]))
    try:
        bind = _storage_bind(storage)
        if bind is not None:
            ContextLoadEventModel.__table__.create(bind=bind, checkfirst=True)
        aggregate_rows = (
            storage.session.query(ContextLoadEventModel)
            .with_entities(
                ContextLoadEventModel.artifact_type.label("artifact_type"),
                ContextLoadEventModel.identifier.label("identifier"),
                func.count(ContextLoadEventModel.id).label("load_count"),
                func.sum(ContextLoadEventModel.estimated_tokens).label("total_estimated_tokens"),
                func.sum(ContextLoadEventModel.estimated_tokens * ContextLoadEventModel.estimated_tokens).label("total_estimated_tokens_sq"),
                func.max(ContextLoadEventModel.estimated_tokens).label("max_estimated_tokens"),
            )
            .group_by(ContextLoadEventModel.artifact_type, ContextLoadEventModel.identifier)
            .all()
        )
        recent_rows = (
            storage.session.query(ContextLoadEventModel)
            .order_by(ContextLoadEventModel.loaded_at.desc(), ContextLoadEventModel.id.desc())
            .limit(recent_limit)
            .all()
        )
        warning_rows = (
            storage.session.query(ContextLoadEventModel)
            .filter(ContextLoadEventModel.estimated_tokens >= warning_threshold)
            .order_by(ContextLoadEventModel.loaded_at.desc(), ContextLoadEventModel.id.desc())
            .limit(warning_limit)
            .all()
        )
        latest_ids = (
            storage.session.query(
                ContextLoadEventModel.artifact_type.label("artifact_type"),
                ContextLoadEventModel.identifier.label("identifier"),
                func.max(ContextLoadEventModel.id).label("latest_id"),
            )
            .group_by(ContextLoadEventModel.artifact_type, ContextLoadEventModel.identifier)
            .subquery()
        )
        latest_rows = (
            storage.session.query(ContextLoadEventModel)
            .join(latest_ids, ContextLoadEventModel.id == latest_ids.c.latest_id)
            .all()
        )
    except Exception:
        return _legacy_context_load_telemetry(storage)

    if not aggregate_rows:
        return _legacy_context_load_telemetry(storage)

    artifacts: Dict[str, Any] = {}
    total_loads = 0
    total_tokens = 0
    recent_events = [_event_to_payload(row) for row in recent_rows]
    warnings = [
        {
            **_event_to_payload(row),
            "warning": "context_load_large_artifact",
            "threshold": warning_threshold,
        }
        for row in warning_rows
    ]
    recent_counts: Dict[str, int] = {}
    recent_tokens: Dict[str, int] = {}
    latest_by_key = {
        f"{row.artifact_type}:{row.identifier}": row
        for row in latest_rows
    }

    for event in recent_events:
        artifact_key = f"{event['artifact_type']}:{event['identifier']}"
        estimated_tokens = int(event.get("estimated_tokens", 0) or 0)
        recent_counts[artifact_key] = recent_counts.get(artifact_key, 0) + 1
        recent_tokens[artifact_key] = recent_tokens.get(artifact_key, 0) + estimated_tokens

    for row in aggregate_rows:
        artifact_key = f"{row.artifact_type}:{row.identifier}"
        total_loads += int(row.load_count or 0)
        total_tokens += int(row.total_estimated_tokens or 0)
        latest_row = latest_by_key.get(artifact_key)
        latest_event = _event_to_payload(latest_row) if latest_row is not None else {
            "artifact_type": str(row.artifact_type or ""),
            "identifier": str(row.identifier or ""),
            "source": "",
            "loaded_at": "",
            "metadata": {},
        }
        artifacts[artifact_key] = {
            "artifact_type": latest_event["artifact_type"],
            "identifier": latest_event["identifier"],
            "load_count": int(row.load_count or 0),
            "last_loaded_at": latest_event["loaded_at"],
            "last_source": latest_event["source"],
            "total_estimated_tokens": int(row.total_estimated_tokens or 0),
            "total_estimated_tokens_sq": float(row.total_estimated_tokens_sq or 0.0),
            "max_estimated_tokens": int(row.max_estimated_tokens or 0),
            "avg_estimated_tokens": 0.0,
            "last_metadata": dict(latest_event.get("metadata") or {}),
        }

    recent_total_loads = len(recent_events)
    recent_total_tokens = sum(int(item.get("estimated_tokens", 0) or 0) for item in recent_events)
    enriched_artifacts: Dict[str, Any] = {}
    for key, item in artifacts.items():
        enriched = dict(item)
        load_count = int(enriched.get("load_count", 0) or 0)
        total_estimated_tokens = int(enriched.get("total_estimated_tokens", 0) or 0)
        total_estimated_tokens_sq = float(enriched.get("total_estimated_tokens_sq", 0.0) or 0.0)
        avg_estimated_tokens = round(total_estimated_tokens / max(1, load_count), 2)
        variance = max(0.0, (total_estimated_tokens_sq / max(1, load_count)) - (avg_estimated_tokens ** 2))
        stddev = math.sqrt(variance)
        peak_sigma = 0.0
        if stddev > 0:
            peak_sigma = round((int(enriched.get("max_estimated_tokens", 0) or 0) - avg_estimated_tokens) / stddev, 2)
        recent_count = recent_counts.get(key, 0)
        recent_token_total = recent_tokens.get(key, 0)
        enriched["avg_estimated_tokens"] = avg_estimated_tokens
        enriched["load_share_pct"] = round((load_count / total_loads) * 100, 2) if total_loads else 0.0
        enriched["token_share_pct"] = round((total_estimated_tokens / total_tokens) * 100, 2) if total_tokens else 0.0
        enriched["recent_load_share_pct"] = round((recent_count / recent_total_loads) * 100, 2) if recent_total_loads else 0.0
        enriched["recent_token_share_pct"] = round((recent_token_total / recent_total_tokens) * 100, 2) if recent_total_tokens else 0.0
        enriched["estimated_token_stddev"] = round(stddev, 2)
        enriched["peak_sigma"] = peak_sigma
        enriched["recent_load_count"] = recent_count
        enriched["recent_total_estimated_tokens"] = recent_token_total
        enriched_artifacts[key] = enriched

    totals = {
        "all_time_load_count": total_loads,
        "all_time_estimated_tokens": total_tokens,
        "recent_load_count": recent_total_loads,
        "recent_estimated_tokens": recent_total_tokens,
    }
    return {
        "policy": policy,
        "stats": {"artifacts": enriched_artifacts, "totals": totals},
        "recent": recent_events,
        "warnings": warnings,
        "file_scan": file_scan if isinstance(file_scan, dict) else {},
    }


def _iter_scannable_files(base_dir: Path) -> Iterable[Path]:
    for path in base_dir.rglob("*"):
        if not path.is_file():
            continue
        parts = set(path.parts)
        if parts & DEFAULT_FILE_SCAN_EXCLUDED_DIRS:
            continue
        if any(part.startswith("venv") for part in path.parts):
            continue
        if path.suffix.lower() not in DEFAULT_FILE_SCAN_EXTENSIONS:
            continue
        yield path


def scan_codebase_context_pressure(storage, *, base_dir: str) -> Dict[str, Any]:
    root = Path(base_dir).resolve()
    policy = get_context_load_policy(storage)
    threshold = max(1, int(policy["file_warning_estimated_tokens"]))
    limit = max(1, int(policy["file_scan_result_limit"]))
    scanned = []
    warnings = []

    for path in _iter_scannable_files(root):
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            continue
        estimated_tokens = estimate_text_tokens(content)
        rel_path = str(path.relative_to(root))
        entry = {
            "path": rel_path,
            "estimated_tokens": estimated_tokens,
            "bytes": path.stat().st_size,
            "warning": estimated_tokens >= threshold,
        }
        scanned.append(entry)
        if entry["warning"]:
            warnings.append(entry)

    scanned.sort(key=lambda item: (int(item.get("estimated_tokens", 0)), str(item.get("path") or "")), reverse=True)
    warnings.sort(key=lambda item: (int(item.get("estimated_tokens", 0)), str(item.get("path") or "")), reverse=True)
    payload = {
        "scanned_at": _utcnow(),
        "base_dir": str(root),
        "scanned_file_count": len(scanned),
        "threshold": threshold,
        "largest_files": scanned[:limit],
        "warnings": warnings[:limit],
    }
    storage.parameters.set_parameter(
        CONTEXT_FILE_SCAN_KEY,
        payload,
        description="Estimated-token scan of code/docs files to flag context-heavy artifacts.",
    )
    if hasattr(storage, "commit"):
        storage.commit()
    if warnings:
        logger.warning("Context pressure scan found %s oversized files above estimated token threshold %s", len(warnings), threshold)
    return payload


def _record_context_load_with_storage(
    storage,
    *,
    artifact_type: str,
    identifier: str,
    content: str,
    source: str,
    metadata: Optional[Dict[str, Any]] = None,
    commit_immediately: bool = True,
) -> Dict[str, Any]:
    identifier = str(identifier or "").strip()
    if not identifier:
        return {}

    estimated_tokens = estimate_text_tokens(content)
    now = _utcnow()
    event = {
        "artifact_type": artifact_type,
        "identifier": identifier,
        "source": source,
        "estimated_tokens": estimated_tokens,
        "loaded_at": now,
        "metadata": dict(metadata or {}),
    }

    try:
        bind = _storage_bind(storage)
        if bind is not None:
            ContextLoadEventModel.__table__.create(bind=bind, checkfirst=True)
        policy = get_context_load_policy(storage)
        storage.session.add(
            ContextLoadEventModel(
                artifact_type=artifact_type,
                identifier=identifier,
                source=source,
                estimated_tokens=estimated_tokens,
                event_metadata=dict(metadata or {}),
            )
        )
        if estimated_tokens >= max(1, policy["warning_estimated_tokens"]):
            logger.warning(
                "Large context artifact loaded: %s (%s) estimated_tokens=%s threshold=%s",
                identifier,
                artifact_type,
                estimated_tokens,
                policy["warning_estimated_tokens"],
            )
        if commit_immediately and hasattr(storage, "commit"):
            storage.commit()
    except OperationalError as exc:
        if "database is locked" not in str(exc).lower():
            raise
        logger.warning(
            "Skipping context telemetry write for %s (%s) due to database lock contention.",
            identifier,
            artifact_type,
        )
        if commit_immediately and hasattr(storage, "rollback"):
            storage.rollback()
    return event


def record_context_load(
    *,
    artifact_type: str,
    identifier: str,
    content: str,
    source: str,
    metadata: Optional[Dict[str, Any]] = None,
    storage=None,
) -> Dict[str, Any]:
    # Observability should never poison the caller's main transaction. Always use
    # an isolated storage session and treat lock contention as a dropped metric.
    use_provided_storage = False
    if storage is not None:
        try:
            bind = _storage_bind(storage)
            engine_url = str(getattr(bind, "url", "") or "")
            use_provided_storage = ":memory:" in engine_url
        except Exception:
            use_provided_storage = False
    if use_provided_storage and storage is not None:
        return _record_context_load_with_storage(
            storage,
            artifact_type=artifact_type,
            identifier=identifier,
            content=content,
            source=source,
            metadata=metadata,
            commit_immediately=True,
        )
    event = {
        "artifact_type": artifact_type,
        "identifier": str(identifier or "").strip(),
        "source": source,
        "estimated_tokens": estimate_text_tokens(content),
        "loaded_at": _utcnow(),
        "metadata": dict(metadata or {}),
    }
    if not event["identifier"]:
        return {}
    should_flush = enqueue_context_observability_event(event)
    if should_flush:
        flush_observability_writes()
    return event
