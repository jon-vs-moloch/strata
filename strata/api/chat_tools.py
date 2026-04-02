"""
@module api.chat_tools
@purpose Define built-in chat tool schemas and dynamic tool loading.

The chat surface needs a compact, inspectable tool registry. Pulling the tool
definitions out of API assembly keeps those schemas reusable without forcing
models to ingest unrelated lifecycle and admin code.
"""

from __future__ import annotations

import glob
import importlib.util
import logging
import os
from typing import Any, Dict, List


logger = logging.getLogger(__name__)


def _with_reason_parameter(tool_schema: Dict[str, Any]) -> Dict[str, Any]:
    enriched = {
        "type": tool_schema.get("type"),
        "function": dict(tool_schema.get("function") or {}),
    }
    function = enriched["function"]
    parameters = dict(function.get("parameters") or {})
    properties = dict(parameters.get("properties") or {})
    properties["reason"] = {
        "type": "string",
        "description": "One short sentence describing why you are calling this tool right now. This is surfaced to the operator.",
    }
    properties["progress_message"] = {
        "type": "string",
        "description": "Optional short status update shown to the operator immediately before the tool runs.",
    }
    parameters["properties"] = properties
    function["parameters"] = parameters
    function["description"] = (
        str(function.get("description") or "")
        + " Always include a short `reason` when calling this tool so the operator can see what you are doing."
        + " Include `progress_message` when you can phrase a natural brief update for the chat UI."
    ).strip()
    return enriched

TASK_GENERATION_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "kickoff_swarm_task",
            "description": "Initialize a formation of coding workers to implement a large feature, refactor, or fix a bug in the codebase.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short title of the task"},
                    "description": {"type": "string", "description": "Detailed prompt for the implementation agents"},
                },
                "required": ["title", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kickoff_background_research",
            "description": "Start an asynchronous, deep research task. Use this to conduct broad context compilation either across the entire codebase or out on the open web.",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "Detailed explanation of what needs to be researched."},
                    "target_scope": {
                        "type": "string",
                        "description": "Whether to perform 'codebase' introspection or 'web' research.",
                        "enum": ["codebase", "web"],
                    },
                },
                "required": ["description", "target_scope"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Start an asynchronous background web search. Use this for quick, targeted fact-finding. The results will be synthesized and posted to the chat once complete.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The simple search query."},
                },
                "required": ["query"],
            },
        },
    },
]

