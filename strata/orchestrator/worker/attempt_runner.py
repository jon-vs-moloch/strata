"""
@module orchestrator.worker.attempt_runner
@purpose Dispatches task execution based on task type.
"""

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from strata.communication.primitives import build_communication_decision, deliver_communication_decision
from strata.core.lanes import infer_lane_from_task
from strata.observability.writer import enqueue_attempt_observability_artifact, flush_observability_writes
from strata.orchestrator.step_outcomes import TerminalToolCallOutcome
from strata.procedures.registry import STARTUP_SMOKE_PROCEDURE_ID, ensure_draft_procedure_for_task, record_procedure_run
from strata.storage.models import TaskModel, TaskType, TaskState, AttemptOutcome
from strata.orchestrator.research import ResearchModule
from strata.orchestrator.decomposition import DecompositionModule
from strata.orchestrator.implementation import ImplementationModule
from strata.eval.job_runner import run_eval_job_task

logger = logging.getLogger(__name__)

DEFAULT_MAX_CONTINUATION_DEPTH = 6
DEFAULT_CONTINUATION_AUDIT_INTERVAL = 3
DEFAULT_MAX_DECOMPOSITION_DEPTH = 3


async def _enqueue_task(enqueue_fn, task_id: str, *, front: bool = False) -> None:
    try:
        await enqueue_fn(task_id, front=front)
    except TypeError:
        await enqueue_fn(task_id)


def _lineage_task_id(task: TaskModel) -> str:
    constraints = dict(getattr(task, "constraints", {}) or {})
    return str(constraints.get("lineage_root_task_id") or getattr(task, "task_id", "") or "").strip()


def _count_ancestor_tasks(storage, task: TaskModel, *, task_types: Optional[set[TaskType]] = None) -> int:
    if storage is None or not hasattr(storage, "tasks"):
        return 0
    total = 0
    seen = set()
    current = task
    while current is not None:
        parent_task_id = getattr(current, "parent_task_id", None)
        if not parent_task_id or parent_task_id in seen:
            break
        seen.add(parent_task_id)
        parent = storage.tasks.get_by_id(parent_task_id)
        if parent is None:
            break
        if not task_types or getattr(parent, "type", None) in task_types:
            total += 1
        current = parent
    return total


async def _queue_trace_review_guardrail(
    storage,
    task: TaskModel,
    enqueue_fn,
    *,
    title: str,
    description: str,
    trigger: str,
    note: str,
    associated_task_ids: Optional[List[str]] = None,
) -> TaskModel:
    payload = {
        "trace_kind": "task_trace",
        "task_id": task.task_id,
        "associated_task_ids": [str(item).strip() for item in list(associated_task_ids or []) if str(item).strip()],
        "reviewer_tier": "trainer",
        "emit_followups": True,
        "persist_to_task": True,
        "spec_scope": "project",
        "audit_mode": "internal",
        "source_task_id": task.task_id,
        "trigger": trigger,
        "guardrail_note": note,
        "provenance": {
            "source_kind": "task_guardrail",
            "source_actor": "attempt_runner",
            "authority_kind": "spec_policy",
            "authority_ref": trigger,
            "derived_from": [f"task:{task.task_id}", f"lineage:{_lineage_task_id(task) or task.task_id}"],
            "governing_spec_refs": [
                ".knowledge/specs/constitution.md",
                ".knowledge/specs/project_spec.md",
                "docs/spec/step-runtime-flow.md",
            ],
            "note": note,
        },
    }
    judge = storage.tasks.create(
        title=title,
        description=description,
        session_id=task.session_id,
        parent_task_id=task.task_id,
        state=TaskState.PENDING,
        flush=False,
        constraints={
            "lane": "trainer",
            "provenance": dict(payload.get("provenance") or {}),
            "lineage_root_task_id": _lineage_task_id(task) or str(task.task_id),
            "system_job": {
                "kind": "trace_review",
                "payload": payload,
            },
        },
    )
    judge.type = TaskType.JUDGE
    judge.depth = int(getattr(task, "depth", 0) or 0) + 1
    task.active_child_ids = list(dict.fromkeys([*(list(getattr(task, "active_child_ids", []) or [])), judge.task_id]))
    task.state = TaskState.PUSHED
    storage.commit()
    await _enqueue_task(enqueue_fn, judge.task_id, front=True)
    return judge


async def _resume_parent_repair_task_if_supported(storage, task: TaskModel, enqueue_fn) -> bool:
    title = str(getattr(task, "title", "") or "").strip().lower()
    if not title.startswith("recovery plan for "):
        return False
    parent_task_id = getattr(task, "parent_task_id", None)
    if not parent_task_id or storage is None or not hasattr(storage, "tasks"):
        return False
    parent = storage.tasks.get_by_id(parent_task_id)
    if parent is None or getattr(parent, "type", None) not in {TaskType.BUG_FIX, TaskType.REFACTOR, TaskType.IMPL}:
        return False
    parent.active_child_ids = [task_id for task_id in list(getattr(parent, "active_child_ids", []) or []) if task_id != task.task_id]
    if not parent.active_child_ids:
        parent.active_child_ids = []
    parent.state = TaskState.PENDING
    parent_constraints = dict(getattr(parent, "constraints", {}) or {})
    parent_constraints["recovered_from_stale_decomposition"] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "source_task_id": str(task.task_id),
        "reason": "Parent repair task is now executable directly; collapsing stale recovery-plan shell.",
    }
    parent.constraints = parent_constraints
    task.state = TaskState.COMPLETE
    task.active_child_ids = []
    storage.commit()
    await _enqueue_task(enqueue_fn, parent.task_id, front=True)
    return True


def _is_root_procedure_task(task: TaskModel) -> bool:
    constraints = dict(getattr(task, "constraints", {}) or {})
    checklist = list(constraints.get("procedure_checklist") or [])
    checklist_item = dict(constraints.get("procedure_checklist_item") or {})
    return bool(checklist) and not checklist_item


