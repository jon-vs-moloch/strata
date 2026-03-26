"""
@module eval.harness_eval
@purpose Execute a lightweight harness-style response path for benchmarks and structured evals.
@owns eval-only system prompt construction, direct harness response generation
@does_not_own chat persistence, SSE events, task orchestration side effects
@key_exports run_harness_response
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from strata.models.adapter import ModelAdapter
from strata.schemas.execution import WeakExecutionContext
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

DEFAULT_CONTEXT_FILES = [
    "README.md",
    "docs/spec/project-philosophy.md",
]


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
            return {
                "system_prompt": str(config.get("system_prompt") or DEFAULT_EVAL_SYSTEM_PROMPT),
                "context_files": list(config.get("context_files") or DEFAULT_CONTEXT_FILES),
            }
    finally:
        storage.close()
    return default_eval_harness_config()


def _normalize_eval_config(config_override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    config = get_active_eval_harness_config()
    if config_override:
        if config_override.get("system_prompt"):
            config["system_prompt"] = str(config_override["system_prompt"])
        if config_override.get("context_files"):
            config["context_files"] = [str(path) for path in config_override["context_files"]]
    return config


def _load_eval_context(context_files: List[str]) -> str:
    parts: List[str] = []
    for raw_path in context_files:
        path = Path(raw_path)
        if not path.exists():
            continue
        raw = path.read_text(encoding="utf-8")
        snippet = raw[:2000].strip()
        if snippet:
            parts.append(f"[{path}]\n{snippet}")
    return "\n\n".join(parts)


async def run_harness_response(
    prompt: str,
    *,
    run_id: str | None = None,
    config_override: Optional[Dict[str, Any]] = None,
) -> tuple[str, float]:
    """
    @summary Generate a harness-style answer without going through the persistent chat API.
    @inputs prompt: user prompt for evaluation, run_id: optional eval run label
    @returns tuple of response text and latency seconds
    @side_effects issues a direct weak-tier inference request
    """
    adapter = ModelAdapter()
    config = _normalize_eval_config(config_override)
    context_block = _load_eval_context(config.get("context_files", []))
    adapter.bind_execution_context(WeakExecutionContext(run_id=run_id or f"harness_eval_{int(time.time() * 1000)}"))
    messages: List[Dict[str, str]] = [
        {
            "role": "system",
            "content": (
                config.get("system_prompt", DEFAULT_EVAL_SYSTEM_PROMPT)
                + ("\n\nRepository context:\n" + context_block if context_block else "")
            ),
        },
        {"role": "user", "content": prompt},
    ]
    started_at = time.perf_counter()
    response = await adapter.chat(messages, temperature=0.0)
    latency_s = time.perf_counter() - started_at
    return response.get("content", ""), latency_s