NON_GENERATIVE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "inspect_branch_state",
            "description": "Inspect the current state of a task branch, including recent attempts, children, open questions, verification posture, and trainer interventions. Prefer this before rewriting plans or invalidating premises.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task branch root or leaf to inspect."},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_self_audit",
            "description": "Request a bounded self-audit of a task branch using the agent tier. Use this to force the agent to inspect its own branch without assuming trainer rescue.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task branch to audit."},
                    "focus": {"type": "string", "description": "Optional specific concern to audit, such as repeated failures, grounding, or open questions."},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rewrite_plan",
            "description": "Replace a failing branch's current plan with a bounded corrective plan. Use this when the existing branch is drifting, looping, or carrying an unhealthy plan forward.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task branch to update."},
                    "plan": {"type": "string", "description": "The new bounded plan the branch should follow."},
                    "rationale": {"type": "string", "description": "Why this replacement plan is better than the current one."},
                },
                "required": ["task_id", "plan", "rationale"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "invalidate_premise",
            "description": "Record that a branch is relying on a false or unsafe premise, along with the correction it should inherit going forward.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task branch carrying the premise."},
                    "premise": {"type": "string", "description": "The assumption or premise that should no longer be trusted."},
                    "correction": {"type": "string", "description": "The corrected understanding or instruction that should replace it."},
                },
                "required": ["task_id", "premise", "correction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_verification_posture",
            "description": "Adjust verification intensity for a task branch. Use aggressive posture when trust is low or recent failures are high, and lighter posture only when the branch is stable.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task branch to update."},
                    "posture": {
                        "type": "string",
                        "description": "Desired verification intensity for this branch.",
                        "enum": ["aggressive", "standard", "light"],
                    },
                    "rationale": {"type": "string", "description": "Why this verification posture should apply."},
                },
                "required": ["task_id", "posture", "rationale"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_user_question",
            "description": "Mark an open system-originated question as actually answered after interpreting the user's latest message. Use this only when the user has truly answered or clarified the open question. If the reply is ambiguous or non-responsive, do not call this tool yet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question_id": {"type": "string", "description": "The open question id currently attached to this session."},
                    "answer": {"type": "string", "description": "Your concise interpretation of the user's actual answer or clarification."},
                    "resolution": {
                        "type": "string",
                        "description": "How to close the question.",
                        "enum": ["resolved", "dismissed"],
                    },
                },
                "required": ["question_id", "answer"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_feedback_signal",
            "description": "Register a lightweight feedback, surprise, correction, or attention signal so the system can prioritize it without waiting for a human reaction.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_type": {
                        "type": "string",
                        "description": "What produced or received the signal.",
                        "enum": ["message", "session", "task", "eval", "tool", "system"],
                    },
                    "source_id": {"type": "string", "description": "Stable identifier for the source item."},
                    "signal_kind": {
                        "type": "string",
                        "description": "Kind of signal being registered.",
                        "enum": ["reaction", "response", "correction", "surprise", "unexpected_success", "unexpected_failure", "importance", "highlight", "emphasize"],
                    },
                    "signal_value": {"type": "string", "description": "Short label or payload for the signal."},
                    "source_preview": {"type": "string", "description": "Short excerpt or summary of the source item."},
                    "expected_outcome": {"type": "string", "description": "Optional expectation that was violated."},
                    "observed_outcome": {"type": "string", "description": "Optional observed outcome."},
                    "note": {"type": "string", "description": "Optional short explanation of why the signal matters."},
                },
                "required": ["source_type", "source_id", "signal_kind", "signal_value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_knowledge_pages",
            "description": "List synthesized knowledge pages by metadata only. Use this before loading a full page when you need to find relevant existing knowledge.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Optional query to filter pages by title, summary, alias, or tag."},
                    "tag": {"type": "string", "description": "Optional tag filter."},
                    "domain": {
                        "type": "string",
                        "description": "Optional domain filter.",
                        "enum": ["system", "agent", "user", "contacts", "project", "world"],
                    },
                    "limit": {"type": "integer", "description": "Maximum number of page metadata results to return."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_knowledge_page_metadata",
            "description": "Fetch metadata for a synthesized knowledge page without loading the full page body.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string", "description": "Knowledge page slug."},
                },
                "required": ["slug"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_knowledge_page",
            "description": "Read a full synthesized knowledge page or a specific section when metadata alone is insufficient.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string", "description": "Knowledge page slug."},
                    "heading": {"type": "string", "description": "Optional heading to fetch a specific section only."},
                },
                "required": ["slug"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_knowledge",
            "description": "Queue a targeted knowledge-maintenance task when a page is missing, stale, inaccurate, or incomplete.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string", "description": "Desired page slug or topic name."},
                    "reason": {"type": "string", "description": "Why the knowledge needs updating."},
                    "domain": {
                        "type": "string",
                        "description": "Knowledge domain for the target page.",
                        "enum": ["system", "agent", "user", "contacts", "project", "world"],
                    },
                    "target_scope": {
                        "type": "string",
                        "description": "Whether to inspect the codebase or the public web.",
                        "enum": ["codebase", "web"],
                    },
                    "evidence_hints": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional hints about missing, stale, or contradictory evidence.",
                    },
                },
                "required": ["slug", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_knowledge_maintenance",
            "description": "Inspect the current knowledge maintenance backlog, including duplicate candidates and stale pages.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "flag_knowledge_issue",
            "description": "Flag a knowledge page for maintenance when you suspect it is stale, duplicated, contradictory, or otherwise needs review. This queues downstream work instead of mutating the wiki directly.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string", "description": "Primary page slug or topic name to flag."},
                    "issue_type": {
                        "type": "string",
                        "description": "What kind of issue was detected.",
                        "enum": ["stale", "duplicate", "correction", "missing_context", "conflict"],
                    },
                    "reason": {"type": "string", "description": "Why this issue should be reviewed."},
                    "related_slugs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional related pages involved in the issue, such as duplicate candidates.",
                    },
                    "target_scope": {
                        "type": "string",
                        "description": "Whether to inspect the codebase or the public web.",
                        "enum": ["codebase", "web"],
                    },
                    "evidence_hints": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional hints about missing, stale, or contradictory evidence.",
                    },
                },
                "required": ["slug", "issue_type", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_swarm_status",
            "description": "Check the status of currently running formation tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Optional specific task ID to check."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_spec",
            "description": "Read one of the durable Strata spec files. Use this before proposing changes to the system's long-term intent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "description": "Which spec to read.", "enum": ["global", "project"]},
                },
                "required": ["scope"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_loaded_context_files",
            "description": "List workspace files that are currently pinned into round-level context.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "load_context_file",
            "description": "Pin a workspace file into persistent round-level context until it is unloaded. Use this for compact, high-value artifacts only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Repository-relative or absolute file path."},
                    "priority": {
                        "type": "string",
                        "description": "How important this pinned context is relative to other pinned items.",
                        "enum": ["critical", "high", "normal", "low"],
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reprioritize_context_file",
            "description": "Change the priority of a pinned context file so the system can compact lower-priority context first under pressure.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Repository-relative or absolute file path."},
                    "priority": {
                        "type": "string",
                        "description": "The new priority for this pinned context item.",
                        "enum": ["critical", "high", "normal", "low"],
                    },
                },
                "required": ["path", "priority"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compact_context",
            "description": "Deterministically unload lower-priority pinned context items until the context budget is healthier.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_tokens": {
                        "type": "integer",
                        "description": "Optional target total estimated tokens to compact down to. Defaults to the configured budget.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "unload_context_file",
            "description": "Remove a previously pinned workspace file from persistent round-level context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Repository-relative or absolute file path."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_spec_update",
            "description": "Queue a reviewed proposal to change a durable spec. Use this when the user expresses a lasting goal, preference, or constraint that should influence future system behavior.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "description": "Which spec should be updated.", "enum": ["global", "project"]},
                    "proposed_change": {"type": "string", "description": "The candidate change that should be reviewed against the current spec."},
                    "rationale": {"type": "string", "description": "Why this should become part of the durable spec."},
                    "user_signal": {"type": "string", "description": "The user statement or intent that triggered this proposal."},
                    "claimed_mutation_class": {"type": "string", "description": "The mutation class this proposal claims under the current active spec."},
                    "proposal_kind": {"type": "string", "description": "The governance path for this change.", "enum": ["amendment", "clarification", "policy_update"]},
                },
                "required": ["scope", "proposed_change", "rationale"],
            },
        },
    },
]