def _procedure_checklist_item_source_hints(item_id: str) -> Dict[str, Any]:
    normalized = str(item_id or "").strip().lower()
    hints = {
        "spec_presence": {
            "preferred_paths": [
                ".knowledge/specs/constitution.md",
                ".knowledge/specs/project_spec.md",
                "docs/spec/system-substrates.md",
            ],
            "guidance": "Verify the canonical spec files directly from their durable locations.",
        },
        "runtime_wiring": {
            "preferred_paths": [
                "scripts/start_api.sh",
                "scripts/worker_daemon.py",
                "strata/api/main.py",
                "strata/orchestrator/worker/runtime_ipc.py",
            ],
            "guidance": "Inspect the split API/worker launch and runtime IPC surfaces directly.",
        },
        "desktop_surface": {
            "preferred_paths": [
                "src-tauri/src/main.rs",
                "strata_ui/src/App.jsx",
                "strata_ui/src/views/SettingsView.jsx",
                "docs/spec/desktop-distribution.md",
            ],
            "guidance": "Inspect the desktop shell and update/status surfaces directly rather than scanning the repo root.",
        },
        "agent_name": {
            "preferred_paths": [
                ".knowledge/user-profile.md",
                ".knowledge/specs/project_spec.md",
                "strata/api/chat_task_admin.py",
                "strata/api/chat_runtime.py",
            ],
            "guidance": "Look for durable naming/profile metadata before inspecting broader runtime surfaces.",
        },
        "identity_language": {
            "preferred_paths": [
                ".knowledge/specs/constitution.md",
                ".knowledge/specs/project_spec.md",
                "docs/spec/system-substrates.md",
                "strata/procedures/registry.py",
            ],
            "guidance": "Inspect the durable specs and Procedure definitions that describe naming, role language, and collaboration style for operator, agent, trainer, and system.",
        },
        "verification_posture": {
            "preferred_paths": [
                ".knowledge/specs/constitution.md",
                ".knowledge/specs/project_spec.md",
                "docs/spec/task-attempt-ontology.md",
                "docs/spec/project-philosophy.md",
                "strata/experimental/verifier.py",
                "strata/orchestrator/worker/resolution_policy.py",
            ],
            "guidance": "Inspect permissions, autonomy, verification policy, and trust-annealing guidance, not the repo root.",
        },
        "runtime_posture": {
            "preferred_paths": [
                ".knowledge/specs/project_spec.md",
                "docs/spec/project-philosophy.md",
                "strata/api/main.py",
                "strata/models/providers.py",
                "strata/observability/host.py",
            ],
            "guidance": "Inspect runtime settings, scope posture, and comfort-throttle codepaths before asking for clarification.",
        },
        "open_questions": {
            "preferred_paths": [
                ".knowledge/specs/investigation-patterns.md",
                "strata/orchestrator/user_questions.py",
                "strata/experimental/trace_review.py",
                "strata/orchestrator/background.py",
            ],
            "guidance": "Inspect question, attention, and review surfaces directly instead of broadly surveying the repository.",
        },
    }
    selected = dict(hints.get(normalized) or {})
    selected.setdefault("preferred_paths", [])
    selected.setdefault("guidance", "")
    return selected


def _default_carry_forward_policy() -> Dict[str, Any]:
    return {
        "tool_call": "summary",
        "tool_result_preview": "summary",
        "tool_result_full": "small_only",
        "next_step_hint": "summary",
        "failure_autopsy": "summary",
    }


def _task_type_from_name(raw: Any, *, default: TaskType) -> TaskType:
    if isinstance(raw, TaskType):
        return raw
    normalized = str(raw or "").strip().upper()
    return getattr(TaskType, normalized, default)


def _terminal_tool_handoff(outcome: TerminalToolCallOutcome, *, task: TaskModel, attempt_id: str) -> Dict[str, Any]:
    return {
        "from_task_id": str(getattr(task, "task_id", "") or ""),
        "from_task_title": str(getattr(task, "title", "") or ""),
        "from_attempt_id": str(attempt_id or ""),
        "tool_call": {
            "name": str(outcome.tool_name or "").strip(),
            "arguments": dict(outcome.tool_arguments or {}),
        },
        "tool_result_preview": str(outcome.tool_result_preview or "").strip(),
        "tool_result_full": str(outcome.tool_result_full or "").strip(),
        "next_step_hint": str(outcome.next_step_hint or "").strip(),
        "source_module": str(outcome.source_module or "").strip(),
        "avoid_repeating_first_tool": {
            "name": str(outcome.tool_name or "").strip(),
            "reason": "This tool call already ran in the prior explicit step; consume its result before deciding whether to repeat it.",
        },
    }


def _tool_branch_matches(branch: Dict[str, Any], outcome: TerminalToolCallOutcome) -> bool:
    expected_tool = str(branch.get("tool_name") or "").strip()
    if expected_tool and expected_tool != str(outcome.tool_name or "").strip():
        return False
    preview = str(outcome.tool_result_preview or "")
    full_result = str(outcome.tool_result_full or "")
    result_contains = str(branch.get("result_contains") or "").strip()
    if result_contains and result_contains not in preview and result_contains not in full_result:
        return False
    return True


async def _enqueue_terminal_tool_followup(storage, task: TaskModel, attempt, outcome: TerminalToolCallOutcome, enqueue_fn) -> Optional[TaskModel]:
    parent_constraints = dict(getattr(task, "constraints", {}) or {})
    continuation_depth = int(parent_constraints.get("continuation_depth") or getattr(task, "depth", 0) or 0)
    next_continuation_depth = continuation_depth + 1
    max_continuation_depth = max(1, int(parent_constraints.get("max_continuation_depth") or DEFAULT_MAX_CONTINUATION_DEPTH))
    continuation_audit_interval = max(1, int(parent_constraints.get("continuation_audit_interval") or DEFAULT_CONTINUATION_AUDIT_INTERVAL))
    branches = list(parent_constraints.get("tool_result_branches") or [])
    selected_branch = next((dict(item or {}) for item in branches if _tool_branch_matches(dict(item or {}), outcome)), None)
    if selected_branch and bool(selected_branch.get("stop_after_tool_step")):
        task.active_child_ids = []
        task.state = TaskState.COMPLETE
        return None
    if next_continuation_depth > max_continuation_depth:
        await _queue_trace_review_guardrail(
            storage,
            task,
            enqueue_fn,
            title=f"Audit Deep Continuation: {str(getattr(task, 'title', '') or task.task_id)[:84]}",
            description=(
                "A continuation chain exceeded the allowed recursion depth. "
                "Review whether the task is already sufficiently decomposed or should cash out instead of continuing."
            ),
            trigger="deep_continuation_guardrail",
            note=(
                f"Continuation depth {next_continuation_depth} exceeded max {max_continuation_depth} "
                f"after terminal tool step `{str(outcome.tool_name or 'tool')}`."
            ),
            associated_task_ids=[str(getattr(task, "parent_task_id", "") or "").strip()],
        )
        return None

    child_title = str(
        (selected_branch or {}).get("next_title")
        or outcome.continuation_title
        or f"Continue after {str(outcome.tool_name or 'tool')}"
    ).strip()
    child_description = str(
        (selected_branch or {}).get("next_description")
        or outcome.continuation_description
        or (
            f"Continue the task after the previous step executed `{str(outcome.tool_name or 'tool')}`. "
            "Consume the inherited tool result before deciding on any new model move."
        )
    ).strip()
    child_type = _task_type_from_name(
        (selected_branch or {}).get("task_type") or outcome.continuation_task_type,
        default=getattr(task, "type", TaskType.RESEARCH),
    )
    handoff = _terminal_tool_handoff(outcome, task=task, attempt_id=str(getattr(attempt, "attempt_id", "") or ""))
    child_constraints = dict(parent_constraints)
    child_constraints.update(dict(outcome.continuation_constraints or {}))
    child_constraints.update(dict((selected_branch or {}).get("constraints") or {}))
    child_constraints["handoff_context"] = handoff
    child_constraints["execution_mode"] = "serial"
    child_constraints["continuation_depth"] = next_continuation_depth
    child_constraints["max_continuation_depth"] = max_continuation_depth
    child_constraints["continuation_audit_interval"] = continuation_audit_interval
    child_constraints["lineage_root_task_id"] = _lineage_task_id(task) or str(task.task_id)
    child_constraints["terminal_tool_origin"] = {
        "tool_name": str(outcome.tool_name or "").strip(),
        "source_module": str(outcome.source_module or "").strip(),
    }

    child = storage.tasks.create(
        title=child_title,
        description=child_description,
        session_id=task.session_id,
        parent_task_id=task.task_id,
        state=TaskState.PENDING,
        constraints=child_constraints,
        flush=False,
    )
    child.type = child_type
    child.depth = int(getattr(task, "depth", 0) or 0) + 1
    child.repo_path = str(getattr(task, "repo_path", ".") or ".")
    task.active_child_ids = [child.task_id]
    task.state = TaskState.PUSHED
    storage.commit()
    await _enqueue_task(enqueue_fn, child.task_id, front=True)
    if next_continuation_depth % continuation_audit_interval == 0:
        audit_task = await _queue_trace_review_guardrail(
            storage,
            task,
            enqueue_fn,
            title=f"Audit Recursive Continuation: {str(getattr(task, 'title', '') or task.task_id)[:80]}",
            description=(
                "A continuation branch reached the configured audit interval. "
                "Verify that the branch remains well-framed and is not recursing unnecessarily."
            ),
            trigger="continuation_interval_audit",
            note=(
                f"Continuation depth reached {next_continuation_depth}; "
                f"tool `{str(outcome.tool_name or 'tool')}` completed and follow-up `{child.title}` was queued."
            ),
            associated_task_ids=[child.task_id, str(getattr(task, "parent_task_id", "") or "").strip()],
        )
        task.active_child_ids = list(dict.fromkeys([child.task_id, audit_task.task_id]))
        task.state = TaskState.PUSHED
        storage.commit()
    return child


