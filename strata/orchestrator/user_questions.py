"""
@module orchestrator.user_questions
@purpose Queue internal user-facing questions for the chat agent to deliver naturally.
@owns pending question lifecycle, session scoping, parameter-backed storage
@does_not_own actual conversational wording or user-response interpretation
@key_exports enqueue_user_question, get_active_question, get_question_for_source, mark_question_asked, resolve_question
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional
from uuid import uuid4


USER_QUESTIONS_KEY = "user_questions:index"
MAX_TERMINAL_QUESTIONS = 50


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_questions(storage) -> List[Dict[str, Any]]:
    rows = storage.parameters.peek_parameter(USER_QUESTIONS_KEY, default_value=[]) or []
    return rows if isinstance(rows, list) else []


def _save_questions(storage, rows: List[Dict[str, Any]]) -> None:
    rows = _compact_questions(rows)
    storage.parameters.set_parameter(
        USER_QUESTIONS_KEY,
        rows,
        description="Internal queue of pending user-facing questions for the chat agent.",
    )


def _compact_questions(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    active = [
        row for row in rows
        if isinstance(row, dict) and row.get("status") not in {"resolved", "dismissed", "cancelled"}
    ]
    terminal = [
        row for row in rows
        if isinstance(row, dict) and row.get("status") in {"resolved", "dismissed", "cancelled"}
    ]
    terminal.sort(key=lambda row: row.get("updated_at", ""), reverse=True)
    return active + terminal[:MAX_TERMINAL_QUESTIONS]


def _derive_brief_question(question: str) -> str:
    text = str(question or "").strip()
    if not text:
        return ""
    for line in text.splitlines():
        candidate = re.sub(r"^\s*[-*0-9.)]+\s*", "", line).strip()
        if not candidate:
            continue
        if len(candidate) <= 180:
            return candidate
        sentence = re.split(r"(?<=[?.!])\s+", candidate)[0].strip()
        return sentence[:177].rstrip() + "..." if len(sentence) > 180 else sentence
    return text[:177].rstrip() + "..." if len(text) > 180 else text


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
        "brief_question": _derive_brief_question(question),
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
