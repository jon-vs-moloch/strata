"""
@module orchestrator.research
@purpose Gather and synthesize contextual information prior to task execution or decomposition.
@owns metadata retrieval, documentation search, context synthesis
@does_not_own LLM API interactions directly (uses ModelAdapter), or task state mutations
@key_exports ResearchModule
@side_effects none
"""

from pathlib import Path
from typing import Dict, Any, Optional, List
import json
import hashlib
from datetime import datetime, timezone
from strata.knowledge.pages import KnowledgePageStore, get_knowledge_source_hints
from strata.core.lanes import infer_lane_from_session_id
from strata.feedback.signals import register_feedback_signal
from strata.orchestrator.step_outcomes import TerminalToolCallOutcome
from strata.orchestrator.tool_health import record_tool_execution, should_throttle_tool
from strata.schemas.core import ResearchReport
from strata.observability.writer import enqueue_attempt_observability_artifact, flush_observability_writes
from strata.storage.models import TaskModel
from strata.system_capabilities import bind_system_procedure, canonical_system_procedure_id


DEFAULT_REPO_ANCHORS = [
    "README.md",
    ".knowledge/specs/constitution.md",
    ".knowledge/specs/project_spec.md",
    "docs/spec/project-philosophy.md",
    "docs/spec/codemap.md",
    "strata/api",
    "strata/eval",
    "strata/orchestrator",
    "strata/knowledge",
    "strata/storage",
]

RESEARCH_ITERATION_POLICY_KEY = "research_iteration_policy"
DEFAULT_RESEARCH_ITERATION_POLICY = {
    "max_iterations": 6,
    "warm_history_count": 5,
    "research_reattempt_limit": 2,
    "default_reattempt_limit": 3,
    "recovery_shell_reattempt_limit": 1,
    "lineage_iteration_limit": 4,
    "repeated_failure_tooling_limit": 3,
    "resolution_analysis_retry_limit": 3,
}


def _sanitize_positive_int(raw_value: Any, default: int, *, minimum: int = 1) -> int:
    try:
        parsed = int(raw_value)
        if parsed < minimum:
            return default
        return parsed
    except Exception:
        return default


def load_research_iteration_policy(storage) -> Dict[str, int]:
    raw = storage.parameters.get_parameter(
        key=RESEARCH_ITERATION_POLICY_KEY,
        default_value=DEFAULT_RESEARCH_ITERATION_POLICY,
        description=(
            "Mutable policy for research/autopsy iteration handling, including loop budget, "
            "warm trace retention, and retry caps across recovery lineage."
        ),
    )
    policy = dict(DEFAULT_RESEARCH_ITERATION_POLICY)
    if isinstance(raw, dict):
        policy.update(raw)
    return {
        "max_iterations": _sanitize_positive_int(
            policy.get("max_iterations"),
            DEFAULT_RESEARCH_ITERATION_POLICY["max_iterations"],
        ),
        "warm_history_count": _sanitize_positive_int(
            policy.get("warm_history_count"),
            DEFAULT_RESEARCH_ITERATION_POLICY["warm_history_count"],
        ),
        "research_reattempt_limit": _sanitize_positive_int(
            policy.get("research_reattempt_limit"),
            DEFAULT_RESEARCH_ITERATION_POLICY["research_reattempt_limit"],
        ),
        "default_reattempt_limit": _sanitize_positive_int(
            policy.get("default_reattempt_limit"),
            DEFAULT_RESEARCH_ITERATION_POLICY["default_reattempt_limit"],
        ),
        "recovery_shell_reattempt_limit": _sanitize_positive_int(
            policy.get("recovery_shell_reattempt_limit"),
            DEFAULT_RESEARCH_ITERATION_POLICY["recovery_shell_reattempt_limit"],
        ),
        "lineage_iteration_limit": _sanitize_positive_int(
            policy.get("lineage_iteration_limit"),
            DEFAULT_RESEARCH_ITERATION_POLICY["lineage_iteration_limit"],
        ),
        "repeated_failure_tooling_limit": _sanitize_positive_int(
            policy.get("repeated_failure_tooling_limit"),
            DEFAULT_RESEARCH_ITERATION_POLICY["repeated_failure_tooling_limit"],
        ),
        "resolution_analysis_retry_limit": _sanitize_positive_int(
            policy.get("resolution_analysis_retry_limit"),
            DEFAULT_RESEARCH_ITERATION_POLICY["resolution_analysis_retry_limit"],
        ),
    }


class ResearchIterationLimitError(Exception):
    """
    @summary Structured failure for exhausted research loops.
    """

    def __init__(self, *, public_message: str, autopsy: Dict[str, Any]):
        super().__init__(public_message)
        self.public_message = public_message
        self.failure_kind = "iteration_budget_exhausted"
        self.autopsy = dict(autopsy or {})


class TaskBoundaryViolationError(Exception):
    """
    @summary Structured failure for work that cannot complete within a single variance-bearing invocation.
    """

    def __init__(self, *, public_message: str, autopsy: Dict[str, Any]):
        super().__init__(public_message)
        self.public_message = public_message
        self.failure_kind = "task_boundary_violation"
        self.autopsy = dict(autopsy or {})


def _clip_text(value: Any, limit: int = 500) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _compact_string_list(values: Any, *, limit: int = 6, char_limit: int = 220) -> List[str]:
    rows: List[str] = []
    for item in list(values or []):
        text = " ".join(str(item or "").split()).strip()
        if not text:
            continue
        if len(text) > char_limit:
            text = text[: char_limit - 3].rstrip() + "..."
        if text not in rows:
            rows.append(text)
        if len(rows) >= limit:
            break
    return rows


def _serialize_research_turn(message: Dict[str, Any]) -> Dict[str, Any]:
    payload = {
        "role": str(message.get("role") or ""),
        "content": _clip_text(message.get("content"), 1200) if message.get("content") else "",
    }
    tool_calls = []
    for call in message.get("tool_calls") or []:
        func = call.get("function") or {}
        tool_calls.append(
            {
                "id": str(call.get("id") or ""),
                "name": str(func.get("name") or ""),
                "arguments": _clip_text(func.get("arguments"), 600),
            }
        )
    if tool_calls:
        payload["tool_calls"] = tool_calls
    tool_call_id = str(message.get("tool_call_id") or "").strip()
    if tool_call_id:
        payload["tool_call_id"] = tool_call_id
    return payload