def _terminal_handoff_payload(task: TaskModel, attempt) -> Dict[str, Any]:
    artifacts = dict(getattr(attempt, "artifacts", {}) or {})
    evidence = dict(getattr(attempt, "evidence", {}) or {})
    autopsy = dict(evidence.get("autopsy") or {}) if isinstance(evidence.get("autopsy"), dict) else {}
    terminal_tool = dict(artifacts.get("terminal_tool_call") or {})
    tool_call = dict(terminal_tool.get("tool_call") or autopsy.get("tool_call") or {})
    return {
        "task_id": str(getattr(task, "task_id", "") or ""),
        "title": str(getattr(task, "title", "") or ""),
        "attempt_id": str(getattr(attempt, "attempt_id", "") or ""),
        "outcome": str(getattr(getattr(attempt, "outcome", None), "value", getattr(attempt, "outcome", "")) or ""),
        "reason": str(getattr(attempt, "reason", "") or ""),
        "tool_call": tool_call,
        "tool_result_preview": str(terminal_tool.get("tool_result_preview") or autopsy.get("tool_result_preview") or ""),
        "tool_result_full": str(terminal_tool.get("tool_result_full") or autopsy.get("tool_result_full") or ""),
        "next_step_hint": str(terminal_tool.get("next_step_hint") or autopsy.get("next_step_hint") or ""),
        "avoid_repeating_first_tool": dict(terminal_tool.get("avoid_repeating_first_tool") or autopsy.get("avoid_repeating_first_tool") or {}),
        "failure_kind": str(evidence.get("failure_kind") or autopsy.get("failure_kind") or ""),
        "step_history": list(artifacts.get("step_history") or []),
        "duration_s": artifacts.get("duration_s"),
    }


def _update_parent_branch_state(storage, task: TaskModel, attempt) -> None:
    parent_task_id = getattr(task, "parent_task_id", None)
    if not parent_task_id:
        return
    parent = storage.tasks.get_by_id(parent_task_id)
    if parent is None:
        return
    parent_constraints = dict(getattr(parent, "constraints", {}) or {})
    branch_state = dict(parent_constraints.get("child_branch_state") or {})
    children = dict(branch_state.get("children") or {})
    payload = _terminal_handoff_payload(task, attempt)
    children[str(task.task_id)] = payload
    branch_state["children"] = children
    branch_state["last_completed_child_id"] = str(task.task_id)
    branch_state["last_updated_at"] = datetime.now(timezone.utc).isoformat()
    branch_state["open_child_ids"] = list(getattr(parent, "active_child_ids", []) or [])
    parent_constraints["child_branch_state"] = branch_state
    parent.constraints = parent_constraints


def _inherit_dependency_handoff(storage, task: TaskModel) -> bool:
    constraints = dict(getattr(task, "constraints", {}) or {})
    execution_mode = str(constraints.get("execution_mode") or "").strip().lower()
    if execution_mode and execution_mode != "serial":
        return False
    completed_dependencies = [dep for dep in list(getattr(task, "dependencies", []) or []) if dep.state == TaskState.COMPLETE]
    if not completed_dependencies:
        return False
    dependency = sorted(
        completed_dependencies,
        key=lambda dep: getattr(dep, "updated_at", None) or getattr(dep, "created_at", None) or datetime.min,
    )[-1]
    attempts = list(storage.attempts.get_by_task_id(dependency.task_id) or [])
    successful = [attempt for attempt in attempts if attempt.outcome == AttemptOutcome.SUCCEEDED]
    if not successful:
        return False
    latest_attempt = sorted(
        successful,
        key=lambda attempt: getattr(attempt, "ended_at", None) or getattr(attempt, "started_at", None) or datetime.min,
    )[-1]
    payload = _terminal_handoff_payload(dependency, latest_attempt)
    handoff_context = dict(constraints.get("handoff_context") or {})
    handoff_context.update(
        {
            "upstream_task_id": payload.get("task_id"),
            "upstream_title": payload.get("title"),
            "upstream_attempt_id": payload.get("attempt_id"),
            "tool_call": payload.get("tool_call") or {},
            "tool_result_preview": payload.get("tool_result_preview") or "",
            "tool_result_full": payload.get("tool_result_full") or "",
            "next_step_hint": payload.get("next_step_hint") or "",
            "avoid_repeating_first_tool": payload.get("avoid_repeating_first_tool") or {},
        }
    )
    parent_task_id = getattr(task, "parent_task_id", None)
    if parent_task_id:
        parent = storage.tasks.get_by_id(parent_task_id)
        if parent is not None:
            handoff_context["parent_branch_state"] = dict((getattr(parent, "constraints", {}) or {}).get("child_branch_state") or {})
    constraints["handoff_context"] = handoff_context
    task.constraints = constraints
    return True


