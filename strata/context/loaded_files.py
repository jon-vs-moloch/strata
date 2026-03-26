"""
@module context.loaded_files
@purpose Maintain a small persistent set of workspace files that should be reloaded into context each round.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from strata.observability.context import estimate_text_tokens, get_context_load_policy, record_context_load


LOADED_CONTEXT_FILES_KEY = "workspace_loaded_context_files"
LOADED_CONTEXT_FILES_DESCRIPTION = "Files explicitly loaded into persistent workspace context across rounds."
DEFAULT_LOADED_CONTEXT_BUDGET_TOKENS = 3200


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
    return {
        "files": list(payload["files"]),
        "budget_tokens": int(payload["budget_tokens"]),
    }


def list_loaded_context_files(storage) -> Dict[str, Any]:
    registry = _load_registry(storage)
    return registry


def load_context_file(storage, raw_path: str, *, source: str, base_dir: str | None = None) -> Dict[str, Any]:
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
        "source": source,
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


def build_loaded_context_block(storage, *, source: str) -> str:
    registry = _load_registry(storage)
    parts: List[str] = []
    updated = False
    files = []
    for entry in registry["files"]:
        path = Path(str(entry.get("path") or ""))
        if not path.exists() or not path.is_file():
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
        refreshed["loaded_at"] = refreshed.get("loaded_at") or None
        files.append(refreshed)
        updated = True
    if updated:
        registry["files"] = files
        storage.parameters.set_parameter(
            LOADED_CONTEXT_FILES_KEY,
            registry,
            description=LOADED_CONTEXT_FILES_DESCRIPTION,
        )
        storage.commit()
    return "\n\n".join(parts)