def _serialize_task_trajectory(task_trajectory: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(task_trajectory or {})
    return {
        "lineage_root": dict(payload.get("lineage_root") or {}),
        "current_task": dict(payload.get("current_task") or {}),
        "recent_path": list(payload.get("recent_path") or [])[-8:],
        "graph_nodes": list(payload.get("graph_nodes") or [])[:24],
        "handoff_summary": dict(payload.get("handoff_summary") or {}),
    }


def _task_trajectory_block(task_trajectory: Optional[Dict[str, Any]]) -> str:
    trajectory = _serialize_task_trajectory(dict(task_trajectory or {}))
    if not any(trajectory.values()):
        return ""
    root = dict(trajectory.get("lineage_root") or {})
    current = dict(trajectory.get("current_task") or {})
    recent_path = list(trajectory.get("recent_path") or [])
    graph_nodes = list(trajectory.get("graph_nodes") or [])
    handoff_summary = dict(trajectory.get("handoff_summary") or {})
    lines = [
        "[TASK TRAJECTORY]",
        f"- Lineage root: {root.get('title') or root.get('task_id') or 'unknown'}",
        f"- Current task: {current.get('title') or current.get('task_id') or 'unknown'}",
        f"- Current depth: {current.get('depth') if current else 'unknown'}",
    ]
    if recent_path:
        lines.append("- Path so far:")
        for node in recent_path:
            lines.append(
                f"  - d{node.get('depth', '?')} {node.get('type', '').lower()}: {node.get('title') or node.get('task_id')}"
            )
    if graph_nodes:
        lines.append("- Lineage DAG snapshot:")
        for node in graph_nodes[:16]:
            lines.append(
                f"  - d{node.get('depth', '?')} {node.get('type', '').lower()} [{node.get('state', '').lower()}] "
                f"{node.get('title') or node.get('task_id')} (children={node.get('child_count', 0)})"
            )
    if handoff_summary:
        tool_name = str(handoff_summary.get("tool_name") or "").strip()
        if tool_name:
            lines.append(f"- Prior handoff tool: {tool_name}")
        next_step_hint = str(handoff_summary.get("next_step_hint") or "").strip()
        if next_step_hint:
            lines.append(f"- Current next-step hint: {next_step_hint}")
    lines.append("- Use this trajectory to avoid repeating already-completed branch moves unless you can justify why repetition is necessary.")
    return "\n" + "\n".join(lines) + "\n"


def _normalize_task_graph_context(task_graph_context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    payload = dict(task_graph_context or {})
    if not payload:
        return {}
    if any(key in payload for key in ("root_title", "current_title", "lineage_path", "dag_nodes")):
        return {
            "root_task_id": str(payload.get("root_task_id") or "").strip(),
            "root_title": str(payload.get("root_title") or "").strip(),
            "current_task_id": str(payload.get("current_task_id") or "").strip(),
            "current_title": str(payload.get("current_title") or "").strip(),
            "lineage_path": list(payload.get("lineage_path") or []),
            "dag_nodes": list(payload.get("dag_nodes") or []),
            "truncated": bool(payload.get("truncated")),
        }

    trajectory = _serialize_task_trajectory(payload)
    root = dict(trajectory.get("lineage_root") or {})
    current = dict(trajectory.get("current_task") or {})
    recent_path = list(trajectory.get("recent_path") or [])
    graph_nodes = list(trajectory.get("graph_nodes") or [])
    return {
        "root_task_id": str(root.get("task_id") or "").strip(),
        "root_title": str(root.get("title") or "").strip(),
        "current_task_id": str(current.get("task_id") or "").strip(),
        "current_title": str(current.get("title") or "").strip(),
        "lineage_path": recent_path,
        "dag_nodes": graph_nodes,
        "truncated": False,
        "handoff_summary": dict(trajectory.get("handoff_summary") or {}),
    }


def _normalize_research_context_hints(context_hints: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    normalized = dict(context_hints or {})
    normalized["spec_paths"] = _compact_string_list(normalized.get("spec_paths") or [], limit=8, char_limit=180)
    normalized["preferred_start_paths"] = _compact_string_list(
        normalized.get("preferred_start_paths") or [],
        limit=6,
        char_limit=140,
    )
    if isinstance(normalized.get("source_hints"), dict):
        source_hints = dict(normalized.get("source_hints") or {})
        source_hints["preferred_paths"] = _compact_string_list(source_hints.get("preferred_paths") or [], limit=6, char_limit=140)
        source_hints["guidance"] = _clip_text(source_hints.get("guidance"), 320)
        normalized["source_hints"] = source_hints
    if isinstance(normalized.get("handoff_context"), dict):
        handoff = dict(normalized.get("handoff_context") or {})
        handoff["tool_result_preview"] = _clip_text(handoff.get("tool_result_preview"), 900)
        handoff["tool_result_full"] = _clip_text(handoff.get("tool_result_full"), 1800)
        if isinstance(handoff.get("parent_branch_state"), dict):
            parent_branch_state = dict(handoff.get("parent_branch_state") or {})
            parent_branch_state["findings"] = _compact_string_list(parent_branch_state.get("findings") or [], limit=5, char_limit=180)
            parent_branch_state["open_child_ids"] = _compact_string_list(parent_branch_state.get("open_child_ids") or [], limit=6, char_limit=48)
            handoff["parent_branch_state"] = parent_branch_state
        normalized["handoff_context"] = handoff
    normalized["reason"] = _clip_text(normalized.get("reason"), 600)
    normalized["evidence_hints"] = _compact_string_list(normalized.get("evidence_hints") or [], limit=6, char_limit=220)
    if isinstance(normalized.get("child_branch_state"), dict):
        branch_state = dict(normalized.get("child_branch_state") or {})
        branch_state["findings"] = _compact_string_list(branch_state.get("findings") or [], limit=5, char_limit=180)
        branch_state["open_child_ids"] = _compact_string_list(branch_state.get("open_child_ids") or [], limit=6, char_limit=48)
        child_rows = list((branch_state.get("children") or {}).values()) if isinstance(branch_state.get("children"), dict) else []
        branch_state["children_preview"] = [
            {
                "task_id": str(item.get("task_id") or ""),
                "title": _clip_text(item.get("title"), 120),
                "outcome": str(item.get("outcome") or ""),
                "failure_kind": str(item.get("failure_kind") or ""),
            }
            for item in child_rows[:4]
            if isinstance(item, dict)
        ]
        branch_state["child_count"] = len(child_rows)
        branch_state.pop("children", None)
        normalized["child_branch_state"] = branch_state
    return normalized


def _build_iteration_limit_autopsy(
    *,
    task_description: str,
    target_scope: str,
    task_context: Optional[Dict[str, Any]],
    policy: Dict[str, int],
    messages: List[Dict[str, Any]],
    wip_file: str,
) -> Dict[str, Any]:
    warm_count = max(1, int(policy.get("warm_history_count", 5) or 5))
    warm_history = [_serialize_research_turn(message) for message in messages[-warm_count:]]
    normalized_task_graph_context = _normalize_task_graph_context(task_graph_context)
    return {
        "failure_kind": "iteration_budget_exhausted",
        "task_description": str(task_description or ""),
        "target_scope": str(target_scope or "codebase"),
        "policy": dict(policy or {}),
        "iteration_count": len(messages),
        "warm_history_count": len(warm_history),
        "warm_history": warm_history,
        "archived_transcript": {
            "path": wip_file,
            "message_count": len(messages),
            "format": "markdown_transcript",
        },
        "task_context": dict(task_context or {}),
        "resume_hint": (
            "Consult the archived transcript for the cold path, but prefer the warm history "
            "for quick recovery or verifier review."
        ),
    }


def _build_task_boundary_autopsy(
    *,
    task_description: str,
    target_scope: str,
    task_context: Optional[Dict[str, Any]],
    response: Optional[Dict[str, Any]],
    tool_call: Optional[Dict[str, Any]] = None,
    tool_result_preview: str = "",
    tool_result_full: str = "",
    next_step_hint: str = "",
) -> Dict[str, Any]:
    serialized_response = _serialize_research_turn(
        {
            "role": "assistant",
            "content": (response or {}).get("content"),
            "tool_calls": (response or {}).get("tool_calls") or [],
        }
    )
    autopsy: Dict[str, Any] = {
        "failure_kind": "task_boundary_violation",
        "task_description": str(task_description or ""),
        "target_scope": str(target_scope or "codebase"),
        "task_context": dict(task_context or {}),
        "single_turn_contract": (
            "A task attempt must complete within one variance-bearing invocation plus bounded deterministic fallout."
        ),
        "model_response": serialized_response,
        "next_step_hint": str(next_step_hint or ""),
    }
    if tool_call:
        autopsy["tool_call"] = _serialize_research_turn(
            {"role": "assistant", "tool_calls": [tool_call], "content": None}
        ).get("tool_calls", [{}])[0]
    if tool_result_preview:
        autopsy["tool_result_preview"] = _clip_text(tool_result_preview, 1200)
    if tool_result_full:
        autopsy["tool_result_full"] = str(tool_result_full)
    return autopsy


def _should_return_raw_file(
    *,
    filepath: str,
    target_scope: str,
    task_description: str,
    spec_paths: Optional[list[str]] = None,
) -> bool:
    normalized_path = str(filepath or "").strip()
    if not normalized_path:
        return False
    normalized_spec_paths = {str(path or "").strip() for path in (spec_paths or []) if str(path or "").strip()}
    lower_path = normalized_path.lower()
    lower_scope = str(target_scope or "").strip().lower()
    lower_desc = str(task_description or "").strip().lower()
    if normalized_path in normalized_spec_paths:
        return True
    if lower_path.startswith(".knowledge/specs/"):
        return True
    return lower_scope == "codebase" and any(
        keyword in lower_desc for keyword in ("alignment", "spec", "repository", "repo", "implementation")
    )


def _best_hint_directory(preferred_start_paths: Optional[list[str]]) -> str:
    for raw_path in preferred_start_paths or []:
        normalized = str(raw_path or "").strip()
        if not normalized:
            continue
        path_obj = Path(normalized)
        candidate = str(path_obj.parent).strip() if path_obj.suffix else normalized
        candidate = candidate or "."
        if candidate != ".":
            return candidate
    return "."


def _clip_lines(value: Any, limit: int = 320) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _task_graph_snapshot(storage, *, task_context: Optional[Dict[str, Any]] = None, limit: int = 48) -> Dict[str, Any]:
    task_context = dict(task_context or {})
    task_id = str(task_context.get("task_id") or "").strip()
    session_id = str(task_context.get("session_id") or "").strip()
    if not task_id or not session_id or not storage or not hasattr(storage, "session"):
        return {}
    tasks = storage.session.query(TaskModel).filter(TaskModel.session_id == session_id).all()
    by_id = {str(getattr(row, "task_id", "") or ""): row for row in tasks}
    current = by_id.get(task_id)
    if current is None:
        return {}

    root = current
    seen = set()
    while getattr(root, "parent_task_id", None) and str(root.parent_task_id) not in seen:
        seen.add(str(root.parent_task_id))
        parent = by_id.get(str(root.parent_task_id))
        if parent is None:
            break
        root = parent

    children_by_parent: Dict[str, List[TaskModel]] = {}
    for row in tasks:
        parent_id = str(getattr(row, "parent_task_id", "") or "").strip()
        if not parent_id:
            continue
        children_by_parent.setdefault(parent_id, []).append(row)
    for rows in children_by_parent.values():
        rows.sort(key=lambda item: (int(getattr(item, "depth", 0) or 0), str(getattr(item, "created_at", "") or ""), str(getattr(item, "title", "") or "")))

    lineage: List[Dict[str, Any]] = []
    cursor = current
    seen = set()
    while cursor is not None and str(getattr(cursor, "task_id", "") or "") not in seen:
        seen.add(str(getattr(cursor, "task_id", "") or ""))
        lineage.append(
            {
                "task_id": str(getattr(cursor, "task_id", "") or ""),
                "title": str(getattr(cursor, "title", "") or ""),
                "type": str(getattr(getattr(cursor, "type", None), "value", getattr(cursor, "type", "")) or ""),
                "state": str(getattr(getattr(cursor, "state", None), "value", getattr(cursor, "state", "")) or ""),
                "depth": int(getattr(cursor, "depth", 0) or 0),
            }
        )
        parent_id = getattr(cursor, "parent_task_id", None)
        cursor = by_id.get(str(parent_id)) if parent_id else None
    lineage.reverse()

    subtree_nodes: List[Dict[str, Any]] = []
    queue: List[TaskModel] = [root]
    seen = set()
    while queue and len(subtree_nodes) < max(1, limit):
        node = queue.pop(0)
        node_id = str(getattr(node, "task_id", "") or "")
        if not node_id or node_id in seen:
            continue
        seen.add(node_id)
        children = list(children_by_parent.get(node_id, []) or [])
        subtree_nodes.append(
            {
                "task_id": node_id,
                "parent_task_id": str(getattr(node, "parent_task_id", "") or "") or None,
                "title": str(getattr(node, "title", "") or ""),
                "summary": _clip_lines(getattr(node, "description", ""), 160),
                "type": str(getattr(getattr(node, "type", None), "value", getattr(node, "type", "")) or ""),
                "state": str(getattr(getattr(node, "state", None), "value", getattr(node, "state", "")) or ""),
                "depth": int(getattr(node, "depth", 0) or 0),
                "child_count": len(children),
            }
        )
        queue.extend(children)

    return {
        "root_task_id": str(getattr(root, "task_id", "") or ""),
        "root_title": str(getattr(root, "title", "") or ""),
        "current_task_id": str(getattr(current, "task_id", "") or ""),
        "current_title": str(getattr(current, "title", "") or ""),
        "lineage_path": lineage,
        "dag_nodes": subtree_nodes,
        "truncated": len(subtree_nodes) >= max(1, limit),
    }


def _render_task_graph_prompt_block(task_graph_context: Optional[Dict[str, Any]]) -> str:
    context = dict(task_graph_context or {})
    if not context:
        return ""
    lineage_lines = []
    for node in list(context.get("lineage_path") or []):
        lineage_lines.append(
            f"- depth {int(node.get('depth') or 0)} | {str(node.get('type') or '').upper()} | "
            f"{str(node.get('state') or '').lower()} | {str(node.get('title') or '').strip()}"
        )
    dag_lines = []
    for node in list(context.get("dag_nodes") or [])[:18]:
        dag_lines.append(
            f"- [{str(node.get('task_id') or '')[:8]}] depth {int(node.get('depth') or 0)} | "
            f"{str(node.get('type') or '').upper()} | {str(node.get('state') or '').lower()} | "
            f"{str(node.get('title') or '').strip()} :: {str(node.get('summary') or '').strip()}"
        )
    suffix = "\n- DAG snapshot truncated for prompt budget." if bool(context.get("truncated")) else ""
    return (
        "\n[TASK GRAPH CONTEXT]\n"
        f"Root task: {str(context.get('root_title') or '').strip()} ({str(context.get('root_task_id') or '')[:8]})\n"
        f"Current task: {str(context.get('current_title') or '').strip()} ({str(context.get('current_task_id') or '')[:8]})\n"
        "Current lineage path:\n"
        + ("\n".join(lineage_lines) if lineage_lines else "- None available")
        + "\nRelevant DAG nodes from the current root task:\n"
        + ("\n".join(dag_lines) if dag_lines else "- None available")
        + suffix
        + "\nUse this graph context to avoid repeating earlier branch work and to decide whether this step should finalize, communicate, store knowledge, or perform one more bounded lookup.\n"
    )


def _render_research_judgment_block(
    *,
    handoff_context: Optional[Dict[str, Any]] = None,
    task_graph_context: Optional[Dict[str, Any]] = None,
) -> str:
    handoff = dict(handoff_context or {})
    graph = dict(task_graph_context or {})
    current = dict(graph.get("current_task") or {})
    depth = int(current.get("depth") or 0)
    lines = [
        "[RESEARCH JUDGMENT]",
        "- Use common sense about what kind of step this is.",
        "- Search when you are still missing a specific fact or source needed to complete the task.",
        "- Synthesize when you already have enough evidence from the current branch to explain the answer clearly.",
        "- Report when the current evidence is already sufficient for the user, the parent task, or durable knowledge storage.",
        "- Treat another lookup as the exception, not the default. Only do one more lookup if the current evidence is genuinely insufficient.",
        "- If the current branch has already gathered enough to answer at a useful level, prefer `finalize_research` instead of extending the branch.",
        "- If the current branch has produced a user-relevant conclusion, prefer reporting or communicating that conclusion rather than silently continuing internal work.",
        "- If the current branch has produced a durable reusable finding, prefer writing/queuing knowledge maintenance instead of re-reading adjacent files without a clear gap.",
    ]
    if handoff:
        lines.append("- Start by asking: did the inherited tool result already answer the question or narrow it enough that I should synthesize rather than search again?")
    if depth >= 4:
        lines.append("- This branch is already fairly deep. Be biased toward synthesis, reporting, or knowledge write-back unless a very specific missing fact blocks completion.")
    if depth >= 6:
        lines.append("- This branch is deep enough that another generic lookup is a smell. Only continue searching if you can name the exact missing fact and why the current evidence cannot support a useful answer.")
    if depth >= 8:
        lines.append("- At this depth, strongly prefer returning a result, communicating a conclusion, or explicitly escalating uncertainty rather than continuing to forage.")
    return "\n" + "\n".join(lines) + "\n"


def _build_prompt_snapshot_payload(
    *,
    prompt_kind: str,
    prompt_version: str,
    system_prompt: str,
    user_message: str,
    tools: List[Dict[str, Any]],
    task_description: str,
    target_scope: str,
    handoff_context: Dict[str, Any],
    task_graph_context: Dict[str, Any],
    spec_paths: List[str],
    preferred_start_paths: List[str],
    focused_guidance: str,
    repo_snapshot: str,
) -> Dict[str, Any]:
    lineage_basis = json.dumps(
        {
            "prompt_kind": prompt_kind,
            "prompt_version": prompt_version,
            "target_scope": target_scope,
            "tool_names": [str(((item or {}).get("function") or {}).get("name") or "") for item in tools],
            "spec_paths": list(spec_paths or []),
        },
        sort_keys=True,
    )
    prompt_lineage_id = hashlib.sha256(lineage_basis.encode("utf-8")).hexdigest()[:16]
    return {
        "prompt_kind": prompt_kind,
        "prompt_version": prompt_version,
        "prompt_lineage_id": prompt_lineage_id,
        "prompt_template_ref": f"{prompt_kind}.{prompt_version}",
        "system_prompt_sha256": hashlib.sha256(system_prompt.encode("utf-8")).hexdigest(),
        "system_prompt_preview": _clip_lines(system_prompt, 1600),
        "user_message": _clip_lines(user_message, 800),
        "static_section_refs": [
            "critical_tool_use",
            "library_structure",
            "knowledge_maintenance",
            "local_context",
        ],
        "unique_context": {
            "task_description": _clip_lines(task_description, 800),
            "target_scope": target_scope,
            "spec_paths": list(spec_paths or []),
            "preferred_start_paths": list(preferred_start_paths or []),
            "focused_guidance": _clip_lines(focused_guidance, 600),
            "handoff_context": dict(handoff_context or {}),
            "task_graph_context": normalized_task_graph_context,
            "repo_snapshot_preview": _clip_lines(repo_snapshot, 1200),
        },
    }


def _build_research_system_prompt(
    target_scope: str,
    task_description: str,
    repo_snapshot: str = "",
    spec_paths: Optional[list[str]] = None,
    preferred_start_paths: Optional[list[str]] = None,
    focused_guidance: str = "",
    disallow_broad_repo_scan: bool = False,
    handoff_context: Optional[Dict[str, Any]] = None,
    task_graph_context: Optional[Dict[str, Any]] = None,
) -> str:
    spec_lines = "\n".join(f"- {path}" for path in (spec_paths or [])) or "- None provided"
    repo_hint_block = f"\nObserved repository snapshot:\n{repo_snapshot}\n" if repo_snapshot else ""
    cleaned_preferred_paths = [str(path).strip() for path in (preferred_start_paths or []) if str(path).strip()]
    preferred_lines = "\n".join(f"- {path}" for path in cleaned_preferred_paths) or "- None provided"
    focused_hint_block = (
        f"\nFocused starting points for this task:\n{preferred_lines}\nGuidance: {focused_guidance}\n"
        if (preferred_start_paths or focused_guidance)
        else ""
    )
    handoff_context = dict(handoff_context or {})
    handoff_lines = []
    is_tool_result_continuation = bool(handoff_context) and str(handoff_context.get("source_module") or "").strip() in {"research", "implementation"}
    if handoff_context:
        tool_name = str(((handoff_context.get("tool_call") or {}).get("name")) or "").strip()
        tool_args = str(((handoff_context.get("tool_call") or {}).get("arguments")) or "").strip()
        if tool_name:
            handoff_lines.append(f"- Prior tool call already executed: {tool_name} {tool_args}".strip())
        tool_result_full = str(handoff_context.get("tool_result_full") or "").strip()
        if tool_result_full:
            handoff_lines.append(f"- Prior full tool result:\n{tool_result_full}")
        tool_result_preview = str(handoff_context.get("tool_result_preview") or "").strip()
        if tool_result_preview and not tool_result_full:
            handoff_lines.append(f"- Prior tool result preview: {tool_result_preview}")
        next_step_hint = str(handoff_context.get("next_step_hint") or "").strip()
        if next_step_hint:
            handoff_lines.append(f"- Prior next-step hint: {next_step_hint}")
        avoid_repeat = dict(handoff_context.get("avoid_repeating_first_tool") or {})
        if avoid_repeat:
            handoff_lines.append(
                f"- Do not repeat {avoid_repeat.get('name')} as your first move unless you can explain why the inherited evidence is insufficient."
            )
    handoff_block = (
        "\nPrior handoff evidence from the parent step:\n" + "\n".join(handoff_lines) + "\n"
        if handoff_lines
        else ""
    )
    codebase_nudge = ""
    lower_desc = (task_description or "").lower()
    if target_scope.lower() == "codebase" or any(
        keyword in lower_desc
        for keyword in ["codebase", "repo", "repository", "alignment", "spec", "implementation"]
    ):
        anchors = "\n".join(f"- {path}" for path in DEFAULT_REPO_ANCHORS)
        codebase_nudge = f"""
[CODEBASE-FIRST BEHAVIOR]
- You DO have access to the local repository through `list_directory` and `read_file`.
- For codebase, alignment, or spec-gap tasks, start by inspecting the local repo before concluding anything is missing.
- Use `list_directory` on "." or a likely subtree, then `read_file` on concrete anchors such as:
{anchors}
- Do not say you need "access to the codebase" when the task already provides repo paths or a snapshot. Inspect the files instead.
- If the snapshot or anchor files seem incomplete, say what you inspected and what is still missing.
"""
    if disallow_broad_repo_scan and preferred_lines != "- None provided":
        codebase_nudge += f"""
- This task is intentionally narrow. Do NOT start with `list_directory(\".\")` or a broad repo-root survey.
- Begin with one of these focused starting points instead:
{preferred_lines}
- Only broaden beyond those hints if you can explain why the hinted surfaces were insufficient.
"""
    continuation_nudge = ""
    if is_tool_result_continuation:
        continuation_nudge = """
[TOOL-RESULT CONTINUATION]
- This step exists because a prior explicit step already executed one tool call and handed you the result.
- The inherited tool result is present in this prompt right now. Do not claim you lack access to it unless it is actually absent from the prompt text.
- Your first job is to interpret the inherited tool result, not to repeat the same tool reflexively.
- You must choose exactly one of these outcomes:
  1. `finalize_research` immediately if the inherited result is already sufficient.
  2. Make exactly one new structured tool call if one additional bounded lookup is genuinely required.
        - Do NOT emit multiple tool calls in this step.
        - Do NOT perform a broad repo scan after inheriting a focused tool result unless you can justify why the inherited result was insufficient.
- Before making another tool call, name the exact missing fact and why the inherited result does not already answer it.
- If the inherited result is a spec, page, or code excerpt directly relevant to the task, the default action should be synthesis/finalization, not a fresh repo scan.
"""
    normalized_task_graph_context = _normalize_task_graph_context(task_graph_context)
    task_graph_block = _render_task_graph_prompt_block(normalized_task_graph_context)
    research_judgment_block = _render_research_judgment_block(
        handoff_context=handoff_context,
        task_graph_context=normalized_task_graph_context,
    )

    return f"""You are an Expert Research Agent building a persistent knowledge library.
Your primary goal is to decompose the user's research task and iteratively gather data.

[CRITICAL - TOOL USE]
To gather information or save data, you MUST use the structured tool-calling format.
If you simply say "I will call a tool" in plain text without a structured tool call, the system will reject your response.
Your current tools: list_directory, read_file, search_web, write_library_file, list_knowledge_pages, read_knowledge_page, inspect_knowledge_maintenance, propose_knowledge_merge, propose_knowledge_correction, queue_knowledge_refresh, submit_feedback_signal.

[LIBRARY STRUCTURE]
- As you find complete atomic findings, you MUST use `write_library_file` to save them locally into the `.knowledge/` memory store.
- Enforce a clean library structure: small, atomic files. ALWAYS include YAML metadata (title, subjects, tags) at the top.
- Use [[Wikilinks]] to cross-reference other documents you create.

[KNOWLEDGE MAINTENANCE]
- Before creating a brand new research note or proposing a new durable page, inspect the synthesized knowledge pages when relevant.
- If you discover overlap, contradictions, or stale claims, use the maintenance tools to propose merge/correction/refresh work rather than silently duplicating the wiki.
- Prefer durable knowledge pages for retrieval, and raw `.knowledge/` notes for draft or intermediate findings.

[LOCAL CONTEXT]
- Repository paths are relative to the repo root.
- Canonical spec paths for this task:
{spec_lines}{repo_hint_block}{focused_hint_block}{handoff_block}{task_graph_block}{research_judgment_block}{codebase_nudge}{continuation_nudge}

When you have collected enough comprehensive information across all sources and saved your atomic notes, call 'finalize_research' with a high-level synthesized report to end the research phase.
Focus area: {target_scope.upper()} scope."""

class ResearchModule:
    """
    @summary Executes the research phase for a given task, querying repo metadata.
    @inputs model: ModelAdapter, storage: StorageManager
    @outputs ResearchReport containing synthesized context
    @side_effects requests completions from the LLM adapter
    @depends models.adapter, schemas.core.ResearchReport
    @invariants always returns a ResearchReport regardless of findings
    """
    def __init__(self, model_adapter, storage_manager, enqueue_task=None):
        """
        @summary Initialize the ResearchModule.
        @inputs model_adapter instance, storage_manager instance
        @outputs none
        """
        self.model = model_adapter
        self.storage = storage_manager
        self.enqueue_task = enqueue_task

    async def conduct_research(
        self,
        task_description: str,
        repo_path: Optional[str] = None,
        target_scope: str = "codebase",
        context_hints: Optional[Dict[str, Any]] = None,
        task_context: Optional[Dict[str, Any]] = None,
        progress_fn=None,
        attempt_id: Optional[str] = None,
    ) -> ResearchReport | TerminalToolCallOutcome:
        """
        @summary Autonomous agent loop for research. Decomposes the task, queries the web/codebase iteratively, and synthesizes.
        """
        import os
        import json
        import httpx
        import re
        
        print(f"Starting autonomous research loop for: {task_description[:50]}...")
        root = repo_path or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        context_hints = _normalize_research_context_hints(context_hints or {})
        repo_snapshot = str(context_hints.get("repo_snapshot") or "").strip()
        spec_paths = context_hints.get("spec_paths") or []
        source_hints = dict(context_hints.get("source_hints") or {})
        if not source_hints and str(context_hints.get("knowledge_operation") or "").strip().lower() == "knowledge_refresh":
            source_hints = get_knowledge_source_hints(str(context_hints.get("knowledge_slug") or ""))
        preferred_start_paths = list(
            context_hints.get("preferred_start_paths")
            or source_hints.get("preferred_paths")
            or []
        )
        focused_guidance = str(
            context_hints.get("focused_guidance")
            or source_hints.get("guidance")
            or ""
        ).strip()
        disallow_broad_repo_scan = bool(context_hints.get("disallow_broad_repo_scan"))
        handoff_context = dict(context_hints.get("handoff_context") or {})
        raw_task_graph_context = dict((task_context or {}).get("task_trajectory") or {}) or _task_graph_snapshot(self.storage, task_context=task_context)
        task_graph_context = _normalize_task_graph_context(raw_task_graph_context)
        research_lane = infer_lane_from_session_id((task_context or {}).get("session_id"))
        research_task_type = str((task_context or {}).get("type") or "").strip() or "RESEARCH"
        research_task_id = str((task_context or {}).get("task_id") or "").strip() or None

        def _record_tool_event(
            tool_name: str,
            *,
            outcome: str,
            failure_kind: Optional[str] = None,
            details: Optional[Dict[str, Any]] = None,
        ) -> None:
            record_tool_execution(
                self.storage,
                tool_name=tool_name,
                outcome=outcome,
                lane=research_lane,
                task_type=research_task_type,
                task_id=research_task_id,
                session_id=str((task_context or {}).get("session_id") or "").strip() or None,
                source="research_module",
                failure_kind=failure_kind,
                details=details or {},
            )
            self.storage.commit()
        
        RESEARCH_TOOLS = [
            {
                "type": "function",
                "function": {
                    "name": "list_directory",
                    "description": "List files and folders in a local repository directory. Use this first when you need to inspect what exists.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Relative path to the directory. Use '.' for repo root."
                            }
                        },
                        "required": ["path"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "search_web",
                    "description": "Search DuckDuckGo for facts, documentation, or tutorials.",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string", "description": "The search query"}},
                        "required": ["query"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read the contents of a local codebase file.",
                    "parameters": {
                        "type": "object",
                        "properties": {"filepath": {"type": "string", "description": "Relative path to the file"}},
                        "required": ["filepath"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "finalize_research",
                    "description": "Call this ONLY when you have fully answered the research goal.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "context_gathered": {"type": "string", "description": "A long paragraph detailing findings."},
                            "key_constraints_discovered": {"type": "array", "items": {"type": "string"}},
                            "suggested_approach": {"type": "string"},
                            "reference_urls": {"type": "array", "items": {"type": "string"}}
                        },
                        "required": ["context_gathered", "key_constraints_discovered", "suggested_approach", "reference_urls"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "write_library_file",
                    "description": "Write a small, atomic markdown file to the `.knowledge/` library. Use this to continuously save finalized, bite-sized components of your research to disk with searchable metadata (title, subjects, tags). Use [[Wikilinks]] to cross-reference other atomic files you create.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filename": {"type": "string", "description": "The exact name of the file to save (e.g. pattern_routing.md)"},
                            "content": {"type": "string", "description": "The complete markdown content to write, including YAML frontmatter tags."}
                        },
                        "required": ["filename", "content"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "submit_feedback_signal",
                    "description": "Register a lightweight feedback, surprise, correction, or attention signal so the system can prioritize it.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "source_type": {"type": "string", "description": "What produced or received the signal."},
                            "source_id": {"type": "string", "description": "Stable identifier for the source item."},
                            "signal_kind": {"type": "string", "description": "Kind of signal being registered."},
                            "signal_value": {"type": "string", "description": "Short label or payload for the signal."},
                            "source_preview": {"type": "string", "description": "Short excerpt or summary of the source item."},
                            "expected_outcome": {"type": "string", "description": "Optional expectation that was violated."},
                            "observed_outcome": {"type": "string", "description": "Optional observed outcome."},
                            "note": {"type": "string", "description": "Optional short explanation of why the signal matters."}
                        },
                        "required": ["source_type", "source_id", "signal_kind", "signal_value"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "list_knowledge_pages",
                    "description": "List synthesized knowledge pages by metadata so you can reuse or inspect existing wiki pages before drafting new notes.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Optional query to filter pages by title, summary, alias, or tag."},
                            "tag": {"type": "string", "description": "Optional tag filter."},
                            "domain": {"type": "string", "description": "Optional domain filter."},
                            "limit": {"type": "integer", "description": "Maximum number of page metadata results to return."}
                        }
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "read_knowledge_page",
                    "description": "Read a synthesized knowledge page or a specific section when you need durable wiki context.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "slug": {"type": "string", "description": "Knowledge page slug."},
                            "heading": {"type": "string", "description": "Optional heading to fetch a specific section only."}
                        },
                        "required": ["slug"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "inspect_knowledge_maintenance",
                    "description": "Inspect the knowledge maintenance backlog, including duplicate candidates and stale pages.",
                    "parameters": {
                        "type": "object",
                        "properties": {}
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "propose_knowledge_merge",
                    "description": "Queue a merge/canonicalization proposal when two knowledge pages overlap or should be consolidated.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "canonical_slug": {"type": "string", "description": "The page that should remain canonical."},
                            "duplicate_slug": {"type": "string", "description": "The page that may be merged into the canonical page."},
                            "reason": {"type": "string", "description": "Why the pages should be merged."},
                            "target_scope": {"type": "string", "description": "Whether to inspect the codebase or the public web.", "enum": ["codebase", "web"]},
                            "evidence_hints": {"type": "array", "items": {"type": "string"}, "description": "Optional evidence supporting the merge."}
                        },
                        "required": ["canonical_slug", "duplicate_slug", "reason"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "propose_knowledge_correction",
                    "description": "Queue a correction or contradiction-resolution proposal for a knowledge page.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "slug": {"type": "string", "description": "The page that needs correction."},
                            "reason": {"type": "string", "description": "What looks wrong, stale, or contradictory."},
                            "target_scope": {"type": "string", "description": "Whether to inspect the codebase or the public web.", "enum": ["codebase", "web"]},
                            "evidence_hints": {"type": "array", "items": {"type": "string"}, "description": "Optional evidence supporting the correction."},
                            "related_slugs": {"type": "array", "items": {"type": "string"}, "description": "Optional other pages involved in the conflict."}
                        },
                        "required": ["slug", "reason"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "queue_knowledge_refresh",
                    "description": "Queue a freshness refresh for a knowledge page when its source evidence may have changed.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "slug": {"type": "string", "description": "The page that should be refreshed."},
                            "reason": {"type": "string", "description": "Why the page may be stale."},
                            "target_scope": {"type": "string", "description": "Whether to inspect the codebase or the public web.", "enum": ["codebase", "web"]},
                            "evidence_hints": {"type": "array", "items": {"type": "string"}, "description": "Optional evidence supporting the refresh."}
                        },
                        "required": ["slug", "reason"]
                    }
                }
            }
        ]

        system_prompt = _build_research_system_prompt(
            target_scope=target_scope,
            task_description=task_description,
            repo_snapshot=repo_snapshot,
            spec_paths=spec_paths,
            preferred_start_paths=preferred_start_paths,
            focused_guidance=focused_guidance,
            disallow_broad_repo_scan=disallow_broad_repo_scan,
            handoff_context=handoff_context,
            task_graph_context=task_graph_context,
        )
        if handoff_context and str(handoff_context.get("source_module") or "").strip() in {"research", "implementation"}:
            user_message = (
                f"RESEARCH CONTINUATION TASK: {task_description}\n"
                "You already have an inherited tool result in context. "
                "Your first responsibility is to decide whether that inherited result is sufficient to answer the question. "
                "If it is sufficient, call finalize_research now. "
                "If it is not sufficient, make exactly one new bounded tool call and do not start fresh."
            )
        else:
            user_message = f"RESEARCH TASK: {task_description}\\nPlease begin your research. Call tools to gather data."
        messages = [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_message,
            }
        ]

        # Use the centralized dynamic parameter telemetry module
        policy = load_research_iteration_policy(self.storage)

        knowledge_pages = KnowledgePageStore(self.storage)
        if progress_fn:
            progress_fn(
                step="model_turn",
                label="Running research turn",
                detail=f"{target_scope} single-shot research",
                attempt_id=attempt_id,
                progress_label="model thinking",
            )
        print(f"Starting single-shot research attempt for: {task_description[:50]}...")
        
        # Phase 3.3: Attempt Context Snapshot
        if attempt_id:
            prompt_snapshot = _build_prompt_snapshot_payload(
                prompt_kind="research_prompt",
                prompt_version="v2",
                system_prompt=system_prompt,
                user_message=user_message,
                tools=RESEARCH_TOOLS,
                task_description=task_description,
                target_scope=target_scope,
                handoff_context=handoff_context,
                task_graph_context=task_graph_context,
                spec_paths=spec_paths,
                preferred_start_paths=preferred_start_paths,
                focused_guidance=focused_guidance,
                repo_snapshot=repo_snapshot,
            )
            should_flush = enqueue_attempt_observability_artifact({
                "task_id": research_task_id,
                "attempt_id": attempt_id,
                "session_id": (task_context or {}).get("session_id"),
                "artifact_kind": "context_snapshot",
                "payload": {
                    **prompt_snapshot,
                    "tool_names": [str(((item or {}).get("function") or {}).get("name") or "") for item in RESEARCH_TOOLS],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            })
            if should_flush:
                flush_observability_writes()

        response = await self.model.chat(messages, tools=RESEARCH_TOOLS)
        if attempt_id:
            should_flush = enqueue_attempt_observability_artifact(
                {
                    "task_id": research_task_id,
                    "attempt_id": attempt_id,
                    "session_id": (task_context or {}).get("session_id"),
                    "artifact_kind": "model_turn_snapshot",
                    "payload": {
                        "prompt_lineage_id": prompt_snapshot.get("prompt_lineage_id"),
                        "prompt_template_ref": prompt_snapshot.get("prompt_template_ref"),
                        "response_status": str(response.get("status") or "").strip(),
                        "message": str(response.get("message") or ""),
                        "model": str(response.get("model") or ""),
                        "provider": str(response.get("provider") or ""),
                        "usage": dict(response.get("usage") or {}),
                        "content_preview": _clip_lines(response.get("content") or "", 1600),
                        "tool_calls": list(response.get("tool_calls") or []),
                        "error": dict(response.get("error") or {}),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                }
            )
            if should_flush:
                flush_observability_writes()

        if response.get("status") == "error":
            err_msg = str(response.get("message") or response.get("content") or "Unknown model adapter error.").strip()
            error_details = dict(response.get("error") or {})
            if error_details:
                raise Exception(
                    f"Research loop aborted: Model adapter returned error: {err_msg}. "
                    f"Context: {json.dumps(error_details, sort_keys=True)[:1600]}"
                )
            raise Exception(f"Research loop aborted: Model adapter returned error: {err_msg}")

        tool_calls = response.get("tool_calls") or []
        if len(tool_calls) > 1:
            raise TaskBoundaryViolationError(
                public_message=(
                    "Research task attempted multiple tool calls in a single variance-bearing invocation. "
                    "Decompose the task into smaller oneshottable units."
                ),
                autopsy=_build_task_boundary_autopsy(
                    task_description=task_description,
                    target_scope=target_scope,
                    task_context=task_context,
                    response=response,
                    next_step_hint="Split the work so each task needs at most one research tool interaction before completion.",
                ),
            )

        final_report_data = None
        if tool_calls:
            call = tool_calls[0]
            func_name = call.get("function", {}).get("name")
            try:
                args = json.loads(call.get("function", {}).get("arguments", "{}"))
            except Exception:
                args = {}
            throttle = should_throttle_tool(
                self.storage,
                tool_name=func_name,
                lane=research_lane,
                task_type=research_task_type,
            )
            if throttle.get("throttle") and func_name != "finalize_research":
                tool_result = (
                    f"Tool '{func_name}' is currently {throttle.get('status')} for this exact scope and has been circuit-broken. "
                    f"Reason: {throttle.get('reason')}. Choose another tool, replan, or trigger tooling repair instead of retrying it unchanged."
                )
                _record_tool_event(
                    func_name,
                    outcome="blocked",
                    failure_kind="circuit_breaker",
                    details={"health": throttle.get("health") or {}},
                )
                raise TaskBoundaryViolationError(
                    public_message=(
                        "Research task selected a circuit-broken tool and would require another variance-bearing turn to continue. "
                        "Decompose or replan the task instead of looping within the same attempt."
                    ),
                    autopsy=_build_task_boundary_autopsy(
                        task_description=task_description,
                        target_scope=target_scope,
                        task_context=task_context,
                        response=response,
                        tool_call=call,
                        tool_result_preview=tool_result,
                        tool_result_full=tool_result if len(str(tool_result or "")) <= 12000 else "",
                        next_step_hint="Choose a different tool or split the task so tool repair and research are separate tasks.",
                    ),
                )

            if func_name == "finalize_research":
                final_report_data = args
            else:
                if progress_fn:
                    progress_fn(
                        step="tool_execution",
                        label="Executing research tool",
                        detail=str(func_name or "tool"),
                        attempt_id=attempt_id,
                        progress_label=f"tool {func_name}",
                    )
                tool_result = ""
                tool_outcome = "success"
                failure_kind = None
                if func_name == "list_directory":
                    rel_path = args.get("path", ".") or "."
                    if disallow_broad_repo_scan and str(rel_path).strip() == "." and preferred_start_paths:
                        rel_path = _best_hint_directory(preferred_start_paths)
                    print(f"  -> Research Agent listing directory: {rel_path}")
                    try:
                        directory = (Path(root) / rel_path).resolve()
                        repo_root = Path(root).resolve()
                        directory.relative_to(repo_root)
                        if not directory.exists():
                            tool_result = f"Directory does not exist: {rel_path}"
                        elif not directory.is_dir():
                            tool_result = f"Path is not a directory: {rel_path}"
                        else:
                            children = sorted(
                                child.name + ("/" if child.is_dir() else "")
                                for child in directory.iterdir()
                                if not child.name.startswith(".")
                            )
                            preview = children[:80]
                            suffix = "\n... truncated ..." if len(children) > 80 else ""
                            tool_result = "\n".join(preview) + suffix if preview else "(empty directory)"
                    except Exception as e:
                        tool_result = f"Directory listing failed: {e}"
                        tool_outcome = "broken"
                        failure_kind = "directory_listing_failed"

                elif func_name == "search_web":
                    query = args.get("query", "python")
                    print(f"  -> Research Agent searching web for: {query}")
                    try:
                        async with httpx.AsyncClient() as client:
                            resp = await client.get(
                                "https://html.duckduckgo.com/html/",
                                params={"q": query},
                                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/91.0.4472.124"},
                                timeout=8.0,
                            )
                            resp.raise_for_status()
                            snippets = re.findall(r'<a class="result__snippet[^>]*>(.*?)</a>', resp.text, re.IGNORECASE | re.DOTALL)
                            results = [re.sub('<[^<]+>', '', s).strip() for s in snippets[:4]]
                            tool_result = "\\n".join(f"- {r}" for r in results) if results else "No snippets found."
                    except Exception as e:
                        tool_result = f"Search failed: {e}"
                        tool_outcome = "broken"
                        failure_kind = "search_failed"

                elif func_name == "read_file":
                    filepath = args.get("filepath", "")
                    print(f"  -> Research Agent reading file: {filepath}")
                    full_path = os.path.join(root, filepath)

                    async def fetch_raw_content():
                        with open(full_path, "r", encoding="utf-8") as f:
                            return f.read()

                    try:
                        if _should_return_raw_file(
                            filepath=filepath,
                            target_scope=target_scope,
                            task_description=task_description,
                            spec_paths=spec_paths,
                        ):
                            raw_content = await fetch_raw_content()
                            tool_result = raw_content[:12000]
                            if len(raw_content) > 12000:
                                tool_result += "\n... truncated ..."
                        else:
                            tool_result = await self.storage.get_resource_summary(
                                resource_id=filepath,
                                raw_content_callback=fetch_raw_content,
                                model_adapter=self.model,
                            )
                    except Exception as e:
                        tool_result = f"File read failed: {e}"
                        tool_outcome = "broken"
                        failure_kind = "file_read_failed"

                elif func_name == "write_library_file":
                    fname = args.get("filename", "untitled.md")
                    content = args.get("content", "")
                    kb_dir = os.path.join(root, ".knowledge")
                    os.makedirs(kb_dir, exist_ok=True)
                    try:
                        with open(os.path.join(kb_dir, fname), "w", encoding="utf-8") as f:
                            f.write(content)
                        tool_result = f"Successfully wrote {fname} to .knowledge library."
                        print(f"  -> Research Agent saved atomic note: {fname}")
                    except Exception as e:
                        tool_result = f"Failed to write file: {e}"
                        tool_outcome = "broken"
                        failure_kind = "library_write_failed"

                elif func_name == "list_knowledge_pages":
                    pages = knowledge_pages.list_pages(
                        query=args.get("query"),
                        tag=args.get("tag"),
                        domain=args.get("domain"),
                        audience="operator",
                        limit=int(args.get("limit") or 8),
                    )
                    if not pages:
                        tool_result = "No synthesized knowledge pages matched that query."
                    else:
                        tool_result = "Knowledge Page Metadata:\n" + "\n".join(
                            f"- {page.get('slug')}: {page.get('title')} | summary={page.get('summary')} | "
                            f"domain={page.get('domain')} | maintenance={page.get('maintenance', {}).get('freshness_status', 'unknown')} | "
                            f"last_updated={page.get('last_updated')}"
                            for page in pages
                        )

                elif func_name == "read_knowledge_page":
                    slug = str(args.get("slug") or "")
                    heading = str(args.get("heading") or "").strip()
                    if heading:
                        section = knowledge_pages.get_page_section(slug, heading, audience="operator")
                        tool_result = section.get("content") or f"No section '{heading}' found in knowledge page '{slug}'."
                    else:
                        page = knowledge_pages.get_page(slug, audience="operator")
                        tool_result = page.get("body") or f"No synthesized knowledge page found for '{slug}'."

                elif func_name == "inspect_knowledge_maintenance":
                    report = knowledge_pages.get_maintenance_report()
                    tool_result = json.dumps(report, indent=2) if report else "No knowledge maintenance report is available yet."

                elif func_name == "submit_feedback_signal":
                    signal = register_feedback_signal(
                        self.storage,
                        source_type=str(args.get("source_type") or "system"),
                        source_id=str(args.get("source_id") or task_description[:32]),
                        signal_kind=str(args.get("signal_kind") or "highlight"),
                        signal_value=str(args.get("signal_value") or ""),
                        source_actor="researcher",
                        session_id="",
                        source_preview=str(args.get("source_preview") or task_description),
                        note=str(args.get("note") or ""),
                        expected_outcome=str(args.get("expected_outcome") or ""),
                        observed_outcome=str(args.get("observed_outcome") or ""),
                        metadata={"module": "research"},
                    )
                    self.storage.commit()
                    tool_result = json.dumps(signal, indent=2)

                elif func_name == "propose_knowledge_merge":
                    canonical_slug = str(args.get("canonical_slug") or "")
                    duplicate_slug = str(args.get("duplicate_slug") or "")
                    reason = str(args.get("reason") or "possible duplicate knowledge pages")
                    task = knowledge_pages.enqueue_update_task(
                        slug=canonical_slug,
                        reason=f"[merge] {reason}",
                        target_scope=str(args.get("target_scope") or target_scope or "codebase"),
                        evidence=[str(item) for item in (args.get("evidence_hints") or [])],
                        operation="knowledge_merge",
                        related_slugs=[duplicate_slug],
                    )
                    self.storage.commit()
                    if self.enqueue_task:
                        await self.enqueue_task(task.task_id)
                    tool_result = (
                        f"Queued knowledge merge proposal task {task.task_id} to evaluate '{canonical_slug}' "
                        f"against duplicate candidate '{duplicate_slug}'."
                    )

                elif func_name == "propose_knowledge_correction":
                    slug = str(args.get("slug") or "")
                    reason = str(args.get("reason") or "possible knowledge correction needed")
                    task = knowledge_pages.enqueue_update_task(
                        slug=slug,
                        reason=f"[correction] {reason}",
                        target_scope=str(args.get("target_scope") or target_scope or "codebase"),
                        evidence=[str(item) for item in (args.get("evidence_hints") or [])],
                        operation="knowledge_correction",
                        related_slugs=[str(item) for item in (args.get("related_slugs") or [])],
                    )
                    self.storage.commit()
                    if self.enqueue_task:
                        await self.enqueue_task(task.task_id)
                    tool_result = f"Queued knowledge correction task {task.task_id} for '{slug}'."

                elif func_name == "queue_knowledge_refresh":
                    slug = str(args.get("slug") or "")
                    reason = str(args.get("reason") or "page may be stale")
                    task = knowledge_pages.enqueue_update_task(
                        slug=slug,
                        reason=f"[refresh] {reason}",
                        target_scope=str(args.get("target_scope") or target_scope or "codebase"),
                        evidence=[str(item) for item in (args.get("evidence_hints") or [])],
                        operation="knowledge_refresh",
                    )
                    self.storage.commit()
                    if self.enqueue_task:
                        await self.enqueue_task(task.task_id)
                    tool_result = f"Queued knowledge refresh task {task.task_id} for '{slug}'."

                else:
                    tool_result = f"Unsupported research tool call: {func_name}"
                    tool_outcome = "broken"
                    failure_kind = "unsupported_tool"

                _record_tool_event(
                    func_name,
                    outcome=tool_outcome,
                    failure_kind=failure_kind,
                    details={"tool_result": tool_result[:500]},
                )
                tool_result_full = tool_result if len(str(tool_result or "")) <= 12000 else ""
                return TerminalToolCallOutcome(
                    tool_name=str(func_name or ""),
                    tool_arguments=dict(args or {}),
                    tool_result_preview=str(tool_result or "")[:1200],
                    tool_result_full=str(tool_result_full or ""),
                    next_step_hint=(
                        "Consume the inherited tool result in a new explicit step. "
                        "If it is sufficient, finalize the research; otherwise choose the next bounded move."
                    ),
                    source_module="research",
                    metadata={
                        "target_scope": target_scope,
                        "task_description": task_description,
                    },
                    continuation_title=f"Continue research after {str(func_name or 'tool')}",
                    continuation_description=(
                        f"Continue the research task after the prior step executed `{str(func_name or 'tool')}`. "
                        "Your first move is to interpret the inherited tool result rather than repeat the same tool call. "
                        "If the inherited result is already enough, finalize the research; otherwise take one new bounded step."
                    ),
                    continuation_task_type="RESEARCH",
                    continuation_constraints={
                        "target_scope": target_scope,
                        "terminal_tool_step": True,
                        "allow_inherited_tool_result": True,
                        "step_role": "consume_tool_result",
                    },
                )
        else:
            raise TaskBoundaryViolationError(
                public_message=(
                    "Research task did not complete in one bounded turn and did not emit finalize_research. "
                    "Reframe or decompose it into a oneshottable task."
                ),
                autopsy=_build_task_boundary_autopsy(
                    task_description=task_description,
                    target_scope=target_scope,
                    task_context=task_context,
                    response=response,
                    next_step_hint="Create a smaller research task whose answer can be finalized in one variance-bearing invocation.",
                ),
            )
                
        kb_dir = os.path.join(root, ".knowledge")
        os.makedirs(kb_dir, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        
        if not final_report_data:
            # Fallback if no final report was emitted.
            wip_file = os.path.join(kb_dir, f"wip_research_{ts}.md")
            with open(wip_file, "w", encoding="utf-8") as f:
                f.write(f"# WIP Research Dump: {task_description}\\n\\n")
                for m in messages:
                    if m.get("content"):
                         f.write(f"**{m['role'].upper()}**: {m['content']}\\n\\n")
            
            autopsy = _build_iteration_limit_autopsy(
                task_description=task_description,
                target_scope=target_scope,
                task_context=task_context,
                policy=policy,
                messages=messages,
                wip_file=wip_file,
            )
            raise ResearchIterationLimitError(
                public_message=(
                    "Research task ended without finalize_research. Partial context saved to durable "
                    f"`.knowledge` library at: {wip_file}"
                ),
                autopsy=autopsy,
            )

        # Telemetry: If it successfully finalized, log a success for the parameter!
        try:
            self.storage.parameters.record_success(RESEARCH_ITERATION_POLICY_KEY)
            self.storage.commit()
        except Exception:
            pass

        # Save Finalized Research to Knowledge Library
        final_file = os.path.join(kb_dir, f"final_research_{ts}.md")
        with open(final_file, "w", encoding="utf-8") as f:
            f.write(f"# 🧠 Final Research Report\\n**Target**: {task_description}\\n\\n")
            f.write(f"### Context Gathered\\n{final_report_data.get('context_gathered', 'Inconclusive')}\\n\\n")
            f.write(f"### Key Constraints\\n{final_report_data.get('key_constraints_discovered', [])}\\n\\n")
            f.write(f"### Suggested Approach\\n{final_report_data.get('suggested_approach', 'Standard best practices')}\\n")
            f.write(f"### Sources\\n{final_report_data.get('reference_urls', [])}\\n")

        return ResearchReport(
            context_gathered=final_report_data.get("context_gathered", "Analysis was inconclusive."),
            key_constraints_discovered=final_report_data.get("key_constraints_discovered", []),
            suggested_approach=final_report_data.get("suggested_approach", "Proceed with standard best practices."),
            reference_urls=final_report_data.get("reference_urls", [])
        )