def _latest_attempt_handoff(storage, task: TaskModel) -> Dict[str, Any]:
    attempts = list(storage.attempts.get_by_task_id(task.task_id) or [])
    if not attempts:
        return {}
    attempt = attempts[0]
    evidence = dict(getattr(attempt, "evidence", {}) or {})
    autopsy = dict(evidence.get("autopsy") or {}) if isinstance(evidence.get("autopsy"), dict) else {}
    tool_call = dict(autopsy.get("tool_call") or {}) if isinstance(autopsy.get("tool_call"), dict) else {}
    handoff: Dict[str, Any] = {
        "from_task_id": str(task.task_id or ""),
        "from_task_title": str(getattr(task, "title", "") or ""),
        "from_attempt_id": str(getattr(attempt, "attempt_id", "") or ""),
        "failure_kind": str(evidence.get("failure_kind") or autopsy.get("failure_kind") or "").strip(),
        "next_step_hint": str(autopsy.get("next_step_hint") or "").strip(),
        "tool_result_preview": str(autopsy.get("tool_result_preview") or "").strip(),
        "tool_call": {
            "name": str(tool_call.get("name") or "").strip(),
            "arguments": str(tool_call.get("arguments") or "").strip(),
        },
    }
    tool_name = str(((handoff.get("tool_call") or {}).get("name")) or "").strip()
    if tool_name:
        handoff["avoid_repeating_first_tool"] = {
            "name": tool_name,
            "reason": "This tool call already ran in the parent step; only repeat it if the inherited result was insufficient.",
        }
    if handoff.get("tool_call", {}).get("name") == "":
        handoff.pop("tool_call", None)
    if not handoff.get("tool_result_preview"):
        handoff.pop("tool_result_preview", None)
    if not handoff.get("next_step_hint"):
        handoff.pop("next_step_hint", None)
    if not handoff.get("failure_kind"):
        handoff.pop("failure_kind", None)
    if not handoff.get("avoid_repeating_first_tool"):
        handoff.pop("avoid_repeating_first_tool", None)
    return {k: v for k, v in handoff.items() if v not in ("", None, {}, [])}


def _ensure_procedure_item_source_hints(task: TaskModel) -> bool:
    constraints = dict(getattr(task, "constraints", {}) or {})
    checklist_item = dict(constraints.get("procedure_checklist_item") or {})
    item_id = str(checklist_item.get("id") or "").strip()
    if not item_id:
        return False
    source_hints = dict(constraints.get("source_hints") or {})
    if source_hints:
        return False
    inferred = _procedure_checklist_item_source_hints(item_id)
    if not inferred:
        return False
    constraints["source_hints"] = inferred
    task.constraints = constraints
    return True


def _emit_task_communication(storage, task, *, content: str, source_kind: str) -> None:
    task_type = str(getattr(task.type, "value", "") or "").lower()
    summary = str(getattr(task, "title", "") or "").strip() or str(getattr(task, "description", "") or "").strip()[:160]
    decision = build_communication_decision(
        role="assistant",
        content=content,
        lane=infer_lane_from_task(task) or "trainer",
        channel="existing_session_message",
        session_id=task.session_id or "default",
        audience="user",
        source_kind=source_kind,
        source_actor="task_runner",
        opened_reason="task_progress",
        tags=["task", task_type, "autonomous"],
        topic_summary=summary[:180],
        session_title=summary[:80],
        communicative_act="notification",
        urgency="normal",
    )
    deliver_communication_decision(storage, decision)


def _is_non_actionable_recovery_subtask(proto) -> bool:
    title = str(getattr(proto, "title", "") or "").strip().lower()
    description = str(getattr(proto, "description", "") or "").strip().lower()
    target_files = list(getattr(proto, "target_files", []) or [])
    generic_markers = [
        title == "error recover",
        "research manually" in description,
        "initial decomposition failed" in description,
        "manual recovery" in description,
        "generic recovery" in description,
    ]
    if any(generic_markers):
        return True
    if not target_files and (title.startswith("recovery") or "recover" in title) and "clarif" not in description:
        return True
    return False


def _recoverable_focus_task(storage, task):
    focus_task_id = str((getattr(task, "constraints", {}) or {}).get("recovery_focus_task_id") or "").strip()
    if focus_task_id and hasattr(storage, "tasks"):
        focus = storage.tasks.get_by_id(focus_task_id)
        if focus is not None:
            return focus
    parent_task_id = getattr(task, "parent_task_id", None)
    if parent_task_id and hasattr(storage, "tasks"):
        focus = storage.tasks.get_by_id(parent_task_id)
        if focus is not None:
            return focus
    return task


def _procedure_checklist_subtasks(storage, task):
    focus = _recoverable_focus_task(storage, task)
    constraints = dict(getattr(focus, "constraints", {}) or {})
    checklist = list(constraints.get("procedure_checklist") or [])
    if not checklist:
        return []
    procedure = ensure_draft_procedure_for_task(storage, focus, checklist=checklist)
    constraints["procedure_id"] = procedure["procedure_id"]
    constraints["procedure_title"] = procedure["title"]
    constraints["procedure_lifecycle_state"] = procedure["lifecycle_state"]
    constraints["procedure_lineage_id"] = procedure["lineage_id"]
    focus.constraints = constraints
    procedure_title = str(constraints.get("procedure_title") or getattr(focus, "title", "") or "Procedure").strip()
    created = []
    for item in checklist:
        item_id = str(item.get("id") or "").strip()
        item_title = str(item.get("title") or "").strip()
        verification = str(item.get("verification") or "").strip()
        if not item_title:
            continue
        source_hints = _procedure_checklist_item_source_hints(item_id)
        created.append(
            {
                "title": f"Procedure Step: {item_title}",
                "description": (
                    f"Advance exactly one checklist item for the Procedure '{procedure_title}'. "
                    f"Focus only on [{item_id}] {item_title}. "
                    f"Verification target: {verification}. "
                    "If the item cannot be completed directly, convert the missing information into an explicit pending question "
                    "or durable attention item instead of broadening scope."
                ).strip(),
                "task_type": TaskType.RESEARCH,
                "constraints": {
                    "lane": dict(getattr(task, "constraints", {}) or {}).get("lane"),
                    "procedure_id": constraints.get("procedure_id"),
                    "procedure_title": procedure_title,
                    "procedure_lifecycle_state": constraints.get("procedure_lifecycle_state"),
                    "procedure_lineage_id": constraints.get("procedure_lineage_id"),
                    "procedure_parent_task_id": getattr(focus, "task_id", None),
                    "execution_mode": "parallel",
                    "source_hints": source_hints,
                    "carry_forward": _default_carry_forward_policy(),
                    "procedure_checklist_item": {
                        "id": item_id,
                        "title": item_title,
                        "verification": verification,
                    },
                    "recovered_from_decomposition_failure": True,
                },
            }
        )
    return created


