"""
@module experimental.verifier
@purpose Lightweight post-output verification with adaptive annealing.
"""

from __future__ import annotations

import json
import re
import asyncio
from datetime import datetime, timezone
from threading import Lock
from time import monotonic
from typing import Any, Dict, List, Optional, Tuple

from strata.core.lanes import infer_lane_from_task
from strata.experimental.trace_review import build_task_trace_summary
from strata.feedback.signals import register_feedback_signal
from strata.observability.writer import enqueue_attempt_observability_artifact, flush_observability_writes
from strata.storage.models import AttemptModel, AttemptOutcome, TaskModel


VERIFIER_REGISTRY_KEY = "output_verifier_registry"
_VERIFIER_REGISTRY_LOCK = Lock()
_VERIFIER_REGISTRY_STATE: Dict[str, Any] | None = None
_VERIFIER_REGISTRY_DIRTY = False
_VERIFIER_REGISTRY_LAST_PERSISTED_AT = 0.0
_VERIFIER_REGISTRY_MIN_PERSIST_INTERVAL_S = 30.0


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text_fragments(summary: Dict[str, Any]) -> List[str]:
    return [
        str((summary.get("task") or {}).get("title") or ""),
        str((summary.get("task") or {}).get("description") or ""),
        *[str((attempt or {}).get("reason") or "") for attempt in (summary.get("attempts") or [])],
        *[str((message or {}).get("content") or "") for message in (summary.get("messages") or [])],
        *[str((note or {}).get("excerpt") or "") for note in (summary.get("attempt_note_excerpts") or [])],
    ]


def repo_fact_contradictions(*, text_fragments: List[str], repo_fact_checks: List[Dict[str, Any]]) -> List[str]:
    contradictions: List[str] = []
    for item in repo_fact_checks or []:
        path = str(item.get("path") or "").strip()
        if not path or not item.get("exists"):
            continue
        if any(
            path in fragment
            and any(
                phrase in fragment.lower()
                for phrase in ("missing", "does not exist", "do not exist", "contains neither")
            )
            for fragment in text_fragments
        ):
            contradictions.append(path)
    return contradictions


def _deterministic_contradictions(summary: Dict[str, Any]) -> List[str]:
    return repo_fact_contradictions(
        text_fragments=_text_fragments(summary),
        repo_fact_checks=list(summary.get("repo_fact_checks") or []),
    )


def _default_registry() -> Dict[str, Any]:
    return {
        "by_mode": {},
        "overall": {
            "verified_count": 0,
            "flawed_count": 0,
            "uncertain_count": 0,
            "deterministic_contradictions": 0,
            "model_verification_count": 0,
        },
    }


def _clone_registry(registry: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "by_mode": {
            str(mode): dict(bucket or {})
            for mode, bucket in dict(registry.get("by_mode") or {}).items()
        },
        "overall": dict(registry.get("overall") or {}),
    }


def _get_registry(storage=None) -> Dict[str, Any]:
    global _VERIFIER_REGISTRY_STATE
    with _VERIFIER_REGISTRY_LOCK:
        if _VERIFIER_REGISTRY_STATE is not None:
            return _clone_registry(_VERIFIER_REGISTRY_STATE)
    loaded = {}
    if storage is not None:
        try:
            loaded = storage.parameters.peek_parameter(VERIFIER_REGISTRY_KEY, default_value=_default_registry()) or _default_registry()
        except Exception:
            loaded = _default_registry()
    registry = _clone_registry(loaded or _default_registry())
    with _VERIFIER_REGISTRY_LOCK:
        if _VERIFIER_REGISTRY_STATE is None:
            _VERIFIER_REGISTRY_STATE = _clone_registry(registry)
        return _clone_registry(_VERIFIER_REGISTRY_STATE)


