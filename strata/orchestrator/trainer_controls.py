"""
@module orchestrator.trainer_controls
@purpose Shared branch-observation and bounded trainer-control helpers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from strata.storage.models import TaskModel, TaskState


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slim_attempt(attempt: Any) -> Dict[str, Any]:
    return {
        "attempt_id": getattr(attempt, "attempt_id", None),
        "outcome": getattr(getattr(attempt, "outcome", None), "value", None),
        "resolution": getattr(getattr(attempt, "resolution", None), "value", None),
        "reason": str(getattr(attempt, "reason", "") or "")[:240],
        "started_at": getattr(getattr(attempt, "started_at", None), "isoformat", lambda: None)(),
        "ended_at": getattr(getattr(attempt, "ended_at", None), "isoformat", lambda: None)(),
        "plan_review": dict(getattr(attempt, "plan_review", {}) or {}),
    }


def _task_summary(task: Optional[TaskModel]) -> Optional[Dict[str, Any]]:
    if not task:
        return None
    constraints = dict(task.constraints or {})
    return {
        "task_id": task.task_id,
        "title": task.title,
        "state": getattr(task.state, "value", str(task.state)),
        "type": getattr(task.type, "value", str(task.type)),
        "session_id": task.session_id,
        "lane": constraints.get("lane"),
        "human_intervention_required": bool(task.human_intervention_required),
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }


def _append_control_event(task: TaskModel, *, kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    constraints = dict(task.constraints or {})
    events = list(constraints.get("trainer_controls") or [])
    event = {"kind": kind, "recorded_at": _now(), **payload}
    events.append(event)
    constraints["trainer_controls"] = events[-12:]
    task.constraints = constraints
    return event


def build_branch_state_summary(storage, *, task_id: str, question_lookup=None, attempt_limit: int = 5, child_limit: int = 8) -> Dict[str, Any]:
    task = storage.tasks.get_by_id(task_id)
    if not task:
        return {"status": "missing", "task_id": task_id}

    parent = storage.tasks.get_by_id(task.parent_task_id) if task.parent_task_id else None
    attempts = list(storage.attempts.get_by_task_id(task.task_id) or [])
    attempts.sort(key=lambda item: getattr(item, "started_at", None) or datetime.min, reverse=True)
    children = (
        storage.session.query(TaskModel)
        .filter(TaskModel.parent_task_id == task.task_id)
        .order_by(TaskModel.updated_at.desc())
        .limit(max(1, int(child_limit)))
        .all()
    )
    constraints = dict(task.constraints or {})
    pending_question = (
        question_lookup(storage, source_type="task_blocked", source_id=task.task_id)
        if question_lookup
        else {}
    )
    return {
        "status": "ok",
        "task": _task_summary(task),
        "parent": _task_summary(parent),
        "children": [_task_summary(child) for child in children],
        "recent_attempts": [_slim_attempt(attempt) for attempt in attempts[: max(1, int(attempt_limit))]],
        "verification_posture": constraints.get("verification_posture"),
        "plan_override": constraints.get("plan_override"),
        "invalidated_premises": list(constraints.get("invalidated_premises") or []),
        "self_audit_requests": list(constraints.get("self_audit_requests") or []),
        "trainer_controls": list(constraints.get("trainer_controls") or []),
        "trace_reviews": list(constraints.get("trace_reviews") or []),
        "pending_question": pending_question or None,
    }


def rewrite_task_plan(storage, *, task: TaskModel, plan: str, rationale: str, actor: str = "trainer") -> Dict[str, Any]:
    normalized_plan = str(plan or "").strip()
    normalized_rationale = str(rationale or "").strip()
    constraints = dict(task.constraints or {})
    override = {
        "plan": normalized_plan,
        "rationale": normalized_rationale,
        "actor": actor,
        "recorded_at": _now(),
    }
    constraints["plan_override"] = override
    task.constraints = constraints
    task.description = (
        (task.description or "").rstrip()
        + f"\n\n[PLAN OVERRIDE]\nReason: {normalized_rationale}\nPlan:\n{normalized_plan}\n"
    )
    if task.state not in {TaskState.COMPLETE, TaskState.CANCELLED, TaskState.ABANDONED}:
        task.state = TaskState.PENDING
        task.human_intervention_required = False
    event = _append_control_event(task, kind="rewrite_plan", payload=override)
    return {"plan_override": override, "event": event}


def invalidate_task_premise(storage, *, task: TaskModel, premise: str, correction: str, actor: str = "trainer") -> Dict[str, Any]:
    normalized_premise = str(premise or "").strip()
    normalized_correction = str(correction or "").strip()
    constraints = dict(task.constraints or {})
    invalidated = list(constraints.get("invalidated_premises") or [])
    record = {
        "premise": normalized_premise,
        "correction": normalized_correction,
        "actor": actor,
        "recorded_at": _now(),
    }
    invalidated.append(record)
    constraints["invalidated_premises"] = invalidated[-12:]
    task.constraints = constraints
    task.description = (
        (task.description or "").rstrip()
        + f"\n\n[INVALIDATED PREMISE]\nPremise: {normalized_premise}\nCorrection: {normalized_correction}\n"
    )
    if task.state not in {TaskState.COMPLETE, TaskState.CANCELLED, TaskState.ABANDONED}:
        task.state = TaskState.PENDING
        task.human_intervention_required = False
    event = _append_control_event(task, kind="invalidate_premise", payload=record)
    return {"invalidated_premise": record, "event": event}


def set_task_verification_posture(storage, *, task: TaskModel, posture: str, rationale: str, actor: str = "trainer") -> Dict[str, Any]:
    constraints = dict(task.constraints or {})
    record = {
        "posture": str(posture or "").strip().lower(),
        "rationale": str(rationale or "").strip(),
        "actor": actor,
        "recorded_at": _now(),
    }
    constraints["verification_posture"] = record
    task.constraints = constraints
    event = _append_control_event(task, kind="set_verification_posture", payload=record)
    return {"verification_posture": record, "event": event}


def record_self_audit_request(task: TaskModel, *, focus: str = "", actor: str = "trainer") -> Dict[str, Any]:
    constraints = dict(task.constraints or {})
    requests = list(constraints.get("self_audit_requests") or [])
    request = {
        "focus": str(focus or "").strip(),
        "actor": actor,
        "recorded_at": _now(),
    }
    requests.append(request)
    constraints["self_audit_requests"] = requests[-12:]
    task.constraints = constraints
    event = _append_control_event(task, kind="request_self_audit", payload=request)
    return {"self_audit_request": request, "event": event}


def update_question_escalation_mode(
    storage,
    *,
    question_id: str,
    escalation_mode: str,
    rationale: str,
    actor: str = "trainer",
    question_update=None,
) -> Dict[str, Any]:
    if question_update is None:
        from strata.orchestrator.user_questions import set_question_escalation_mode as question_update

    updated = question_update(
        storage,
        question_id,
        escalation_mode=escalation_mode,
        rationale=rationale,
    )
    source_id = str((updated or {}).get("source_id") or "").strip()
    task = storage.tasks.get_by_id(source_id) if source_id else None
    if task:
        event = _append_control_event(
            task,
            kind="update_question_escalation_mode",
            payload={
                "question_id": question_id,
                "escalation_mode": str(updated.get("escalation_mode") or escalation_mode).strip(),
                "rationale": str(rationale or "").strip(),
                "actor": actor,
            },
        )
        return {"question": updated, "event": event}
    return {"question": updated}