def _procedure_item_recovery_subtasks(storage, task):
    focus = _recoverable_focus_task(storage, task)
    constraints = dict(getattr(focus, "constraints", {}) or {})
    procedure = ensure_draft_procedure_for_task(storage, focus)
    constraints["procedure_id"] = procedure["procedure_id"]
    constraints["procedure_title"] = procedure["title"]
    constraints["procedure_lifecycle_state"] = procedure["lifecycle_state"]
    constraints["procedure_lineage_id"] = procedure["lineage_id"]
    focus.constraints = constraints
    checklist_item = dict(constraints.get("procedure_checklist_item") or {})
    item_id = str(checklist_item.get("id") or "").strip()
    item_title = str(checklist_item.get("title") or getattr(focus, "title", "") or "Procedure item").strip()
    verification = str(checklist_item.get("verification") or "").strip()
    if not item_id or not item_title:
        return []
    source_hints = dict(constraints.get("source_hints") or _procedure_checklist_item_source_hints(item_id))
    handoff_context = _latest_attempt_handoff(storage, focus)
    preferred_paths = list(source_hints.get("preferred_paths") or [])
    guidance = str(source_hints.get("guidance") or "").strip()
    base_constraints = {
        "lane": constraints.get("lane") or dict(getattr(task, "constraints", {}) or {}).get("lane"),
        "procedure_id": constraints.get("procedure_id"),
        "procedure_title": constraints.get("procedure_title"),
        "procedure_parent_task_id": constraints.get("procedure_parent_task_id") or getattr(focus, "parent_task_id", None),
        "procedure_checklist_item": checklist_item,
        "source_hints": source_hints,
        "carry_forward": _default_carry_forward_policy(),
        "handoff_context": handoff_context,
        "execution_mode": "serial",
        "recovered_from_decomposition_failure": True,
    }
    subtasks = []
    inspect_dependencies = []
    inspect_targets = preferred_paths or [f"[{item_id}] {item_title}"]
    for index, path in enumerate(inspect_targets, start=1):
        normalized_path = str(path).strip()
        proto_id = f"inspect_{index}"
        inspect_dependencies.append(proto_id)
        subtasks.append(
            {
                "proto_id": proto_id,
                "title": f"Inspect {normalized_path} for {item_title}",
                "description": (
                    f"Inspect exactly one source for checklist item [{item_id}] {item_title}. "
                    f"Focus only on '{normalized_path}'. "
                    f"Verification target: {verification}. "
                    f"{guidance}".strip()
                ),
                "task_type": TaskType.RESEARCH,
                "constraints": {
                    **base_constraints,
                    "target_files": [normalized_path],
                    "preferred_start_paths": [normalized_path],
                    "disallow_broad_repo_scan": True,
                    "recovery_phase": "inspect",
                    "inspect_target_path": normalized_path,
                },
                "dependencies": [],
            }
        )

    subtasks.extend([
        {
            "proto_id": "decide_status",
            "title": f"Decide status for {item_title}",
            "description": (
                f"Use the inspected evidence for [{item_id}] {item_title} to decide one of two outcomes only: "
                "either the checklist item is satisfied from current evidence, or it still needs clarification/attention. "
                "Do not broaden scope."
            ),
            "task_type": TaskType.RESEARCH,
            "constraints": {
                **base_constraints,
                "target_files": preferred_paths,
                "preferred_start_paths": preferred_paths,
                "disallow_broad_repo_scan": True,
                "recovery_phase": "decide",
            },
            "dependencies": inspect_dependencies,
        },
        {
            "proto_id": "cash_out",
            "title": f"Cash out {item_title}",
            "description": (
                f"Take the status decision for [{item_id}] {item_title} and cash it out into durable progress: "
                "persist the confirmed setting/knowledge if satisfied, or create an explicit pending question or durable attention item if unresolved."
            ),
            "task_type": TaskType.RESEARCH,
            "constraints": {
                **base_constraints,
                "target_files": preferred_paths,
                "preferred_start_paths": preferred_paths,
                "disallow_broad_repo_scan": True,
                "recovery_phase": "cash_out",
            },
            "dependencies": ["decide_status"],
        },
    ])
    return subtasks


def _read_text_if_available(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _startup_smoke_repo_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "strata").is_dir() and (cwd / "strata_ui").is_dir():
        return cwd
    for candidate in [cwd, *cwd.parents]:
        if (candidate / "strata").is_dir() and (candidate / "strata_ui").is_dir():
            return candidate
    return cwd


def _run_startup_smoke_check(task: TaskModel) -> Dict[str, Any] | None:
    constraints = dict(getattr(task, "constraints", {}) or {})
    if str(constraints.get("procedure_id") or "").strip() != STARTUP_SMOKE_PROCEDURE_ID:
        return None
    checklist_item = dict(constraints.get("procedure_checklist_item") or {})
    item_id = str(checklist_item.get("id") or "").strip()
    if not item_id:
        return None

    root_dir = _startup_smoke_repo_root()
    source_hints = dict(constraints.get("source_hints") or _procedure_checklist_item_source_hints(item_id))
    preferred_paths = [str(path).strip() for path in list(source_hints.get("preferred_paths") or []) if str(path).strip()]
    existing_paths = []
    missing_paths = []
    unreadable_paths = []
    path_checks = []

    for rel_path in preferred_paths:
        abs_path = root_dir / rel_path
        exists = abs_path.exists()
        readable = abs_path.is_file() and bool(_read_text_if_available(abs_path))
        if exists:
            existing_paths.append(rel_path)
        else:
            missing_paths.append(rel_path)
        if exists and abs_path.is_file() and not readable:
            unreadable_paths.append(rel_path)
        path_checks.append(
            {
                "path": rel_path,
                "exists": exists,
                "readable": readable if abs_path.is_file() else exists,
            }
        )

    if item_id == "spec_presence":
        satisfied = not missing_paths and not unreadable_paths
        summary = (
            "Verified canonical spec files are present and readable."
            if satisfied
            else "Canonical spec files are missing or unreadable."
        )
        return {
            "satisfied": satisfied,
            "summary": summary,
            "evidence": {
                "kind": "deterministic_startup_smoke_check",
                "item_id": item_id,
                "path_checks": path_checks,
                "missing_paths": missing_paths,
                "unreadable_paths": unreadable_paths,
            },
        }

    if item_id == "runtime_wiring":
        runtime_ipc_text = _read_text_if_available(root_dir / "strata/orchestrator/worker/runtime_ipc.py")
        api_main_text = _read_text_if_available(root_dir / "strata/api/main.py")
        wiring_markers = {
            "runtime_ipc_present": bool(runtime_ipc_text),
            "api_external_worker_mode": "STRATA_API_EMBED_WORKER" in api_main_text or "external-worker mode" in api_main_text,
        }
        satisfied = not missing_paths and wiring_markers["runtime_ipc_present"] and wiring_markers["api_external_worker_mode"]
        summary = (
            "Verified separate API and worker runtime wiring surfaces are present."
            if satisfied
            else "Split API/worker runtime wiring could not be verified deterministically."
        )
        return {
            "satisfied": satisfied,
            "summary": summary,
            "evidence": {
                "kind": "deterministic_startup_smoke_check",
                "item_id": item_id,
                "path_checks": path_checks,
                "markers": wiring_markers,
                "missing_paths": missing_paths,
            },
        }

    if item_id == "desktop_surface":
        app_text = _read_text_if_available(root_dir / "strata_ui/src/App.jsx")
        settings_text = _read_text_if_available(root_dir / "strata_ui/src/views/SettingsView.jsx")
        desktop_text = _read_text_if_available(root_dir / "src-tauri/src/main.rs")
        surface_markers = {
            "settings_has_updater_surface": "desktop_update_status" in settings_text,
            "desktop_has_update_command": "desktop_update_status" in desktop_text,
            "app_has_lane_status_surface": "TopModeTab" in app_text and "laneDetails" in app_text,
        }
        satisfied = not missing_paths and all(surface_markers.values())
        summary = (
            "Verified desktop runtime status/update surfaces are present."
            if satisfied
            else "Desktop runtime status/update surfaces could not be verified deterministically."
        )
        return {
            "satisfied": satisfied,
            "summary": summary,
            "evidence": {
                "kind": "deterministic_startup_smoke_check",
                "item_id": item_id,
                "path_checks": path_checks,
                "markers": surface_markers,
                "missing_paths": missing_paths,
            },
        }

    return None