def _persist_registry_if_due() -> None:
    global _VERIFIER_REGISTRY_DIRTY, _VERIFIER_REGISTRY_LAST_PERSISTED_AT
    now = monotonic()
    with _VERIFIER_REGISTRY_LOCK:
        if not _VERIFIER_REGISTRY_DIRTY:
            return
        if (now - _VERIFIER_REGISTRY_LAST_PERSISTED_AT) < _VERIFIER_REGISTRY_MIN_PERSIST_INTERVAL_S:
            return
        payload = _clone_registry(_VERIFIER_REGISTRY_STATE or _default_registry())
    try:
        from strata.storage.services.main import StorageManager

        storage = StorageManager()
        try:
            storage.parameters.set_parameter(
                VERIFIER_REGISTRY_KEY,
                payload,
                description="Rolling verifier outcomes used to anneal lightweight post-output verification.",
            )
            storage.commit()
        finally:
            storage.close()
    except Exception:
        return
    with _VERIFIER_REGISTRY_LOCK:
        _VERIFIER_REGISTRY_DIRTY = False
        _VERIFIER_REGISTRY_LAST_PERSISTED_AT = now


def _record_registry_result(storage, *, mode: str, artifact: Dict[str, Any]) -> Dict[str, Any]:
    global _VERIFIER_REGISTRY_STATE, _VERIFIER_REGISTRY_DIRTY
    registry = _get_registry(storage)
    registry.setdefault("by_mode", {})
    registry.setdefault("overall", {})
    mode_bucket = dict(registry["by_mode"].get(mode) or {})
    overall_bucket = dict(registry.get("overall") or {})

    def _apply(bucket: Dict[str, Any]) -> Dict[str, Any]:
        bucket["verified_count"] = int(bucket.get("verified_count", 0) or 0) + 1
        verdict = str(artifact.get("verdict") or "uncertain").strip().lower()
        if verdict == "flawed":
            bucket["flawed_count"] = int(bucket.get("flawed_count", 0) or 0) + 1
        if verdict == "uncertain":
            bucket["uncertain_count"] = int(bucket.get("uncertain_count", 0) or 0) + 1
        if artifact.get("deterministic_contradictions"):
            bucket["deterministic_contradictions"] = int(bucket.get("deterministic_contradictions", 0) or 0) + 1
        if artifact.get("verification_kind") == "model":
            bucket["model_verification_count"] = int(bucket.get("model_verification_count", 0) or 0) + 1
        return bucket

    registry["by_mode"][mode] = _apply(mode_bucket)
    registry["overall"] = _apply(overall_bucket)
    with _VERIFIER_REGISTRY_LOCK:
        _VERIFIER_REGISTRY_STATE = _clone_registry(registry)
        _VERIFIER_REGISTRY_DIRTY = True
    return registry


def select_verification_policy(storage, *, mode: str, window: int = 24) -> Dict[str, Any]:
    mode = str(mode or "unknown").strip().lower() or "unknown"
    rows: List[Tuple[AttemptModel, TaskModel]] = (
        storage.session.query(AttemptModel, TaskModel)
        .join(TaskModel, AttemptModel.task_id == TaskModel.task_id)
        .order_by(AttemptModel.started_at.desc())
        .limit(max(1, int(window or 24)))
        .all()
    )
    relevant: List[AttemptModel] = []
    for attempt, task in rows:
        if infer_lane_from_task(task) == mode:
            relevant.append(attempt)
    sample_count = len(relevant)
    failed_count = sum(1 for attempt in relevant if attempt.outcome == AttemptOutcome.FAILED)
    recent_error_rate = failed_count / sample_count if sample_count else 1.0

    registry = _get_registry(storage)
    mode_bucket = dict((registry.get("by_mode") or {}).get(mode) or {})
    flawed_count = int(mode_bucket.get("flawed_count", 0) or 0)
    uncertain_count = int(mode_bucket.get("uncertain_count", 0) or 0)
    verified_count = int(mode_bucket.get("verified_count", 0) or 0)
    verifier_issue_rate = (flawed_count + uncertain_count) / verified_count if verified_count else 1.0

    if sample_count < 8 or recent_error_rate >= 0.18 or verifier_issue_rate >= 0.22:
        cadence = 1
    elif sample_count < 24 or recent_error_rate >= 0.08 or verifier_issue_rate >= 0.12:
        cadence = 2
    else:
        cadence = 4

    next_index = sample_count + 1
    should_verify = cadence == 1 or (next_index % cadence == 0)
    return {
        "mode": mode,
        "sample_count": sample_count,
        "recent_error_rate": round(recent_error_rate, 4),
        "verifier_issue_rate": round(verifier_issue_rate, 4),
        "cadence": cadence,
        "should_verify": should_verify,
    }


