"""
@module orchestrator.background
@purpose Thin control loop for the Strata background worker.
@owns orchestrator.worker.*

This loop exists to keep execution discipline outside the model itself.
Instead of asking one model call to be reliable, Strata wraps work in
routing, retries, evaluation, telemetry, and resolution policies that a
small local model can benefit from.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Dict, Optional
from urllib.parse import urlparse, urlunparse
import httpx

from strata.core.lanes import infer_lane_from_task, normalize_lane
from strata.experimental.trace_review import list_attempt_observability_artifacts
from strata.feedback.signals import register_feedback_signal
from strata.storage.models import TaskModel, TaskState, AttemptOutcome, TaskType
from strata.orchestrator.worker.queue_recovery import recover_tasks
from strata.orchestrator.worker.idle_policy import run_idle_tasks
from strata.orchestrator.worker.telemetry import synthesize_model_performance
from strata.orchestrator.worker.attempt_runner import run_attempt
from strata.orchestrator.worker.resolution_policy import determine_resolution, apply_resolution
from strata.orchestrator.worker.plan_review import generate_plan_review
from strata.orchestrator.capability_incidents import (
    annotate_capability_incident,
    get_capability_incident,
    record_capability_incident,
)
from strata.observability.writer import enqueue_attempt_observability_artifact, flush_observability_writes
from strata.orchestrator.worker.routing_policy import select_model_tier
from strata.eval.job_runner import run_eval_job_task
from strata.experimental.verifier import (
    count_recent_verifier_mechanism_failures,
    emit_verifier_attention_signal,
    verify_task_output,
)
from strata.system_capabilities import bind_system_procedure, canonical_system_procedure_id
from strata.procedures.registry import build_procedure_task_constraints

logger = logging.getLogger(__name__)
LANE_NAMES = ("trainer", "agent")


def _models_endpoint_from_chat_endpoint(endpoint: str) -> str:
    parsed = urlparse(str(endpoint or "").strip())
    path = parsed.path or ""
    if path.endswith("/chat/completions"):
        path = path[: -len("/chat/completions")] + "/models"
    elif path.endswith("/completions"):
        path = path[: -len("/completions")] + "/models"
    elif not path.endswith("/models"):
        path = path.rstrip("/") + "/models"
    return urlunparse(parsed._replace(path=path))


def _candidate_endpoints_for_context(lane_model, ctx):
    pool = lane_model.registry.pools.get(ctx.mode)
    if pool is None:
        return []
    allow_cloud = pool.allow_cloud if ctx.allow_cloud is None else bool(ctx.allow_cloud)
    allow_local = pool.allow_local if ctx.allow_local is None else bool(ctx.allow_local)
    endpoints = []
    for endpoint in pool.endpoints:
        if endpoint.transport == "cloud" and not allow_cloud:
            continue
        if endpoint.transport == "local" and not allow_local:
            continue
        endpoints.append(endpoint)
    return endpoints


async def _preflight_local_model_catalog(lane_model, *, timeout_s: float = 3.0) -> tuple[bool, str]:
    endpoint = str(getattr(lane_model, "endpoint", "") or "").strip()
    model_id = str(getattr(lane_model, "active_model", "") or "").strip()
    if not endpoint or not model_id:
        return False, "local preflight missing endpoint or model"
    models_endpoint = _models_endpoint_from_chat_endpoint(endpoint)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s)) as client:
            response = await client.get(models_endpoint)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        return False, str(exc)
    catalog = list(payload.get("data") or [])
    if any(str(item.get("id") or "").strip() == model_id for item in catalog if isinstance(item, dict)):
        return True, ""
    return False, f"local model '{model_id}' not present in {models_endpoint}"


async def _preflight_lane_model(
    name: str,
    lane_model,
    ctx,
    *,
    timeout_s: float = 6.0,
    auto_swap_local_missing: bool = False,
    allow_endpoint_failover: bool = False,
) -> tuple[bool, str]:
    lane_model.bind_execution_context(ctx)
    endpoint = str(getattr(lane_model, "endpoint", "") or "").strip().lower()
    if endpoint.startswith("http://127.0.0.1:") or endpoint.startswith("http://localhost:"):
        ok, reason = await _preflight_local_model_catalog(lane_model, timeout_s=min(timeout_s, 3.0))
        if ok or not auto_swap_local_missing or "not present" not in reason:
            return ok, reason

        models_endpoint = _models_endpoint_from_chat_endpoint(getattr(lane_model, "endpoint", "") or "")
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(min(timeout_s, 3.0))) as client:
                response = await client.get(models_endpoint)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            return False, f"{reason}; auto-swap probe failed: {exc}"

        available_ids = [
            str(item.get("id") or "").strip()
            for item in list(payload.get("data") or [])
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        ]
        if not available_ids:
            return False, reason

        missing_model_id = str(getattr(lane_model, "active_model", "") or "").strip()
        lane_model.active_model = available_ids[0]
        logger.warning(
            "Local model '%s' for lane '%s' was unavailable; auto-swapping to '%s'.",
            missing_model_id or "unknown",
            name,
            available_ids[0],
        )
        return True, ""
    try:
        response = await asyncio.wait_for(
            lane_model.chat(
                [{"role": "user", "content": "ping"}],
                timeout=5.0,
                max_retries=1,
            ),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        ok, reason = False, f"{name} preflight timed out after {timeout_s:.1f}s"
    except Exception as exc:
        ok, reason = False, str(exc)
    else:
        if not isinstance(response, dict):
            ok, reason = False, f"{name} preflight returned invalid response type"
        elif str(response.get("status") or "").strip().lower() != "success":
            ok, reason = False, str(response.get("message") or response.get("content") or f"{name} preflight returned non-success status")
        else:
            return True, ""

    if not allow_endpoint_failover:
        return ok, reason

    original_model = str(getattr(lane_model, "active_model", "") or "").strip()
    for candidate in _candidate_endpoints_for_context(lane_model, ctx):
        candidate_model = str(getattr(candidate, "model", "") or "").strip()
        if not candidate_model or candidate_model == original_model:
            continue
        lane_model.active_model = candidate_model
        fallback_ok, fallback_reason = await _preflight_lane_model(
            name,
            lane_model,
            ctx,
            timeout_s=timeout_s,
            auto_swap_local_missing=auto_swap_local_missing,
            allow_endpoint_failover=False,
        )
        if fallback_ok:
            logger.warning(
                "Primary model '%s' for lane '%s' failed preflight; falling back to '%s'.",
                original_model or "unknown",
                name,
                candidate_model,
            )
            return True, ""
        reason = f"{reason}; fallback '{candidate_model}' failed: {fallback_reason}"

    lane_model.active_model = original_model
    return False, reason


def _parse_timestamp(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        normalized = text.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _latest_blocked_task_review_at(task: TaskModel) -> Optional[datetime]:
    constraints = dict(getattr(task, "constraints", {}) or {})
    latest = None
    for item in constraints.get("trace_reviews") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("trace_kind") or "").strip().lower() != "task_trace":
            continue
        if str(item.get("reviewer_tier") or "").strip().lower() != "trainer":
            continue
        recorded_at = _parse_timestamp(item.get("recorded_at"))
        if recorded_at and (latest is None or recorded_at > latest):
            latest = recorded_at
    return latest


def _latest_blocked_task_evidence_at(storage, task: TaskModel) -> Optional[datetime]:
    latest = None
    for attempt in storage.attempts.get_by_task_id(task.task_id) or []:
        for candidate in [getattr(attempt, "ended_at", None), getattr(attempt, "started_at", None)]:
            parsed = _parse_timestamp(candidate)
            if parsed and (latest is None or parsed > latest):
                latest = parsed
    for artifact in list_attempt_observability_artifacts(storage, task_id=task.task_id, limit=8):
        parsed = _parse_timestamp(artifact.get("created_at"))
        if parsed and (latest is None or parsed > latest):
            latest = parsed
    return latest


def _blocked_task_has_new_evidence_since_review(storage, task: TaskModel) -> bool:
    latest_review_at = _latest_blocked_task_review_at(task)
    if latest_review_at is None:
        return True
    latest_evidence_at = _latest_blocked_task_evidence_at(storage, task)
    if latest_evidence_at is None:
        return False
    return latest_evidence_at > latest_review_at


def resolution_from_plan_review(review: Optional[dict]):
    recommendation = str((review or {}).get("recommendation") or "").strip().lower()
    if recommendation not in {"decompose", "internal_replan", "abandon_to_parent"}:
        return None
    from strata.schemas.core import AttemptResolutionSchema

    return AttemptResolutionSchema(
        reasoning=str((review or {}).get("rationale") or f"Plan review recommended {recommendation}.").strip(),
        resolution=recommendation,
        new_subtasks=[],
    )


def emit_task_execution_attention_signal(
    storage,
    *,
    task,
    attempt,
    context,
    plan_review: Optional[dict] = None,
    error: Optional[BaseException] = None,
):
    prior_attempts = [
        row for row in (storage.attempts.get_by_task_id(task.task_id) or [])
        if str(getattr(row, "attempt_id", "")) != str(getattr(attempt, "attempt_id", ""))
    ]
    prior_failed = [row for row in prior_attempts if row.outcome == AttemptOutcome.FAILED]
    prior_succeeded = [row for row in prior_attempts if row.outcome == AttemptOutcome.SUCCEEDED]
    plan_review = dict(plan_review or {})
    plan_health = str(plan_review.get("plan_health") or "").strip().lower()
    recommendation = str(plan_review.get("recommendation") or "").strip().lower()
    outcome = getattr(attempt.outcome, "value", "").strip().lower() if attempt.outcome else ""

    signal_kind = None
    signal_value = outcome or "unknown"
    expected_outcome = "progress"
    observed_outcome = outcome or "unknown"
    note_bits = []

    if attempt.outcome == AttemptOutcome.FAILED and not prior_failed:
        signal_kind = "unexpected_failure"
        signal_value = "first_failure"
        observed_outcome = "failed"
        note_bits.append("Task failed on its first recorded failed attempt.")
    elif attempt.outcome == AttemptOutcome.SUCCEEDED and prior_failed:
        signal_kind = "unexpected_success"
        signal_value = "recovered_after_failures"
        expected_outcome = "continued_struggle"
        observed_outcome = "succeeded"
        note_bits.append(f"Task succeeded after {len(prior_failed)} prior failed attempt(s).")
    elif attempt.outcome == AttemptOutcome.FAILED and len(prior_failed) >= 2:
        signal_kind = "importance"
        signal_value = "repeated_failures"
        observed_outcome = "failed_repeatedly"
        note_bits.append(f"Task has {len(prior_failed) + 1} failed attempts.")
    elif attempt.outcome == AttemptOutcome.SUCCEEDED and (
        plan_health in {"degraded", "invalid"} or recommendation in {"decompose", "internal_replan", "abandon_to_parent"}
    ):
        signal_kind = "surprise"
        signal_value = "success_but_plan_degraded"
        expected_outcome = "stable_success"
        observed_outcome = "succeeded_but_needs_restructure"
        note_bits.append("Attempt succeeded, but plan review says the branch remains unhealthy.")

    if not signal_kind:
        return None

    usage = dict(getattr(attempt, "artifacts", {}) or {})
    model_id = f"{usage.get('provider', 'unknown')}/{usage.get('model', 'unknown')}"
    return register_feedback_signal(
        storage,
        source_type="task_execution",
        source_id=str(task.task_id),
        signal_kind=signal_kind,
        signal_value=signal_value,
        source_actor="background_worker",
        session_id=str(task.session_id or "").strip(),
        source_preview=str(task.title or task.description or f"Task {task.task_id}")[:220],
        note=" ".join(
            [
                part
                for part in [
                    *note_bits,
                    f"task_type={getattr(task.type, 'value', str(task.type))}",
                    f"context_mode={getattr(context, 'mode', 'unknown')}",
                    f"model_id={model_id}",
                    f"plan_health={plan_health or 'unknown'}",
                    f"recommendation={recommendation or 'unknown'}",
                    f"error={str(error)[:180] if error else ''}",
                ]
                if str(part).strip()
            ]
        )[:500],
        expected_outcome=expected_outcome,
        observed_outcome=observed_outcome,
        metadata={
            "task_id": task.task_id,
            "attempt_id": getattr(attempt, "attempt_id", None),
            "task_type": getattr(task.type, "value", str(task.type)),
            "context_mode": getattr(context, "mode", "unknown"),
            "candidate_change_id": getattr(context, "candidate_change_id", None),
            "run_mode": getattr(context, "run_mode", "weak_eval" if getattr(context, "evaluation_run", False) else "normal"),
            "plan_health": plan_health,
            "recommendation": recommendation,
            "prior_failed_attempts": len(prior_failed),
            "prior_succeeded_attempts": len(prior_succeeded),
            "error": str(error)[:220] if error else "",
        },
    )


async def queue_task_attention_review(storage, *, task, signal: dict) -> Optional[dict]:
    prioritization = dict((signal or {}).get("prioritization") or {})
    priority = str(prioritization.get("priority") or "").strip().lower()
    if priority not in {"review_soon", "urgent"}:
        return None
    try:
        from strata.api.main import _queue_eval_system_job
    except Exception as exc:
        logger.warning("Unable to import system-job queue helper for task attention review: %s", exc)
        return None

    session_id = str(task.session_id or "").strip() or None
    return await _queue_eval_system_job(
        storage,
        kind="trace_review",
        title=f"Task Attention Review: {str(task.title or task.task_id)[:80]}",
        description=f"Queued task trace review after {priority} task-execution attention signal.",
        payload={
            "trace_kind": "task_trace",
            "task_id": task.task_id,
            "reviewer_tier": "trainer",
            "emit_followups": True,
            "persist_to_task": True,
            "spec_scope": "project",
            "attention_signal_id": signal.get("signal_id"),
            "prioritization": prioritization,
            "source_task_id": task.task_id,
            "provenance": {
                "source_kind": "feedback_signal",
                "source_actor": str((signal or {}).get("source_actor") or "system"),
                "authority_kind": "spec_policy",
                "authority_ref": "feedback-prioritization",
                "derived_from": [f"signal:{signal.get('signal_id')}"] if signal.get("signal_id") else [f"task:{task.task_id}"],
                "governing_spec_refs": [
                    ".knowledge/specs/constitution.md",
                    ".knowledge/specs/project_spec.md",
                    "docs/spec/step-runtime-flow.md",
                ],
            },
        },
        session_id=session_id,
        dedupe_signature={
            "trace_kind": "task_trace",
            "reviewer_tier": "trainer",
            "task_id": task.task_id,
        },
    )


def _provenance_record(
    *,
    source_kind: str,
    source_actor: str,
    authority_kind: str,
    authority_ref: str,
    derived_from: list[str] | None = None,
    governing_spec_refs: list[str] | None = None,
    note: str = "",
) -> Dict[str, object]:
    payload: Dict[str, object] = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "source_kind": str(source_kind or "").strip(),
        "source_actor": str(source_actor or "").strip() or "system",
        "authority_kind": str(authority_kind or "").strip() or "spec_policy",
        "authority_ref": str(authority_ref or "").strip() or "unknown",
        "derived_from": list(derived_from or []),
        "governing_spec_refs": list(
            governing_spec_refs
            or [
                ".knowledge/specs/constitution.md",
                ".knowledge/specs/project_spec.md",
                "docs/spec/step-runtime-flow.md",
            ]
        ),
    }
    if str(note or "").strip():
        payload["note"] = str(note).strip()
    return payload


def _should_queue_direct_audit(verification: Dict[str, object]) -> bool:
    verdict = str(verification.get("verdict") or "").strip().lower()
    recommended_action = str(verification.get("recommended_action") or "").strip().lower()
    mechanism_failure_kind = str(verification.get("mechanism_failure_kind") or "").strip().lower()
    confidence = float(verification.get("confidence") or 0.0)
    if recommended_action == "audit":
        return True
    if verdict == "flawed" and confidence >= 0.8:
        return True
    if recommended_action == "escalate" and confidence >= 0.7:
        return True
    if mechanism_failure_kind:
        return True
    return False


def _incident_queue_latched(
    storage,
    *,
    incident_id: str,
    task_key: str,
) -> bool:
    normalized_incident_id = str(incident_id or "").strip()
    if not normalized_incident_id:
        return False
    incident = get_capability_incident(storage, incident_id=normalized_incident_id)
    if not incident or str(incident.get("status") or "").strip().lower() == "closed":
        return False
    metadata = dict(incident.get("metadata") or {})
    latched_task_id = str(metadata.get(task_key) or "").strip()
    if not latched_task_id:
        return False
    return True


async def queue_direct_audit_review(storage, *, task: TaskModel, verification: Dict[str, object]) -> Optional[dict]:
    try:
        from strata.api.main import _queue_eval_system_job
    except Exception as exc:
        logger.warning("Unable to import system-job queue helper for direct audit review: %s", exc)
        return None

    verdict = str(verification.get("verdict") or "uncertain").strip().lower() or "uncertain"
    mechanism_failure_kind = str(verification.get("mechanism_failure_kind") or "").strip().lower()
    reason_bits = [
        f"verdict={verdict}",
        f"recommended_action={str(verification.get('recommended_action') or '').strip().lower() or 'verify_more'}",
    ]
    if mechanism_failure_kind:
        reason_bits.append(f"mechanism_failure_kind={mechanism_failure_kind}")
    session_id = str(task.session_id or "").strip() or None
    incident_id = str(verification.get("incident_id") or "").strip()
    if incident_id and _incident_queue_latched(storage, incident_id=incident_id, task_key="audit_task_id"):
        return None
    derived_from = [
        f"task:{task.task_id}",
        *([f"attempt:{verification.get('attempt_id')}"] if verification.get("attempt_id") else []),
        *([f"incident:{incident_id}"] if incident_id else []),
    ]
    audit_task = await _queue_eval_system_job(
        storage,
        kind="trace_review",
        title=f"Audit Now: {str(task.title or task.task_id)[:84]}",
        description="Queued direct audit after a severe verifier outcome.",
        payload={
            "trace_kind": "task_trace",
            "task_id": task.task_id,
            "reviewer_tier": "trainer",
            "emit_followups": True,
            "persist_to_task": True,
            "spec_scope": "project",
            "audit_mode": "internal",
            "source_task_id": task.task_id,
            "trigger": "verifier_direct_audit",
            "verification_snapshot": {
                "attempt_id": verification.get("attempt_id"),
                "verdict": verification.get("verdict"),
                "confidence": verification.get("confidence"),
                "recommended_action": verification.get("recommended_action"),
                "failure_modes": list(verification.get("failure_modes") or []),
                "mechanism_failure_kind": mechanism_failure_kind,
                "incident_id": incident_id,
            },
            "provenance": _provenance_record(
                source_kind="verifier_review",
                source_actor="lightweight_verifier",
                authority_kind="spec_policy",
                authority_ref="verifier_direct_audit",
                derived_from=derived_from,
                note=" ".join(reason_bits),
            ),
        },
        session_id=session_id,
        dedupe_signature={
            "trace_kind": "task_trace",
            "reviewer_tier": "trainer",
            "task_id": task.task_id,
            "trigger": "verifier_direct_audit",
            "incident_id": incident_id or None,
        },
    )
    if incident_id and audit_task:
        annotate_capability_incident(
            storage,
            incident_id=incident_id,
            metadata={
                "audit_task_id": audit_task.get("task_id"),
                "audit_requested_at": datetime.now(timezone.utc).isoformat(),
                "audit_trigger": "verifier_direct_audit",
            },
        )
    return audit_task


def _attach_task_incident(task: TaskModel, incident: Dict[str, object]) -> None:
    constraints = dict(getattr(task, "constraints", {}) or {})
    rows = list(constraints.get("capability_incidents") or [])
    summary = {
        "incident_id": incident.get("incident_id"),
        "capability_ref": incident.get("capability_ref"),
        "status": incident.get("status"),
        "reason": incident.get("reason"),
        "opened_at": incident.get("opened_at"),
        "last_seen_at": incident.get("last_seen_at"),
        "occurrence_count": incident.get("occurrence_count", 1),
    }
    rows = [
        item
        for item in rows
        if not isinstance(item, dict) or str(item.get("incident_id") or "").strip() != str(summary["incident_id"] or "").strip()
    ]
    rows.append(summary)
    constraints["capability_incidents"] = rows[-8:]
    task.constraints = constraints


def _upsert_verification_task(
    storage,
    *,
    task: TaskModel,
    attempt,
    verification: Dict[str, object],
    signal_id: Optional[str] = None,
    queued_review_task_id: Optional[str] = None,
    direct_audit_task_id: Optional[str] = None,
    incident_id: Optional[str] = None,
) -> TaskModel:
    normalized_attempt_id = str(getattr(attempt, "attempt_id", "") or "").strip()
    if not normalized_attempt_id:
        raise ValueError("attempt_id is required for verification task projection")

    existing = None
    for row in storage.session.query(TaskModel).filter(TaskModel.parent_task_id == task.task_id).all():
        constraints = dict(getattr(row, "constraints", {}) or {})
        if str(constraints.get("inline_process_kind") or "").strip().lower() != "verification":
            continue
        if str(constraints.get("verification_attempt_id") or "").strip() != normalized_attempt_id:
            continue
        existing = row
        break

    verdict = str(verification.get("verdict") or "uncertain").strip().lower() or "uncertain"
    recommended_action = str(verification.get("recommended_action") or "verify_more").strip().lower() or "verify_more"
    confidence = float(verification.get("confidence") or 0.0)
    mechanism_failure_kind = str(verification.get("mechanism_failure_kind") or "").strip().lower()
    failure_modes = list(verification.get("failure_modes") or [])
    title = f"Verification Review: {str(task.title or task.task_id)[:72]}"
    description_bits = [
        f"Verification completed for attempt {normalized_attempt_id[-6:]}.",
        f"Verdict: {verdict}.",
        f"Recommended action: {recommended_action}.",
    ]
    if mechanism_failure_kind:
        description_bits.append(f"Mechanism failure: {mechanism_failure_kind}.")
    if failure_modes:
        description_bits.append(f"Failure modes: {', '.join(str(item) for item in failure_modes)}.")
    description = " ".join(description_bits)
    constraints = bind_system_procedure({
        "lane": infer_lane_from_task(task),
        "source_task_id": task.task_id,
        "inline_process_kind": "verification",
        "verification_attempt_id": normalized_attempt_id,
        "verification_recorded_at": str(verification.get("recorded_at") or "").strip(),
        "verification_summary": {
            "verdict": verdict,
            "confidence": confidence,
            "recommended_action": recommended_action,
            "failure_modes": failure_modes,
            "mechanism_failure_kind": mechanism_failure_kind,
        },
        "attention_signal_id": str(signal_id or "").strip() or None,
        "queued_review_task_id": str(queued_review_task_id or "").strip() or None,
        "direct_audit_task_id": str(direct_audit_task_id or "").strip() or None,
        "capability_incident_id": str(incident_id or "").strip() or None,
        "provenance": _provenance_record(
            source_kind="verifier_review",
            source_actor="lightweight_verifier",
            authority_kind="spec_policy",
            authority_ref="project_inline_verification_as_task",
            derived_from=[f"task:{task.task_id}", f"attempt:{normalized_attempt_id}"],
            note=f"Projected inline verification into canonical task history with verdict={verdict}.",
        ),
    },
    procedure_id=canonical_system_procedure_id(process_name="verification_process"),
    capability_kind="process",
    capability_name="verification_process")

    if existing is not None:
        existing.title = title
        existing.description = description
        existing.state = TaskState.COMPLETE
        existing.type = TaskType.JUDGE
        existing.constraints = constraints
        return existing

    created = storage.tasks.create(
        title=title,
        description=description,
        session_id=task.session_id,
        parent_task_id=task.task_id,
        state=TaskState.COMPLETE,
        type=TaskType.JUDGE,
        depth=int(getattr(task, "depth", 0) or 0) + 1,
        priority=float(getattr(task, "priority", 0.0) or 0.0),
        constraints=constraints,
        flush=False,
    )
    return created


def _mark_degraded_process(
    storage,
    task: TaskModel,
    *,
    process_name: str,
    reason: str,
    metadata: Optional[Dict[str, object]] = None,
    attempt_id: Optional[str] = None,
    signal_id: Optional[str] = None,
    provenance: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    incident = record_capability_incident(
        storage,
        capability_kind="process",
        capability_name=process_name,
        status="degraded",
        reason=reason,
        task_id=task.task_id,
        attempt_id=attempt_id,
        session_id=str(task.session_id or "").strip() or None,
        signal_id=signal_id,
        provenance=dict(provenance or {}),
        snapshot={
            "task_title": str(task.title or "").strip(),
            "lane": normalize_lane(infer_lane_from_task(task)) or "agent",
            "state": getattr(getattr(task, "state", None), "value", getattr(task, "state", None)),
        },
        metadata=dict(metadata or {}),
    )
    constraints = dict(getattr(task, "constraints", {}) or {})
    degraded = list(constraints.get("degraded_processes") or [])
    entry = {
        "incident_id": incident.get("incident_id"),
        "process": str(process_name or "").strip(),
        "status": "degraded",
        "reason": str(reason or "").strip(),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    if metadata:
        entry["metadata"] = dict(metadata)
    degraded = [item for item in degraded if not isinstance(item, dict) or str(item.get("process") or "").strip() != entry["process"]]
    degraded.append(entry)
    constraints["degraded_processes"] = degraded[-8:]
    task.constraints = constraints
    _attach_task_incident(task, incident)
    return incident


async def queue_process_repair_task(
    storage,
    *,
    task: TaskModel,
    process_name: str,
    reason: str,
    enqueue_fn,
    metadata: Optional[Dict[str, object]] = None,
    incident_id: Optional[str] = None,
) -> Optional[TaskModel]:
    target = str(process_name or "").strip()
    if not target:
        return None
    normalized_incident_id = str(incident_id or "").strip()
    if normalized_incident_id and _incident_queue_latched(
        storage,
        incident_id=normalized_incident_id,
        task_key="repair_task_id",
    ):
        return None

    for existing in storage.session.query(TaskModel).all():
        constraints = dict(getattr(existing, "constraints", {}) or {})
        if str(constraints.get("target_scope") or "").strip().lower() != "tooling":
            continue
        if str(constraints.get("tool_modification_target") or "").strip() != target:
            continue
        if getattr(existing, "state", None) in {TaskState.COMPLETE, TaskState.CANCELLED, TaskState.ABANDONED}:
            continue
        return existing

    lane = infer_lane_from_task(task)
    procedure_id = canonical_system_procedure_id(process_name=target)
    repair_constraints = bind_system_procedure({
        "lane": lane,
        "target_scope": "tooling",
        "source_task_id": task.task_id,
        "provenance": _provenance_record(
            source_kind="degraded_process",
            source_actor="background_worker",
            authority_kind="spec_policy",
            authority_ref="process_repair_queue",
            derived_from=[
                f"task:{task.task_id}",
                f"process:{target}",
                *([f"incident:{incident_id}"] if incident_id else []),
            ],
            note=reason,
        ),
        "tool_modification_target": target,
        "tool_improvement_reason": "tool_broken",
        "tool_improvement_reasoning": reason,
        "tooling_repair_mode": "direct_or_meta_tool",
        "target_files": [
            "strata/experimental/verifier.py",
            "strata/orchestrator/background.py",
            "strata/experimental/trace_review.py",
        ],
        "degraded_process": target,
        "capability_incident_id": incident_id,
        "degraded_process_metadata": dict(metadata or {}),
    },
    procedure_id=procedure_id,
    capability_kind="process",
    capability_name=target)
    if procedure_id:
        repair_constraints = build_procedure_task_constraints(
            storage,
            procedure_id,
            base=repair_constraints,
        )
    repair_task = storage.tasks.create(
        title=f"Process Repair: {target}",
        description=(
            "A reusable system process is degrading repeatedly and needs bounded repair. "
            f"Target: {target}. Reason: {reason}. "
            "Fix the degraded mechanism before trusting its outputs again."
        ),
        session_id=task.session_id,
        state=TaskState.PENDING,
        type=TaskType.BUG_FIX,
        depth=task.depth + 1,
        priority=float(task.priority or 0.0),
        constraints=repair_constraints,
    )
    storage.commit()
    storage.tasks.add_dependency(task.task_id, repair_task.task_id)
    storage.commit()
    if normalized_incident_id:
        annotate_capability_incident(
            storage,
            incident_id=normalized_incident_id,
            metadata={
                "repair_task_id": repair_task.task_id,
                "repair_requested_at": datetime.now(timezone.utc).isoformat(),
                "repair_target": target,
            },
        )
    await enqueue_fn(repair_task.task_id)
    return repair_task


async def ensure_continuous_supervision_job(
    storage_factory,
    *,
    queue_system_job=None,
    get_proposal_config=None,
    enabled: bool = True,
    minimum_run_count: int = 1,
) -> Optional[dict]:
    if not enabled:
        return None

    if queue_system_job is None:
        from strata.api.main import _queue_eval_system_job as queue_system_job
    if get_proposal_config is None:
        from strata.api.experiment_runtime import get_active_eval_proposal_config as get_proposal_config

    proposal_config = dict(get_proposal_config() or {})
    bootstrap_policy = dict(proposal_config.get("bootstrap") or {})
    proposer_tiers = [
        str(tier).lower()
        for tier in bootstrap_policy.get("continuous_proposer_tiers", ["agent", "trainer"])
        if str(tier).lower() in {"agent", "trainer"}
    ] or ["agent", "trainer"]
    run_count = max(max(1, int(minimum_run_count or 1)), int(bootstrap_policy.get("continuous_run_count", 1) or 1))
    suite_name = "bootstrap_mcq_v1"

    storage = storage_factory()
    try:
        return await queue_system_job(
            storage,
            kind="bootstrap_cycle",
            title="Bootstrap Cycle",
            description="Queued trainer-over-agent bootstrap cycle.",
            payload={
                "proposer_tiers": proposer_tiers,
                "auto_promote": True,
                "suite_name": suite_name,
                "run_count": run_count,
                "baseline_change_id": "baseline",
            },
            session_id="trainer:default",
            dedupe_signature={
                "suite_name": suite_name,
                "run_count": run_count,
                "proposer_tiers": proposer_tiers,
            },
        )
    finally:
        storage.close()


async def ensure_blocked_weak_task_review(
    storage_factory,
    *,
    queue_system_job=None,
    enabled: bool = True,
) -> Optional[dict]:
    if not enabled:
        return None

    if queue_system_job is None:
        from strata.api.main import _queue_eval_system_job as queue_system_job

    storage = storage_factory()
    try:
        weak_blocked_tasks = (
            storage.session.query(TaskModel)
            .filter(
                TaskModel.state == TaskState.BLOCKED,
                TaskModel.human_intervention_required == True,
            )
            .order_by(TaskModel.updated_at.desc())
            .all()
        )
        for candidate in weak_blocked_tasks:
            if normalize_lane(infer_lane_from_task(candidate)) != "agent":
                continue
            if not _blocked_task_has_new_evidence_since_review(storage, candidate):
                continue
            return await queue_system_job(
                storage,
                kind="trace_review",
                title=f"Agent Supervision Review: {str(candidate.title or candidate.task_id)[:72]}",
                description="Queued trainer-agent review for blocked agent-lane work before another bootstrap cycle.",
                payload={
                    "trace_kind": "task_trace",
                    "task_id": candidate.task_id,
                    "reviewer_tier": "trainer",
                    "emit_followups": True,
                    "persist_to_task": True,
                    "spec_scope": "project",
                    "supervision_reason": "weak_blocked_task",
                },
                session_id="trainer:default",
                dedupe_signature={
                    "trace_kind": "task_trace",
                    "reviewer_tier": "trainer",
                    "task_id": candidate.task_id,
                    "supervision_reason": "weak_blocked_task",
                },
            )
        return None
    finally:
        storage.close()

class BackgroundWorker:
    """
    @summary Managed background loop for asynchronous task execution.
    """

    def __init__(
        self,
        storage_factory,
        model_adapter,
        memory=None,
        settings_provider: Optional[Callable[[], dict]] = None,
        model_adapter_factory: Optional[Callable[[], object]] = None,
    ):
        self._storage_factory = storage_factory
        self._model = model_adapter
        self._model_adapter_factory = model_adapter_factory or self._default_model_adapter_factory(model_adapter)
        self._memory = memory
        self._settings_provider = settings_provider or (lambda: {})
        self._lane_queues: Dict[str, asyncio.Queue] = {lane: asyncio.Queue() for lane in LANE_NAMES}
        self._running_tasks: Dict[str, Optional[asyncio.Task]] = {lane: None for lane in LANE_NAMES}
        self._on_update_callback = None
        self._running = False
        self._paused = False
        self._paused_lanes: set[str] = set()
        self._current_processes: Dict[str, Optional[asyncio.Task]] = {lane: None for lane in LANE_NAMES}
        self._current_task_ids: Dict[str, Optional[str]] = {lane: None for lane in LANE_NAMES}
        self._lane_started_at: Dict[str, Optional[datetime]] = {lane: None for lane in LANE_NAMES}
        self._lane_last_activity_at: Dict[str, Optional[datetime]] = {lane: None for lane in LANE_NAMES}
        self._lane_activity_details: Dict[str, Dict[str, object]] = {lane: {} for lane in LANE_NAMES}
        self._lane_step_history: Dict[str, list[Dict[str, object]]] = {lane: [] for lane in LANE_NAMES}
        self._lane_models: Dict[str, object] = {lane: self._model_adapter_factory() for lane in LANE_NAMES}
        self._active_experiment_id: Optional[str] = None # Added for bootstrap experiments
        self._tier_health = {"trainer": "unknown", "agent": "unknown"}
        self._tier_retry_after_s: Dict[str, float] = {lane: 0.0 for lane in LANE_NAMES}

    def _default_model_adapter_factory(self, template_adapter):
        def _factory():
            adapter = type(template_adapter)()
            selected_models = dict(getattr(template_adapter, "_selected_models", {}) or {})
            if selected_models and hasattr(adapter, "_selected_models"):
                adapter._selected_models = selected_models
            return adapter

        return _factory

    def _lane_queue(self, lane: Optional[str]) -> asyncio.Queue:
        normalized_lane = normalize_lane(lane) or "agent"
        return self._lane_queues.setdefault(normalized_lane, asyncio.Queue())

    def _lane_model(self, lane: Optional[str]):
        normalized_lane = normalize_lane(lane) or "agent"
        if normalized_lane not in self._lane_models:
            self._lane_models[normalized_lane] = self._model_adapter_factory()
        return self._lane_models[normalized_lane]

    def _settings(self) -> dict:
        try:
            return dict(self._settings_provider() or {})
        except Exception as exc:
            logger.error(f"Failed to read worker settings; using defaults. ({exc})")
            return {}

    async def _retry_lane_preflight_if_due(self, lane: str) -> bool:
        normalized_lane = normalize_lane(lane) or "agent"
        now = asyncio.get_running_loop().time()
        if now < float(self._tier_retry_after_s.get(normalized_lane) or 0.0):
            return False

        from strata.schemas.execution import TrainerExecutionContext, AgentExecutionContext

        context = (
            TrainerExecutionContext(run_id=f"recheck_{normalized_lane}")
            if normalized_lane == "trainer"
            else AgentExecutionContext(run_id=f"recheck_{normalized_lane}")
        )
        settings = self._settings()
        lane_model = self._lane_model(normalized_lane)
        try:
            ok, reason = await _preflight_lane_model(
                normalized_lane,
                lane_model,
                context,
                auto_swap_local_missing=bool(
                    dict(settings.get("model_catalog_policy") or {}).get("auto_swap_local_missing", False)
                ),
                allow_endpoint_failover=True,
            )
        except Exception as exc:
            ok, reason = False, str(exc)

        if ok:
            self._tier_health[normalized_lane] = "ok"
            self._tier_retry_after_s[normalized_lane] = 0.0
            logger.warning("Lane '%s' recovered after background preflight retry.", normalized_lane)
            return True

        self._tier_health[normalized_lane] = "error"
        self._tier_retry_after_s[normalized_lane] = now + 60.0
        logger.warning(
            "Lane '%s' remains unavailable after background preflight retry: %s",
            normalized_lane,
            reason or "unknown preflight error",
        )
        return False

    async def start(self):
        if self._running:
            return
            
        # 1. Deep preflight model check (trainer-agent + agent)
        logger.info("Performing deep preflight check (trainer-agent + agent tiers)...")
        from strata.schemas.execution import TrainerExecutionContext, AgentExecutionContext
        settings = self._settings()
        
        contexts = [
            ("trainer", TrainerExecutionContext(run_id="preflight")),
            ("agent", AgentExecutionContext(run_id="preflight"))
        ]

        for name, ctx in contexts:
            logger.info(f"Checking {name} model tier...")
            lane_model = self._lane_model(name)
            try:
                ok, reason = await _preflight_lane_model(
                    name,
                    lane_model,
                    ctx,
                    auto_swap_local_missing=bool(
                        dict(settings.get("model_catalog_policy") or {}).get("auto_swap_local_missing", False)
                    ),
                    allow_endpoint_failover=True,
                )
                if not ok:
                    raise RuntimeError(reason)
                logger.info(f"  -> {name} tier is reachable.")
                self._tier_health[name] = "ok"
                self._tier_retry_after_s[name] = 0.0
            except Exception as e:
                # Special case: if the trainer-agent tier is missing a cloud key,
                # we do not necessarily want to hard-fail if the local agent tier is alive.
                # We still log it loudly so the operator knows bootstrap quality is degraded.
                logger.error(f"  -> {name} tier FAILED check: {e}")
                self._tier_health[name] = "error"
                self._tier_retry_after_s[name] = asyncio.get_running_loop().time() + 60.0
                logger.warning("Worker proceeding with lane '%s' unavailable until its configured transport is healthy again.", name)

        if all(status == "error" for status in self._tier_health.values()):
            raise Exception("Worker cannot start: both trainer and agent lanes are unreachable.")

        # 2. Start Recovery Sweep
        await recover_tasks(
            self._storage_factory,
            self._lane_queue("trainer"),
            recover_orphaned_running=not settings.get("testing_mode", False),
            requeue_existing_pending=settings.get("replay_pending_tasks_on_startup", False),
            task_filter=lambda task: normalize_lane(infer_lane_from_task(task)) == "trainer",
        )
        await recover_tasks(
            self._storage_factory,
            self._lane_queue("agent"),
            recover_orphaned_running=not settings.get("testing_mode", False),
            requeue_existing_pending=settings.get("replay_pending_tasks_on_startup", False),
            task_filter=lambda task: normalize_lane(infer_lane_from_task(task)) == "agent",
        )
        if not settings.get("testing_mode", False):
            replayed = await self.enqueue_runnable_tasks()
            if replayed:
                logger.info("Seeded %s runnable task(s) into the worker queue during startup.", replayed)

        # 3. Start Loop
        self._running = True
        if not settings.get("testing_mode", False):
            await self._ensure_lane_idle_policies(settings)
        for lane in LANE_NAMES:
            self._running_tasks[lane] = asyncio.create_task(self._loop(lane), name=f"background-worker:{lane}")
        logger.info("BackgroundWorker started (Hardened Startup)")

    async def stop(self):
        self._running = False
        for lane, process in list(self._current_processes.items()):
            if process:
                process.cancel()
        for lane, loop_task in list(self._running_tasks.items()):
            if not loop_task:
                continue
            loop_task.cancel()
            try:
                await loop_task
            except asyncio.CancelledError:
                pass
            self._running_tasks[lane] = None
            self._current_processes[lane] = None
            self._current_task_ids[lane] = None
            self._lane_started_at[lane] = None
            self._lane_last_activity_at[lane] = None
            self._lane_activity_details[lane] = {}
            self._lane_step_history[lane] = []
        logger.info("BackgroundWorker stopped")

    def set_on_update(self, callback):
        self._on_update_callback = callback
        
    async def _notify(self, task_id: str, state: str):
        now = datetime.now(timezone.utc)
        for lane_name, current_task_id in self._current_task_ids.items():
            if current_task_id == task_id:
                self._lane_last_activity_at[lane_name] = now
                details = dict(self._lane_activity_details.get(lane_name) or {})
                details["current_task_state"] = str(state or "").upper()
                details["step_updated_at"] = now.isoformat()
                self._lane_activity_details[lane_name] = details
                break
        if self._on_update_callback:
            try:
                if asyncio.iscoroutinefunction(self._on_update_callback):
                    await self._on_update_callback(task_id, state)
                else:
                    self._on_update_callback(task_id, state)
            except Exception as e:
                logger.error(f"Failed to notify update: {e}")

    def _mark_lane_progress(
        self,
        lane: Optional[str],
        *,
        step: str,
        label: str,
        detail: str = "",
        task_id: Optional[str] = None,
        task_title: Optional[str] = None,
        attempt_id: Optional[str] = None,
        progress_label: Optional[str] = None,
    ) -> None:
        normalized_lane = normalize_lane(lane)
        if not normalized_lane:
            return
        now = datetime.now(timezone.utc)
        self._lane_last_activity_at[normalized_lane] = now
        details = dict(self._lane_activity_details.get(normalized_lane) or {})
        event = {
            "step": str(step or "").strip().lower() or "unknown",
            "label": str(label or "").strip() or "Working",
            "detail": str(detail or "").strip(),
            "at": now.isoformat(),
        }
        details.update(
            {
                "step": event["step"],
                "step_label": event["label"],
                "step_detail": event["detail"],
                "step_updated_at": now.isoformat(),
            }
        )
        if task_id is not None:
            details["current_task_id"] = str(task_id)
        if task_title is not None:
            details["current_task_title"] = str(task_title or "")
        if attempt_id is not None:
            details["active_attempt_id"] = str(attempt_id or "")
        if progress_label is not None:
            details["progress_label"] = str(progress_label or "")
        history = list(self._lane_step_history.get(normalized_lane) or [])
        previous = history[-1] if history else None
        duplicate_event = (
            isinstance(previous, dict)
            and previous.get("step") == event["step"]
            and previous.get("label") == event["label"]
            and previous.get("detail") == event["detail"]
        )
        if not duplicate_event:
            history.append(event)
        self._lane_step_history[normalized_lane] = history[-18:]
        details["recent_steps"] = list(self._lane_step_history[normalized_lane][-6:])
        ticker_items = []
        for item in self._lane_step_history[normalized_lane][-8:]:
            rendered = f"{item.get('label')}{f': {item.get('detail')}' if item.get('detail') else ''}"
            if not ticker_items or ticker_items[-1] != rendered:
                ticker_items.append(rendered)
        details["ticker_items"] = ticker_items
        self._lane_activity_details[normalized_lane] = details

    async def enqueue(self, task_id: str):
        await self.enqueue_with_priority(task_id, front=False)
        
    async def enqueue_with_priority(self, task_id: str, *, front: bool = False):
        queue = self._lane_queue(self._lane_for_task_id(task_id))
        if task_id in {queued_task_id for queued_task_id in list(getattr(queue, "_queue", []))}:
            logger.info("Skipped enqueue for task %s because it is already queued.", task_id)
            return
        if task_id in {current_task_id for current_task_id in self._current_task_ids.values() if current_task_id}:
            logger.info("Skipped enqueue for task %s because it is already active.", task_id)
            return
        if front:
            queue._queue.appendleft(task_id)
        else:
            await queue.put(task_id)
        logger.info(f"Enqueued task {task_id}")

    def _task_has_live_children(self, task: TaskModel, storage=None) -> bool:
        child_ids = list(getattr(task, "active_child_ids", []) or [])
        if not child_ids:
            return False
        owned_storage = None
        if storage is None:
            owned_storage = self._storage_factory()
            storage = owned_storage
        try:
            for child_id in child_ids:
                child = storage.tasks.get_by_id(str(child_id))
                if child is None:
                    continue
                if child.state not in {TaskState.COMPLETE, TaskState.CANCELLED, TaskState.ABANDONED}:
                    return True
            return False
        finally:
            if owned_storage is not None:
                owned_storage.session.close()

    @staticmethod
    def _task_queue_rank(task: TaskModel) -> tuple:
        constraints = dict(getattr(task, "constraints", {}) or {})
        procedure_id = str(constraints.get("procedure_id") or "").strip().lower()
        phase = str(constraints.get("recovery_phase") or "").strip().lower()
        phase_rank = {"inspect": 0, "decide": 1, "cash_out": 2}.get(phase, 9)
        has_handoff = bool(constraints.get("handoff_context")) or bool(constraints.get("terminal_tool_origin"))
        has_hints = bool(constraints.get("source_hints")) or bool(constraints.get("preferred_start_paths"))
        has_parent = 0 if getattr(task, "parent_task_id", None) else 1
        updated_at = getattr(task, "updated_at", None)
        created_at = getattr(task, "created_at", None)
        recency = updated_at or created_at
        recency_key = 0.0
        if recency is not None:
            try:
                recency_key = -float(recency.timestamp())
            except Exception:
                recency_key = 0.0
        return (
            0 if procedure_id == "startup_sanity_check" else 1,
            0 if has_handoff else 1,
            has_parent,
            0 if has_hints else 1,
            phase_rank,
            recency_key,
            -int(getattr(task, "depth", 0) or 0),
            str(getattr(task, "task_id", "") or ""),
        )

    async def wait_until_idle(self, timeout: float = 5.0, lane: Optional[str] = None) -> bool:
        normalized_lane = normalize_lane(lane)
        deadline = asyncio.get_running_loop().time() + max(0.1, float(timeout))
        while asyncio.get_running_loop().time() < deadline:
            if normalized_lane:
                if self._current_processes.get(normalized_lane) is None:
                    return True
            elif all(process is None for process in self._current_processes.values()):
                return True
            await asyncio.sleep(0.05)
        if normalized_lane:
            return self._current_processes.get(normalized_lane) is None
        return all(process is None for process in self._current_processes.values())

    def clear_queue(self, lane: Optional[str] = None) -> int:
        cleared = 0
        lanes = [normalize_lane(lane)] if normalize_lane(lane) else list(LANE_NAMES)
        for lane_name in lanes:
            queue = self._lane_queue(lane_name)
            while True:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                else:
                    queue.task_done()
                    cleared += 1
        if cleared:
            logger.info("Cleared %s queued task(s) from the worker backlog.", cleared)
        return cleared

    def _lane_for_task_id(self, task_id: str) -> Optional[str]:
        storage = self._storage_factory()
        try:
            task = storage.session.query(TaskModel).filter_by(task_id=task_id).first()
            if not task:
                return None
            return normalize_lane(infer_lane_from_task(task))
        finally:
            storage.session.close()

    def _task_is_paused(self, task_id: str) -> bool:
        storage = self._storage_factory()
        try:
            task = storage.session.query(TaskModel).filter_by(task_id=task_id).first()
            if not task:
                return False
            return bool((task.constraints or {}).get("paused"))
        finally:
            storage.session.close()

    def _lane_has_runnable_or_active_work(self, lane: str) -> bool:
        normalized_lane = normalize_lane(lane)
        if not normalized_lane:
            return False
        if self._current_processes.get(normalized_lane) is not None:
            return True
        storage = self._storage_factory()
        try:
            query = storage.session.query(TaskModel).filter(TaskModel.state.in_([TaskState.PENDING, TaskState.WORKING]))
            for task in query.all():
                task_lane = normalize_lane(infer_lane_from_task(task))
                if task_lane != normalized_lane:
                    continue
                constraints = dict(task.constraints or {})
                if constraints.get("paused") or task.human_intervention_required:
                    continue
                if self._task_has_live_children(task, storage):
                    continue
                return True
            return False
        finally:
            storage.session.close()

    async def enqueue_runnable_tasks(self, lane: Optional[str] = None) -> int:
        normalized_lane = normalize_lane(lane)
        storage = self._storage_factory()
        try:
            query = storage.session.query(TaskModel).filter(TaskModel.state.in_([TaskState.PENDING, TaskState.WORKING]))
            candidates = query.all()
            candidates = sorted(candidates, key=self._task_queue_rank)
            enqueued = 0
            for task in candidates:
                if task.human_intervention_required:
                    continue
                if task.state == TaskState.BLOCKED:
                    continue
                if task.state == TaskState.PUSHED or self._task_has_live_children(task, storage):
                    continue
                constraints = dict(task.constraints or {})
                if constraints.get("paused"):
                    continue
                task_lane = normalize_lane(infer_lane_from_task(task))
                if normalized_lane and task_lane != normalized_lane:
                    continue
                if task.dependencies and any(dep.state != TaskState.COMPLETE for dep in task.dependencies):
                    continue
                if task.task_id in {task_id for task_id in self._current_task_ids.values() if task_id}:
                    continue
                await self.enqueue_with_priority(task.task_id, front=False)
                enqueued += 1
            return enqueued
        finally:
            storage.session.close()

    async def _ensure_lane_idle_policies(self, settings: Optional[dict] = None):
        settings = settings or self._settings()
        if settings.get("testing_mode", False) or self._paused:
            return

        if (
            self._tier_health.get("trainer") == "ok"
            and "trainer" not in self._paused_lanes
            and not self._lane_has_runnable_or_active_work("trainer")
        ):
            try:
                review_seed = await ensure_blocked_weak_task_review(
                    self._storage_factory,
                    enabled=True,
                )
                if review_seed and review_seed.get("status") == "queued":
                    logger.info("Queued blocked agent-task supervision review %s for idle trainer lane.", review_seed.get("task_id"))
                else:
                    seeded = await ensure_continuous_supervision_job(
                        self._storage_factory,
                        enabled=True,
                        minimum_run_count=3 if settings.get("heavy_reflection_mode", False) else 1,
                    )
                    if seeded and seeded.get("status") == "queued":
                        logger.info("Queued continuous supervision job %s for idle trainer lane.", seeded.get("task_id"))
            except Exception as exc:
                logger.warning("Unable to ensure continuous supervision job for the trainer lane: %s", exc)

        if (
            settings.get("automatic_task_generation", False)
            and self._tier_health.get("agent") != "error"
            and "agent" not in self._paused_lanes
        ):
            if not self._lane_has_runnable_or_active_work("agent"):
                await run_idle_tasks(self._storage_factory, self._lane_model("agent"), self._lane_queue("agent"))

    def _update_task_control_state(
        self,
        task_id: str,
        *,
        paused: Optional[bool] = None,
        state: Optional[TaskState] = None,
        attempt_outcome: Optional[AttemptOutcome] = None,
        reason: Optional[str] = None,
        extra_constraints: Optional[Dict[str, Any]] = None,
    ) -> bool:
        storage = self._storage_factory()
        try:
            task = storage.session.query(TaskModel).filter_by(task_id=task_id).first()
            if not task:
                return False
            if task.state in {TaskState.COMPLETE, TaskState.CANCELLED, TaskState.ABANDONED}:
                return False
            constraints = dict(task.constraints or {})
            if paused is True:
                constraints["paused"] = True
            elif paused is False:
                constraints.pop("paused", None)
            if extra_constraints:
                constraints.update(extra_constraints)
            task.constraints = constraints
            if state is not None and task.state not in {TaskState.COMPLETE, TaskState.CANCELLED, TaskState.ABANDONED}:
                task.state = state
            if attempt_outcome is not None:
                open_attempt = next((row for row in storage.attempts.get_by_task_id(task_id) if row.outcome is None), None)
                if open_attempt:
                    storage.attempts.update_outcome(open_attempt.attempt_id, attempt_outcome, reason=reason)
            storage.commit()
            return True
        finally:
            storage.session.close()

    async def _loop(self, lane: str):
        normalized_lane = normalize_lane(lane) or "agent"
        lane_queue = self._lane_queue(normalized_lane)
        idle_ticks = 0
        while self._running:
            if self._paused or normalized_lane in self._paused_lanes:
                await asyncio.sleep(0.5)
                continue
            if self._tier_health.get(normalized_lane) == "error":
                await self._retry_lane_preflight_if_due(normalized_lane)
                await asyncio.sleep(1.0)
                continue

            try:
                task_id = await asyncio.wait_for(lane_queue.get(), timeout=1.0)
                idle_ticks = 0
                task_lane = self._lane_for_task_id(task_id)
                if task_lane and normalize_lane(task_lane) != normalized_lane:
                    await self._lane_queue(task_lane).put(task_id)
                    lane_queue.task_done()
                    await asyncio.sleep(0.05)
                    continue
                if task_lane and task_lane in self._paused_lanes:
                    await lane_queue.put(task_id)
                    lane_queue.task_done()
                    await asyncio.sleep(0.1)
                    continue
                if self._task_is_paused(task_id):
                    lane_queue.task_done()
                    continue

                self._current_task_ids[normalized_lane] = task_id
                now = datetime.now(timezone.utc)
                self._lane_started_at[normalized_lane] = now
                self._lane_last_activity_at[normalized_lane] = now
                self._lane_activity_details[normalized_lane] = {
                    "current_task_id": str(task_id),
                    "step": "queued",
                    "step_label": "Starting task",
                    "step_detail": "",
                    "step_updated_at": now.isoformat(),
                }
                self._lane_step_history[normalized_lane] = []
                self._current_processes[normalized_lane] = asyncio.create_task(self._run_task_cycle(task_id, lane=normalized_lane))
                try:
                    await self._current_processes[normalized_lane]
                except asyncio.CancelledError:
                    logger.info(f"Task process {task_id} was forced STOPPED.")
                finally:
                    self._current_processes[normalized_lane] = None
                    self._current_task_ids[normalized_lane] = None
                    self._lane_started_at[normalized_lane] = None
                    self._lane_last_activity_at[normalized_lane] = datetime.now(timezone.utc)
                    self._lane_activity_details[normalized_lane] = {}
                    self._lane_step_history[normalized_lane] = []
                    await self._ensure_lane_idle_policies()

                lane_queue.task_done()
            except asyncio.TimeoutError:
                idle_ticks += 1
                if idle_ticks >= 30:
                    settings = self._settings()
                    if settings.get("testing_mode", False):
                        logger.info("Testing mode active; skipping autonomous idle task generation.")
                    else:
                        replayed = await self.enqueue_runnable_tasks(normalized_lane)
                        if replayed:
                            logger.info("Worker idle with runnable backlog; re-enqueued %s task(s).", replayed)
                            idle_ticks = 0
                            continue
                        await self._ensure_lane_idle_policies(settings)
                        if not settings.get("automatic_task_generation", False):
                            logger.info("Automatic task generation disabled; worker remains idle without spawning new tasks.")
                    await synthesize_model_performance(self._storage_factory)
                    idle_ticks = 0
            except asyncio.CancelledError:
                break

    async def _run_task_cycle(self, task_id: str, *, lane: Optional[str] = None):
        """
        @summary The orchestrator cycle for a single task: Run -> Resolve -> Review.
        """
        storage = self._storage_factory()
        try:
            task = storage.session.query(TaskModel).filter_by(task_id=task_id).first()
            if not task or task.state in [TaskState.COMPLETE, TaskState.CANCELLED]:
                return
            if (task.constraints or {}).get("system_job"):
                self._mark_lane_progress(
                    lane or infer_lane_from_task(task),
                    step="queued",
                    label="Starting task",
                    detail=str(task.title or task.description or task.task_id),
                    task_id=task.task_id,
                    task_title=task.title,
                )
                logger.info(f"Running queued system job for task {task_id}")
                await run_eval_job_task(
                    task,
                    storage,
                    self._lane_model(lane),
                    progress_fn=lambda **payload: self._mark_lane_progress(
                        lane or infer_lane_from_task(task),
                        step=payload.get("step", "system_job"),
                        label=payload.get("label", "Working"),
                        detail=payload.get("detail", ""),
                        task_id=task.task_id,
                        task_title=task.title,
                        progress_label=payload.get("progress_label"),
                        attempt_id=payload.get("attempt_id"),
                    ),
                )
                await self._notify(task_id, task.state.value)
                return

            # --- ROUTING ---
            from strata.orchestrator.worker.routing_policy import select_model_tier
            context = select_model_tier(task)
            
            # Application of experimental override if active
            if self._active_experiment_id:
                from strata.schemas.execution import AgentExecutionContext
                context = AgentExecutionContext(
                    run_id=f"exp_{task_id}",
                    candidate_change_id=self._active_experiment_id,
                    evaluation_run=True
                )
            
            lane_model = self._lane_model(lane or context.mode)
            lane_model.bind_execution_context(context)
            logger.info(f"Routing task {task_id} to {context.mode} execution context [Exp: {self._active_experiment_id}]")
            self._mark_lane_progress(
                lane or context.mode,
                step="routing",
                label="Routing",
                detail=f"{task.type.value.lower()} via {context.mode}",
                task_id=task.task_id,
                task_title=task.title,
            )

            constraints = dict(task.constraints or {})
            if constraints.get("lane") != context.mode:
                constraints["lane"] = context.mode
                task.constraints = constraints

            storage.commit()
            await self._notify(task_id, task.state.value)
            
            # --- ATTEMPT ---
            success, error, attempt = await run_attempt(
                task,
                storage,
                lane_model,
                self._notify,
                self.enqueue,
                progress_fn=lambda **payload: self._mark_lane_progress(
                    lane or context.mode,
                    task_id=task.task_id,
                    task_title=task.title,
                    **payload,
                ),
            )
            
            # Determine execution context details for metrics
            run_mode = getattr(context, "run_mode", "normal") if hasattr(context, "run_mode") else "normal"
            if getattr(context, "evaluation_run", False):
                run_mode = "weak_eval"
            ctx_mode = context.mode
            change_id = getattr(context, "candidate_change_id", None)
            attempt_artifacts = dict(getattr(attempt, "artifacts", {}) or {})

            def _record_attempt_efficiency_metrics(outcome: str):
                from strata.orchestrator.worker.telemetry import record_metric
                duration_s = float(attempt_artifacts.get("duration_s", 0.0) or 0.0)
                usage = attempt_artifacts.get("usage") or {}
                base_kwargs = {
                    "storage": storage,
                    "model_id": f"{attempt_artifacts.get('provider', 'unknown')}/{attempt_artifacts.get('model', 'unknown')}",
                    "task_type": task.type.value if hasattr(task.type, 'value') else str(task.type),
                    "task_id": task_id,
                    "run_mode": run_mode,
                    "execution_context": ctx_mode,
                    "candidate_change_id": change_id,
                    "details": {"outcome": outcome},
                }
                if duration_s > 0.0:
                    record_metric(
                        base_kwargs["storage"],
                        metric_name="task_attempt_duration_s",
                        value=duration_s,
                        model_id=base_kwargs["model_id"],
                        task_type=base_kwargs["task_type"],
                        task_id=base_kwargs["task_id"],
                        run_mode=base_kwargs["run_mode"],
                        execution_context=base_kwargs["execution_context"],
                        candidate_change_id=base_kwargs["candidate_change_id"],
                        details=base_kwargs["details"],
                    )
                for key, metric_name in (
                    ("prompt_tokens", "task_prompt_tokens"),
                    ("completion_tokens", "task_completion_tokens"),
                    ("total_tokens", "task_total_tokens"),
                ):
                    if usage.get(key) is not None:
                        record_metric(
                            base_kwargs["storage"],
                            metric_name=metric_name,
                            value=float(usage.get(key) or 0.0),
                            model_id=base_kwargs["model_id"],
                            task_type=base_kwargs["task_type"],
                            task_id=base_kwargs["task_id"],
                            run_mode=base_kwargs["run_mode"],
                            execution_context=base_kwargs["execution_context"],
                            candidate_change_id=base_kwargs["candidate_change_id"],
                            details=base_kwargs["details"],
                        )

            if not success:
                self._mark_lane_progress(
                    lane or context.mode,
                    step="resolution",
                    label="Resolving failure",
                    detail=str(error or "")[:180],
                    attempt_id=getattr(attempt, "attempt_id", None),
                )
                # Update attempt outcome to FAILED before review so the reviewer sees the actual failure state.
                storage.attempts.update_outcome(attempt.attempt_id, AttemptOutcome.FAILED, reason=str(error))
                fallback_resolution = await determine_resolution(task, error, lane_model, storage)

                review = None
                try:
                    self._mark_lane_progress(
                        lane or context.mode,
                        step="review",
                        label="Reviewing failed attempt",
                        detail="Generating plan review",
                        attempt_id=getattr(attempt, "attempt_id", None),
                    )
                    review = await asyncio.wait_for(
                        generate_plan_review(task, attempt, lane_model, storage),
                        timeout=8.0,
                    )
                    storage.attempts.set_plan_review(attempt.attempt_id, review)
                    storage.commit()
                    should_flush = enqueue_attempt_observability_artifact(
                        {
                            "task_id": task.task_id,
                            "attempt_id": attempt.attempt_id,
                            "session_id": task.session_id,
                            "artifact_kind": "plan_review",
                            "payload": dict(review or {}),
                        }
                    )
                    if should_flush:
                        flush_observability_writes()
                except Exception as review_err:
                    logger.error(f"Failed to generate failure-time plan review for attempt {attempt.attempt_id}: {review_err}")

                # --- RESOLUTION ---
                resolution_data = resolution_from_plan_review(review) or fallback_resolution
                
                # Map string resolution to Enum
                from strata.storage.models import AttemptResolution
                try:
                    res_enum = AttemptResolution(resolution_data.resolution)
                    storage.attempts.set_resolution(attempt.attempt_id, res_enum)
                except ValueError:
                    logger.error(f"Invalid resolution choice: {resolution_data.resolution}")
                
                await apply_resolution(task, resolution_data, error, storage, self.enqueue)

                if (
                    str(getattr(resolution_data, "resolution", "") or "").strip().lower() == "decompose"
                    and task.state == TaskState.PUSHED
                    and self._task_has_live_children(task, storage)
                ):
                    self._mark_lane_progress(
                        lane or context.mode,
                        step="complete",
                        label="Handed off to children",
                        detail=f"{len(list(getattr(task, 'active_child_ids', []) or []))} child task(s) queued",
                        attempt_id=getattr(attempt, "attempt_id", None),
                        progress_label="children in progress",
                    )
                    return
                
                # --- RECORD METRICS ---
                from strata.orchestrator.worker.telemetry import record_metric
                record_metric(
                    storage,
                    metric_name="task_failure",
                    value=1.0,
                    model_id=f"{attempt_artifacts.get('provider', 'unknown')}/{attempt_artifacts.get('model', 'unknown')}",
                    task_type=task.type.value if hasattr(task.type, 'value') else str(task.type),
                    task_id=task_id,
                    run_mode=run_mode,
                    execution_context=ctx_mode,
                    candidate_change_id=change_id,
                    details={"error": str(error), "resolution": resolution_data.resolution}
                )
                _record_attempt_efficiency_metrics("failed")
                
                # Record valid candidate rate if applicable
                from strata.storage.models import CandidateModel
                candidates = storage.session.query(CandidateModel).filter_by(task_id=task_id).all()
                if candidates:
                    from strata.orchestrator.evaluation import EvaluationPipeline
                    evaluator = EvaluationPipeline(storage, context=context)
                    valid_count = 0
                    for c in candidates:
                        sc = await evaluator.evaluate_candidate(task, c)
                        if sc.valid:
                            valid_count += 1
                    
                    record_metric(
                        storage,
                        metric_name="valid_candidate_rate",
                        value=valid_count / len(candidates),
                        model_id=f"{attempt_artifacts.get('provider', 'unknown')}/{attempt_artifacts.get('model', 'unknown')}",
                        task_type=task.type.value if hasattr(task.type, 'value') else str(task.type),
                        task_id=task_id,
                        run_mode=run_mode,
                        execution_context=ctx_mode,
                        candidate_change_id=change_id,
                        details={"total": len(candidates), "valid": valid_count}
                    )
                
                storage.commit()
            else:
                # --- SUCCESS METRICS ---
                from strata.orchestrator.worker.telemetry import record_metric
                record_metric(
                    storage,
                    metric_name="task_success",
                    value=1.0,
                    model_id=f"{attempt.artifacts.get('provider', 'unknown')}/{attempt.artifacts.get('model', 'unknown')}",
                    task_type=task.type.value if hasattr(task.type, 'value') else str(task.type),
                    task_id=task_id,
                    run_mode=run_mode,
                    execution_context=ctx_mode,
                    candidate_change_id=change_id
                )
                _record_attempt_efficiency_metrics("succeeded")
                storage.commit()

            if success:
                # Coordination nodes that expanded into active children should hand control off
                # immediately instead of spending a full verifier/review cycle on the parent.
                if task.state == TaskState.PUSHED and self._task_has_live_children(task, storage):
                    self._mark_lane_progress(
                        lane or context.mode,
                        step="complete",
                        label="Handed off to children",
                        detail=f"{len(list(getattr(task, 'active_child_ids', []) or []))} child task(s) in progress",
                        attempt_id=getattr(attempt, "attempt_id", None),
                        progress_label="children in progress",
                    )
                    return

                # Only successful attempts should enter output verification and branch-health review.
                # Failed attempts already ran through failure-time review/resolution above.
                try:
                    attempt_id = getattr(attempt, "attempt_id", None)
                    task_id_str = getattr(task, "task_id", None)
                    self._mark_lane_progress(
                        lane or context.mode,
                        step="verification",
                        label="Verifying output",
                        detail="Checking correctness and quality",
                        attempt_id=attempt_id,
                    )
                    verification = await verify_task_output(
                        storage,
                        task=task,
                        attempt=attempt,
                        model_adapter=lane_model,
                        context=context,
                        progress_fn=lambda **payload: self._mark_lane_progress(
                            lane or context.mode,
                            step=str(payload.get("step") or "verification"),
                            label=str(payload.get("label") or "Verifying output"),
                            detail=str(payload.get("detail") or ""),
                            attempt_id=attempt_id,
                            progress_label=str(payload.get("progress_label") or ""),
                        ),
                    )
                    if verification:
                        attempt.artifacts = {
                            **dict(getattr(attempt, "artifacts", {}) or {}),
                            "verifier": {
                                "recorded_at": verification.get("recorded_at"),
                                "verification_kind": verification.get("verification_kind"),
                                "verdict": verification.get("verdict"),
                                "confidence": verification.get("confidence"),
                                "recommended_action": verification.get("recommended_action"),
                                "failure_modes": list(verification.get("failure_modes") or []),
                                "mechanism_failure_kind": verification.get("mechanism_failure_kind") or "",
                                "policy": dict(verification.get("policy") or {}),
                            },
                        }
                        mechanism_failure_kind = str(verification.get("mechanism_failure_kind") or "").strip().lower()
                        process_incident = None
                        if mechanism_failure_kind:
                            consecutive_mechanism_failures = count_recent_verifier_mechanism_failures(
                                storage,
                                mode=str(verification.get("mode") or context.mode or infer_lane_from_task(task) or "unknown"),
                            )
                            process_incident = _mark_degraded_process(
                                storage,
                                task,
                                process_name="verification_process",
                                reason=(
                                    f"Verifier mechanism failure: {mechanism_failure_kind}. "
                                    f"Consecutive recent verifier mechanism failures: {consecutive_mechanism_failures}."
                                ),
                                attempt_id=attempt.attempt_id,
                                metadata={
                                    "mechanism_failure_kind": mechanism_failure_kind,
                                    "consecutive_recent_failures": consecutive_mechanism_failures,
                                    "verification_kind": verification.get("verification_kind"),
                                },
                                provenance=_provenance_record(
                                    source_kind="verifier_review",
                                    source_actor="lightweight_verifier",
                                    authority_kind="spec_policy",
                                    authority_ref="degrade_process_on_verifier_mechanism_failure",
                                    derived_from=[
                                        f"task:{task.task_id}",
                                        f"attempt:{attempt.attempt_id}",
                                    ],
                                    note=f"Verifier mechanism failure kind={mechanism_failure_kind}",
                                ),
                            )
                            verification["incident_id"] = process_incident.get("incident_id")
                            should_flush = enqueue_attempt_observability_artifact(
                                {
                                    "task_id": task.task_id,
                                    "attempt_id": attempt.attempt_id,
                                    "session_id": task.session_id,
                                    "artifact_kind": "capability_incident",
                                    "payload": dict(process_incident),
                                }
                            )
                            if should_flush:
                                flush_observability_writes()
                            if consecutive_mechanism_failures >= 3:
                                repair_task = await queue_process_repair_task(
                                    storage,
                                    task=task,
                                    process_name="verification_process",
                                    reason=(
                                        "Repeated verifier machinery failures are degrading output verification. "
                                        f"Latest failure kind: {mechanism_failure_kind}."
                                    ),
                                    enqueue_fn=self.enqueue,
                                    metadata={
                                        "mechanism_failure_kind": mechanism_failure_kind,
                                        "consecutive_recent_failures": consecutive_mechanism_failures,
                                        "verification_kind": verification.get("verification_kind"),
                                    },
                                    incident_id=str(process_incident.get("incident_id") or "").strip() or None,
                                )
                                logger.warning(
                                    "Queued verifier repair task %s after %s consecutive verifier mechanism failures.",
                                    getattr(repair_task, "task_id", None),
                                    consecutive_mechanism_failures,
                                )
                        verifier_signal = emit_verifier_attention_signal(
                            storage,
                            task=task,
                            verification=verification,
                        )
                        direct_audit = None
                        if _should_queue_direct_audit(verification):
                            if not str(verification.get("incident_id") or "").strip():
                                output_incident = record_capability_incident(
                                    storage,
                                    capability_kind="task_output",
                                    capability_name=task.task_id,
                                    status="degraded",
                                    reason=(
                                        "Verifier requested immediate audit for task output. "
                                        f"verdict={verification.get('verdict')}; "
                                        f"recommended_action={verification.get('recommended_action') or 'verify_more'}."
                                    ),
                                    task_id=task.task_id,
                                    attempt_id=attempt.attempt_id,
                                    session_id=str(task.session_id or "").strip() or None,
                                    signal_id=(verifier_signal or {}).get("signal_id"),
                                    provenance=_provenance_record(
                                        source_kind="verifier_review",
                                        source_actor="lightweight_verifier",
                                        authority_kind="spec_policy",
                                        authority_ref="degrade_task_output_pending_audit",
                                        derived_from=[f"task:{task.task_id}", f"attempt:{attempt.attempt_id}"],
                                        note="Immediate audit required before trusting this task output.",
                                    ),
                                    snapshot={
                                        "task_title": str(task.title or "").strip(),
                                        "verification_kind": verification.get("verification_kind"),
                                        "verdict": verification.get("verdict"),
                                        "confidence": verification.get("confidence"),
                                    },
                                    metadata={
                                        "recommended_action": verification.get("recommended_action"),
                                        "failure_modes": list(verification.get("failure_modes") or []),
                                    },
                                )
                                verification["incident_id"] = output_incident.get("incident_id")
                                _attach_task_incident(task, output_incident)
                                should_flush = enqueue_attempt_observability_artifact(
                                    {
                                        "task_id": task.task_id,
                                        "attempt_id": attempt.attempt_id,
                                        "session_id": task.session_id,
                                        "artifact_kind": "capability_incident",
                                        "payload": dict(output_incident),
                                    }
                                )
                                if should_flush:
                                    flush_observability_writes()
                            direct_audit = await queue_direct_audit_review(
                                storage,
                                task=task,
                                verification=verification,
                            )
                            if direct_audit:
                                logger.warning(
                                    "Queued direct audit review %s for task %s after verifier outcome %s.",
                                    direct_audit.get("task_id"),
                                    task.task_id,
                                    verification.get("verdict"),
                                )
                        queued_review_task_id = None
                        if verifier_signal:
                            queued_review = await queue_task_attention_review(
                                storage,
                                task=task,
                                signal=verifier_signal,
                            )
                            queued_review_task_id = str((queued_review or {}).get("task_id") or "").strip() or None
                            logger.info(
                                "Verifier flagged task %s as %s%s",
                                task.task_id,
                                verification.get("verdict"),
                                f" and queued review {queued_review.get('task_id')}" if queued_review else "",
                            )
                        _upsert_verification_task(
                            storage,
                            task=task,
                            attempt=attempt,
                            verification=verification,
                            signal_id=str((verifier_signal or {}).get("signal_id") or "").strip() or None,
                            queued_review_task_id=queued_review_task_id,
                            direct_audit_task_id=str((direct_audit or {}).get("task_id") or "").strip() or None,
                            incident_id=str(verification.get("incident_id") or "").strip() or None,
                        )
                        storage.commit()
                except Exception as verifier_err:
                    try:
                        storage.rollback()
                    except Exception:
                        pass
                    logger.error(
                        "Failed to verify attempt %s for task %s: %s",
                        attempt_id,
                        task_id_str,
                        verifier_err,
                    )

                # --- REVIEW ---
                try:
                    attempt_id_str = getattr(attempt, "attempt_id", None)
                    self._mark_lane_progress(
                        lane or context.mode,
                        step="review",
                        label="Reviewing branch health",
                        detail="Updating plan review and attention signals",
                        attempt_id=attempt_id_str,
                    )
                    existing_review = dict(getattr(attempt, "plan_review", {}) or {})
                    review = existing_review
                    if not review or not str(review.get("recommendation") or "").strip():
                        review = await generate_plan_review(task, attempt, lane_model, storage)
                        storage.attempts.set_plan_review(attempt.attempt_id, review)
                        should_flush = enqueue_attempt_observability_artifact(
                            {
                                "task_id": task.task_id,
                                "attempt_id": attempt.attempt_id,
                                "session_id": task.session_id,
                                "artifact_kind": "plan_review",
                                "payload": dict(review or {}),
                            }
                        )
                        if should_flush:
                            flush_observability_writes()
                    attention_signal = emit_task_execution_attention_signal(
                        storage,
                        task=task,
                        attempt=attempt,
                        context=context,
                        plan_review=review,
                        error=error,
                    )
                    if attention_signal:
                        queued_review = await queue_task_attention_review(
                            storage,
                            task=task,
                            signal=attention_signal,
                        )
                        logger.info(
                            "Emitted task execution attention signal %s for task %s%s",
                            attention_signal.get("signal_kind"),
                            task.task_id,
                            f" and queued review {queued_review.get('task_id')}" if queued_review else "",
                        )
                    storage.commit()
                except Exception as review_err:
                    try:
                        storage.rollback()
                    except Exception:
                        pass
                    logger.error("Failed to generate plan review for attempt %s: %s", attempt_id_str, review_err)

            self._mark_lane_progress(
                lane or context.mode,
                step="complete",
                label="Attempt complete",
                detail=f"{'succeeded' if success else 'failed'} · {task.state.value.lower()}",
                attempt_id=getattr(attempt, "attempt_id", None),
            )

        except Exception as e:
            logger.exception(f"Fatal error in _run_task_cycle for {task_id}: {e}")
        finally:
            storage.session.close()

    def pause(self, lane: Optional[str] = None):
        normalized_lane = normalize_lane(lane)
        if normalized_lane:
            self._paused_lanes.add(normalized_lane)
            return
        self._paused = True

    def resume(self, lane: Optional[str] = None):
        normalized_lane = normalize_lane(lane)
        if normalized_lane:
            self._paused_lanes.discard(normalized_lane)
            return
        self._paused = False

    def stop_current(self, lane: Optional[str] = None):
        normalized_lane = normalize_lane(lane)
        if normalized_lane:
            process = self._current_processes.get(normalized_lane)
            if process:
                process.cancel()
                return True
            return False
        cancelled = False
        for process in self._current_processes.values():
            if process:
                process.cancel()
                cancelled = True
        return cancelled

    def pause_task(self, task_id: str) -> bool:
        updated = self._update_task_control_state(
            task_id,
            paused=True,
            state=TaskState.PENDING,
            attempt_outcome=AttemptOutcome.CANCELLED if task_id in {item for item in self._current_task_ids.values() if item} else None,
            reason="Paused by operator.",
        )
        if not updated:
            return False
        for lane_name, current_task_id in self._current_task_ids.items():
            if current_task_id == task_id and self._current_processes.get(lane_name):
                self._current_processes[lane_name].cancel()
        return True

    async def resume_task(self, task_id: str) -> bool:
        updated = self._update_task_control_state(
            task_id,
            paused=False,
            state=TaskState.PENDING,
        )
        if not updated:
            return False
        await self.enqueue(task_id)
        return True

    def stop_task(self, task_id: str) -> bool:
        updated = self._update_task_control_state(
            task_id,
            paused=False,
            state=TaskState.CANCELLED,
            attempt_outcome=AttemptOutcome.CANCELLED if task_id in {item for item in self._current_task_ids.values() if item} else None,
            reason="Cancelled by operator.",
        )
        if not updated:
            return False
        for lane_name, current_task_id in self._current_task_ids.items():
            if current_task_id == task_id and self._current_processes.get(lane_name):
                self._current_processes[lane_name].cancel()
        storage = self._storage_factory()
        try:
            cascades = storage.apply_dependency_cascade()
            if cascades:
                logger.info("Cancelled task %s triggered %s dependent cancellation(s).", task_id, cascades)
        finally:
            storage.close()
        return True

    async def replay_task(self, task_id: str, overrides: Optional[Dict[str, Any]] = None) -> bool:
        updated = self._update_task_control_state(
            task_id,
            paused=False,
            state=TaskState.PENDING,
            attempt_outcome=AttemptOutcome.CANCELLED if task_id in {item for item in self._current_task_ids.values() if item} else None,
            reason="Replayed by operator.",
            extra_constraints=overrides,
        )
        if not updated:
            return False
        await self.enqueue(task_id)
        return True

    def lane_status(self, lane: str) -> str:
        normalized_lane = normalize_lane(lane)
        if not normalized_lane:
            return "UNKNOWN"
        if not self._running:
            return "STOPPED"
        if self._paused or normalized_lane in self._paused_lanes:
            return "PAUSED"
        if self._current_processes.get(normalized_lane) is not None:
            return "RUNNING"
        return "IDLE"

    def _lane_runtime_snapshot(self, lane: str) -> Dict[str, object]:
        normalized_lane = normalize_lane(lane)
        snapshot: Dict[str, object] = {
            "status": self.lane_status(lane),
            "tier_health": self._tier_health.get(normalized_lane or "", "unknown"),
            "activity_mode": "UNKNOWN",
            "activity_label": "Unknown",
            "activity_reason": "",
            "queue_depth": 0,
            "current_task_id": None,
            "current_task_started_at": None,
            "current_task_title": "",
            "current_task_state": None,
            "active_attempt_id": None,
            "step": "",
            "step_label": "",
            "step_detail": "",
            "step_updated_at": None,
            "progress_label": "",
            "recent_steps": [],
            "ticker_items": [],
            "last_activity_at": None,
            "heartbeat_state": "unknown",
            "heartbeat_age_s": None,
        }
        if not normalized_lane:
            return snapshot

        snapshot["queue_depth"] = self._lane_queue(normalized_lane).qsize()
        current_task_id = self._current_task_ids.get(normalized_lane)
        started_at = _parse_timestamp(self._lane_started_at.get(normalized_lane))
        last_activity_at = _parse_timestamp(self._lane_last_activity_at.get(normalized_lane)) or started_at

        if current_task_id:
            snapshot["current_task_id"] = str(current_task_id)
        if started_at is not None:
            snapshot["current_task_started_at"] = started_at.isoformat()
        if last_activity_at is not None:
            snapshot["last_activity_at"] = last_activity_at.isoformat()
        snapshot.update(dict(self._lane_activity_details.get(normalized_lane) or {}))

        # Reconcile volatile in-memory lane activity with durable task/attempt state so
        # operator-facing status does not drift from the actual active branch.
        if current_task_id:
            storage = None
            try:
                storage = self._storage_factory()
                task = storage.tasks.get_by_id(str(current_task_id))
                if task is not None:
                    snapshot["current_task_title"] = str(getattr(task, "title", "") or snapshot.get("current_task_title") or "")
                    task_state = getattr(task, "state", None)
                    snapshot["current_task_state"] = getattr(task_state, "value", task_state) if task_state is not None else snapshot.get("current_task_state")
                    if started_at is None:
                        task_updated = _parse_timestamp(getattr(task, "updated_at", None) or getattr(task, "created_at", None))
                        if task_updated is not None:
                            snapshot["current_task_started_at"] = task_updated.isoformat()
                active_attempt_id = str(snapshot.get("active_attempt_id") or "").strip()
                attempt = None
                if active_attempt_id:
                    attempt = storage.attempts.get_by_id(active_attempt_id)
                    if attempt is None and task is not None:
                        snapshot["active_attempt_id"] = None
                if attempt is None and task is not None:
                    attempts = list(storage.attempts.get_by_task_id(str(current_task_id)) or [])
                    active_attempts = [
                        candidate
                        for candidate in attempts
                        if getattr(candidate, "outcome", None) is None and getattr(candidate, "ended_at", None) is None
                    ]
                    if active_attempts:
                        attempt = active_attempts[0]
                        snapshot["active_attempt_id"] = str(getattr(attempt, "attempt_id", "") or "")
                if attempt is not None:
                    attempt_started = _parse_timestamp(getattr(attempt, "started_at", None))
                    if attempt_started is not None:
                        snapshot["current_task_started_at"] = attempt_started.isoformat()
            except Exception:
                logger.debug("Failed to reconcile lane runtime snapshot for %s", normalized_lane, exc_info=True)
            finally:
                if storage is not None:
                    storage.session.close()

        now = datetime.now(timezone.utc)
        if last_activity_at is not None:
            heartbeat_age_s = max(0.0, (now - last_activity_at).total_seconds())
            snapshot["heartbeat_age_s"] = round(heartbeat_age_s, 2)
            if heartbeat_age_s <= 45:
                snapshot["heartbeat_state"] = "active"
            elif heartbeat_age_s <= 180:
                snapshot["heartbeat_state"] = "quiet"
            else:
                snapshot["heartbeat_state"] = "stalled"

        status = str(snapshot["status"] or "UNKNOWN")
        tier_health = str(snapshot["tier_health"] or "unknown").lower()
        if status == "STOPPED":
            snapshot["activity_mode"] = "STOPPED"
            snapshot["activity_label"] = "Stopped"
            snapshot["activity_reason"] = "Worker is offline."
        elif status == "PAUSED":
            snapshot["activity_mode"] = "PAUSED"
            snapshot["activity_label"] = "Paused"
            snapshot["activity_reason"] = "Execution is paused."
        elif tier_health == "error":
            snapshot["activity_mode"] = "OFFLINE"
            snapshot["activity_label"] = "Offline"
            snapshot["activity_reason"] = "Lane health is degraded."
        elif current_task_id:
            if snapshot["heartbeat_state"] == "stalled":
                snapshot["activity_mode"] = "STALLED"
                snapshot["activity_label"] = "Stalled"
                snapshot["activity_reason"] = str(snapshot.get("step_detail") or snapshot.get("step_label") or "Waiting for progress heartbeat.").strip()
            else:
                snapshot["activity_mode"] = "GENERATING"
                snapshot["activity_label"] = "Generating"
                snapshot["activity_reason"] = str(snapshot.get("step_detail") or snapshot.get("step_label") or snapshot.get("current_task_title") or "Making progress.").strip()
        elif snapshot["queue_depth"]:
            snapshot["activity_mode"] = "QUEUED"
            snapshot["activity_label"] = "Queued"
            snapshot["activity_reason"] = "Runnable work is queued."
        else:
            snapshot["activity_mode"] = "IDLE"
            snapshot["activity_label"] = "Idle"
            snapshot["activity_reason"] = "No runnable work is queued."
        return snapshot

    @property
    def status(self):
        return {
            "worker": "STOPPED" if not self._running else ("PAUSED" if self._paused else "RUNNING"),
            "global_paused": self._paused,
            "paused_lanes": sorted(self._paused_lanes),
            "tiers": self._tier_health,
            "lanes": {
                "trainer": self.lane_status("trainer"),
                "agent": self.lane_status("agent"),
            },
            "lane_details": {
                "trainer": self._lane_runtime_snapshot("trainer"),
                "agent": self._lane_runtime_snapshot("agent"),
            },
        }
