"""
@module experimental.trace_review
@purpose Build compact summaries of arbitrary traces and review them with either the strong or weak tier.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from strata.api.message_feedback import list_message_feedback_events
from strata.feedback.signals import get_feedback_signal, register_feedback_signal
from strata.schemas.execution import TrainerExecutionContext, AgentExecutionContext
from strata.storage.models import AttemptObservabilityArtifactModel, MessageModel, TaskModel


def _clip(text: Any, limit: int = 240) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."

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


def _attempt_observability_payload(artifact: AttemptObservabilityArtifactModel) -> Dict[str, Any]:
    payload = dict(artifact.payload or {})
    summary = ""
    if artifact.artifact_kind == "failure_autopsy":
        evidence = dict(payload.get("evidence") or {})
        failure_kind = str(evidence.get("failure_kind") or payload.get("failure_kind") or "").strip()
        reason = _clip(payload.get("reason"), 180)
        summary = " | ".join(part for part in [failure_kind, reason] if part)
    elif artifact.artifact_kind == "plan_review":
        plan_health = str(payload.get("plan_health") or "").strip()
        recommendation = str(payload.get("recommendation") or "").strip()
        rationale = _clip(payload.get("rationale"), 180)
        summary = " | ".join(part for part in [plan_health, recommendation, rationale] if part)
    elif artifact.artifact_kind == "terminal_tool_call":
        tool_name = str(((payload.get("tool_call") or {}).get("name")) or "").strip()
        preview = _clip(payload.get("tool_result_preview"), 180)
        next_step_hint = _clip(payload.get("next_step_hint"), 180)
        summary = " | ".join(part for part in [tool_name, preview, next_step_hint] if part)
    return {
        "artifact_id": artifact.id,
        "task_id": artifact.task_id,
        "attempt_id": artifact.attempt_id,
        "session_id": artifact.session_id,
        "artifact_kind": artifact.artifact_kind,
        "created_at": _iso(artifact.created_at),
        "summary": summary,
        "payload": payload,
    }


def list_attempt_observability_artifacts(
    storage,
    *,
    task_id: Optional[str] = None,
    attempt_id: Optional[str] = None,
    session_id: Optional[str] = None,
    artifact_kind: Optional[str] = None,
    limit: int = 25,
) -> List[Dict[str, Any]]:
    if not hasattr(storage, "session"):
        return []
    query = storage.session.query(AttemptObservabilityArtifactModel)
    if task_id:
        query = query.filter(AttemptObservabilityArtifactModel.task_id == task_id)
    if attempt_id:
        query = query.filter(AttemptObservabilityArtifactModel.attempt_id == attempt_id)
    if session_id:
        query = query.filter(AttemptObservabilityArtifactModel.session_id == session_id)
    if artifact_kind:
        query = query.filter(AttemptObservabilityArtifactModel.artifact_kind == artifact_kind)
    rows = (
        query
        .order_by(AttemptObservabilityArtifactModel.created_at.desc(), AttemptObservabilityArtifactModel.id.desc())
        .limit(max(1, min(limit, 200)))
        .all()
    )
    return [_attempt_observability_payload(row) for row in rows]


def build_attempt_intelligence(
    storage,
    *,
    task: TaskModel,
    attempt_id: Optional[str] = None,
    recent_attempt_limit: int = 4,
    recent_artifact_limit: int = 6,
) -> Dict[str, Any]:
    attempts = storage.attempts.get_by_task_id(task.task_id)
    recent_attempts = []
    branch_failure_kinds: Dict[str, int] = {}
    branch_failure_count = 0
    for attempt in attempts[: max(1, recent_attempt_limit)]:
        evidence = dict(getattr(attempt, "evidence", {}) or {})
        autopsy = dict(evidence.get("autopsy") or {}) if isinstance(evidence.get("autopsy"), dict) else {}
        failure_kind = str(evidence.get("failure_kind") or autopsy.get("failure_kind") or "").strip()
        if getattr(getattr(attempt, "outcome", None), "value", None) == "failed":
            branch_failure_count += 1
            if failure_kind:
                branch_failure_kinds[failure_kind] = branch_failure_kinds.get(failure_kind, 0) + 1
        recent_attempts.append(
            {
                "attempt_id": attempt.attempt_id,
                "outcome": getattr(attempt.outcome, "value", None),
                "resolution": getattr(attempt.resolution, "value", None),
                "failure_kind": failure_kind,
                "reason": _clip(attempt.reason, 180),
            }
        )

    lineage_iteration_failures = 0
    visited = set()
    current = task
    while current and getattr(current, "task_id", None) and current.task_id not in visited:
        visited.add(current.task_id)
        current_attempts = storage.attempts.get_by_task_id(current.task_id)
        for attempt in current_attempts:
            evidence = dict(getattr(attempt, "evidence", {}) or {})
            failure_kind = str(evidence.get("failure_kind") or "").strip()
            reason = str(getattr(attempt, "reason", "") or "").lower()
            if failure_kind == "iteration_budget_exhausted" or "iteration limit reached" in reason:
                lineage_iteration_failures += 1
        parent_task_id = getattr(current, "parent_task_id", None)
        current = storage.tasks.get_by_id(parent_task_id) if parent_task_id else None

    artifacts = list_attempt_observability_artifacts(
        storage,
        task_id=task.task_id,
        attempt_id=attempt_id,
        limit=max(1, recent_artifact_limit),
    )
    recent_artifacts = [
        {
            "artifact_kind": item.get("artifact_kind"),
            "summary": str(item.get("summary") or ""),
            "created_at": item.get("created_at"),
        }
        for item in artifacts
    ]

    top_failure_kinds = [
        {"failure_kind": kind, "count": count}
        for kind, count in sorted(branch_failure_kinds.items(), key=lambda item: (-item[1], item[0]))
    ]

    return {
        "task_id": task.task_id,
        "attempt_id": attempt_id,
        "branch_failure_count": branch_failure_count,
        "lineage_iteration_failures": lineage_iteration_failures,
        "top_failure_kinds": top_failure_kinds,
        "recent_attempts": recent_attempts,
        "recent_artifacts": recent_artifacts,
    }


def render_attempt_intelligence(intelligence: Dict[str, Any]) -> str:
    if not isinstance(intelligence, dict) or not intelligence:
        return "No recent attempt intelligence available."

    lines = [
        "Attempt Intelligence:",
        f"- Branch failure count: {int(intelligence.get('branch_failure_count', 0) or 0)}",
        f"- Lineage iteration failures: {int(intelligence.get('lineage_iteration_failures', 0) or 0)}",
    ]
    failure_kinds = intelligence.get("top_failure_kinds") or []
    if failure_kinds:
        formatted = ", ".join(
            f"{item.get('failure_kind')} x{int(item.get('count', 0) or 0)}"
            for item in failure_kinds[:3]
            if item.get("failure_kind")
        )
        if formatted:
            lines.append(f"- Frequent failure kinds: {formatted}")

    recent_attempts = intelligence.get("recent_attempts") or []
    if recent_attempts:
        lines.append("- Recent attempts:")
        for item in recent_attempts[:4]:
            lines.append(
                "  "
                + " | ".join(
                    part
                    for part in [
                        str(item.get("outcome") or "unknown"),
                        str(item.get("failure_kind") or "").strip(),
                        str(item.get("resolution") or "").strip(),
                        str(item.get("reason") or "").strip(),
                    ]
                    if part
                )
            )

    recent_artifacts = intelligence.get("recent_artifacts") or []
    if recent_artifacts:
        lines.append("- Recent autopsy/review artifacts:")
        for item in recent_artifacts[:4]:
            summary = str(item.get("summary") or "").strip()
            kind = str(item.get("artifact_kind") or "").strip()
            if summary or kind:
                lines.append("  " + " | ".join(part for part in [kind, summary] if part))

    return "\n".join(lines)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _collect_missing_file_claims(task: TaskModel, attempts: List[Any], messages: List[MessageModel]) -> List[str]:
    candidates = [
        str(task.title or ""),
        str(task.description or ""),
        *[str((attempt.reason or "")) for attempt in attempts],
        *[str((message.content or "")) for message in messages],
    ]
    claims: List[str] = []
    patterns = [
        re.compile(r"`([^`]+)`"),
        re.compile(r"([./][A-Za-z0-9_./-]+\.[A-Za-z0-9_-]+)"),
    ]
    for text in candidates:
        lowered = text.lower()
        if "missing" not in lowered and "does not exist" not in lowered and "contains neither" not in lowered:
            continue
        for pattern in patterns:
            for match in pattern.findall(text):
                candidate = str(match or "").strip()
                if not candidate:
                    continue
                if candidate.endswith("/"):
                    continue
                if candidate not in claims:
                    claims.append(candidate)
    return claims


def _build_repo_fact_checks(task: TaskModel, attempts: List[Any], messages: List[MessageModel]) -> List[Dict[str, Any]]:
    constraints = dict(task.constraints or {})
    repo_root = _repo_root()
    candidate_paths: List[str] = []
    for path in constraints.get("spec_paths") or []:
        normalized = str(path or "").strip()
        if normalized and normalized not in candidate_paths:
            candidate_paths.append(normalized)
    for path in _collect_missing_file_claims(task, attempts, messages):
        if path not in candidate_paths:
            candidate_paths.append(path)

    checks: List[Dict[str, Any]] = []
    for rel_path in candidate_paths:
        try:
            resolved = (repo_root / rel_path).resolve()
            exists = resolved.exists() and str(resolved).startswith(str(repo_root))
        except Exception:
            resolved = repo_root / rel_path
            exists = False
        checks.append(
            {
                "path": rel_path,
                "exists": bool(exists),
                "is_file": bool(exists and resolved.is_file()),
            }
        )
    return checks


def _extract_attempt_note_paths(attempts: List[Any]) -> List[str]:
    note_paths: List[str] = []
    for attempt in attempts:
        reason = str(getattr(attempt, "reason", "") or "")
        for match in re.findall(r"(\.?/?\.knowledge/[A-Za-z0-9_./-]+\.md)", reason):
            normalized = str(match or "").strip()
            if normalized.startswith("./"):
                normalized = normalized[2:]
            if normalized and normalized not in note_paths:
                note_paths.append(normalized)
    return note_paths


def _build_attempt_note_excerpts(attempts: List[Any], *, char_limit: int = 1200) -> List[Dict[str, Any]]:
    repo_root = _repo_root()
    excerpts: List[Dict[str, Any]] = []
    for rel_path in _extract_attempt_note_paths(attempts):
        try:
            resolved = (repo_root / rel_path).resolve()
            if not str(resolved).startswith(str(repo_root)) or not resolved.exists() or not resolved.is_file():
                continue
            content = resolved.read_text(encoding="utf-8")
        except Exception:
            continue
        excerpt = content[:char_limit]
        if len(content) > char_limit:
            excerpt += "..."
        excerpts.append({"path": rel_path, "excerpt": excerpt})
    return excerpts


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
    repo_fact_checks = _build_repo_fact_checks(task, attempts, messages)
    attempt_note_excerpts = _build_attempt_note_excerpts(attempts)
    observability_artifacts = (
        storage.session.query(AttemptObservabilityArtifactModel)
        .filter(AttemptObservabilityArtifactModel.task_id == task_id)
        .order_by(AttemptObservabilityArtifactModel.created_at.desc(), AttemptObservabilityArtifactModel.id.desc())
        .limit(12)
        .all()
    )

    return {
        "task": _task_payload(task),
        "attempts": [_attempt_payload(attempt) for attempt in attempts[:6]],
        "attempt_count": len(attempts),
        "child_tasks": [_task_payload(child) for child in children],
        "message_count": len(messages),
        "messages": [_message_payload(message) for message in messages],
        "associated_reports": list((task.constraints or {}).get("associated_reports") or []),
        "repo_fact_checks": repo_fact_checks,
        "attempt_note_excerpts": attempt_note_excerpts,
        "observability_artifacts": [_attempt_observability_payload(item) for item in observability_artifacts],
    }


def build_session_trace_summary(
    storage,
    *,
    session_id: str,
    message_limit: int = 20,
    task_limit: int = 10,
    feedback_limit: int = 20,
) -> Dict[str, Any]:
    messages = storage.messages.get_all(session_id=session_id)
    feedback_events = list_message_feedback_events(storage, session_id=session_id, limit=feedback_limit)
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
        "feedback_event_count": len(feedback_events),
        "feedback_events": [
            {
                "event_id": event.get("event_id"),
                "action": event.get("action"),
                "reaction": event.get("reaction"),
                "message_id": event.get("message_id"),
                "message_role": event.get("message_role"),
                "message_preview": _clip(event.get("message_preview"), 220),
                "created_at": event.get("created_at"),
                "distillation_status": event.get("distillation_status"),
            }
            for event in feedback_events
        ],
        "feedback_summaries": [
            f"{event.get('action')} {event.get('reaction')} on '{_clip(event.get('message_preview'), 120)}'"
            for event in feedback_events
        ],
        "tasks": [_task_payload(task) for task in tasks],
    }


def build_feedback_signal_trace_summary(storage, *, signal_id: str) -> Dict[str, Any]:
    signal = get_feedback_signal(storage, signal_id)
    if not signal:
        raise ValueError(f"Feedback signal not found: {signal_id}")
    prioritization = dict(signal.get("prioritization") or {})
    return {
        "signal_id": signal.get("signal_id"),
        "source_type": signal.get("source_type"),
        "source_id": signal.get("source_id"),
        "session_id": signal.get("session_id"),
        "signal_kind": signal.get("signal_kind"),
        "signal_value": signal.get("signal_value"),
        "source_actor": signal.get("source_actor"),
        "source_preview": _clip(signal.get("source_preview"), 320),
        "note": _clip(signal.get("note"), 320),
        "expected_outcome": signal.get("expected_outcome"),
        "observed_outcome": signal.get("observed_outcome"),
        "status": signal.get("status"),
        "created_at": signal.get("created_at"),
        "prioritization": prioritization,
        "surprise_score": prioritization.get("surprise_score"),
        "alignment_risk": prioritization.get("alignment_risk"),
        "target_surface": prioritization.get("target_surface"),
        "metadata": dict(signal.get("metadata") or {}),
    }


def build_trace_summary(
    *,
    trace_kind: str,
    storage=None,
    trace_payload: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
    signal_id: Optional[str] = None,
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
    if normalized_kind in {"feedback_signal_trace", "signal_trace", "reflection_trace"}:
        if storage is None or not signal_id:
            raise ValueError(f"{normalized_kind} requires storage and signal_id")
        return build_feedback_signal_trace_summary(storage, signal_id=signal_id)
    if normalized_kind == "generic_trace":
        return dict(trace_payload or {})
    raise ValueError(f"Unsupported trace_kind: {normalized_kind}")


def _reviewer_context(reviewer_tier: str, *, run_id: str, candidate_change_id: Optional[str]) -> Any:
    tier = str(reviewer_tier or "trainer").strip().lower()
    if tier == "agent":
        return AgentExecutionContext(
            run_id=run_id,
            candidate_change_id=candidate_change_id,
            evaluation_run=True,
        )
    return TrainerExecutionContext(
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
            "Focus on whether the conversation stayed aligned, asked the right questions, used tools or memory appropriately, and responded well to explicit user feedback such as reactions or correction signals."
        )
    if kind in {"feedback_signal_trace", "signal_trace", "reflection_trace"}:
        return (
            "Focus on whether this internal attention or surprise signal was well-calibrated, whether the system was right to notice it, and what model, policy, or expectation should change in response."
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


def _apply_repo_fact_check_overrides(review: Dict[str, Any], trace_summary: Dict[str, Any]) -> Dict[str, Any]:
    checks = list((trace_summary or {}).get("repo_fact_checks") or [])
    existing_paths = {
        str(item.get("path") or "").strip()
        for item in checks
        if item.get("exists")
    }
    if not existing_paths:
        return review

    text_fragments = [
        str((trace_summary.get("task") or {}).get("title") or ""),
        str((trace_summary.get("task") or {}).get("description") or ""),
        *[str((attempt or {}).get("reason") or "") for attempt in (trace_summary.get("attempts") or [])],
        *[str((message or {}).get("content") or "") for message in (trace_summary.get("messages") or [])],
        *[str((item or {}).get("excerpt") or "") for item in (trace_summary.get("attempt_note_excerpts") or [])],
    ]
    contradiction_paths = [
        path
        for path in sorted(existing_paths)
        if any(
            path in fragment
            and any(phrase in fragment.lower() for phrase in ("missing", "does not exist", "contains neither"))
            for fragment in text_fragments
        )
    ]
    if not contradiction_paths:
        return review

    evidence = list(review.get("evidence") or [])
    evidence.append(
        "Deterministic repo fact-check found these paths exist despite the trace claiming they were missing: "
        + ", ".join(contradiction_paths)
    )
    interventions = list(review.get("targeted_interventions") or [])
    interventions.append(
        {
            "kind": "context",
            "target": "alignment and research grounding",
            "description": (
                "Reject stale missing-file premises when deterministic repo checks show the canonical spec files exist; "
                "require exact file inspection before escalating."
            ),
            "priority": "high",
        }
    )
    review["overall_assessment"] = "needs_intervention"
    review["failure_family"] = "false_premise"
    review["primary_failure_mode"] = "repo_fact_miss"
    review["evidence"] = evidence
    review["targeted_interventions"] = interventions
    rationale = str(review.get("rationale") or "").strip()
    review["rationale"] = (
        rationale + " " if rationale else ""
    ) + "The trace premise conflicts with deterministic repo state, so the task should be corrected before retry."
    telemetry = list(review.get("telemetry_to_watch") or [])
    if "repo_fact_mismatch_rate" not in telemetry:
        telemetry.append("repo_fact_mismatch_rate")
    review["telemetry_to_watch"] = telemetry
    return review


def _collect_verifier_reviews(trace_summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    reviews: List[Dict[str, Any]] = []
    task_constraints = dict(((trace_summary.get("task") or {}).get("constraints") or {}))
    for item in task_constraints.get("verifier_reviews") or []:
        if isinstance(item, dict):
            reviews.append(dict(item))
    for attempt in trace_summary.get("attempts") or []:
        verifier = dict(((attempt or {}).get("artifacts") or {}).get("verifier") or {})
        if verifier:
            reviews.append(verifier)
    for artifact in trace_summary.get("observability_artifacts") or []:
        if str((artifact or {}).get("artifact_kind") or "").strip().lower() != "verifier_review":
            continue
        payload = dict((artifact or {}).get("payload") or {})
        if payload:
            reviews.append(payload)
    return reviews


def _collect_prior_trace_reviews(trace_summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    task_constraints = dict(((trace_summary.get("task") or {}).get("constraints") or {}))
    rows = []
    for item in task_constraints.get("trace_reviews") or []:
        if isinstance(item, dict):
            rows.append(dict(item))
    return rows


def _apply_verifier_supervision_overrides(review: Dict[str, Any], trace_summary: Dict[str, Any]) -> Dict[str, Any]:
    verifier_reviews = _collect_verifier_reviews(trace_summary)
    if not verifier_reviews:
        return review

    flawed_or_uncertain = [
        item
        for item in verifier_reviews
        if str(item.get("verdict") or "").strip().lower() in {"flawed", "uncertain"}
    ]
    if not flawed_or_uncertain:
        return review

    prior_trace_reviews = _collect_prior_trace_reviews(trace_summary)
    prior_review_unavailable = any(
        str(item.get("overall_assessment") or "").strip().lower() == "review_unavailable"
        for item in prior_trace_reviews
    )
    current_assessment = str(review.get("overall_assessment") or "").strip().lower()
    current_failure_mode = str(review.get("primary_failure_mode") or review.get("failure_family") or "").strip().lower()

    if current_failure_mode in {"uncorrected_verifier_failures", "trainer_supervision_gap"}:
        return review
    supervision_gap_signal = prior_review_unavailable or len(flawed_or_uncertain) >= 2
    if not supervision_gap_signal and current_assessment in {"needs_intervention", "misaligned", "degraded"} and current_failure_mode not in {
        "review_unavailable",
        "unknown",
    }:
        return review

    evidence = list(review.get("evidence") or [])
    evidence.append(
        f"Verifier flagged {len(flawed_or_uncertain)} recent attempt(s) as flawed or uncertain before this trainer review."
    )
    if prior_review_unavailable:
        evidence.append("A prior trainer trace review already degraded to review_unavailable on this branch.")

    interventions = list(review.get("targeted_interventions") or [])
    interventions.append(
        {
            "kind": "routing",
            "target": "trainer supervision policy",
            "description": (
                "When verifier findings repeatedly mark a branch flawed or uncertain, interrupt passive retry loops and "
                "force a trainer-authored corrective plan or user-facing clarification."
            ),
            "priority": "high",
        }
    )
    interventions.append(
        {
            "kind": "tooling",
            "target": "trace review robustness",
            "description": (
                "Treat repeated verifier failures as first-class supervision evidence, and keep trainer review available "
                "even when model output is partially structured or noisy."
            ),
            "priority": "high",
        }
    )

    telemetry = list(review.get("telemetry_to_watch") or [])
    for metric in ["verifier_issue_rate", "trainer_correction_latency"]:
        if metric not in telemetry:
            telemetry.append(metric)

    review["overall_assessment"] = "needs_intervention"
    review["failure_family"] = "trainer_supervision_gap"
    review["primary_failure_mode"] = "uncorrected_verifier_failures"
    if not str(review.get("summary") or "").strip():
        review["summary"] = (
            "Verifier findings repeatedly marked this branch flawed or uncertain, but trainer supervision did not "
            "convert those findings into a concrete correction."
        )
    review["evidence"] = evidence
    review["targeted_interventions"] = interventions
    rationale = str(review.get("rationale") or "").strip()
    review["rationale"] = (
        rationale + " " if rationale else ""
    ) + "Repeated verifier concerns without a concrete trainer correction indicate a supervision gap, not just a task-level retry problem."
    review["telemetry_to_watch"] = telemetry
    return review


async def review_trace(
    model_adapter,
    *,
    trace_kind: str,
    trace_summary: Dict[str, Any],
    reviewer_tier: str = "trainer",
    candidate_change_id: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_kind = str(trace_kind or "generic_trace").strip() or "generic_trace"
    run_id = f"trace_review_{normalized_kind}_{candidate_change_id or 'ad_hoc'}"
    prompt = f"""