def _trim_summary_for_verifier(summary: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "task": summary.get("task"),
        "attempt_count": summary.get("attempt_count"),
        "attempts": list(summary.get("attempts") or [])[:1],
        "messages": list(summary.get("messages") or [])[-2:],
        "repo_fact_checks": list(summary.get("repo_fact_checks") or []),
        "attempt_note_excerpts": list(summary.get("attempt_note_excerpts") or [])[:1],
    }


def _coerce_verification_summary(
    *,
    artifact_kind: str,
    summary: Optional[Dict[str, Any]] = None,
    text_fragments: Optional[List[str]] = None,
    repo_fact_checks: Optional[List[Dict[str, Any]]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if summary:
        payload = dict(summary)
    else:
        payload = {}
    fragments = [str(item) for item in (text_fragments or []) if str(item).strip()]
    payload.setdefault("task", {})
    payload["artifact_kind"] = str(artifact_kind or "step").strip() or "step"
    payload["messages"] = [
        {"role": "system", "content": fragment}
        for fragment in fragments[:8]
    ]
    payload["repo_fact_checks"] = list(repo_fact_checks or payload.get("repo_fact_checks") or [])
    if metadata:
        payload["metadata"] = dict(metadata)
    return payload


def _normalize_verifier_result(parsed: Dict[str, Any], *, verification_kind: str) -> Dict[str, Any]:
    verdict = str(parsed.get("verdict") or "uncertain").strip().lower()
    if verdict not in {"good", "flawed", "uncertain"}:
        verdict = "uncertain"
    recommended_action = str(parsed.get("recommended_action") or "verify_more").strip().lower()
    if recommended_action not in {"accept", "revise", "verify_more", "escalate", "audit"}:
        recommended_action = "verify_more"
    try:
        confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.5) or 0.5)))
    except Exception:
        confidence = 0.5
    return {
        "status": "ok",
        "verification_kind": verification_kind,
        "verdict": verdict,
        "confidence": confidence,
        "reasons": [str(item) for item in (parsed.get("reasons") or []) if str(item).strip()],
        "failure_modes": [str(item) for item in (parsed.get("failure_modes") or []) if str(item).strip()],
        "recommended_action": recommended_action,
        "checks_run": [str(item) for item in (parsed.get("checks_run") or []) if str(item).strip()],
        "claims_examined": [str(item) for item in (parsed.get("claims_examined") or []) if str(item).strip()],
        "residual_risk": [str(item) for item in (parsed.get("residual_risk") or []) if str(item).strip()],
    }


async def verify_task_output(
    storage,
    *,
    task: TaskModel,
    attempt: AttemptModel,
    model_adapter,
    context,
    progress_fn=None,
) -> Optional[Dict[str, Any]]:
    mode = str(getattr(context, "mode", infer_lane_from_task(task) or "unknown") or "unknown").strip().lower()
    if progress_fn:
        progress_fn(step="verification", label="Preparing verifier", detail=str(task.title or task.task_id), progress_label="prepare verifier")
    summary = _trim_summary_for_verifier(build_task_trace_summary(storage, task_id=task.task_id, message_limit=6))
    artifact = await verify_artifact(
        storage,
        mode=mode,
        model_adapter=model_adapter,
        artifact_kind="task_output",
        summary=summary,
        task=task,
        attempt=attempt,
        progress_fn=progress_fn,
    )
    return artifact


