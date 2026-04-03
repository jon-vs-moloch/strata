"""
@module procedures.registry
@purpose Durable Procedure definitions and queue helpers.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional
from uuid import uuid4

from strata.core.lanes import canonical_session_id_for_lane, default_work_pool_for_lane, normalize_lane, normalize_work_pool
from strata.orchestrator.user_questions import enqueue_user_question, get_question_for_source
from strata.storage.models import TaskModel, TaskState, TaskType
from strata.system_capabilities import (
    AUDIT_TRACE_REVIEW_PROCEDURE_ID,
    BOOTSTRAP_CYCLE_PROCEDURE_ID,
    KNOWLEDGE_REFRESH_PROCEDURE_ID,
    PROCESS_REPAIR_PROCEDURE_ID,
    TASK_DECOMPOSITION_PROCEDURE_ID,
    VERIFICATION_REVIEW_PROCEDURE_ID,
)


PROCEDURE_REGISTRY_KEY = "procedure_registry"
DEFAULT_PROCEDURE_LANE = "agent"
STARTUP_SMOKE_PROCEDURE_ID = "preflight"
ONBOARDING_PROCEDURE_ID = "operator_onboarding"
DEFAULT_PROCEDURES: Dict[str, Dict[str, Any]] = {
    BOOTSTRAP_CYCLE_PROCEDURE_ID: {
        "procedure_id": BOOTSTRAP_CYCLE_PROCEDURE_ID,
        "title": "Bootstrap Cycle",
        "summary": "Generate eval-change proposals, compare them against recent history, run bounded evals, and either promote a candidate or cash out into audit.",
        "repeatable": True,
        "lifecycle_state": "draft",
        "lineage_id": BOOTSTRAP_CYCLE_PROCEDURE_ID,
        "variant_of": None,
        "mutable": True,
        "target_lane": "trainer",
        "task_type": "JUDGE",
        "instructions": (
            "Treat bootstrap as a canonical Procedure. Generate proposals, resolve them against recent history, "
            "evaluate only the novel candidates, and audit the cycle if no candidate is promotable."
        ),
        "checklist": [
            {"id": "generate_proposals", "title": "Generate proposals", "verification": "The configured proposer tiers produce candidate changes."},
            {"id": "resolve_history", "title": "Resolve against recent history", "verification": "Duplicate or recently-tested candidates are filtered before eval."},
            {"id": "cash_out_cycle", "title": "Cash out the cycle", "verification": "The cycle either promotes a candidate or triggers an audit instead of silently stalling."},
        ],
        "success_criteria": {
            "deliverables": [
                "A reviewed set of candidate eval changes",
                "Either a promotion decision or an explicit audit artifact",
            ],
        },
    },
    AUDIT_TRACE_REVIEW_PROCEDURE_ID: {
        "procedure_id": AUDIT_TRACE_REVIEW_PROCEDURE_ID,
        "title": "Audit Trace Review",
        "summary": "Review a task, session, or branch trace, diagnose what happened, and route follow-up supervision, repair, or re-greening decisions.",
        "repeatable": True,
        "lifecycle_state": "tested",
        "lineage_id": AUDIT_TRACE_REVIEW_PROCEDURE_ID,
        "variant_of": None,
        "mutable": True,
        "target_lane": "trainer",
        "task_type": "JUDGE",
        "instructions": (
            "Treat this as the canonical Audit Procedure for reviewing traces. Build a compact summary, assess the branch, "
            "identify damaged machinery vs ordinary task failure, and emit explicit follow-up actions."
        ),
        "checklist": [
            {"id": "trace_summary", "title": "Build the compact trace summary", "verification": "The summary includes the relevant tasks, attempts, artifacts, and provenance."},
            {"id": "audit_assessment", "title": "Assess the branch or artifact", "verification": "The review distinguishes healthy output, branch failure, and damaged machinery."},
            {"id": "followup_routing", "title": "Route follow-up work", "verification": "The audit emits explicit follow-up actions such as re-green, repair, question, or attention."},
        ],
        "success_criteria": {
            "deliverables": [
                "A durable audit review artifact",
                "Explicit follow-up routing or re-greening evidence",
            ],
        },
    },
    TASK_DECOMPOSITION_PROCEDURE_ID: {
        "procedure_id": TASK_DECOMPOSITION_PROCEDURE_ID,
        "title": "Task Decomposition",
        "summary": "Break a non-oneshottable task into bounded, dependency-aware leaf work and preserve the resulting workflow structure as a draft Procedure when useful.",
        "repeatable": True,
        "lifecycle_state": "tested",
        "lineage_id": TASK_DECOMPOSITION_PROCEDURE_ID,
        "variant_of": None,
        "mutable": True,
        "target_lane": DEFAULT_PROCEDURE_LANE,
        "task_type": "DECOMP",
        "instructions": (
            "Treat decomposition as a canonical Procedure. Produce actionable leaf work, preserve successful structure, "
            "and fold reusable decomposition into a draft Procedure when possible."
        ),
        "checklist": [
            {"id": "frame_task", "title": "Frame the task and success criteria", "verification": "The decomposition reflects the real goal, constraints, and oneshottable boundary rules."},
            {"id": "emit_leaf_tasks", "title": "Emit actionable leaf tasks", "verification": "Each subtask is oneshottable and includes concrete routing metadata such as files, validator, and dependencies."},
            {"id": "preserve_workflow", "title": "Preserve reusable workflow structure", "verification": "Useful decomposition is captured into Procedure metadata instead of remaining branch-local only."},
        ],
        "success_criteria": {
            "deliverables": [
                "A bounded subtask DAG",
                "A Procedure-linked decomposition lineage",
            ],
        },
    },
    PROCESS_REPAIR_PROCEDURE_ID: {
        "procedure_id": PROCESS_REPAIR_PROCEDURE_ID,
        "title": "Process Repair",
        "summary": "Repair a degraded reusable internal process and record the evidence needed to trust it again.",
        "repeatable": True,
        "lifecycle_state": "draft",
        "lineage_id": PROCESS_REPAIR_PROCEDURE_ID,
        "variant_of": None,
        "mutable": True,
        "target_lane": DEFAULT_PROCEDURE_LANE,
        "task_type": "BUG_FIX",
        "instructions": (
            "Treat this as bounded repair work on reusable internal machinery. Inspect the degraded process, patch the owning artifact, "
            "and leave evidence that later audit can use to re-green it."
        ),
        "checklist": [
            {"id": "inspect_incident", "title": "Inspect the incident and degraded capability", "verification": "The repair understands what failed, when, and under what authority."},
            {"id": "patch_owner", "title": "Patch the owning artifact", "verification": "The actual Procedure, tool, or runtime primitive is repaired directly."},
            {"id": "leave_repair_evidence", "title": "Leave repair evidence", "verification": "The branch records enough evidence for later verification or audit to re-green the capability."},
        ],
        "success_criteria": {
            "deliverables": [
                "A direct repair to the owning artifact",
                "Durable evidence supporting later re-greening",
            ],
        },
    },
    VERIFICATION_REVIEW_PROCEDURE_ID: {
        "procedure_id": VERIFICATION_REVIEW_PROCEDURE_ID,
        "title": "Verification Review",
        "summary": "Diagnose verification machinery failures and restore the Verifier to a trustworthy state.",
        "repeatable": True,
        "lifecycle_state": "draft",
        "lineage_id": VERIFICATION_REVIEW_PROCEDURE_ID,
        "variant_of": PROCESS_REPAIR_PROCEDURE_ID,
        "mutable": True,
        "target_lane": DEFAULT_PROCEDURE_LANE,
        "task_type": "BUG_FIX",
        "instructions": (
            "Treat repeated verification machinery failure as degraded reusable system capability. Repair the Verifier path, "
            "separate mechanism failure from substantive uncertainty, and leave evidence for later audit."
        ),
        "checklist": [
            {"id": "classify_failure", "title": "Classify the verifier failure mode", "verification": "The repair distinguishes parse/transport/mechanism failure from a genuine uncertain verdict."},
            {"id": "repair_verifier", "title": "Repair the verifier path", "verification": "The broken verifier logic or dependency path is patched directly."},
            {"id": "support_re_green", "title": "Support later re-greening", "verification": "The repair leaves explicit evidence supporting later verifier or audit confirmation."},
        ],
        "success_criteria": {
            "deliverables": [
                "A repaired verification path",
                "Cleaner verifier incident evidence",
            ],
        },
    },
    KNOWLEDGE_REFRESH_PROCEDURE_ID: {
        "procedure_id": KNOWLEDGE_REFRESH_PROCEDURE_ID,
        "title": "Knowledge Refresh",
        "summary": "Inspect durable knowledge, gather only missing evidence, and cash out into a synthesized page update rather than open-ended research drift.",
        "repeatable": True,
        "lifecycle_state": "draft",
        "lineage_id": KNOWLEDGE_REFRESH_PROCEDURE_ID,
        "variant_of": None,
        "mutable": True,
        "target_lane": DEFAULT_PROCEDURE_LANE,
        "task_type": "RESEARCH",
        "instructions": (
            "Treat knowledge refresh as a bounded Procedure. Inspect the current page and its canonical sources first, "
            "gather only missing evidence, then update or queue the durable knowledge outcome without drifting into open-ended repo exploration."
        ),
        "checklist": [
            {"id": "inspect_current_knowledge", "title": "Inspect the current knowledge payload", "verification": "The current page/source state is read before searching for new evidence."},
            {"id": "gather_missing_evidence", "title": "Gather only missing evidence", "verification": "The branch uses focused sources and avoids broad repo foraging when the task is already narrow."},
            {"id": "cash_out_update", "title": "Cash out the knowledge result", "verification": "The work ends in a durable page update, refresh proposal, or explicit unresolved attention item."},
        ],
        "success_criteria": {
            "deliverables": [
                "A bounded knowledge update or refresh result",
                "Clear provenance for why the knowledge changed",
            ],
        },
    },
    STARTUP_SMOKE_PROCEDURE_ID: {
        "procedure_id": STARTUP_SMOKE_PROCEDURE_ID,
        "title": "Preflight",
        "summary": "Run a small, source-grounded startup checklist that tests intended operator-visible behavior before broader autonomous work begins.",
        "repeatable": True,
        "lifecycle_state": "vetted",
        "lineage_id": STARTUP_SMOKE_PROCEDURE_ID,
        "variant_of": None,
        "mutable": True,
        "target_lane": DEFAULT_PROCEDURE_LANE,
        "task_type": "RESEARCH",
        "instructions": (
            "Run this preflight checklist before broader autonomous work. Treat each item as a small, source-grounded verification task "
            "about intended product behavior, not just repository existence. Prefer direct evidence from the hinted sources, and cash out each item explicitly."
        ),
        "checklist": [
            {
                "id": "spec_presence",
                "title": "Confirm the core spec surfaces are present",
                "verification": "The canonical constitution and project spec locations exist and are readable.",
            },
            {
                "id": "runtime_wiring",
                "title": "Confirm the split runtime wiring is present",
                "verification": "The system can identify separate API and worker launch surfaces in the codebase.",
            },
            {
                "id": "operator_runtime_surface",
                "title": "Confirm the operator can inspect live runtime state",
                "verification": "The desktop/UI surface includes visible lane status and task inspection surfaces for the operator.",
            },
            {
                "id": "procedure_workbench_surface",
                "title": "Confirm Procedure and Workbench inspection surfaces exist",
                "verification": "The UI exposes Procedure inspection and Workbench/debugging entry points instead of hiding them behind backend-only machinery.",
            },
        ],
        "success_criteria": {
            "required_checklist_ids": [
                "spec_presence",
                "runtime_wiring",
                "operator_runtime_surface",
                "procedure_workbench_surface",
            ],
            "deliverables": [
                "A concise preflight summary",
                "Durable evidence that the preflight Procedure completed",
            ],
        },
    },
    ONBOARDING_PROCEDURE_ID: {
        "procedure_id": ONBOARDING_PROCEDURE_ID,
        "title": "Operator Onboarding",
        "summary": "Establish the agent's identity, operator defaults, and baseline trust posture.",
        "repeatable": True,
        "lifecycle_state": "vetted",
        "lineage_id": ONBOARDING_PROCEDURE_ID,
        "variant_of": None,
        "mutable": True,
        "target_lane": DEFAULT_PROCEDURE_LANE,
        "task_type": "RESEARCH",
        "instructions": (
            "Run the onboarding checklist carefully. Treat this as a rich, verifiable procedure rather than a generic task. "
            "If an item cannot be completed directly, surface it as an explicit pending question or attention item instead of silently skipping it."
        ),
        "checklist": [
            {
                "id": "agent_name",
                "title": "Choose or confirm the agent name",
                "verification": "The session metadata must contain participant_names for user, agent, trainer, and system.",
            },
            {
                "id": "identity_language",
                "title": "Confirm personality and role language",
                "verification": "The system should know how to refer to the operator, agent, trainer, and system, and what collaboration tone it should default to.",
            },
            {
                "id": "verification_posture",
                "title": "Establish permissions, autonomy, and verification posture",
                "verification": "The system should know its starting autonomy and verification posture, including whether to begin with aggressive verification while trust anneals.",
            },
            {
                "id": "runtime_posture",
                "title": "Confirm scope of work and runtime preferences",
                "verification": "The operator's scope and comfort constraints should be written into durable settings or queued as pending clarification.",
            },
            {
                "id": "open_questions",
                "title": "Surface unresolved onboarding items",
                "verification": "Every unresolved onboarding issue should become either a queued user question or a durable attention item.",
            },
        ],
        "success_criteria": {
            "required_checklist_ids": [
                "agent_name",
                "identity_language",
                "verification_posture",
                "runtime_posture",
                "open_questions",
            ],
            "deliverables": [
                "A concise onboarding summary",
                "Updated participant naming or a queued naming question",
                "Durable record of unresolved onboarding items",
            ],
        },
    }
}


def _default_registry() -> Dict[str, Any]:
    return {"procedures": deepcopy(DEFAULT_PROCEDURES)}


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower())
    return cleaned.strip("_") or "procedure"


def _normalize_stats(source: Dict[str, Any]) -> Dict[str, Any]:
    raw = dict(source or {})
    return {
        "run_count": int(raw.get("run_count") or 0),
        "success_count": int(raw.get("success_count") or 0),
        "failure_count": int(raw.get("failure_count") or 0),
        "tested_at": str(raw.get("tested_at") or "").strip() or None,
        "last_run_at": str(raw.get("last_run_at") or "").strip() or None,
        "last_source_task_id": str(raw.get("last_source_task_id") or "").strip() or None,
    }


def _normalize_procedure(definition: Dict[str, Any]) -> Dict[str, Any]:
    procedure_id = str(definition.get("procedure_id") or "").strip()
    if not procedure_id:
        raise ValueError("procedure_id is required")
    normalized = deepcopy(definition)
    normalized["procedure_id"] = procedure_id
    normalized["title"] = str(normalized.get("title") or procedure_id.replace("_", " ").title()).strip()
    normalized["summary"] = str(normalized.get("summary") or "").strip()
    normalized["repeatable"] = bool(normalized.get("repeatable", True))
    lifecycle_state = str(normalized.get("lifecycle_state") or "draft").strip().lower()
    if lifecycle_state not in {"draft", "tested", "vetted", "retired"}:
        lifecycle_state = "draft"
    normalized["lifecycle_state"] = lifecycle_state
    normalized["lineage_id"] = str(normalized.get("lineage_id") or procedure_id).strip() or procedure_id
    normalized["variant_of"] = str(normalized.get("variant_of") or "").strip() or None
    normalized["mutable"] = bool(normalized.get("mutable", True))
    normalized["target_lane"] = normalize_lane(normalized.get("target_lane")) or DEFAULT_PROCEDURE_LANE
    normalized["target_work_pool"] = (
        normalize_work_pool(normalized.get("target_work_pool"))
        or default_work_pool_for_lane(normalized["target_lane"])
    )
    normalized["target_execution_profile"] = (
        normalize_work_pool(normalized.get("target_execution_profile"))
        or normalized["target_work_pool"]
    )
    normalized["task_type"] = str(normalized.get("task_type") or "RESEARCH").strip().upper()
    normalized["instructions"] = str(normalized.get("instructions") or "").strip()
    normalized["checklist"] = [
        {
            "id": str(item.get("id") or f"step_{index + 1}").strip(),
            "title": str(item.get("title") or "").strip(),
            "verification": str(item.get("verification") or "").strip(),
        }
        for index, item in enumerate(normalized.get("checklist") or [])
        if str(item.get("title") or "").strip()
    ]
    normalized["success_criteria"] = dict(normalized.get("success_criteria") or {})
    normalized["draft_source"] = dict(normalized.get("draft_source") or {})
    normalized["stats"] = _normalize_stats(dict(normalized.get("stats") or {}))
    return normalized


def get_procedure_registry(storage) -> Dict[str, Any]:
    registry = storage.parameters.peek_parameter(PROCEDURE_REGISTRY_KEY, default_value=_default_registry()) or _default_registry()
    procedures = {
        procedure_id: _normalize_procedure(definition)
        for procedure_id, definition in dict(registry.get("procedures") or {}).items()
    }
    merged = deepcopy(DEFAULT_PROCEDURES)
    merged.update(procedures)
    procedures = merged
    return {"procedures": procedures}


def list_procedures(storage) -> List[Dict[str, Any]]:
    registry = get_procedure_registry(storage)
    return sorted(registry["procedures"].values(), key=lambda item: item["title"].lower())


def get_procedure(storage, procedure_id: str) -> Dict[str, Any]:
    normalized_id = str(procedure_id or "").strip()
    registry = get_procedure_registry(storage)
    procedure = registry["procedures"].get(normalized_id)
    if not procedure:
        raise KeyError(f"Unknown procedure: {normalized_id}")
    return procedure


def build_procedure_task_constraints(
    storage,
    procedure_id: str,
    *,
    base: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    procedure = get_procedure(storage, procedure_id)
    constraints = dict(base or {})
    constraints.update(
        {
            "procedure_id": procedure["procedure_id"],
            "procedure_title": procedure.get("title"),
            "procedure_lifecycle_state": procedure.get("lifecycle_state"),
            "procedure_lineage_id": procedure.get("lineage_id"),
            "procedure_summary": procedure.get("summary"),
            "procedure_checklist": list(procedure.get("checklist") or []),
            "procedure_repeatable": bool(procedure.get("repeatable", True)),
            "verification_required": True,
            "work_pool": normalize_work_pool(constraints.get("work_pool")) or procedure.get("target_work_pool"),
            "execution_profile": normalize_work_pool(constraints.get("execution_profile")) or procedure.get("target_execution_profile"),
        }
    )
    return constraints


def save_procedure(storage, definition: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _normalize_procedure(definition)
    registry = get_procedure_registry(storage)
    procedures = dict(registry.get("procedures") or {})
    procedures[normalized["procedure_id"]] = normalized
    storage.parameters.set_parameter(
        PROCEDURE_REGISTRY_KEY,
        {"procedures": procedures},
        description=(
            "Durable registry of reusable Procedure artifacts such as onboarding or maintenance workflows. "
            "A Procedure is a first-class reusable workflow artifact, not merely ordinary prose about a procedure."
        ),
    )
    return normalized


def ensure_draft_procedure_for_task(storage, task: TaskModel, *, checklist: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    constraints = dict(getattr(task, "constraints", {}) or {})
    existing_id = str(constraints.get("procedure_id") or "").strip()
    if existing_id:
        try:
            return get_procedure(storage, existing_id)
        except KeyError:
            pass

    root = task
    seen = set()
    while getattr(root, "parent_task_id", None) and getattr(root, "parent_task_id", None) not in seen:
        seen.add(str(getattr(root, "task_id", "")))
        parent = storage.tasks.get_by_id(str(root.parent_task_id))
        if parent is None:
            break
        root = parent

    root_constraints = dict(getattr(root, "constraints", {}) or {})
    root_procedure_id = str(root_constraints.get("procedure_id") or "").strip()
    if root_procedure_id:
        return get_procedure(storage, root_procedure_id)

    title = str(getattr(root, "title", "") or getattr(task, "title", "") or "Recovered workflow").strip()
    draft_id = f"draft_{_slugify(title)}_{str(getattr(root, 'task_id', '') or uuid4())[:8]}"
    normalized_checklist: List[Dict[str, Any]] = []
    for index, item in enumerate(list(checklist or []), start=1):
        item_title = str(item.get("title") or "").strip()
        if not item_title:
            continue
        normalized_checklist.append(
            {
                "id": str(item.get("id") or item.get("proto_id") or f"step_{index}").strip() or f"step_{index}",
                "title": item_title,
                "verification": str(item.get("verification") or item.get("description") or "").strip(),
            }
        )

    procedure = save_procedure(
        storage,
        {
            "procedure_id": draft_id,
            "title": title,
            "summary": str(getattr(root, "description", "") or getattr(task, "description", "") or "").strip()[:400],
            "repeatable": True,
            "lifecycle_state": "draft",
            "lineage_id": draft_id,
            "variant_of": None,
            "mutable": True,
            "target_lane": normalize_lane(root_constraints.get("lane") or constraints.get("lane")) or DEFAULT_PROCEDURE_LANE,
            "target_work_pool": normalize_work_pool(root_constraints.get("work_pool") or constraints.get("work_pool")),
            "target_execution_profile": normalize_work_pool(root_constraints.get("execution_profile") or constraints.get("execution_profile")),
            "task_type": str(getattr(task, "type", TaskType.RESEARCH).value if getattr(task, "type", None) else "RESEARCH"),
            "instructions": (
                "This is a draft Procedure discovered from live execution. Treat it as provisional working memory for the workflow "
                "until it succeeds and accumulates evidence."
            ),
            "checklist": normalized_checklist,
            "success_criteria": dict(getattr(root, "success_criteria", {}) or {}),
            "draft_source": {
                "root_task_id": getattr(root, "task_id", None),
                "source_task_id": getattr(task, "task_id", None),
                "source_title": str(getattr(task, "title", "") or "").strip(),
            },
        },
    )

    for node in [root, task]:
        node_constraints = dict(getattr(node, "constraints", {}) or {})
        node_constraints["procedure_id"] = procedure["procedure_id"]
        node_constraints["procedure_title"] = procedure["title"]
        node_constraints["procedure_lifecycle_state"] = procedure["lifecycle_state"]
        node_constraints["procedure_lineage_id"] = procedure["lineage_id"]
        node.constraints = node_constraints
    storage.commit()
    return procedure


def record_procedure_run(storage, procedure_id: str, *, outcome: str, source_task_id: Optional[str] = None) -> Dict[str, Any]:
    procedure = get_procedure(storage, procedure_id)
    updated = deepcopy(procedure)
    stats = _normalize_stats(dict(updated.get("stats") or {}))
    stats["run_count"] += 1
    stats["last_run_at"] = datetime.now(timezone.utc).isoformat()
    if source_task_id:
        stats["last_source_task_id"] = str(source_task_id)
    normalized_outcome = str(outcome or "").strip().lower()
    if normalized_outcome == "succeeded":
        stats["success_count"] += 1
        if updated.get("lifecycle_state") == "draft":
            updated["lifecycle_state"] = "tested"
            stats["tested_at"] = datetime.now(timezone.utc).isoformat()
    elif normalized_outcome == "failed":
        stats["failure_count"] += 1
    updated["stats"] = stats
    return save_procedure(storage, updated)


def queue_procedure(storage, worker, *, procedure_id: str, session_id: Optional[str] = None, lane: Optional[str] = None, work_pool: Optional[str] = None, execution_profile: Optional[str] = None):
    procedure = get_procedure(storage, procedure_id)
    target_lane = normalize_lane(lane) or procedure.get("target_lane") or DEFAULT_PROCEDURE_LANE
    target_work_pool = normalize_work_pool(work_pool) or procedure.get("target_work_pool") or default_work_pool_for_lane(target_lane)
    target_execution_profile = normalize_work_pool(execution_profile) or normalize_work_pool(work_pool) or procedure.get("target_execution_profile") or target_work_pool
    resolved_session_id = canonical_session_id_for_lane(target_lane, session_id or "default")
    checklist = list(procedure.get("checklist") or [])
    checklist_lines = "\n".join(
        f"- [{item['id']}] {item['title']} :: verify by {item['verification']}"
        for item in checklist
    )
    description = (
        f"{procedure.get('instructions')}\n\n"
        f"Checklist:\n{checklist_lines}\n\n"
        "When you finish or get blocked, report checklist status explicitly."
    ).strip()
    task = storage.tasks.create(
        title=f"Procedure: {procedure.get('title')}",
        description=description,
        session_id=resolved_session_id,
        state=TaskState.PENDING,
        constraints=build_procedure_task_constraints(
            storage,
            procedure["procedure_id"],
            base={
                "lane": target_lane,
                "work_pool": target_work_pool,
                "execution_profile": target_execution_profile,
            },
        ),
        success_criteria=procedure.get("success_criteria") or {},
    )
    try:
        task.type = TaskType[str(procedure.get("task_type") or "RESEARCH").upper()]
    except Exception:
        task.type = TaskType.RESEARCH
    storage.commit()
    if procedure["procedure_id"] == ONBOARDING_PROCEDURE_ID:
        existing = get_question_for_source(
            storage,
            source_type="procedure_onboarding_intro",
            source_id=procedure["procedure_id"],
        )
        if not existing:
            enqueue_user_question(
                storage,
                session_id=resolved_session_id,
                source_type="procedure_onboarding_intro",
                source_id=procedure["procedure_id"],
                escalation_mode="non_blocking",
                lane=target_lane,
                question=(
                    "I’m starting your onboarding Procedure now. I’ll inspect the current project and settings, "
                    "and I may ask follow-ups if anything is unclear. If you want to shortcut the process, you can "
                    "tell me your preferred agent name, role language, verification posture, and local/cloud or quiet-hardware preferences."
                ),
                context={
                    "procedure_id": procedure["procedure_id"],
                    "task_id": task.task_id,
                    "lane": target_lane,
                    "work_pool": target_work_pool,
                },
            )
    return task


def list_procedure_tasks(storage, procedure_id: str) -> List[TaskModel]:
    normalized_id = str(procedure_id or "").strip()
    if not normalized_id:
        return []
    procedure = get_procedure(storage, normalized_id)
    expected_title = f"Procedure: {procedure.get('title')}"
    matches: List[TaskModel] = []
    for task in storage.session.query(TaskModel).all():
        constraints = dict(getattr(task, "constraints", {}) or {})
        if constraints.get("procedure_id") == normalized_id or str(getattr(task, "title", "") or "").strip() == expected_title:
            matches.append(task)
    matches.sort(key=lambda item: getattr(item, "created_at", None) or 0)
    return matches


def get_procedure_status(storage, procedure_id: str) -> Dict[str, Any]:
    tasks = list_procedure_tasks(storage, procedure_id)
    active_states = {TaskState.PENDING, TaskState.WORKING, TaskState.BLOCKED}
    latest = tasks[-1] if tasks else None
    has_active = any(getattr(task, "state", None) in active_states for task in tasks)
    has_completed = any(getattr(task, "state", None) == TaskState.COMPLETE for task in tasks)
    return {
        "procedure_id": procedure_id,
        "has_any": bool(tasks),
        "has_active": has_active,
        "has_completed": has_completed,
        "needs_queue": not has_active and not has_completed,
        "task_ids": [str(task.task_id) for task in tasks],
        "latest_task_id": str(latest.task_id) if latest is not None else None,
        "latest_state": getattr(getattr(latest, "state", None), "value", None) if latest is not None else None,
    }


def get_onboarding_status(storage) -> Dict[str, Any]:
    return get_procedure_status(storage, ONBOARDING_PROCEDURE_ID)


def get_startup_smoke_status(storage) -> Dict[str, Any]:
    return get_procedure_status(storage, STARTUP_SMOKE_PROCEDURE_ID)


def ensure_startup_smoke_task(storage, worker, *, session_id: Optional[str] = None, lane: Optional[str] = None):
    status = get_startup_smoke_status(storage)
    if not status.get("needs_queue"):
        return None
    return queue_procedure(
        storage,
        worker,
        procedure_id=STARTUP_SMOKE_PROCEDURE_ID,
        session_id=session_id,
        lane=lane,
    )


def ensure_onboarding_task(storage, worker, *, session_id: Optional[str] = None, lane: Optional[str] = None):
    status = get_onboarding_status(storage)
    if not status.get("needs_queue"):
        return None
    return queue_procedure(
        storage,
        worker,
        procedure_id=ONBOARDING_PROCEDURE_ID,
        session_id=session_id,
        lane=lane,
    )
