"""
@module experimental.trace_review
@purpose Build compact summaries of arbitrary traces and review them with either the strong or weak tier.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from strata.schemas.execution import StrongExecutionContext, WeakExecutionContext
from strata.storage.models import MessageModel, TaskModel


def _clip(text: Any, limit: int = 240) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _extract_json_object(raw: str) -> Dict[str, Any]:
    normalized = str(raw or "").strip()
    if normalized.startswith("```"):
        normalized = re.sub(r"^```(?:json)?", "", normalized).strip()
        normalized = re.sub(r"```$", "", normalized).strip()
    try:
        return json.loads(normalized)
    except Exception:
        pass
    match = re.search(r"\{.*\}", normalized, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in trace review response.")
    return json.loads(match.group(0))


def _iso(value: Any) -> Optional[str]:
    if not value:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _task_payload(task: TaskModel) -> Dict[str, Any]:
    return {
        "task_id": task.task_id,
        "parent_task_id": task.parent_task_id,
        "title": task.title,
        "description": _clip(task.description, 320),
        "type": getattr(task.type, "value", str(task.type)),
        "state": getattr(task.state, "value", str(task.state)),
        "priority": task.priority,
        "session_id": task.session_id,
        "constraints": dict(task.constraints or {}),
        "created_at": _iso(task.created_at),
        "updated_at": _iso(task.updated_at),
    }


def _attempt_payload(attempt: Any) -> Dict[str, Any]:
    return {
        "attempt_id": attempt.attempt_id,
        "started_at": _iso(attempt.started_at),
        "ended_at": _iso(attempt.ended_at),
        "outcome": getattr(attempt.outcome, "value", None),
        "resolution": getattr(attempt.resolution, "value", None),
        "reason": _clip(attempt.reason, 280),
        "evidence": dict(attempt.evidence or {}),
        "artifacts": dict(attempt.artifacts or {}),
        "plan_review": dict(attempt.plan_review or {}),
    }


def _message_payload(message: MessageModel) -> Dict[str, Any]:
    return {
        "message_id": message.message_id,
        "role": message.role,
        "created_at": _iso(message.created_at),
        "associated_task_id": message.associated_task_id,
        "content": _clip(message.content, 360),
        "is_intervention": bool(message.is_intervention),
    }


def build_eval_trace_summary(
    *,
    candidate_change_id: str,
    baseline_change_id: str,
    benchmark_reports: Optional[List[Dict[str, Any]]] = None,
    structured_reports: Optional[List[Dict[str, Any]]] = None,
    suite_name: Optional[str] = None,
) -> Dict[str, Any]:
    benchmark_runs = benchmark_reports or []
    structured_runs = structured_reports or []
    benchmark_samples: List[Dict[str, Any]] = []
    structured_samples: List[Dict[str, Any]] = []

    for report in benchmark_runs[:2]:
        for sample in (report.get("samples") or [])[:3]:
            benchmark_samples.append(
                {
                    "prompt_id": sample.get("prompt_id"),
                    "winner": sample.get("winner"),
                    "baseline_score": sample.get("baseline_score"),
                    "harness_score": sample.get("harness_score"),
                    "baseline_latency_s": sample.get("baseline_latency_s"),
                    "harness_latency_s": sample.get("harness_latency_s"),
                    "rationale": _clip(sample.get("rationale"), 180),
                    "baseline_response": _clip(sample.get("baseline_response")),
                    "harness_response": _clip(sample.get("harness_response")),
                }
            )

    for report in structured_runs[:2]:
        for sample in (report.get("samples") or [])[:4]:
            structured_samples.append(
                {
                    "case_id": sample.get("case_id"),
                    "baseline_correct": sample.get("baseline_correct"),
                    "harness_correct": sample.get("harness_correct"),
                    "baseline_latency_s": sample.get("baseline_latency_s"),
                    "harness_latency_s": sample.get("harness_latency_s"),
                    "baseline_response": _clip(sample.get("baseline_response")),
                    "harness_response": _clip(sample.get("harness_response")),
                }
            )

    return {
        "candidate_change_id": candidate_change_id,
        "baseline_change_id": baseline_change_id,
        "suite_name": suite_name,
        "benchmark_runs": [
            {
                "run_label": report.get("run_label"),
                "baseline_wins": report.get("baseline_wins"),
                "harness_wins": report.get("harness_wins"),
                "ties": report.get("ties"),
                "average_baseline_score": report.get("average_baseline_score"),
                "average_harness_score": report.get("average_harness_score"),
            }
            for report in benchmark_runs[:3]
        ],
        "structured_runs": [
            {
                "run_label": report.get("run_label"),
                "suite_name": report.get("suite_name"),
                "baseline_accuracy": report.get("baseline_accuracy"),
                "harness_accuracy": report.get("harness_accuracy"),
                "baseline_avg_latency_s": report.get("baseline_avg_latency_s"),
                "harness_avg_latency_s": report.get("harness_avg_latency_s"),
            }
            for report in structured_runs[:3]
        ],
        "benchmark_samples": benchmark_samples,
        "structured_samples": structured_samples,
    }


def build_task_trace_summary(
    storage,
    *,
    task_id: str,
    include_session_messages: bool = True,
    child_limit: int = 6,
    message_limit: int = 12,
) -> Dict[str, Any]:
    task = storage.tasks.get_by_id(task_id)
    if not task:
        raise ValueError(f"Task not found: {task_id}")

    attempts = storage.attempts.get_by_task_id(task_id)
    children = (
        storage.session.query(TaskModel)
        .filter(TaskModel.parent_task_id == task_id)
        .order_by(TaskModel.updated_at.desc())
        .limit(max(1, child_limit))
        .all()
    )
    messages: List[MessageModel] = []
    if include_session_messages and task.session_id:
        session_messages = storage.messages.get_all(session_id=task.session_id)
        messages = [msg for msg in session_messages if msg.associated_task_id == task_id][-message_limit:]

    return {
        "task": _task_payload(task),
        "attempts": [_attempt_payload(attempt) for attempt in attempts[:6]],
        "attempt_count": len(attempts),
        "child_tasks": [_task_payload(child) for child in children],
        "message_count": len(messages),
        "messages": [_message_payload(message) for message in messages],
        "associated_reports": list((task.constraints or {}).get("associated_reports") or []),
    }


def build_session_trace_summary(
    storage,
    *,
    session_id: str,
    message_limit: int = 20,
    task_limit: int = 10,
) -> Dict[str, Any]:
    messages = storage.messages.get_all(session_id=session_id)
    tasks = (
        storage.session.query(TaskModel)
        .filter(TaskModel.session_id == session_id)
        .order_by(TaskModel.updated_at.desc())
        .limit(max(1, task_limit))
        .all()
    )
    return {
        "session_id": session_id,
        "message_count": len(messages),
        "messages": [_message_payload(message) for message in messages[-message_limit:]],
        "tasks": [_task_payload(task) for task in tasks],
    }


def build_trace_summary(
    *,
    trace_kind: str,
    storage=None,
    trace_payload: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
    candidate_change_id: Optional[str] = None,
    baseline_change_id: Optional[str] = None,
    benchmark_reports: Optional[List[Dict[str, Any]]] = None,
    structured_reports: Optional[List[Dict[str, Any]]] = None,
    suite_name: Optional[str] = None,
    include_session_messages: bool = True,
) -> Dict[str, Any]:
    normalized_kind = str(trace_kind or "").strip() or "generic_trace"
    if normalized_kind == "eval_trace":
        return build_eval_trace_summary(
            candidate_change_id=str(candidate_change_id or "candidate"),
            baseline_change_id=str(baseline_change_id or "baseline"),
            benchmark_reports=benchmark_reports,
            structured_reports=structured_reports,
            suite_name=suite_name,
        )
    if normalized_kind == "task_trace":
        if storage is None or not task_id:
            raise ValueError("task_trace requires storage and task_id")
        return build_task_trace_summary(
            storage,
            task_id=task_id,
            include_session_messages=include_session_messages,
        )
    if normalized_kind == "session_trace":
        if storage is None or not session_id:
            raise ValueError("session_trace requires storage and session_id")
        return build_session_trace_summary(storage, session_id=session_id)
    if normalized_kind == "generic_trace":
        return dict(trace_payload or {})
    raise ValueError(f"Unsupported trace_kind: {normalized_kind}")


def _reviewer_context(reviewer_tier: str, *, run_id: str, candidate_change_id: Optional[str]) -> Any:
    tier = str(reviewer_tier or "strong").strip().lower()
    if tier == "weak":
        return WeakExecutionContext(
            run_id=run_id,
            candidate_change_id=candidate_change_id,
            evaluation_run=True,
        )
    return StrongExecutionContext(
        run_id=run_id,
        candidate_change_id=candidate_change_id,
    )


def _trace_focus(trace_kind: str) -> str:
    kind = str(trace_kind or "generic_trace")
    if kind == "task_trace":
        return (
            "Focus on whether the task made progress, used the right tools, got stuck, or needs a bounded system-side fix."
        )
    if kind == "session_trace":
        return (
            "Focus on whether the conversation stayed aligned, asked the right questions, and used tools or memory appropriately."
        )
    if kind == "eval_trace":
        return (
            "Focus on weak-model failure modes, harness mis-teaching, and the smallest system-side change likely to improve downstream performance."
        )
    return (
        "Focus on failure modes, avoid vague advice, and recommend bounded interventions that can be tested."
    )


def _normalize_review_fields(review: Dict[str, Any], *, trace_kind: str) -> Dict[str, Any]:
    normalized = dict(review)
    normalized["failure_family"] = str(
        normalized.get("failure_family") or normalized.get("primary_failure_mode") or "unknown"
    ).strip()
    predicted_outcome = str(normalized.get("predicted_outcome") or "uncertain").strip().lower() or "uncertain"
    if predicted_outcome not in {"improve", "regress", "neutral", "uncertain"}:
        predicted_outcome = "uncertain"
    normalized["predicted_outcome"] = predicted_outcome
    try:
        normalized["confidence"] = max(0.0, min(1.0, float(normalized.get("confidence", 0.5) or 0.5)))
    except Exception:
        normalized["confidence"] = 0.5
    try:
        normalized["expected_value"] = float(normalized.get("expected_value", 0.0) or 0.0)
    except Exception:
        normalized["expected_value"] = 0.0
    normalized["risk"] = dict(normalized.get("risk") or {})
    normalized["predicted_delta"] = {
        str(key): float(value or 0.0)
        for key, value in dict(normalized.get("predicted_delta") or {}).items()
    }
    normalized["domains_affected"] = [
        str(item)
        for item in (normalized.get("domains_affected") or [trace_kind])
        if str(item).strip()
    ]
    normalized["rationale"] = str(normalized.get("rationale") or normalized.get("summary") or "").strip()
    return normalized


async def review_trace(
    model_adapter,
    *,
    trace_kind: str,
    trace_summary: Dict[str, Any],
    reviewer_tier: str = "strong",
    candidate_change_id: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_kind = str(trace_kind or "generic_trace").strip() or "generic_trace"
    run_id = f"trace_review_{normalized_kind}_{candidate_change_id or 'ad_hoc'}"
    prompt = f"""
