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
from typing import Any, Dict, Iterable, Optional

from strata.storage.services.main import StorageManager


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
    "node_modules", "venv", ".venv", "memory", "runtime", "attic",
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


def get_context_load_telemetry(storage) -> Dict[str, Any]:
    stats = storage.parameters.peek_parameter(CONTEXT_LOAD_STATS_KEY, default_value={"artifacts": {}}) or {"artifacts": {}}
    recent = storage.parameters.peek_parameter(CONTEXT_LOAD_RECENT_KEY, default_value=[]) or []
    warnings = storage.parameters.peek_parameter(CONTEXT_LOAD_WARNINGS_KEY, default_value=[]) or []
    file_scan = storage.parameters.peek_parameter(CONTEXT_FILE_SCAN_KEY, default_value={}) or {}
    return {
        "policy": get_context_load_policy(storage),
        "stats": stats if isinstance(stats, dict) else {"artifacts": {}},
        "recent": recent if isinstance(recent, list) else [],
        "warnings": warnings if isinstance(warnings, list) else [],
        "file_scan": file_scan if isinstance(file_scan, dict) else {},
    }


def _iter_scannable_files(base_dir: Path) -> Iterable[Path]:
    for path in base_dir.rglob("*"):
        if not path.is_file():
            continue
        parts = set(path.parts)
        if parts & DEFAULT_FILE_SCAN_EXCLUDED_DIRS:
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
) -> Dict[str, Any]:
    identifier = str(identifier or "").strip()
    if not identifier:
        return {}

    policy = get_context_load_policy(storage)
    estimated_tokens = estimate_text_tokens(content)
    now = _utcnow()
    key = f"{artifact_type}:{identifier}"
    stats_payload = storage.parameters.peek_parameter(CONTEXT_LOAD_STATS_KEY, default_value={"artifacts": {}}) or {"artifacts": {}}
    artifacts = dict(stats_payload.get("artifacts") or {})
    current = dict(artifacts.get(key) or {})
    current["artifact_type"] = artifact_type
    current["identifier"] = identifier
    current["load_count"] = int(current.get("load_count", 0)) + 1
    current["last_loaded_at"] = now
    current["last_source"] = source
    current["total_estimated_tokens"] = int(current.get("total_estimated_tokens", 0)) + estimated_tokens
    current["max_estimated_tokens"] = max(int(current.get("max_estimated_tokens", 0)), estimated_tokens)
    current["avg_estimated_tokens"] = round(current["total_estimated_tokens"] / max(1, current["load_count"]), 2)
    current["last_metadata"] = dict(metadata or {})
    artifacts[key] = current
    stats_payload["artifacts"] = artifacts

    event = {
        "artifact_type": artifact_type,
        "identifier": identifier,
        "source": source,
        "estimated_tokens": estimated_tokens,
        "loaded_at": now,
        "metadata": dict(metadata or {}),
    }
    recent = list(storage.parameters.peek_parameter(CONTEXT_LOAD_RECENT_KEY, default_value=[]) or [])
    recent.append(event)
    recent = recent[-max(1, policy["recent_event_limit"]):]

    warnings = list(storage.parameters.peek_parameter(CONTEXT_LOAD_WARNINGS_KEY, default_value=[]) or [])
    if estimated_tokens >= max(1, policy["warning_estimated_tokens"]):
        warning = {
            **event,
            "warning": "context_load_large_artifact",
            "threshold": policy["warning_estimated_tokens"],
        }
        warnings.append(warning)
        warnings = warnings[-max(1, policy["recent_warning_limit"]):]
        logger.warning(
            "Large context artifact loaded: %s (%s) estimated_tokens=%s threshold=%s",
            identifier,
            artifact_type,
            estimated_tokens,
            policy["warning_estimated_tokens"],
        )

    storage.parameters.set_parameter(
        CONTEXT_LOAD_STATS_KEY,
        stats_payload,
        description="Aggregate context-load usage by artifact.",
    )
    storage.parameters.set_parameter(
        CONTEXT_LOAD_RECENT_KEY,
        recent,
        description="Recent context-load events.",
    )
    storage.parameters.set_parameter(
        CONTEXT_LOAD_WARNINGS_KEY,
        warnings,
        description="Recent warnings for large context-load artifacts.",
    )
    storage.commit()
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
    if storage is not None:
        return _record_context_load_with_storage(
            storage,
            artifact_type=artifact_type,
            identifier=identifier,
            content=content,
            source=source,
            metadata=metadata,
        )

    ephemeral_storage = StorageManager()
    try:
        return _record_context_load_with_storage(
            ephemeral_storage,
            artifact_type=artifact_type,
            identifier=identifier,
            content=content,
            source=source,
            metadata=metadata,
        )
    finally:
        ephemeral_storage.close()