async def verify_artifact(
    storage,
    *,
    mode: str,
    model_adapter=None,
    artifact_kind: str = "step",
    summary: Optional[Dict[str, Any]] = None,
    text_fragments: Optional[List[str]] = None,
    repo_fact_checks: Optional[List[Dict[str, Any]]] = None,
    task: Optional[TaskModel] = None,
    attempt: Optional[AttemptModel] = None,
    metadata: Optional[Dict[str, Any]] = None,
    progress_fn=None,
) -> Optional[Dict[str, Any]]:
    normalized_mode = str(mode or "unknown").strip().lower() or "unknown"
    policy = select_verification_policy(storage, mode=normalized_mode)
    summary_payload = _coerce_verification_summary(
        artifact_kind=artifact_kind,
        summary=summary,
        text_fragments=text_fragments,
        repo_fact_checks=repo_fact_checks,
        metadata=metadata,
    )
    contradictions = _deterministic_contradictions(summary_payload)

    if not policy.get("should_verify") and not contradictions:
        return None

    artifact: Dict[str, Any]
    if contradictions:
        artifact = {
            "status": "ok",
            "verification_kind": "deterministic",
            "verdict": "flawed",
            "confidence": 0.98,
            "reasons": [
                "Deterministic repo checks contradicted the artifact's filesystem claim."
            ],
            "failure_modes": ["grounding", "overclaim"],
            "recommended_action": "escalate",
            "checks_run": ["repo_fact_checks", "artifact_fragment_scan"],
            "claims_examined": contradictions,
            "residual_risk": [],
        }
    elif model_adapter is None:
        artifact = {
            "status": "ok",
            "verification_kind": "skipped",
            "verdict": "uncertain",
            "confidence": 0.1,
            "reasons": ["No verifier model was available for non-deterministic checks."],
            "failure_modes": ["other"],
            "recommended_action": "verify_more",
            "checks_run": ["deterministic_only"],
            "claims_examined": [],
            "residual_risk": ["The artifact was not reviewed by a verifier model."],
        }
    else:
        if progress_fn:
            progress_fn(
                step="verification",
                label="Running verifier model",
                detail=str(getattr(task, "title", None) or getattr(attempt, "attempt_id", None) or artifact_kind),
                progress_label="verifier model",
            )
        prompt = f"""
You are Strata's lightweight verifier.
Given the artifact context and the attached evidence, decide whether the thing that just happened is good and correct.
Do not rewrite it. Judge it.

Return only JSON with this schema:
{{
  "verdict": "good|flawed|uncertain",
  "confidence": 0.0,
  "reasons": ["short reason"],
  "failure_modes": ["grounding|overclaim|incomplete|bad_reasoning|tool_misread|policy_miss|other"],
  "recommended_action": "accept|revise|verify_more|escalate|audit",
  "checks_run": ["which checks you relied on"],
  "claims_examined": ["specific claim"],
  "residual_risk": ["what could still be wrong"]
}}

Policy:
- Be stricter when evidence is indirect, summarized, cached, or incomplete.
- If the output claims a fact that the evidence does not justify, mark it flawed or uncertain.
- Prefer "uncertain" over endorsing an overconfident answer.

Verification policy:
{json.dumps(policy, indent=2)}

Verification summary:
{json.dumps(summary_payload, indent=2)}
""".strip()
        try:
            response = await asyncio.wait_for(
                model_adapter.chat([{"role": "user", "content": prompt}], temperature=0.0),
                timeout=90.0,
            )
        except asyncio.TimeoutError:
            response = {
                "content": "",
                "status": "error",
                "message": "Verifier model timed out after 90s",
                "usage": {},
            }
        raw_content = response.get("content", "")
        try:
            parsed = model_adapter.extract_structured_object(raw_content)
            if "error" in parsed:
                raise ValueError(str(parsed["error"]))
            artifact = _normalize_verifier_result(parsed, verification_kind="model")
        except Exception as exc:
            artifact = {
                "status": "ok",
                "verification_kind": "model",
                "verdict": "uncertain",
                "confidence": 0.2,
                "reasons": [f"Verifier response could not be parsed reliably: {exc}"],
                "failure_modes": ["other"],
                "recommended_action": "verify_more",
                "checks_run": ["model_verifier_parse_fallback"],
                "claims_examined": [],
                "residual_risk": ["Verifier output was malformed or mixed-format."],
            }
        artifact["raw_response"] = raw_content[:1600]

    artifact["recorded_at"] = _utcnow_iso()
    artifact["mode"] = normalized_mode
    artifact["artifact_kind"] = str(artifact_kind or "step").strip() or "step"
    artifact["task_id"] = getattr(task, "task_id", None)
    artifact["attempt_id"] = getattr(attempt, "attempt_id", None)
    artifact["policy"] = policy
    artifact["summary"] = summary_payload
    artifact["deterministic_contradictions"] = contradictions

    if attempt is not None:
        should_flush = enqueue_attempt_observability_artifact(
            {
                "task_id": getattr(task, "task_id", None) or getattr(attempt, "task_id", None),
                "attempt_id": getattr(attempt, "attempt_id", None),
                "session_id": getattr(task, "session_id", None),
                "artifact_kind": "verifier_review",
                "payload": {
                    "recorded_at": artifact.get("recorded_at"),
                    "mode": normalized_mode,
                    "artifact_kind": artifact.get("artifact_kind"),
                    "verification_kind": artifact.get("verification_kind"),
                    "verdict": artifact.get("verdict"),
                    "confidence": artifact.get("confidence"),
                    "recommended_action": artifact.get("recommended_action"),
                    "failure_modes": artifact.get("failure_modes") or [],
                    "deterministic_contradictions": contradictions,
                },
            }
        )
        if should_flush:
            flush_observability_writes()
    _record_registry_result(storage, mode=normalized_mode, artifact=artifact)
    return artifact


