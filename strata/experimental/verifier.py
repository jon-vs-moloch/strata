"""
@module experimental.verifier
@purpose Lightweight post-output verification with adaptive annealing.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from strata.core.lanes import infer_lane_from_task
from strata.experimental.trace_review import build_task_trace_summary
from strata.feedback.signals import register_feedback_signal
from strata.storage.models import AttemptModel, AttemptOutcome, TaskModel


VERIFIER_REGISTRY_KEY = "output_verifier_registry"


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


def _deterministic_contradictions(summary: Dict[str, Any]) -> List[str]:
    text_fragments = _text_fragments(summary)
    contradictions: List[str] = []
    for item in (summary.get("repo_fact_checks") or []):
        path = str(item.get("path") or "").strip()
        if not path or not item.get("exists"):
            continue
        if any(
            path in fragment
            and any(phrase in fragment.lower() for phrase in ("missing", "does not exist", "contains neither"))
            for fragment in text_fragments
        ):
            contradictions.append(path)
    return contradictions


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


def _record_registry_result(storage, *, mode: str, artifact: Dict[str, Any]) -> Dict[str, Any]:
    registry = storage.parameters.peek_parameter(VERIFIER_REGISTRY_KEY, default_value=_default_registry()) or _default_registry()
    registry = dict(registry)
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
    storage.parameters.set_parameter(
        VERIFIER_REGISTRY_KEY,
        registry,
        description="Rolling verifier outcomes used to anneal lightweight post-output verification.",
    )
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

    registry = storage.parameters.peek_parameter(VERIFIER_REGISTRY_KEY, default_value=_default_registry()) or _default_registry()
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


async def verify_task_output(storage, *, task: TaskModel, attempt: AttemptModel, model_adapter, context) -> Optional[Dict[str, Any]]:
    mode = str(getattr(context, "mode", infer_lane_from_task(task) or "unknown") or "unknown").strip().lower()
    policy = select_verification_policy(storage, mode=mode)
    summary = _trim_summary_for_verifier(build_task_trace_summary(storage, task_id=task.task_id, message_limit=6))
    contradictions = _deterministic_contradictions(summary)

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
                "Deterministic repo checks contradicted the output's filesystem claim."
            ],
            "failure_modes": ["grounding", "overclaim"],
            "recommended_action": "escalate",
            "checks_run": ["repo_fact_checks", "attempt_note_excerpt_scan"],
            "claims_examined": contradictions,
            "residual_risk": [],
        }
    else:
        prompt = f"""
You are Strata's lightweight verifier.
Given the task context, the most recent attempt, and the attached evidence, decide whether the output is good and correct.
Do not rewrite the output. Judge it.

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
{json.dumps(summary, indent=2)}
""".strip()
        response = await model_adapter.chat([{"role": "user", "content": prompt}], temperature=0.0)
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
    artifact["mode"] = mode
    artifact["task_id"] = task.task_id
    artifact["attempt_id"] = attempt.attempt_id
    artifact["policy"] = policy
    artifact["summary"] = summary
    artifact["deterministic_contradictions"] = contradictions

    attempt_artifacts = dict(attempt.artifacts or {})
    attempt_artifacts["verifier"] = artifact
    attempt.artifacts = attempt_artifacts

    constraints = dict(task.constraints or {})
    reviews = list(constraints.get("verifier_reviews") or [])
    reviews.append(
        {
            "recorded_at": artifact.get("recorded_at"),
            "mode": mode,
            "verification_kind": artifact.get("verification_kind"),
            "verdict": artifact.get("verdict"),
            "confidence": artifact.get("confidence"),
            "recommended_action": artifact.get("recommended_action"),
            "failure_modes": artifact.get("failure_modes") or [],
            "deterministic_contradictions": contradictions,
        }
    )
    constraints["verifier_reviews"] = reviews[-12:]
    task.constraints = constraints
    _record_registry_result(storage, mode=mode, artifact=artifact)
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
