"""
@module orchestrator.user_questions
@purpose Queue internal user-facing questions for the chat agent to deliver naturally.
@owns pending question lifecycle, session scoping, parameter-backed storage
@does_not_own actual conversational wording or user-response interpretation
@key_exports enqueue_user_question, get_active_question, get_question_for_source, mark_question_asked, resolve_question, set_question_escalation_mode, ensure_question_escalation_for_source
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional
from uuid import uuid4

from strata.communication.primitives import build_communication_decision, deliver_communication_decision
from strata.core.lanes import canonical_session_id_for_lane, infer_lane_from_session_id, normalize_lane


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


def _default_session_title_for_question(source_type: str, brief_question: str) -> str:
    if str(source_type or "").strip().lower() == "task_blocked":
        return "Needs Your Input"
    brief = str(brief_question or "").strip()
    return brief[:80] if brief else "System Question"


def _normalize_escalation_mode(escalation_mode: str | None) -> str:
    normalized = str(escalation_mode or "blocking").strip().lower()
    return "non_blocking" if normalized in {"non_blocking", "nonblocking", "advisory", "continue"} else "blocking"


def _append_escalation_transition(row: Dict[str, Any], *, from_mode: str, to_mode: str, rationale: str = "") -> None:
    history = list(row.get("escalation_history") or [])
    history.append(
        {
            "from_mode": from_mode,
            "to_mode": to_mode,
            "rationale": str(rationale or "").strip(),
            "recorded_at": _now(),
        }
    )
    row["escalation_history"] = history[-12:]


def _sync_task_blocking_state_from_question(storage, row: Dict[str, Any]) -> None:
    if not isinstance(row, dict) or str(row.get("source_type") or "").strip().lower() != "task_blocked":
        return
    if str(row.get("status") or "").strip().lower() not in {"pending", "asked"}:
        return
    task_id = str(row.get("source_id") or "").strip()
    if not task_id or not hasattr(storage, "tasks"):
        return
    task = storage.tasks.get_by_id(task_id)
    if not task:
        return
    normalized_mode = _normalize_escalation_mode(row.get("escalation_mode"))
    if normalized_mode == "blocking":
        task.human_intervention_required = True
        if getattr(task, "state", None) not in {"COMPLETE", "CANCELLED", "ABANDONED"}:
            try:
                task.state = type(task.state).BLOCKED
            except Exception:
                pass
        return
    task.human_intervention_required = False
    try:
        state_enum = type(task.state)
        if task.state == state_enum.BLOCKED:
            task.state = state_enum.PENDING
    except Exception:
        pass


def _default_session_title_for_mode(source_type: str, brief_question: str, escalation_mode: str) -> str:
    if str(source_type or "").strip().lower() == "task_blocked":
        return "Needs Your Input" if escalation_mode == "blocking" else "Quick Question"
    brief = str(brief_question or "").strip()
    return brief[:80] if brief else "System Question"


def enqueue_user_question(
    storage,
    *,
    session_id: str,
    question: str,
    source_type: str,
    source_id: str,
    lane: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    escalation_mode: str = "blocking",
) -> Dict[str, Any]:
    rows = _load_questions(storage)
    brief_question = _derive_brief_question(question)
    normalized_escalation_mode = _normalize_escalation_mode(escalation_mode)
    resolved_lane = (
        normalize_lane(lane)
        or normalize_lane((context or {}).get("lane"))
        or infer_lane_from_session_id(session_id)
        or "trainer"
    )
    resolved_session_id = canonical_session_id_for_lane(resolved_lane, session_id or "default")
    question_id = f"uq_{uuid4().hex[:12]}"
    item = {
        "question_id": question_id,
        "session_id": resolved_session_id,
        "question": str(question).strip(),
        "brief_question": brief_question,
        "source_type": source_type,
        "source_id": source_id,
        "escalation_mode": normalized_escalation_mode,
        "context": context or {},
        "status": "pending",
        "created_at": _now(),
        "updated_at": _now(),
    }
    rows.append(item)
    _save_questions(storage, rows)
    try:
        decision = build_communication_decision(
            role="assistant",
            content=str(question).strip(),
            lane=resolved_lane,
            channel="existing_session_message",
            session_id=resolved_session_id,
            audience="user",
            source_kind=(
                "task_blocked_question"
                if source_type == "task_blocked" and normalized_escalation_mode == "blocking"
                else "task_non_blocking_question"
                if source_type == "task_blocked"
                else "system_question"
            ),
            source_actor="orchestrator_question_queue",
            opened_reason=f"{source_type}:{normalized_escalation_mode}",
            tags=["question", source_type, normalized_escalation_mode],
            topic_summary=brief_question,
            session_title=_default_session_title_for_mode(source_type, brief_question, normalized_escalation_mode),
            communicative_act="question",
            urgency="high" if source_type == "task_blocked" and normalized_escalation_mode == "blocking" else "normal",
        )
        delivery = deliver_communication_decision(storage, decision)
        delivered_session_id = str((delivery or {}).get("session_id") or "").strip()
        if delivered_session_id:
            item["session_id"] = delivered_session_id
        item["status"] = "asked"
        item["updated_at"] = _now()
        _save_questions(storage, rows)
    except Exception:
        pass
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


def set_question_escalation_mode(
    storage,
    question_id: str,
    *,
    escalation_mode: str,
    rationale: str = "",
) -> Dict[str, Any]:
    rows = _load_questions(storage)
    updated = {}
    normalized_mode = _normalize_escalation_mode(escalation_mode)
    for row in rows:
        if not isinstance(row, dict) or row.get("question_id") != question_id:
            continue
        previous_mode = _normalize_escalation_mode(row.get("escalation_mode"))
        if previous_mode != normalized_mode:
            row["escalation_mode"] = normalized_mode
            _append_escalation_transition(
                row,
                from_mode=previous_mode,
                to_mode=normalized_mode,
                rationale=rationale,
            )
        row["updated_at"] = _now()
        _sync_task_blocking_state_from_question(storage, row)
        updated = row
        break
    _save_questions(storage, rows)
    return updated


def ensure_question_escalation_for_source(
    storage,
    *,
    source_type: str,
    source_id: str,
    escalation_mode: str,
    rationale: str = "",
) -> Dict[str, Any]:
    existing = get_question_for_source(storage, source_type=source_type, source_id=source_id)
    if not existing:
        return {}
    return set_question_escalation_mode(
        storage,
        existing["question_id"],
        escalation_mode=escalation_mode,
        rationale=rationale,
    )
