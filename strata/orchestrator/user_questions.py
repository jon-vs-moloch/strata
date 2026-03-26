"""
@module orchestrator.user_questions
@purpose Queue internal user-facing questions for the chat agent to deliver naturally.
@owns pending question lifecycle, session scoping, parameter-backed storage
@does_not_own actual conversational wording or user-response interpretation
@key_exports enqueue_user_question, get_active_question, get_question_for_source, mark_question_asked, resolve_question
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4


USER_QUESTIONS_KEY = "user_questions:index"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_questions(storage) -> List[Dict[str, Any]]:
    rows = storage.parameters.peek_parameter(USER_QUESTIONS_KEY, default_value=[]) or []
    return rows if isinstance(rows, list) else []


def _save_questions(storage, rows: List[Dict[str, Any]]) -> None:
    storage.parameters.set_parameter(
        USER_QUESTIONS_KEY,
        rows,
        description="Internal queue of pending user-facing questions for the chat agent.",
    )


def enqueue_user_question(
    storage,
    *,
    session_id: str,
    question: str,
    source_type: str,
    source_id: str,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    rows = _load_questions(storage)
    question_id = f"uq_{uuid4().hex[:12]}"
    item = {
        "question_id": question_id,
        "session_id": session_id or "default",
        "question": str(question).strip(),
        "source_type": source_type,
        "source_id": source_id,
        "context": context or {},
        "status": "pending",
        "created_at": _now(),
        "updated_at": _now(),
    }
    rows.append(item)
    _save_questions(storage, rows)
    return item


def get_active_question(storage, session_id: str) -> Dict[str, Any]:
    rows = _load_questions(storage)
    candidates = [
        row for row in rows
        if isinstance(row, dict)
        and row.get("session_id") == (session_id or "default")
        and row.get("status") in {"pending", "asked"}
    ]
    candidates.sort(key=lambda row: row.get("created_at", ""))
    return candidates[0] if candidates else {}


def get_question_for_source(storage, *, source_type: str, source_id: str) -> Dict[str, Any]:
    rows = _load_questions(storage)
    candidates = [
        row for row in rows
        if isinstance(row, dict)
        and row.get("source_type") == source_type
        and str(row.get("source_id")) == str(source_id)
        and row.get("status") in {"pending", "asked"}
    ]
    candidates.sort(key=lambda row: row.get("created_at", ""))
    return candidates[0] if candidates else {}


def mark_question_asked(storage, question_id: str) -> Dict[str, Any]:
    rows = _load_questions(storage)
    updated = {}
    for row in rows:
        if isinstance(row, dict) and row.get("question_id") == question_id:
            row["status"] = "asked"
            row["updated_at"] = _now()
            updated = row
            break
    _save_questions(storage, rows)
    return updated


def resolve_question(storage, question_id: str, *, resolution: str = "resolved", response: str = "") -> Dict[str, Any]:
    rows = _load_questions(storage)
    updated = {}
    for row in rows:
        if isinstance(row, dict) and row.get("question_id") == question_id:
            row["status"] = resolution
            row["response"] = response
            row["updated_at"] = _now()
            updated = row
            break
    _save_questions(storage, rows)
    return updated