async def _expand_root_procedure_task(task, storage, enqueue_fn, *, progress_fn=None, attempt_id=None):
    procedure_children = _procedure_checklist_subtasks(storage, task)
    if not procedure_children:
        raise RuntimeError("Root Procedure expansion produced no checklist children.")
    spawned_ids = []
    for item in procedure_children:
        sub = storage.tasks.create(
            title=item["title"],
            description=item["description"],
            session_id=task.session_id,
            parent_task_id=task.task_id,
            state=TaskState.PENDING,
            constraints=item["constraints"],
            flush=False,
        )
        sub.type = item["task_type"]
        sub.depth = task.depth + 1
        spawned_ids.append(sub.task_id)
    task.active_child_ids = list(dict.fromkeys(spawned_ids))
    task.state = TaskState.PUSHED
    storage.commit()
    if progress_fn:
        progress_fn(
            step="decompose",
            label="Expanding procedure",
            detail=f"Spawned {len(spawned_ids)} checklist children",
            progress_label="children in progress",
            attempt_id=attempt_id,
        )
    for task_id in spawned_ids:
        await _enqueue_task(enqueue_fn, task_id, front=True)
    return spawned_ids

async def run_attempt(task: TaskModel, storage, model_adapter, notify_fn, enqueue_fn, progress_fn=None):
    """
    @summary Execute a single task attempt.
    """
    for prior_attempt in storage.attempts.get_by_task_id(task.task_id):
        if prior_attempt.outcome is None and prior_attempt.ended_at is None:
            storage.attempts.update_outcome(
                prior_attempt.attempt_id,
                AttemptOutcome.CANCELLED,
                reason="Superseded by a newer attempt.",
            )

    dependency_handoff_updated = _inherit_dependency_handoff(storage, task)
    # Start a new Attempt
    attempt = storage.attempts.create(task_id=task.task_id)
    constraints_updated = _ensure_procedure_item_source_hints(task)
    if task.state != TaskState.WORKING:
        task.state = TaskState.WORKING
    storage.commit()
    if dependency_handoff_updated or constraints_updated or task.state == TaskState.WORKING:
        await notify_fn(task.task_id, task.state.value)
    started_at = time.perf_counter()
    step_history = []

    def _record_progress(*, step: str, label: str, detail: str = "", progress_label: str | None = None, attempt_id: str | None = None):
        event = {
            "step": str(step or "").strip().lower() or "unknown",
            "label": str(label or "").strip() or "Working",
            "detail": str(detail or "").strip(),
            "at": datetime.now(timezone.utc).isoformat(),
        }
        step_history.append(event)
        if progress_fn:
            progress_fn(
                step=event["step"],
                label=event["label"],
                detail=event["detail"],
                progress_label=progress_label,
                attempt_id=attempt_id or attempt.attempt_id,
            )
    
    logger.info(f"Running task {task.task_id} ({task.type.value}), Attempt {attempt.attempt_id}")
    _record_progress(
        step="attempt",
        label="Attempt running",
        detail=f"{task.type.value.lower()} attempt started",
        progress_label="attempt active",
        attempt_id=attempt.attempt_id,
    )

    try:
        execution_result = None
        if task.type == TaskType.RESEARCH and _is_root_procedure_task(task):
            await _expand_root_procedure_task(
                task,
                storage,
                enqueue_fn,
                progress_fn=_record_progress,
                attempt_id=attempt.attempt_id,
            )
            storage.attempts.update_outcome(attempt.attempt_id, AttemptOutcome.SUCCEEDED)
            attempt.artifacts["duration_s"] = round(time.perf_counter() - started_at, 4)
            attempt.artifacts["step_history"] = list(step_history)
            attempt.artifacts["procedure_expansion"] = {
                "mode": "deterministic_root_checklist_expansion",
                "active_child_ids": list(getattr(task, "active_child_ids", []) or []),
            }
            storage.commit()
            await notify_fn(task.task_id, task.state.value)
            return True, None, attempt
        startup_smoke_check = _run_startup_smoke_check(task) if task.type == TaskType.RESEARCH else None
        if startup_smoke_check is not None:
            _record_progress(
                step="deterministic_check",
                label="Running deterministic check",
                detail=startup_smoke_check.get("summary", ""),
                progress_label="deterministic check",
                attempt_id=attempt.attempt_id,
            )
            if not startup_smoke_check.get("satisfied"):
                raise RuntimeError(startup_smoke_check.get("summary") or "Deterministic startup smoke check did not satisfy its verification target.")
            storage.attempts.update_outcome(attempt.attempt_id, AttemptOutcome.SUCCEEDED)
            deterministic_evidence = dict(startup_smoke_check.get("evidence") or {})
            attempt.artifacts = {
                **dict(getattr(attempt, "artifacts", {}) or {}),
                "duration_s": round(time.perf_counter() - started_at, 4),
                "step_history": list(step_history),
                "deterministic_check": deterministic_evidence,
            }
            attempt.evidence = {
                **dict(getattr(attempt, "evidence", {}) or {}),
                "deterministic_check": deterministic_evidence,
            }
            _update_parent_branch_state(storage, task, attempt)
            task.state = TaskState.COMPLETE
            procedure_id = str(dict(getattr(task, "constraints", {}) or {}).get("procedure_id") or "").strip()
            if procedure_id:
                record_procedure_run(storage, procedure_id, outcome="succeeded", source_task_id=task.task_id)
            storage.commit()
            await notify_fn(task.task_id, task.state.value)
            return True, None, attempt
        if task.type == TaskType.RESEARCH:
            execution_result = await _run_research(task, storage, model_adapter, enqueue_fn, progress_fn=_record_progress, attempt_id=attempt.attempt_id)
        elif task.type == TaskType.DECOMP:
            execution_result = await _run_decomposition(task, storage, model_adapter, enqueue_fn)
        elif task.type in {TaskType.IMPL, TaskType.BUG_FIX, TaskType.REFACTOR}:
            execution_result = await _run_implementation(task, storage, model_adapter, progress_fn=_record_progress, attempt_id=attempt.attempt_id)
        elif task.type == TaskType.JUDGE:
            execution_result = await _run_judge(task, storage, model_adapter)
        else:
            raise NotImplementedError(f"Unsupported task type {task.type}")

        if isinstance(execution_result, TerminalToolCallOutcome):
            terminal_payload = {
                "tool_call": {
                    "name": str(execution_result.tool_name or "").strip(),
                    "arguments": dict(execution_result.tool_arguments or {}),
                },
                "tool_result_preview": str(execution_result.tool_result_preview or "").strip(),
                "tool_result_full": str(execution_result.tool_result_full or "").strip(),
                "next_step_hint": str(execution_result.next_step_hint or "").strip(),
                "avoid_repeating_first_tool": {
                    "name": str(execution_result.tool_name or "").strip(),
                    "reason": "This tool already ran in the prior step; consume its result explicitly before deciding whether to repeat it.",
                },
                "source_module": str(execution_result.source_module or "").strip(),
                "metadata": dict(execution_result.metadata or {}),
            }
            attempt.artifacts["terminal_tool_call"] = terminal_payload
            should_flush = enqueue_attempt_observability_artifact(
                {
                    "task_id": task.task_id,
                    "attempt_id": attempt.attempt_id,
                    "session_id": task.session_id,
                    "artifact_kind": "terminal_tool_call",
                    "payload": terminal_payload,
                }
            )
            if should_flush:
                flush_observability_writes()
            spawned_followup = await _enqueue_terminal_tool_followup(storage, task, attempt, execution_result, enqueue_fn)
            if spawned_followup is not None:
                _emit_task_communication(
                    storage,
                    task,
                    content=(
                        f"Tool step complete: `{execution_result.tool_name}` executed. "
                        f"Queued explicit follow-up step '{spawned_followup.title}'."
                    ),
                    source_kind="task_tool_step_handoff",
                )
            else:
                _emit_task_communication(
                    storage,
                    task,
                    content=f"Tool step complete: `{execution_result.tool_name}` executed with no follow-up step queued.",
                    source_kind="task_tool_step_handoff",
                )

        # If we got here without exception, it succeeded
        storage.attempts.update_outcome(attempt.attempt_id, AttemptOutcome.SUCCEEDED)
        
        # Populate operational artifacts for the attempt
        if hasattr(model_adapter, 'last_response') and model_adapter.last_response:
            attempt.artifacts["model"] = model_adapter.last_response.model
            attempt.artifacts["provider"] = model_adapter.last_response.provider
            attempt.artifacts["usage"] = model_adapter.last_response.usage or {}
        attempt.artifacts["duration_s"] = round(time.perf_counter() - started_at, 4)
        attempt.artifacts["step_history"] = list(step_history)
        _update_parent_branch_state(storage, task, attempt)

        if task.state != TaskState.PUSHED:
            task.state = TaskState.COMPLETE
            procedure_id = str(dict(getattr(task, "constraints", {}) or {}).get("procedure_id") or "").strip()
            if procedure_id:
                record_procedure_run(storage, procedure_id, outcome="succeeded", source_task_id=task.task_id)
        storage.commit()
        await notify_fn(task.task_id, task.state.value)
        return True, None, attempt

    except Exception as e:
        artifacts: Dict[str, Any] = {}
        evidence: Dict[str, Any] = {}
        if hasattr(model_adapter, 'last_response') and model_adapter.last_response:
            artifacts["model"] = model_adapter.last_response.model
            artifacts["provider"] = model_adapter.last_response.provider
            artifacts["usage"] = model_adapter.last_response.usage or {}
        artifacts["duration_s"] = round(time.perf_counter() - started_at, 4)
        artifacts["step_history"] = list(step_history)
        failure_kind = str(getattr(e, "failure_kind", "") or "").strip()
        if failure_kind:
            evidence["failure_kind"] = failure_kind
        autopsy = getattr(e, "autopsy", None)
        if isinstance(autopsy, dict) and autopsy:
            evidence["autopsy"] = autopsy
        storage.rollback()
        failed_attempt = storage.attempts.get_by_id(attempt.attempt_id) if getattr(attempt, "attempt_id", None) else None
        if failed_attempt:
            failed_attempt.artifacts = {**dict(failed_attempt.artifacts or {}), **artifacts}
            failed_attempt.evidence = {**dict(failed_attempt.evidence or {}), **evidence}
            storage.attempts.update_outcome(failed_attempt.attempt_id, AttemptOutcome.FAILED, reason=str(e))
            _update_parent_branch_state(storage, task, failed_attempt)
            storage.commit()
            if evidence:
                should_flush = enqueue_attempt_observability_artifact(
                    {
                        "task_id": task.task_id,
                        "attempt_id": failed_attempt.attempt_id,
                        "session_id": task.session_id,
                        "artifact_kind": "failure_autopsy",
                        "payload": {
                            "reason": str(e),
                            "evidence": dict(evidence),
                            "artifacts": dict(failed_attempt.artifacts or {}),
                        },
                    }
                )
                if should_flush:
                    flush_observability_writes()
            procedure_id = str(dict(getattr(task, "constraints", {}) or {}).get("procedure_id") or "").strip()
            if procedure_id:
                record_procedure_run(storage, procedure_id, outcome="failed", source_task_id=task.task_id)
            attempt = failed_attempt
        else:
            attempt.artifacts = {**dict(getattr(attempt, "artifacts", {}) or {}), **artifacts}
            attempt.evidence = {**dict(getattr(attempt, "evidence", {}) or {}), **evidence}
        return False, e, attempt