def emit_verifier_attention_signal(storage, *, task: TaskModel, verification: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not verification:
        return None
    verdict = str(verification.get("verdict") or "").strip().lower()
    confidence = float(verification.get("confidence") or 0.0)
    recommended_action = str(verification.get("recommended_action") or "").strip().lower()

    if verdict == "good":
        return None

    signal_kind = "surprise" if verdict == "flawed" else "importance"
    if verdict == "uncertain" and confidence < 0.6 and recommended_action not in {"audit", "escalate"}:
        signal_kind = "highlight"

    return register_feedback_signal(
        storage,
        source_type="output_verification",
        source_id=str(verification.get("attempt_id") or task.task_id),
        signal_kind=signal_kind,
        signal_value=str(verification.get("verdict") or "uncertain"),
        source_actor="lightweight_verifier",
        session_id=str(task.session_id or "").strip(),
        source_preview=str(task.title or task.description or task.task_id)[:220],
        note=" ".join(
            part
            for part in [
                f"verification_kind={verification.get('verification_kind')}",
                f"recommended_action={recommended_action or 'verify_more'}",
                f"failure_modes={','.join(verification.get('failure_modes') or [])}",
                f"confidence={round(confidence, 3)}",
            ]
            if str(part).strip()
        )[:500],
        expected_outcome="good_and_correct",
        observed_outcome=str(verification.get("verdict") or "uncertain"),
        metadata={
            "task_id": task.task_id,
            "attempt_id": verification.get("attempt_id"),
            "mode": verification.get("mode"),
            "recommended_action": recommended_action,
            "confidence": confidence,
            "verification_kind": verification.get("verification_kind"),
            "failure_modes": list(verification.get("failure_modes") or []),
            "deterministic_contradictions": list(verification.get("deterministic_contradictions") or []),
        },
    )
