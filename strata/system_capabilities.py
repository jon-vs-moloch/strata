"""
@module system_capabilities
@purpose Canonical identities for Strata's own reusable system machinery.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


AUDIT_TRACE_REVIEW_PROCEDURE_ID = "audit_trace_review"
TASK_DECOMPOSITION_PROCEDURE_ID = "task_decomposition"
PROCESS_REPAIR_PROCEDURE_ID = "process_repair"
VERIFICATION_REVIEW_PROCEDURE_ID = "verification_review"
KNOWLEDGE_REFRESH_PROCEDURE_ID = "knowledge_refresh"
BOOTSTRAP_CYCLE_PROCEDURE_ID = "bootstrap_cycle"

SYSTEM_PROCEDURE_TITLES: Dict[str, str] = {
    AUDIT_TRACE_REVIEW_PROCEDURE_ID: "Audit Trace Review",
    TASK_DECOMPOSITION_PROCEDURE_ID: "Task Decomposition",
    PROCESS_REPAIR_PROCEDURE_ID: "Process Repair",
    VERIFICATION_REVIEW_PROCEDURE_ID: "Verification Review",
    KNOWLEDGE_REFRESH_PROCEDURE_ID: "Knowledge Refresh",
    BOOTSTRAP_CYCLE_PROCEDURE_ID: "Bootstrap Cycle",
}


def canonical_system_procedure_id(
    *,
    system_job_kind: Optional[str] = None,
    process_name: Optional[str] = None,
    task_type: Optional[str] = None,
) -> Optional[str]:
    normalized_job = str(system_job_kind or "").strip().lower()
    normalized_process = str(process_name or "").strip().lower()
    normalized_task_type = str(task_type or "").strip().upper()
    if normalized_job == "bootstrap_cycle":
        return BOOTSTRAP_CYCLE_PROCEDURE_ID
    if normalized_job == "trace_review":
        return AUDIT_TRACE_REVIEW_PROCEDURE_ID
    if normalized_process == "verification_process":
        return VERIFICATION_REVIEW_PROCEDURE_ID
    if normalized_process:
        return PROCESS_REPAIR_PROCEDURE_ID
    if normalized_task_type == "KNOWLEDGE_REFRESH":
        return KNOWLEDGE_REFRESH_PROCEDURE_ID
    if normalized_task_type == "DECOMP":
        return TASK_DECOMPOSITION_PROCEDURE_ID
    return None


def procedure_title(procedure_id: Optional[str]) -> str:
    normalized = str(procedure_id or "").strip()
    return SYSTEM_PROCEDURE_TITLES.get(normalized, normalized.replace("_", " ").title() or "Procedure")


def bind_system_procedure(
    constraints: Optional[Dict[str, Any]] = None,
    *,
    procedure_id: Optional[str],
    capability_kind: str,
    capability_name: str,
) -> Dict[str, Any]:
    updated = dict(constraints or {})
    normalized_procedure_id = str(procedure_id or "").strip()
    if normalized_procedure_id:
        updated["procedure_id"] = normalized_procedure_id
        updated["procedure_title"] = procedure_title(normalized_procedure_id)
    updated["system_capability"] = {
        "kind": str(capability_kind or "").strip() or "process",
        "name": str(capability_name or "").strip() or normalized_procedure_id or "system_capability",
        "procedure_id": normalized_procedure_id or None,
    }
    return updated