TRAINER_CONTROL_TOOL_NAMES = {
    "inspect_branch_state",
    "request_self_audit",
    "rewrite_plan",
    "invalidate_premise",
    "set_verification_posture",
}


def filter_chat_tools_for_lane(tools: List[Dict[str, Any]], lane: str | None) -> List[Dict[str, Any]]:
    normalized_lane = str(lane or "").strip().lower()
    if normalized_lane == "trainer":
        return list(tools)
    filtered: List[Dict[str, Any]] = []
    for tool in tools:
        name = str(((tool.get("function") or {}).get("name")) or "").strip()
        if name in TRAINER_CONTROL_TOOL_NAMES:
            continue
        filtered.append(tool)
    return filtered


def load_dynamic_tools(*, base_dir: str, global_settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    if global_settings.get("testing_mode", False):
        logger.info("Testing mode active; suppressing chat tool exposure for cleaner evals.")
        return []

    dynamic_tools: List[Dict[str, Any]] = []
    tools_dir = os.path.join(base_dir, "strata", "tools")

    if global_settings.get("automatic_task_generation", False):
        dynamic_tools.extend(_with_reason_parameter(tool) for tool in TASK_GENERATION_TOOLS)
    dynamic_tools.extend(_with_reason_parameter(tool) for tool in NON_GENERATIVE_TOOLS)

    for tool_file in glob.glob(os.path.join(tools_dir, "*.py")):
        if tool_file.endswith("__init__.py"):
            continue
        module_name = f"dynamic_tools.{os.path.basename(tool_file)[:-3]}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, tool_file)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                if hasattr(module, "TOOL_SCHEMA"):
                    dynamic_tools.append(_with_reason_parameter(getattr(module, "TOOL_SCHEMA")))
                    logger.info("Loaded dynamic tool: %s", os.path.basename(tool_file))
        except Exception as exc:
            logger.error("Failed to dynamic load tool from %s: %s", tool_file, exc)

    return dynamic_tools