You are Strata's trace reviewer.
Your job is to review this trace, judge it, and suggest targeted interventions.
You are acting as an investigator, not a passive summarizer.

Trace kind: {normalized_kind}
Reviewer tier: {str(reviewer_tier or 'trainer').lower()}
{_trace_focus(normalized_kind)}

Rules:
- Treat prior plan reviews, verifier outputs, and model claims as hypotheses to test against the trace, not facts to inherit.
- If verifier artifacts already indicate repeated flawed or uncertain outputs, explain why supervision failed to correct course.
- Prefer bounded system-side corrections over generic advice like "retry" or "be more careful."

Return only JSON with this schema:
{{
  "summary": "one paragraph",
  "overall_assessment": "short verdict",
  "failure_family": "reusable family label",
  "primary_failure_mode": "short label",
  "recommended_title": "optional 2-6 word session title if a rename would help, else empty string",
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
        parsed = model_adapter.extract_structured_object(raw_content)
        parse_error = str((parsed or {}).get("error") or "").strip()
        if parse_error:
            parsed = {
                "summary": "Trace review model did not return usable structured output.",
                "overall_assessment": "review_unavailable",
                "failure_family": "review_unavailable",
                "primary_failure_mode": "review_unavailable",
                "evidence": [parse_error],
                "targeted_interventions": [],
                "predicted_outcome": "uncertain",
                "predicted_delta": {},
                "confidence": 0.0,
                "expected_value": 0.0,
                "risk": {},
                "domains_affected": [normalized_kind],
                "rationale": "",
                "telemetry_to_watch": [],
            }
        review = _normalize_review_fields(parsed, trace_kind=normalized_kind)
        review = _apply_repo_fact_check_overrides(review, trace_summary)
        review = _apply_verifier_supervision_overrides(review, trace_summary)
        review["status"] = "unavailable" if review.get("overall_assessment") == "review_unavailable" else "ok"
        review["trace_kind"] = normalized_kind
        review["reviewer_tier"] = str(reviewer_tier or "trainer").lower()
        review["recorded_at"] = datetime.now(timezone.utc).isoformat()
        review["trace_summary"] = trace_summary
        review["raw_response"] = _clip(raw_content, 1600)
        return review
    except Exception as exc:
        return {
            "status": "unavailable",
            "trace_kind": normalized_kind,
            "reviewer_tier": str(reviewer_tier or "trainer").lower(),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "summary": "Trace review could not be completed.",
            "overall_assessment": "review_unavailable",
            "failure_family": "review_unavailable",
            "primary_failure_mode": "review_unavailable",
            "recommended_title": "",
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


def emit_trace_review_attention_signal(
    storage,
    *,
    trace_kind: str,
    trace_summary: Dict[str, Any],
    review: Dict[str, Any],
    reviewer_tier: str,
    session_id: Optional[str] = None,
    task_id: Optional[str] = None,
    source_actor: str = "trace_reviewer",
) -> Optional[Dict[str, Any]]:
    normalized_kind = str(trace_kind or "generic_trace").strip() or "generic_trace"
    normalized_status = str(review.get("status") or "").strip().lower()
    normalized_assessment = str(review.get("overall_assessment") or "").strip().lower()
    confidence = float(review.get("confidence") or 0.0)
    interventions = list(review.get("targeted_interventions") or [])
    primary_failure_mode = str(
        review.get("primary_failure_mode") or review.get("failure_family") or "unknown"
    ).strip()
    summary = str(review.get("summary") or "").strip()

    signal_kind: Optional[str] = None
    signal_value = normalized_assessment or primary_failure_mode or normalized_kind
    expected_outcome = "healthy_trace"
    observed_outcome = signal_value

    if normalized_status != "ok" or normalized_assessment == "review_unavailable":
        signal_kind = "unexpected_failure"
        expected_outcome = "review_available"
        observed_outcome = normalized_assessment or "review_unavailable"
    elif normalized_assessment in {"needs_intervention", "misaligned", "blocked", "failed", "degraded"}:
        signal_kind = "surprise"
    elif interventions and confidence >= 0.6:
        signal_kind = "importance"
        expected_outcome = "stable_trace"
        observed_outcome = "intervention_opportunity"

    if not signal_kind:
        return None

    source_id = (
        str(task_id or "").strip()
        or str(session_id or "").strip()
        or str(review.get("timeline_artifact_id") or "").strip()
        or normalized_kind
    )
    source_type = (
        "task_review"
        if normalized_kind == "task_trace"
        else "session_review"
        if normalized_kind == "session_trace"
        else "eval_review"
        if normalized_kind == "eval_trace"
        else "trace_review"
    )
    preview = summary or f"{normalized_kind} review: {primary_failure_mode}"
    note = (
        f"{normalized_kind} reviewed by {str(reviewer_tier or 'trainer').lower()}; "
        f"assessment={normalized_assessment or 'unknown'}; "
        f"failure_mode={primary_failure_mode or 'unknown'}; "
        f"interventions={len(interventions)}."
    )

    return register_feedback_signal(
        storage,
        source_type=source_type,
        source_id=source_id,
        signal_kind=signal_kind,
        signal_value=signal_value,
        source_actor=source_actor,
        session_id=str(session_id or "").strip(),
        source_preview=preview,
        note=note,
        expected_outcome=expected_outcome,
        observed_outcome=observed_outcome,
        metadata={
            "trace_kind": normalized_kind,
            "reviewer_tier": str(reviewer_tier or "trainer").strip().lower(),
            "overall_assessment": normalized_assessment,
            "primary_failure_mode": primary_failure_mode,
            "confidence": confidence,
            "targeted_intervention_count": len(interventions),
            "domains_affected": list(review.get("domains_affected") or []),
            "timeline_artifact_id": review.get("timeline_artifact_id"),
            "audit_artifact_id": review.get("audit_artifact_id"),
            "trace_subject_id": (
                str(task_id or "").strip()
                or str(session_id or "").strip()
                or str((trace_summary or {}).get("candidate_change_id") or "").strip()
            ),
            "authority_kind": "spec_policy",
            "authority_ref": "trace_review_attention",
            "derived_from": [
                *([f"task:{task_id}"] if str(task_id or "").strip() else []),
                *([f"session:{session_id}"] if str(session_id or "").strip() else []),
                *([f"timeline:{review.get('timeline_artifact_id')}"] if review.get("timeline_artifact_id") else []),
            ],
            "governing_spec_refs": [
                ".knowledge/specs/constitution.md",
                ".knowledge/specs/project_spec.md",
                "docs/spec/step-runtime-flow.md",
            ],
        },
    )


def emit_recursive_audit_attention_signal(
    storage,
    *,
    artifact_type: str,
    artifact_id: str,
    audit_artifact: Dict[str, Any],
    source_actor: str = "audit_registry",
) -> Optional[Dict[str, Any]]:
    summary_verdict = dict(audit_artifact.get("summary_verdict") or {})
    verdict_status = str(summary_verdict.get("status") or "pass").strip().lower() or "pass"
    if verdict_status == "pass":
        return None

    signal_kind = "unexpected_failure" if verdict_status == "fail" else "surprise"
    return register_feedback_signal(
        storage,
        source_type="audit",
        source_id=str(audit_artifact.get("artifact_id") or artifact_id or "audit").strip(),
        signal_kind=signal_kind,
        signal_value=verdict_status,
        source_actor=source_actor,
        session_id="",
        source_preview=str(audit_artifact.get("rationale") or f"Recursive audit of {artifact_type}:{artifact_id}").strip(),
        note=f"Recursive audit of {artifact_type}:{artifact_id} produced summary verdict '{verdict_status}'.",
        expected_outcome="pass",
        observed_outcome=verdict_status,
        metadata={
            "artifact_type": str(artifact_type or "").strip().lower(),
            "artifact_id": str(artifact_id or "").strip(),
            "audit_artifact_id": audit_artifact.get("artifact_id"),
            "summary_verdict": summary_verdict,
        },
    )
