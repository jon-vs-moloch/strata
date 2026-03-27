"""
@module experimental.audit_registry
@purpose Persist auditable timeline and audit artifacts without requiring a schema migration.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4


TIMELINE_ARTIFACT_PREFIX = "audit_artifact:timeline"
AUDIT_ARTIFACT_PREFIX = "audit_artifact:audit"
ARTIFACT_INDEX_KEY = "audit_artifact:index"
MAX_AUDIT_INDEX = 400


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_hash(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _artifact_key(prefix: str, artifact_id: str) -> str:
    return f"{prefix}:{artifact_id}"


def _append_index(storage, row: Dict[str, Any]) -> None:
    rows = storage.parameters.peek_parameter(ARTIFACT_INDEX_KEY, default_value=[]) or []
    if not isinstance(rows, list):
        rows = []
    rows = [dict(item) for item in rows if isinstance(item, dict) and item.get("artifact_id") != row.get("artifact_id")]
    rows.append(dict(row))
    storage.parameters.set_parameter(
        ARTIFACT_INDEX_KEY,
        rows[-MAX_AUDIT_INDEX:],
        description="Recent durable audit and timeline artifacts.",
    )


def get_timeline_artifact(storage, artifact_id: str) -> Dict[str, Any]:
    payload = storage.parameters.peek_parameter(_artifact_key(TIMELINE_ARTIFACT_PREFIX, artifact_id), default_value={}) or {}
    return dict(payload) if isinstance(payload, dict) else {}


def get_audit_artifact(storage, artifact_id: str) -> Dict[str, Any]:
    payload = storage.parameters.peek_parameter(_artifact_key(AUDIT_ARTIFACT_PREFIX, artifact_id), default_value={}) or {}
    return dict(payload) if isinstance(payload, dict) else {}


def _build_task_trace_events(trace_summary: Dict[str, Any], *, timeline_id: str) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    task = dict(trace_summary.get("task") or {})
    if task:
        events.append(
            {
                "id": f"{timeline_id}:task_created",
                "timeline_id": timeline_id,
                "type": "task_created",
                "timestamp_utc": task.get("created_at") or _now(),
                "subject_id": task.get("task_id"),
                "source_trace_refs": [{"kind": "task", "task_id": task.get("task_id")}],
                "payload": {"task": task},
                "inferred": False,
            }
        )
    for idx, attempt in enumerate(trace_summary.get("attempts") or []):
        attempt = dict(attempt or {})
        outcome = str(attempt.get("outcome") or "").strip().lower()
        event_type = "attempt_started"
        if outcome == "succeeded":
            event_type = "attempt_completed"
        elif outcome in {"failed", "cancelled", "superseded"}:
            event_type = "attempt_failed"
        events.append(
            {
                "id": f"{timeline_id}:attempt:{idx}",
                "timeline_id": timeline_id,
                "type": event_type,
                "timestamp_utc": attempt.get("ended_at") or attempt.get("started_at") or _now(),
                "subject_id": attempt.get("attempt_id"),
                "source_trace_refs": [{"kind": "attempt", "attempt_id": attempt.get("attempt_id")}],
                "payload": attempt,
                "inferred": False,
            }
        )
    for idx, message in enumerate(trace_summary.get("messages") or []):
        message = dict(message or {})
        events.append(
            {
                "id": f"{timeline_id}:message:{idx}",
                "timeline_id": timeline_id,
                "type": "artifact_created",
                "timestamp_utc": message.get("created_at") or _now(),
                "actor_id": message.get("role"),
                "subject_id": message.get("message_id"),
                "source_trace_refs": [{"kind": "message", "message_id": message.get("message_id")}],
                "payload": message,
                "inferred": False,
            }
        )
    for idx, report in enumerate(trace_summary.get("associated_reports") or []):
        report = dict(report or {})
        events.append(
            {
                "id": f"{timeline_id}:report:{idx}",
                "timeline_id": timeline_id,
                "type": "artifact_created",
                "timestamp_utc": _now(),
                "subject_id": report.get("candidate_change_id"),
                "source_trace_refs": [{"kind": "report_ref", "candidate_change_id": report.get("candidate_change_id")}],
                "payload": report,
                "inferred": True,
                "inference_notes": ["Associated report was reconstructed from task constraints."],
            }
        )
    return events


def _build_session_trace_events(trace_summary: Dict[str, Any], *, timeline_id: str) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for idx, message in enumerate(trace_summary.get("messages") or []):
        message = dict(message or {})
        events.append(
            {
                "id": f"{timeline_id}:session_message:{idx}",
                "timeline_id": timeline_id,
                "type": "artifact_created",
                "timestamp_utc": message.get("created_at") or _now(),
                "actor_id": message.get("role"),
                "subject_id": message.get("message_id"),
                "source_trace_refs": [{"kind": "message", "message_id": message.get("message_id")}],
                "payload": message,
                "inferred": False,
            }
        )
    for idx, task in enumerate(trace_summary.get("tasks") or []):
        task = dict(task or {})
        events.append(
            {
                "id": f"{timeline_id}:session_task:{idx}",
                "timeline_id": timeline_id,
                "type": "task_created",
                "timestamp_utc": task.get("created_at") or _now(),
                "subject_id": task.get("task_id"),
                "source_trace_refs": [{"kind": "task", "task_id": task.get("task_id")}],
                "payload": task,
                "inferred": False,
            }
        )
    return events


def _build_eval_trace_events(trace_summary: Dict[str, Any], *, timeline_id: str) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for idx, run in enumerate(trace_summary.get("benchmark_runs") or []):
        run = dict(run or {})
        events.append(
            {
                "id": f"{timeline_id}:benchmark:{idx}",
                "timeline_id": timeline_id,
                "type": "mutation_compared",
                "timestamp_utc": _now(),
                "source_trace_refs": [{"kind": "benchmark_run", "run_label": run.get("run_label")}],
                "payload": run,
                "inferred": False,
            }
        )
    for idx, run in enumerate(trace_summary.get("structured_runs") or []):
        run = dict(run or {})
        events.append(
            {
                "id": f"{timeline_id}:structured:{idx}",
                "timeline_id": timeline_id,
                "type": "mutation_compared",
                "timestamp_utc": _now(),
                "source_trace_refs": [{"kind": "structured_run", "run_label": run.get("run_label"), "suite_name": run.get("suite_name")}],
                "payload": run,
                "inferred": False,
            }
        )
    return events


def _build_generic_events(trace_summary: Dict[str, Any], *, timeline_id: str) -> List[Dict[str, Any]]:
    return [
        {
            "id": f"{timeline_id}:generic:0",
            "timeline_id": timeline_id,
            "type": "artifact_created",
            "timestamp_utc": _now(),
            "source_trace_refs": [{"kind": "generic_trace"}],
            "payload": dict(trace_summary or {}),
            "inferred": False,
        }
    ]


def build_trace_timeline(
    trace_kind: str,
    trace_summary: Dict[str, Any],
    *,
    timeline_id: Optional[str] = None,
) -> Dict[str, Any]:
    artifact_id = timeline_id or f"timeline_{uuid4().hex[:12]}"
    normalized_kind = str(trace_kind or "generic_trace").strip() or "generic_trace"
    if normalized_kind == "task_trace":
        events = _build_task_trace_events(trace_summary, timeline_id=artifact_id)
    elif normalized_kind == "session_trace":
        events = _build_session_trace_events(trace_summary, timeline_id=artifact_id)
    elif normalized_kind == "eval_trace":
        events = _build_eval_trace_events(trace_summary, timeline_id=artifact_id)
    else:
        events = _build_generic_events(trace_summary, timeline_id=artifact_id)
    return {
        "artifact_id": artifact_id,
        "artifact_type": "timeline_artifact",
        "trace_kind": normalized_kind,
        "created_at_utc": _now(),
        "parent_artifact_ids": [],
        "events": events,
        "metadata": {"event_count": len(events)},
        "content_hash": _content_hash({"trace_kind": normalized_kind, "events": events}),
    }


def persist_timeline_artifact(
    storage,
    *,
    trace_kind: str,
    trace_summary: Dict[str, Any],
    applicable_spec_version: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    artifact = build_trace_timeline(trace_kind, trace_summary)
    artifact["applicable_spec_version"] = applicable_spec_version
    artifact["metadata"] = {**dict(artifact.get("metadata") or {}), **dict(metadata or {})}
    storage.parameters.set_parameter(
        _artifact_key(TIMELINE_ARTIFACT_PREFIX, artifact["artifact_id"]),
        artifact,
        description=f"Durable {trace_kind} timeline artifact.",
    )
    _append_index(
        storage,
        {
            "artifact_id": artifact["artifact_id"],
            "artifact_type": artifact["artifact_type"],
            "trace_kind": trace_kind,
            "created_at_utc": artifact["created_at_utc"],
        },
    )
    return artifact


def _metric_verdict(*, alignment: str = "pass", interpretability: str = "pass", efficiency: str = "pass", throughput: str = "pass") -> Dict[str, str]:
    return {
        "alignment": alignment,
        "interpretability": interpretability,
        "efficiency": efficiency,
        "throughput": throughput,
    }


def _overall_verdict(judgments: List[Dict[str, Any]]) -> Dict[str, Any]:
    if any(str((item.get("metric_verdict") or {}).get("alignment") or "pass") == "fail" for item in judgments):
        status = "fail"
    elif any("warn" in (item.get("metric_verdict") or {}).values() for item in judgments):
        status = "warn"
    else:
        status = "pass"
    return {"status": status, "judgment_count": len(judgments)}


def audit_timeline_artifact(
    storage,
    *,
    timeline_artifact: Dict[str, Any],
    spec_version_used: Optional[str],
    rationale: str,
    confidence: float = 0.7,
    extra_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    judgments: List[Dict[str, Any]] = []
    for event in timeline_artifact.get("events") or []:
        event = dict(event or {})
        metric_verdict = _metric_verdict()
        compliance_status = "pass"
        rationale_bits: List[str] = []
        if not (event.get("source_trace_refs") or []):
            metric_verdict["interpretability"] = "warn"
            rationale_bits.append("Event has no direct evidence refs.")
        if bool(event.get("inferred")):
            metric_verdict["interpretability"] = "warn"
            rationale_bits.append("Event was inferred during normalization.")
        judgments.append(
            {
                "event_id": event.get("id"),
                "metric_verdict": metric_verdict,
                "compliance_status": compliance_status,
                "rationale": " ".join(rationale_bits).strip() or "Event is auditable against the current governing spec.",
                "evidence_refs": list(event.get("source_trace_refs") or []),
            }
        )
    artifact_id = f"audit_{uuid4().hex[:12]}"
    artifact = {
        "artifact_id": artifact_id,
        "artifact_type": "audit_artifact",
        "created_at_utc": _now(),
        "audit_target_type": "timeline",
        "audit_target_id": timeline_artifact.get("artifact_id"),
        "spec_version_used": spec_version_used,
        "event_judgments": judgments,
        "summary_verdict": _overall_verdict(judgments),
        "rationale": rationale,
        "evidence_refs": [{"kind": "timeline_artifact", "artifact_id": timeline_artifact.get("artifact_id")}],
        "confidence": max(0.0, min(1.0, float(confidence or 0.0))),
        "divergence_notes": list((extra_context or {}).get("divergence_notes") or []),
        "parent_artifact_ids": [timeline_artifact.get("artifact_id")],
        "metadata": dict(extra_context or {}),
        "content_hash": _content_hash({"timeline": timeline_artifact.get("content_hash"), "judgments": judgments}),
    }
    storage.parameters.set_parameter(
        _artifact_key(AUDIT_ARTIFACT_PREFIX, artifact_id),
        artifact,
        description=f"Durable audit artifact for {timeline_artifact.get('artifact_id')}.",
    )
    _append_index(
        storage,
        {
            "artifact_id": artifact_id,
            "artifact_type": artifact["artifact_type"],
            "audit_target_type": artifact["audit_target_type"],
            "audit_target_id": artifact["audit_target_id"],
            "created_at_utc": artifact["created_at_utc"],
        },
    )
    return artifact


def audit_stored_artifact(
    storage,
    *,
    artifact_type: str,
    artifact_id: str,
    spec_version_used: Optional[str],
    rationale: str,
) -> Dict[str, Any]:
    normalized_type = str(artifact_type or "").strip().lower()
    if normalized_type == "audit":
        target = get_audit_artifact(storage, artifact_id)
    else:
        target = get_timeline_artifact(storage, artifact_id)
    if not target:
        raise ValueError(f"Artifact not found: {artifact_type}:{artifact_id}")
    synthetic_timeline = {
        "artifact_id": f"timeline_{uuid4().hex[:12]}",
        "artifact_type": "timeline_artifact",
        "trace_kind": f"{normalized_type}_artifact",
        "created_at_utc": _now(),
        "parent_artifact_ids": [artifact_id],
        "events": [
            {
                "id": f"artifact_audit:{artifact_id}",
                "timeline_id": artifact_id,
                "type": "audit_completed" if normalized_type == "audit" else "artifact_created",
                "timestamp_utc": target.get("created_at_utc") or _now(),
                "subject_id": artifact_id,
                "source_trace_refs": [{"kind": f"{normalized_type}_artifact", "artifact_id": artifact_id}],
                "payload": {
                    "artifact_type": target.get("artifact_type"),
                    "content_hash": target.get("content_hash"),
                    "summary_verdict": target.get("summary_verdict"),
                },
                "inferred": False,
            }
        ],
        "metadata": {"recursive_target_type": normalized_type},
        "content_hash": _content_hash(target),
    }
    return audit_timeline_artifact(
        storage,
        timeline_artifact=synthetic_timeline,
        spec_version_used=spec_version_used,
        rationale=rationale,
        confidence=0.65,
        extra_context={"recursive_target_type": normalized_type, "recursive_target_id": artifact_id},
    )
