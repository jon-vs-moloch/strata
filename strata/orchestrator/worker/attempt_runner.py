"""
@module orchestrator.worker.attempt_runner
@purpose Dispatches task execution based on task type.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict
from strata.communication.primitives import build_communication_decision, deliver_communication_decision
from strata.core.lanes import infer_lane_from_task
from strata.observability.writer import enqueue_attempt_observability_artifact, flush_observability_writes
from strata.storage.models import TaskModel, TaskType, TaskState, AttemptOutcome
from strata.orchestrator.research import ResearchModule
from strata.orchestrator.decomposition import DecompositionModule
from strata.orchestrator.implementation import ImplementationModule
from strata.eval.job_runner import run_eval_job_task

logger = logging.getLogger(__name__)


async def _enqueue_task(enqueue_fn, task_id: str, *, front: bool = False) -> None:
    try:
        await enqueue_fn(task_id, front=front)
    except TypeError:
        await enqueue_fn(task_id)


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
                    "procedure_parent_task_id": getattr(focus, "task_id", None),
                    "source_hints": source_hints,
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
    checklist_item = dict(constraints.get("procedure_checklist_item") or {})
    item_id = str(checklist_item.get("id") or "").strip()
    item_title = str(checklist_item.get("title") or getattr(focus, "title", "") or "Procedure item").strip()
    verification = str(checklist_item.get("verification") or "").strip()
    if not item_id or not item_title:
        return []
    source_hints = dict(constraints.get("source_hints") or _procedure_checklist_item_source_hints(item_id))
    preferred_paths = list(source_hints.get("preferred_paths") or [])
    guidance = str(source_hints.get("guidance") or "").strip()
    base_constraints = {
        "lane": constraints.get("lane") or dict(getattr(task, "constraints", {}) or {}).get("lane"),
        "procedure_id": constraints.get("procedure_id"),
        "procedure_title": constraints.get("procedure_title"),
        "procedure_parent_task_id": constraints.get("procedure_parent_task_id") or getattr(focus, "parent_task_id", None),
        "procedure_checklist_item": checklist_item,
        "source_hints": source_hints,
        "recovered_from_decomposition_failure": True,
    }
    return [
        {
            "proto_id": "inspect_sources",
            "title": f"Inspect sources for {item_title}",
            "description": (
                f"Inspect only the most relevant sources for checklist item [{item_id}] {item_title}. "
                f"Verification target: {verification}. "
                f"{guidance}".strip()
            ),
            "task_type": TaskType.RESEARCH,
            "constraints": {
                **base_constraints,
                "target_files": preferred_paths,
                "preferred_start_paths": preferred_paths,
                "disallow_broad_repo_scan": True,
                "recovery_phase": "inspect",
            },
            "dependencies": [],
        },
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
            "dependencies": ["inspect_sources"],
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
    ]

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

    # Start a new Attempt
    attempt = storage.attempts.create(task_id=task.task_id)
    constraints_updated = _ensure_procedure_item_source_hints(task)
    if task.state != TaskState.WORKING:
        task.state = TaskState.WORKING
    storage.commit()
    if constraints_updated or task.state == TaskState.WORKING:
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
        if task.type == TaskType.RESEARCH:
            await _run_research(task, storage, model_adapter, enqueue_fn, progress_fn=_record_progress, attempt_id=attempt.attempt_id)
        elif task.type == TaskType.DECOMP:
            await _run_decomposition(task, storage, model_adapter, enqueue_fn)
        elif task.type == TaskType.IMPL:
            await _run_implementation(task, storage, model_adapter, progress_fn=_record_progress, attempt_id=attempt.attempt_id)
        elif task.type == TaskType.JUDGE:
            await _run_judge(task, storage, model_adapter)
        else:
            raise NotImplementedError(f"Unsupported task type {task.type}")

        # If we got here without exception, it succeeded
        storage.attempts.update_outcome(attempt.attempt_id, AttemptOutcome.SUCCEEDED)
        
        # Populate operational artifacts for the attempt
        if hasattr(model_adapter, 'last_response') and model_adapter.last_response:
            attempt.artifacts["model"] = model_adapter.last_response.model
            attempt.artifacts["provider"] = model_adapter.last_response.provider
            attempt.artifacts["usage"] = model_adapter.last_response.usage or {}
        attempt.artifacts["duration_s"] = round(time.perf_counter() - started_at, 4)
        attempt.artifacts["step_history"] = list(step_history)
            
        task.state = TaskState.COMPLETE
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
    # Formatter would go here, simplified for brevity
    _emit_task_communication(
        storage,
        task,
        content=f"🔬 **Research Complete**\n{report.suggested_approach}",
        source_kind="task_research_complete",
    )
    task.state = TaskState.WORKING
    storage.commit()

async def _run_decomposition(task, storage, model_adapter, enqueue_fn):
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
    _emit_task_communication(
        storage,
        task,
        content=f"🛠️ **Implementation Staged**\nI've generated {len(candidate_ids)} candidates.",
        source_kind="task_implementation_staged",
    )
    task.state = TaskState.WORKING
    storage.commit()


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