You are Strata's trace reviewer.
Your job is to review this trace, judge it, and suggest targeted interventions.

Trace kind: {normalized_kind}
Reviewer tier: {str(reviewer_tier or 'strong').lower()}
{_trace_focus(normalized_kind)}

Return only JSON with this schema:
{{
  "summary": "one paragraph",
  "overall_assessment": "short verdict",
  "failure_family": "reusable family label",
  "primary_failure_mode": "short label",
  "evidence": ["specific observation", "specific observation"],
  "targeted_interventions": [
    {{
      "kind": "prompt|tooling|routing|telemetry|spec|context|other",
      "target": "what should change",
      "description": "bounded intervention",
      "priority": "high|medium|low"
    }}
  ],
  "predicted_outcome": "improve|regress|neutral|uncertain",
  "predicted_delta": {{"metric_name": 0.0}},
  "confidence": 0.0,
  "expected_value": 0.0,
  "risk": {{"regression": 0.0, "overfit": 0.0, "instability": 0.0}},
  "domains_affected": ["eval"],
  "rationale": "why you expect this outcome",
  "telemetry_to_watch": ["metric or trace to watch"]
}}

Trace summary:
{json.dumps(trace_summary, indent=2)}
""".strip()

    model_adapter.bind_execution_context(
        _reviewer_context(
            reviewer_tier,
            run_id=run_id,
            candidate_change_id=candidate_change_id,
        )
    )
    try:
        response = await model_adapter.chat([{"role": "user", "content": prompt}], temperature=0.1)
        raw_content = response.get("content", "")
        review = _normalize_review_fields(_extract_json_object(raw_content), trace_kind=normalized_kind)
        review["status"] = "ok"
        review["trace_kind"] = normalized_kind
        review["reviewer_tier"] = str(reviewer_tier or "strong").lower()
        review["recorded_at"] = datetime.now(timezone.utc).isoformat()
        review["trace_summary"] = trace_summary
        review["raw_response"] = _clip(raw_content, 1600)
        return review
    except Exception as exc:
        return {
            "status": "unavailable",
            "trace_kind": normalized_kind,
            "reviewer_tier": str(reviewer_tier or "strong").lower(),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "summary": "Trace review could not be completed.",
            "overall_assessment": "review_unavailable",
            "failure_family": "review_unavailable",
            "primary_failure_mode": "review_unavailable",
            "evidence": [str(exc)],
            "targeted_interventions": [],
            "predicted_outcome": "uncertain",
            "predicted_delta": {},
            "expected_value": 0.0,
            "risk": {},
            "domains_affected": [normalized_kind],
            "rationale": "",
            "telemetry_to_watch": [],
            "confidence": 0.0,
            "trace_summary": trace_summary,
            "error": str(exc),
        }


def append_trace_review_to_task(storage, *, task_id: str, review: Dict[str, Any], limit: int = 10) -> bool:
    task = storage.tasks.get_by_id(task_id)
    if not task:
        return False
    constraints = dict(task.constraints or {})
    reviews = list(constraints.get("trace_reviews") or [])
    slim_review = {
        "recorded_at": review.get("recorded_at"),
        "trace_kind": review.get("trace_kind"),
        "reviewer_tier": review.get("reviewer_tier"),
        "overall_assessment": review.get("overall_assessment"),
        "primary_failure_mode": review.get("primary_failure_mode"),
        "summary": review.get("summary"),
        "targeted_interventions": review.get("targeted_interventions") or [],
        "telemetry_to_watch": review.get("telemetry_to_watch") or [],
    }
    reviews.append(slim_review)
    constraints["trace_reviews"] = reviews[-max(1, limit):]
    task.constraints = constraints
    return True
