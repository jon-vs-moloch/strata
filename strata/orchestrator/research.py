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
from strata.knowledge.pages import KnowledgePageStore
from strata.core.lanes import infer_lane_from_session_id
from strata.feedback.signals import register_feedback_signal
from strata.orchestrator.tool_health import record_tool_execution, should_throttle_tool
from strata.schemas.core import ResearchReport


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


def _build_research_system_prompt(
    target_scope: str,
    task_description: str,
    repo_snapshot: str = "",
    spec_paths: Optional[list[str]] = None,
    preferred_start_paths: Optional[list[str]] = None,
    focused_guidance: str = "",
    disallow_broad_repo_scan: bool = False,
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
{spec_lines}{repo_hint_block}{focused_hint_block}{codebase_nudge}

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
    ) -> ResearchReport:
        """
        @summary Autonomous agent loop for research. Decomposes the task, queries the web/codebase iteratively, and synthesizes.
        """
        import os
        import json
        import httpx
        import re
        
        print(f"Starting autonomous research loop for: {task_description[:50]}...")
        root = repo_path or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        context_hints = context_hints or {}
        repo_snapshot = str(context_hints.get("repo_snapshot") or "").strip()
        spec_paths = context_hints.get("spec_paths") or []
        source_hints = dict(context_hints.get("source_hints") or {})
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

        messages = [
            {
                "role": "system",
                "content": _build_research_system_prompt(
                    target_scope=target_scope,
                    task_description=task_description,
                    repo_snapshot=repo_snapshot,
                    spec_paths=spec_paths,
                    preferred_start_paths=preferred_start_paths,
                    focused_guidance=focused_guidance,
                    disallow_broad_repo_scan=disallow_broad_repo_scan,
                ),
            },
            {
                "role": "user",
                "content": f"RESEARCH TASK: {task_description}\\nPlease begin your research. Call tools to gather data."
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
        response = await self.model.chat(messages, tools=RESEARCH_TOOLS)

        if response.get("status") == "error":
            err_msg = response.get("message", "Unknown model adapter error.")
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
                raise TaskBoundaryViolationError(
                    public_message=(
                        "Research task required a second variance-bearing move after completing a tool interaction. "
                        "Decompose it into smaller oneshottable research tasks."
                    ),
                    autopsy=_build_task_boundary_autopsy(
                        task_description=task_description,
                        target_scope=target_scope,
                        task_context=task_context,
                        response=response,
                        tool_call=call,
                        tool_result_preview=tool_result,
                        next_step_hint=(
                            "Split the work so one task can inspect exactly one source or artifact and a later task can synthesize."
                        ),
                    ),
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
                
        from datetime import datetime
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