async def _run_research(task, storage, model_adapter, enqueue_fn, *, progress_fn=None, attempt_id=None):
    research = ResearchModule(model_adapter, storage, enqueue_task=enqueue_fn)
    report = await research.conduct_research(
        task_description=task.description,
        repo_path=task.repo_path,
        progress_fn=progress_fn,
        attempt_id=attempt_id,
        context_hints=dict(task.constraints or {}),
        task_context={
            "task_id": task.task_id,
            "parent_task_id": task.parent_task_id,
            "title": task.title,
            "type": getattr(task.type, "value", str(task.type)),
            "state": getattr(task.state, "value", str(task.state)),
            "session_id": task.session_id,
        },
    )
    if isinstance(report, TerminalToolCallOutcome):
        return report
    # Formatter would go here, simplified for brevity
    _emit_task_communication(
        storage,
        task,
        content=f"🔬 **Research Complete**\n{report.suggested_approach}",
        source_kind="task_research_complete",
    )
    task.state = TaskState.WORKING
    storage.commit()
    return report

async def _run_decomposition(task, storage, model_adapter, enqueue_fn):
    if await _resume_parent_repair_task_if_supported(storage, task, enqueue_fn):
        return
    constraints = dict(getattr(task, "constraints", {}) or {})
    max_decomposition_depth = max(1, int(constraints.get("max_decomposition_depth") or DEFAULT_MAX_DECOMPOSITION_DEPTH))
    decomposition_depth = _count_ancestor_tasks(storage, task, task_types={TaskType.DECOMP})
    if decomposition_depth >= max_decomposition_depth:
        await _queue_trace_review_guardrail(
            storage,
            task,
            enqueue_fn,
            title=f"Audit Recursive Decomposition: {str(getattr(task, 'title', '') or task.task_id)[:78]}",
            description=(
                "This task has already been decomposed multiple times. "
                "Audit whether the task is already sufficiently decomposed or whether the decomposition procedure is misbehaving."
            ),
            trigger="deep_decomposition_guardrail",
            note=(
                f"Decomposition ancestry depth {decomposition_depth} met/exceeded max {max_decomposition_depth}. "
                "Escalating to audit instead of generating another decomposition layer."
            ),
            associated_task_ids=[str(getattr(task, "parent_task_id", "") or "").strip()],
        )
        return
    decomp_mod = DecompositionModule(model_adapter, storage)
    decomp = await decomp_mod.decompose_task(task.title, task.description)
    actionable = []
    for tid, proto in (decomp.subtasks or {}).items():
        if _is_non_actionable_recovery_subtask(proto):
            logger.warning(
                "Rejected non-actionable recovery subtask from decomposition for task %s: %s",
                task.task_id,
                getattr(proto, "title", tid),
            )
            continue
        actionable.append((tid, proto))
    if not actionable:
        item_fallback = _procedure_item_recovery_subtasks(storage, task)
        if item_fallback:
            spawned_by_proto_id = {}
            spawned_ids = []
            for item in item_fallback:
                sub = storage.tasks.create(
                    title=item["title"],
                    description=item["description"],
                    session_id=task.session_id,
                    parent_task_id=task.task_id,
                    state=TaskState.PENDING,
                    constraints=item["constraints"],
                    flush=False,
                )
                sub.type = item["task_type"]
                sub.depth = task.depth + 1
                spawned_ids.append(sub.task_id)
                spawned_by_proto_id[str(item["proto_id"])] = sub
            for item in item_fallback:
                sub = spawned_by_proto_id.get(str(item["proto_id"]))
                if sub is None:
                    continue
                for dep_tid in list(item.get("dependencies") or []):
                    dep_task = spawned_by_proto_id.get(str(dep_tid))
                    if dep_task is None or dep_task.task_id == sub.task_id:
                        continue
                    if dep_task not in sub.dependencies:
                        sub.dependencies.append(dep_task)
            task.active_child_ids = list(dict.fromkeys(spawned_ids))
            task.state = TaskState.PUSHED
            storage.commit()
            for task_id in spawned_ids:
                await _enqueue_task(enqueue_fn, task_id, front=True)
            return
        procedure_fallback = _procedure_checklist_subtasks(storage, task)
        if procedure_fallback:
            spawned_ids = []
            for item in procedure_fallback:
                sub = storage.tasks.create(
                    title=item["title"],
                    description=item["description"],
                    session_id=task.session_id,
                    parent_task_id=task.task_id,
                    state=TaskState.PENDING,
                    constraints=item["constraints"],
                    flush=False,
                )
                sub.type = item["task_type"]
                sub.depth = task.depth + 1
                spawned_ids.append(sub.task_id)
            task.active_child_ids = list(dict.fromkeys(spawned_ids))
            task.state = TaskState.PUSHED
            storage.commit()
            for task_id in spawned_ids:
                await _enqueue_task(enqueue_fn, task_id, front=True)
            return
        raise RuntimeError(
            "Decomposition produced no actionable subtasks. Treat this as a recoverable planning failure and generate a more concrete recovery plan instead of escalating by default."
        )
    spawned_ids = []
    spawned_by_proto_id = {}
    for tid, proto in actionable:
        sub = storage.tasks.create(
            title=proto.title,
            description=proto.description,
            session_id=task.session_id,
            parent_task_id=task.task_id,
            state=TaskState.PENDING,
            flush=False,
            constraints={
                "target_files": proto.target_files,
                "edit_type": proto.edit_type,
                "validator": proto.validator,
                "max_diff_size": proto.max_diff_size
            }
        )
        sub.type = TaskType.IMPL
        sub.depth = task.depth + 1
        spawned_ids.append(sub.task_id)
        spawned_by_proto_id[str(tid)] = sub
    for tid, proto in actionable:
        sub = spawned_by_proto_id.get(str(tid))
        if sub is None:
            continue
        for dep_tid in list(getattr(proto, "dependencies", []) or []):
            dep_task = spawned_by_proto_id.get(str(dep_tid))
            if dep_task is None or dep_task.task_id == sub.task_id:
                continue
            if dep_task not in sub.dependencies:
                sub.dependencies.append(dep_task)
    task.active_child_ids = list(dict.fromkeys(spawned_ids))
    task.state = TaskState.PUSHED
    storage.commit()
    for task_id in spawned_ids:
        await _enqueue_task(enqueue_fn, task_id, front=True)

async def _run_implementation(task, storage, model_adapter, *, progress_fn=None, attempt_id=None):
    research_mod = ResearchModule(model_adapter, storage)
    impl_mod = ImplementationModule(model_adapter, storage, research_mod)
    candidate_ids = await impl_mod.implement_task(task.task_id, progress_fn=progress_fn, attempt_id=attempt_id)
    if isinstance(candidate_ids, TerminalToolCallOutcome):
        return candidate_ids
    _emit_task_communication(
        storage,
        task,
        content=f"🛠️ **Implementation Staged**\nI've generated {len(candidate_ids)} candidates.",
        source_kind="task_implementation_staged",
    )
    task.state = TaskState.WORKING
    storage.commit()
    return candidate_ids


async def _run_judge(task, storage, model_adapter):
    payload = await run_eval_job_task(task, storage, model_adapter)
    summary = payload.get("recommendation") or payload.get("suite_name") or payload.get("run_label") or "completed"
    _emit_task_communication(
        storage,
        task,
        content=f"📏 **Eval Job Complete**\n{task.title}: {summary}",
        source_kind="task_eval_complete",
    )
    task.state = TaskState.WORKING
    storage.commit()
