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
    parameters["properties"] = properties
    function["parameters"] = parameters
    function["description"] = (
        str(function.get("description") or "")
        + " Always include a short `reason` when calling this tool so the operator can see what you are doing."
    ).strip()
    return enriched

TASK_GENERATION_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "kickoff_swarm_task",
            "description": "Initialize a swarm of coding agents to implement a large feature, refactor, or fix a bug in the codebase.",
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
            "name": "check_swarm_status",
            "description": "Check the status of currently running tasks.",
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
                },
                "required": ["path"],
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
                },
                "required": ["scope", "proposed_change", "rationale"],
            },
        },
    },
]


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
