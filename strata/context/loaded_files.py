"""
@module context.loaded_files
@purpose Maintain a small persistent set of workspace files that should be reloaded into context each round.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from strata.observability.context import estimate_text_tokens, get_context_load_policy, record_context_load


LOADED_CONTEXT_FILES_KEY = "workspace_loaded_context_files"
LOADED_CONTEXT_FILES_DESCRIPTION = "Files explicitly loaded into persistent workspace context across rounds."
DEFAULT_LOADED_CONTEXT_BUDGET_TOKENS = 3200
DEFAULT_CONTEXT_PRIORITY = "normal"
CONTEXT_PRIORITY_ORDER = {
    "critical": 0,
    "high": 1,
    "normal": 2,
    "low": 3,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_priority(raw_priority: Any) -> str:
    value = str(raw_priority or DEFAULT_CONTEXT_PRIORITY).strip().lower()
    return value if value in CONTEXT_PRIORITY_ORDER else DEFAULT_CONTEXT_PRIORITY


def _canonicalize_path(raw_path: str, *, base_dir: str | None = None) -> Path:
    candidate = Path(str(raw_path or "").strip())
    if not candidate.is_absolute():
        root = Path(base_dir or ".").resolve()
        candidate = (root / candidate).resolve()
    return candidate


def _load_registry(storage) -> Dict[str, Any]:
    payload = storage.parameters.peek_parameter(
        LOADED_CONTEXT_FILES_KEY,
        default_value={"files": [], "budget_tokens": DEFAULT_LOADED_CONTEXT_BUDGET_TOKENS},
    ) or {"files": [], "budget_tokens": DEFAULT_LOADED_CONTEXT_BUDGET_TOKENS}
    if not isinstance(payload, dict):
        payload = {"files": [], "budget_tokens": DEFAULT_LOADED_CONTEXT_BUDGET_TOKENS}
    payload["files"] = list(payload.get("files") or [])
    try:
        payload["budget_tokens"] = int(payload.get("budget_tokens") or DEFAULT_LOADED_CONTEXT_BUDGET_TOKENS)
    except Exception:
        payload["budget_tokens"] = DEFAULT_LOADED_CONTEXT_BUDGET_TOKENS
    return payload


def get_loaded_context_registry(storage) -> Dict[str, Any]:
    payload = _load_registry(storage)
    files = sorted(
        [dict(entry or {}) for entry in payload["files"]],
        key=lambda entry: (
            CONTEXT_PRIORITY_ORDER.get(_normalize_priority(entry.get("priority")), CONTEXT_PRIORITY_ORDER[DEFAULT_CONTEXT_PRIORITY]),
            str(entry.get("added_at") or ""),
            str(entry.get("path") or ""),
        ),
    )
    return {
        "files": files,
        "budget_tokens": int(payload["budget_tokens"]),
        "total_estimated_tokens": sum(int((entry or {}).get("estimated_tokens") or 0) for entry in files),
    }


def list_loaded_context_files(storage) -> Dict[str, Any]:
    registry = _load_registry(storage)
    return registry


def load_context_file(
    storage,
    raw_path: str,
    *,
    source: str,
    base_dir: str | None = None,
    priority: str = DEFAULT_CONTEXT_PRIORITY,
) -> Dict[str, Any]:
    registry = _load_registry(storage)
    policy = get_context_load_policy(storage)
    budget = max(1, int(registry.get("budget_tokens") or DEFAULT_LOADED_CONTEXT_BUDGET_TOKENS))
    path = _canonicalize_path(raw_path, base_dir=base_dir)
    if not path.exists() or not path.is_file():
        return {"status": "missing", "path": str(path)}
    content = path.read_text(encoding="utf-8", errors="ignore")
    estimated_tokens = estimate_text_tokens(content)

    files = [entry for entry in registry["files"] if isinstance(entry, dict)]
    existing = [entry for entry in files if entry.get("path") != str(path)]
    total_tokens = sum(int(entry.get("estimated_tokens") or 0) for entry in existing)
    if total_tokens + estimated_tokens > budget:
        return {
            "status": "over_budget",
            "path": str(path),
            "estimated_tokens": estimated_tokens,
            "budget_tokens": budget,
            "current_tokens": total_tokens,
            "warning_threshold": int(policy.get("warning_estimated_tokens") or 0),
        }

    entry = {
        "path": str(path),
        "estimated_tokens": estimated_tokens,
        "loaded_at": None,
        "added_at": _now_iso(),
        "last_touched_at": _now_iso(),
        "source": source,
        "priority": _normalize_priority(priority),
    }
    existing.append(entry)
    registry["files"] = existing
    storage.parameters.set_parameter(
        LOADED_CONTEXT_FILES_KEY,
        registry,
        description=LOADED_CONTEXT_FILES_DESCRIPTION,
    )
    storage.commit()
    return {
        "status": "ok",
        "path": str(path),
        "estimated_tokens": estimated_tokens,
        "budget_tokens": budget,
        "files": existing,
    }


def unload_context_file(storage, raw_path: str, *, base_dir: str | None = None) -> Dict[str, Any]:
    registry = _load_registry(storage)
    path = _canonicalize_path(raw_path, base_dir=base_dir)
    before = len(registry["files"])
    registry["files"] = [entry for entry in registry["files"] if entry.get("path") != str(path)]
    storage.parameters.set_parameter(
        LOADED_CONTEXT_FILES_KEY,
        registry,
        description=LOADED_CONTEXT_FILES_DESCRIPTION,
    )
    storage.commit()
    return {
        "status": "ok",
        "path": str(path),
        "removed": before != len(registry["files"]),
        "files": registry["files"],
    }


def reprioritize_context_file(
    storage,
    raw_path: str,
    *,
    priority: str,
    base_dir: str | None = None,
) -> Dict[str, Any]:
    registry = _load_registry(storage)
    path = _canonicalize_path(raw_path, base_dir=base_dir)
    normalized_priority = _normalize_priority(priority)
    updated = False
    for entry in registry["files"]:
        if str(entry.get("path") or "") != str(path):
            continue
        entry["priority"] = normalized_priority
        entry["last_touched_at"] = _now_iso()
        updated = True
        break
    storage.parameters.set_parameter(
        LOADED_CONTEXT_FILES_KEY,
        registry,
        description=LOADED_CONTEXT_FILES_DESCRIPTION,
    )
    storage.commit()
    return {
        "status": "ok",
        "path": str(path),
        "updated": updated,
        "priority": normalized_priority,
        "files": registry["files"],
    }


def _entry_compaction_rank(entry: Dict[str, Any]) -> tuple:
    return (
        CONTEXT_PRIORITY_ORDER.get(_normalize_priority(entry.get("priority")), CONTEXT_PRIORITY_ORDER[DEFAULT_CONTEXT_PRIORITY]),
        str(entry.get("last_touched_at") or entry.get("added_at") or ""),
        int(entry.get("estimated_tokens") or 0),
    )


def compact_context_files(storage, *, target_tokens: int | None = None) -> Dict[str, Any]:
    registry = _load_registry(storage)
    budget = max(1, int(registry.get("budget_tokens") or DEFAULT_LOADED_CONTEXT_BUDGET_TOKENS))
    files = [dict(entry or {}) for entry in registry.get("files") or [] if isinstance(entry, dict)]
    total_tokens = sum(int(entry.get("estimated_tokens") or 0) for entry in files)
    desired_tokens = budget if target_tokens is None else max(0, int(target_tokens))
    if total_tokens <= desired_tokens:
        return {
            "status": "ok",
            "removed": [],
            "remaining": files,
            "total_estimated_tokens": total_tokens,
            "target_tokens": desired_tokens,
        }
    removable = sorted(files, key=_entry_compaction_rank, reverse=True)
    removed: List[Dict[str, Any]] = []
    remaining = list(files)
    current_tokens = total_tokens
    for entry in removable:
        if current_tokens <= desired_tokens:
            break
        remaining = [candidate for candidate in remaining if candidate.get("path") != entry.get("path")]
        removed.append(entry)
        current_tokens -= int(entry.get("estimated_tokens") or 0)
    registry["files"] = remaining
    storage.parameters.set_parameter(
        LOADED_CONTEXT_FILES_KEY,
        registry,
        description=LOADED_CONTEXT_FILES_DESCRIPTION,
    )
    storage.commit()
    return {
        "status": "ok",
        "removed": removed,
        "remaining": remaining,
        "total_estimated_tokens": current_tokens,
        "target_tokens": desired_tokens,
    }


def build_loaded_context_notice(storage) -> str:
    registry = get_loaded_context_registry(storage)
    files = list(registry.get("files") or [])
    total_tokens = int(registry.get("total_estimated_tokens") or 0)
    budget_tokens = int(registry.get("budget_tokens") or DEFAULT_LOADED_CONTEXT_BUDGET_TOKENS)
    if not files:
        return "No persistent workspace context is currently pinned."
    rendered = [
        f"- {entry.get('path')} [{_normalize_priority(entry.get('priority'))}] ({entry.get('estimated_tokens')} est. tokens)"
        for entry in files[:6]
    ]
    pressure = "high" if total_tokens >= int(budget_tokens * 0.85) else "normal"
    return (
        f"Persistent context pressure: {pressure} ({total_tokens}/{budget_tokens} est. tokens)\n"
        "Pinned context files:\n"
        + "\n".join(rendered)
        + (
            "\nIf pressure is high, unload or reprioritize lower-value context before pinning more."
            if pressure == "high"
            else ""
        )
    )


def build_loaded_context_block(storage, *, source: str) -> str:
    registry = _load_registry(storage)
    parts: List[str] = []
    changed = False
    files = []
    for entry in registry["files"]:
        path = Path(str(entry.get("path") or ""))
        if not path.exists() or not path.is_file():
            changed = True
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        record_context_load(
            artifact_type="loaded_context_file",
            identifier=str(path),
            content=content,
            source=source,
            metadata={"path": str(path)},
            storage=storage,
        )
        snippet = content[:2000].strip()
        if snippet:
            parts.append(f"[{path}]\n{snippet}")
        refreshed = dict(entry)
        refreshed["loaded_at"] = _now_iso()
        refreshed["last_touched_at"] = _now_iso()
        files.append(refreshed)
    if changed:
        registry["files"] = files
        storage.parameters.set_parameter(
            LOADED_CONTEXT_FILES_KEY,
            registry,
            description=LOADED_CONTEXT_FILES_DESCRIPTION,
        )
        storage.commit()
    return "\n\n".join(parts)
