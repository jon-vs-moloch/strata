"""
@module eval.harness_eval
@purpose Execute configurable harness-style response paths for benchmarks and structured evals.
@owns eval-only system prompt construction, safe tool/web augmentation, profile selection
@does_not_own chat persistence, SSE events, task orchestration side effects
@key_exports run_harness_response, EVAL_PROFILES
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from strata.models.adapter import ModelAdapter
from strata.context.loaded_files import get_loaded_context_registry
from strata.observability.context import record_context_load
from strata.schemas.execution import StrongExecutionContext, WeakExecutionContext
from strata.storage.services.main import StorageManager


EVAL_HARNESS_CONFIG_KEY = "eval_harness_active_config"
EVAL_HARNESS_CONFIG_DESCRIPTION = (
    "Active lightweight eval-harness configuration used for benchmark and structured-eval runs."
)

DEFAULT_EVAL_SYSTEM_PROMPT = """You are Strata, a unified AI engineering system.
Answer directly, clearly, and concisely.
In quiet testing mode, do not create tasks, do not rely on background work, and do not mention unavailable tooling unless it is strictly necessary to answer truthfully.
If asked what the system should do while idle in quiet testing mode, prefer "remain idle, wait for explicit input, avoid background activity."
Prefer giving the best direct answer from the repository philosophy and current harness intent."""

LEGACY_DEFAULT_CONTEXT_FILES = [
    "README.md",
    "docs/spec/project-philosophy.md",
]

DEFAULT_CONTEXT_FILES = [
    ".knowledge/specs/project_spec.md",
    "docs/spec/eval-brief.md",
    "docs/spec/project-philosophy.md",
]

EVAL_PROFILES: Dict[str, Dict[str, Any]] = {
    "raw_model": {
        "scaffold": False,
        "use_tools": False,
        "use_web": False,
        "use_context": False,
    },
    "harness_no_capes": {
        "scaffold": True,
        "use_tools": False,
        "use_web": False,
        "use_context": True,
    },
    "harness_tools_no_web": {
        "scaffold": True,
        "use_tools": True,
        "use_web": False,
        "use_context": True,
    },
    "harness_web_no_tools": {
        "scaffold": True,
        "use_tools": False,
        "use_web": True,
        "use_context": True,
    },
    "harness_tools_web": {
        "scaffold": True,
        "use_tools": True,
        "use_web": True,
        "use_context": True,
    },
}


def default_eval_harness_config() -> Dict[str, Any]:
    return {
        "system_prompt": DEFAULT_EVAL_SYSTEM_PROMPT,
        "context_files": list(DEFAULT_CONTEXT_FILES),
    }


def get_active_eval_harness_config() -> Dict[str, Any]:
    storage = StorageManager()
    try:
        config = storage.parameters.peek_parameter(
            EVAL_HARNESS_CONFIG_KEY,
            default_value=default_eval_harness_config(),
        )
        if isinstance(config, dict):
            context_files = list(config.get("context_files") or DEFAULT_CONTEXT_FILES)
            if context_files == LEGACY_DEFAULT_CONTEXT_FILES:
                context_files = list(DEFAULT_CONTEXT_FILES)
            loaded = get_loaded_context_registry(storage).get("files") or []
            for item in loaded:
                path = str(item.get("path") or "").strip()
                if path and path not in context_files:
                    context_files.append(path)
            return {
                "system_prompt": str(config.get("system_prompt") or DEFAULT_EVAL_SYSTEM_PROMPT),
                "context_files": context_files,
            }
    finally:
        storage.close()
    return default_eval_harness_config()


def _normalize_eval_config(config_override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    config = get_active_eval_harness_config()
    if config_override:
        if "system_prompt" in config_override:
            config["system_prompt"] = str(config_override["system_prompt"])
        if "context_files" in config_override:
            config["context_files"] = [str(path) for path in (config_override.get("context_files") or [])]
    return config


def _load_eval_context(context_files: List[str]) -> str:
    parts: List[str] = []
    for raw_path in context_files:
        path = Path(raw_path)
        if not path.exists():
            continue
        raw = path.read_text(encoding="utf-8", errors="ignore")
        record_context_load(
            artifact_type="eval_context_file",
            identifier=str(path),
            content=raw,
            source="eval.harness_eval._load_eval_context",
            metadata={"path": str(path)},
        )
        snippet = raw[:2000].strip()
        if snippet:
            parts.append(f"[{path}]\n{snippet}")
    return "\n\n".join(parts)


async def _search_web_snippets(query: str) -> str:
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=12.0,
            )
            response.raise_for_status()
            snippets = re.findall(r'<a class="result__snippet[^>]*>(.*?)</a>', response.text, re.IGNORECASE | re.DOTALL)
            cleaned = [re.sub("<[^<]+>", "", snippet).strip() for snippet in snippets[:4]]
            return "\n".join(f"- {snippet}" for snippet in cleaned if snippet)
    except Exception as exc:
        return f"Web search unavailable: {exc}"
    return ""


def _extract_json_object(raw: str) -> Dict[str, Any]:
    try:
        return json.loads(raw)
    except Exception:
        pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except Exception:
        return {}


async def _run_safe_tool_loop(
    adapter: ModelAdapter,
    *,
    prompt: str,
    run_id: str,
    mode: str,
    system_prompt: str,
    context_files: List[str],
    allow_web: bool,
    max_iters: int = 2,
) -> tuple[str, float, Dict[str, Any]]:
    if mode == "strong":
        adapter.bind_execution_context(StrongExecutionContext(run_id=run_id))
    else:
        adapter.bind_execution_context(WeakExecutionContext(run_id=run_id))

    context_lookup = {str(path): _load_eval_context([str(path)]) for path in context_files}
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_context_file",
                "description": "Read one of the eval-approved repository context files.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        }
    ]
    if allow_web:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "search_web",
                    "description": "Search the public web for concise factual snippets.",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
            }
        )

    messages: List[Dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                system_prompt
                + "\n\nOnly use the provided safe tools. Do not invent tools or background work."
            ),
        },
        {"role": "user", "content": prompt},
    ]
    started_at = time.perf_counter()
    final_usage: Dict[str, Any] = {}

    for _ in range(max_iters):
        response = await adapter.chat(messages, temperature=0.0, tools=tools)
        final_usage = response.get("usage") or final_usage
        tool_calls = response.get("tool_calls") or []
        content = response.get("content", "")
        if not tool_calls:
            return content, time.perf_counter() - started_at, final_usage
        messages.append({"role": "assistant", "content": content or None, "tool_calls": tool_calls})
        for call in tool_calls:
            func = call.get("function", {})
            name = func.get("name")
            args = _extract_json_object(func.get("arguments", "{}"))
            tool_result = ""
            if name == "read_context_file":
                path = str(args.get("path") or "")
                tool_result = context_lookup.get(path, f"Unavailable context file: {path}")
            elif name == "search_web" and allow_web:
                query = str(args.get("query") or prompt)
                tool_result = await _search_web_snippets(query)
            else:
                tool_result = f"Tool unavailable: {name}"
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", "eval_tool_call"),
                    "name": name,
                    "content": tool_result,
                }
            )

    response = await adapter.chat(messages, temperature=0.0)
    final_usage = response.get("usage") or final_usage
    return response.get("content", ""), time.perf_counter() - started_at, final_usage


async def run_harness_response(
    prompt: str,
    *,
    run_id: str | None = None,
    config_override: Optional[Dict[str, Any]] = None,
    mode: str = "weak",
    profile: str = "harness_no_capes",
) -> tuple[str, float, Dict[str, Any]]:
    """
    Generate an eval-safe answer with an explicit profile.
    Profiles can enable or disable scaffold context, safe tools, and web augmentation.
    """
    profile_config = dict(EVAL_PROFILES.get(profile, EVAL_PROFILES["harness_no_capes"]))
    adapter = ModelAdapter()
    run_id = run_id or f"harness_eval_{int(time.time() * 1000)}"

    if not profile_config["scaffold"]:
        if mode == "strong":
            adapter.bind_execution_context(StrongExecutionContext(run_id=run_id))
        else:
            adapter.bind_execution_context(WeakExecutionContext(run_id=run_id))
        started_at = time.perf_counter()
        response = await adapter.chat([{"role": "user", "content": prompt}], temperature=0.0)
        return response.get("content", ""), time.perf_counter() - started_at, response.get("usage") or {}

    config = _normalize_eval_config(config_override)
    context_files = list(config.get("context_files", [])) if profile_config["use_context"] else []
    system_prompt = config.get("system_prompt", DEFAULT_EVAL_SYSTEM_PROMPT)

    if profile_config["use_tools"]:
        return await _run_safe_tool_loop(
            adapter,
            prompt=prompt,
            run_id=run_id,
            mode=mode,
            system_prompt=system_prompt,
            context_files=context_files,
            allow_web=profile_config["use_web"],
        )

    context_block = _load_eval_context(context_files)
    web_block = ""
    if profile_config["use_web"]:
        web_snippets = await _search_web_snippets(prompt)
        if web_snippets:
            web_block = "\n\nWeb context:\n" + web_snippets

    if mode == "strong":
        adapter.bind_execution_context(StrongExecutionContext(run_id=run_id))
    else:
        adapter.bind_execution_context(WeakExecutionContext(run_id=run_id))

    messages: List[Dict[str, str]] = [
        {
            "role": "system",
            "content": system_prompt
            + ("\n\nRepository context:\n" + context_block if context_block else "")
            + web_block,
        },
        {"role": "user", "content": prompt},
    ]
    started_at = time.perf_counter()
    response = await adapter.chat(messages, temperature=0.0)
    latency_s = time.perf_counter() - started_at
    return response.get("content", ""), latency_s, response.get("usage") or {}
