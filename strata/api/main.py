"""
@module api.main
@purpose Expose internal storage and Strata orchestrator state to the UI via REST.
@owns API routing, JSON serialization, StorageManager lifecycle, background worker
@does_not_own business logic orchestration, database schema definitions
@key_exports app

The API is part of the harness, not just a frontend convenience layer.
It exposes the system's state, controls, and telemetry so both humans and
agents can inspect what the harness is learning and how it is behaving.
"""

import os
import logging
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Any, Optional, Union
import json
import asyncio
from strata.storage.services.main import StorageManager
from strata.storage.models import TaskModel, TaskType, TaskState, ParameterModel
from strata.models.adapter import ModelAdapter
from strata.orchestrator.background import BackgroundWorker
from strata.api.hotreload import HotReloader
from strata.schemas.core import ResearchReport, ResearchReport as LocalResearchReport, TaskDecomposition, AttemptResolutionSchema
from strata.memory.semantic import SemanticMemory
from strata.orchestrator.worker.telemetry import build_telemetry_snapshot
from strata.models.providers import get_provider_telemetry_snapshot
from strata.eval.benchmark import run_benchmark, persist_benchmark_report
from strata.eval.structured_eval import run_structured_eval, persist_structured_eval_report
from strata.eval.harness_eval import (
    EVAL_HARNESS_CONFIG_DESCRIPTION,
    EVAL_HARNESS_CONFIG_KEY,
    default_eval_harness_config,
    get_active_eval_harness_config,
)
from strata.experimental.experiment_runner import (
    ExperimentRunner,
    iter_experiment_reports,
    normalize_experiment_report,
    report_has_weak_gain,
)
from strata.orchestrator.tools_pipeline import ToolsPromotionPipeline
import importlib.util
import glob
import re

logger = logging.getLogger(__name__)

# ── Module-level singletons ────────────────────────────────────────────────────
GLOBAL_SETTINGS = {
    "max_sync_tool_iterations": 3,
    "automatic_task_generation": False,
    "testing_mode": False,
    "replay_pending_tasks_on_startup": False,
}
SETTINGS_PARAMETER_KEY = "orchestrator_global_settings"
SETTINGS_PARAMETER_DESCRIPTION = (
    "Persisted API/orchestrator settings shared between the UI and the worker startup path."
)

_model = ModelAdapter()
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_hotreloader = HotReloader(_BASE_DIR)
_memory = SemanticMemory()
_worker = BackgroundWorker(
    storage_factory=StorageManager,   # each task gets a fresh session
    model_adapter=_model,
    memory=_memory,
    settings_provider=lambda: GLOBAL_SETTINGS,
)
_event_queue = asyncio.Queue()

async def _broadcast_event(data: Dict[str, Any]):
    """Push event to SSE queue for UI consumption."""
    await _event_queue.put(data)

# Register worker update listener
_worker.set_on_update(lambda tid, state: asyncio.create_task(_broadcast_event({"type": "task_update", "task_id": tid, "state": state})))

# ── True Tool Calling ──────────────────────────────────────────────────────────
# We no longer use string heuristics. Instead, we provide the LLM with explicitly 
# defined tools to route tasks or fetch facts.
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
                    "description": {"type": "string", "description": "Detailed prompt for the implementation agents"}
                },
                "required": ["title", "description"]
            }
        }
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
                    "target_scope": {"type": "string", "description": "Whether to perform 'codebase' introspection or 'web' research.", "enum": ["codebase", "web"]}
                },
                "required": ["description", "target_scope"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Start an asynchronous background web search. Use this for quick, targeted fact-finding. The results will be synthesized and posted to the chat once complete.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The simple search query."}
                },
                "required": ["query"]
            }
        }
    },
]

NON_GENERATIVE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "check_swarm_status",
            "description": "Check the status of currently running tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Optional specific task ID to check."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "amend_project_spec",
            "description": "Permanently update the technical specification or architectural goals for this project. Use this when the user makes a significant pivot or sets new high-level constraints.",
            "parameters": {
                "type": "object",
                "properties": {
                    "amendment": {"type": "string", "description": "The new specification text or update to append/replace."}
                },
                "required": ["amendment"]
            }
        }
    }
]

def load_dynamic_tools() -> List[Dict[str, Any]]:
    """
    @summary Hot-loads tool schemas from the tools/ directory.
    @returns list of OpenAI-style tool definitions
    """
    if GLOBAL_SETTINGS.get("testing_mode", False):
        logger.info("Testing mode active; suppressing chat tool exposure for cleaner evals.")
        return []

    dynamic_tools = []
    tools_dir = os.path.join(_BASE_DIR, "strata", "tools")

    if GLOBAL_SETTINGS.get("automatic_task_generation", False):
        dynamic_tools.extend(TASK_GENERATION_TOOLS)
    dynamic_tools.extend(NON_GENERATIVE_TOOLS)
    
    # Load custom tools from the tools/ directory
    for tool_file in glob.glob(os.path.join(tools_dir, "*.py")):
        if tool_file.endswith("__init__.py"): continue
        
        module_name = f"dynamic_tools.{os.path.basename(tool_file)[:-3]}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, tool_file)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                if hasattr(module, "TOOL_SCHEMA"):
                    dynamic_tools.append(getattr(module, "TOOL_SCHEMA"))
                    logger.info(f"Loaded dynamic tool: {os.path.basename(tool_file)}")
        except Exception as e:
            logger.error(f"Failed to dynamic load tool from {tool_file}: {e}")
            
    return dynamic_tools

def _normalized_settings(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    normalized = dict(GLOBAL_SETTINGS)
    if payload:
        normalized.update(payload)
    return normalized

def _slugify_candidate_suffix(raw: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    return slug[:48] or "candidate"

def _extract_json_object(raw: str) -> Dict[str, Any]:
    try:
        return json.loads(raw)
    except Exception:
        pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in model output.")
    return json.loads(match.group(0))

async def _generate_eval_candidate_from_tier(
    proposer_tier: str,
    current_config: Dict[str, Any],
) -> Dict[str, Any]:
    adapter = ModelAdapter()
    if proposer_tier == "weak":
        from strata.schemas.execution import WeakExecutionContext
        context = WeakExecutionContext(run_id=f"bootstrap_proposal_{int(datetime.now(timezone.utc).timestamp() * 1000)}")
    else:
        from strata.schemas.execution import StrongExecutionContext
        context = StrongExecutionContext(run_id=f"bootstrap_proposal_{int(datetime.now(timezone.utc).timestamp() * 1000)}")
    adapter.bind_execution_context(context)

    proposal_prompt = f"""
You are proposing one small harness-side change to improve weak-model self-improvement in Strata.
Return only JSON with this schema:
{{
  "candidate_suffix": "short_slug_like_name",
  "system_prompt": "full replacement system prompt",
  "context_files": ["README.md", "docs/spec/project-philosophy.md"],
  "rationale": "short explanation of why this should improve weak-model self-improvement",
  "expected_gain": "what telemetry should improve"
}}

Constraints:
- Propose a small, reversible change to the eval harness only.
- The change must be safe to apply to future eval runs from either proposer tier.
- Keep context_files short and repository-local.
- Optimize for the real goal: the weak model proposing and surviving system improvements.

Current eval harness config:
{json.dumps(current_config, indent=2)}
""".strip()

    response = await adapter.chat(
        [{"role": "user", "content": proposal_prompt}],
        temperature=0.2 if proposer_tier == "weak" else 0.1,
    )
    proposal = _extract_json_object(response.get("content", ""))
    suffix = _slugify_candidate_suffix(str(proposal.get("candidate_suffix", proposer_tier)))
    return {
        "proposer_tier": proposer_tier,
        "candidate_change_id": f"{proposer_tier}_{suffix}_{int(datetime.now(timezone.utc).timestamp())}",
        "eval_harness_config_override": {
            "system_prompt": str(proposal.get("system_prompt") or current_config.get("system_prompt") or ""),
            "context_files": [str(path) for path in proposal.get("context_files") or current_config.get("context_files") or []],
        },
        "rationale": str(proposal.get("rationale") or ""),
        "expected_gain": str(proposal.get("expected_gain") or ""),
        "raw_proposal": proposal,
    }

async def _generate_tool_candidate_from_tier(
    proposer_tier: str,
    *,
    tool_name: str,
    task_description: str,
) -> Dict[str, Any]:
    adapter = ModelAdapter()
    if proposer_tier == "weak":
        from strata.schemas.execution import WeakExecutionContext
        context = WeakExecutionContext(run_id=f"tool_bootstrap_{int(datetime.now(timezone.utc).timestamp() * 1000)}")
    else:
        from strata.schemas.execution import StrongExecutionContext
        context = StrongExecutionContext(run_id=f"tool_bootstrap_{int(datetime.now(timezone.utc).timestamp() * 1000)}")
    adapter.bind_execution_context(context)
    proposal_prompt = f"""
Create a small, safe Strata dynamic tool.
Return only JSON with this schema:
{{
  "source": "full python source for strata/tools/{tool_name}.experimental.py",
  "manifest": {{
    "validator": "python_import_only",
    "smoke_test": "strata/tools/tests/test_{tool_name}_smoke.py"
  }},
  "smoke_test": "full python smoke test source",
  "rationale": "why this tool helps bootstrap progress",
  "expected_gain": "what operator-visible gain this tool should unlock"
}}

Requirements:
- The tool must define a valid TOOL_SCHEMA.
- The implementation must be read-only or narrowly scoped.
- The smoke test should pass with a plain `python` invocation.
- Task: {task_description}
""".strip()
    response = await adapter.chat(
        [{"role": "user", "content": proposal_prompt}],
        temperature=0.15 if proposer_tier == "strong" else 0.25,
    )
    proposal = _extract_json_object(response.get("content", ""))
    return {
        "proposer_tier": proposer_tier,
        "candidate_change_id": f"{proposer_tier}_{tool_name}_{int(datetime.now(timezone.utc).timestamp())}",
        "tool_name": tool_name,
        "source": str(proposal.get("source") or ""),
        "manifest": proposal.get("manifest") or {},
        "smoke_test": str(proposal.get("smoke_test") or ""),
        "rationale": str(proposal.get("rationale") or ""),
        "expected_gain": str(proposal.get("expected_gain") or ""),
        "raw_proposal": proposal,
    }

def _apply_experiment_promotion(storage: StorageManager, candidate_change_id: str, *, force: bool = False) -> Dict[str, Any]:
    runner = ExperimentRunner(storage, _model)
    report = runner.get_persisted_experiment_report(candidate_change_id)
    if not report:
        raise HTTPException(status_code=404, detail="No persisted experiment report found for candidate_change_id")
    if report.get("recommendation") != "promote" and not force:
        raise HTTPException(status_code=400, detail="Experiment report does not recommend promotion")

    promotion_state = storage.parameters.peek_parameter(
        key="promoted_eval_candidates",
        default_value={"current": None, "history": []},
    ) or {"current": None, "history": []}
    history = list(promotion_state.get("history", []))
    history.append(
        {
            "candidate_change_id": candidate_change_id,
            "recommendation": report.get("recommendation"),
            "recorded_at": report.get("recorded_at"),
            "promoted_at": datetime.now(timezone.utc).isoformat(),
            "proposal_metadata": report.get("proposal_metadata") or {},
        }
    )
    promotion_state["current"] = candidate_change_id
    promotion_state["history"] = history
    storage.parameters.set_parameter(
        key="promoted_eval_candidates",
        value=promotion_state,
        description="Accepted eval-harness candidates and their promotion history.",
    )

    applied_config = None
    if report.get("eval_harness_config_override"):
        applied_config = report["eval_harness_config_override"]
        storage.parameters.set_parameter(
            EVAL_HARNESS_CONFIG_KEY,
            applied_config,
            description=EVAL_HARNESS_CONFIG_DESCRIPTION,
        )

    storage.commit()
    return {
        "candidate_change_id": candidate_change_id,
        "recommendation": report.get("recommendation"),
        "applied_eval_harness_config": applied_config,
        "proposal_metadata": report.get("proposal_metadata") or {},
    }

def _build_dashboard_snapshot(storage: StorageManager, limit: int = 10) -> Dict[str, Any]:
    telemetry = build_telemetry_snapshot(storage, limit=limit)
    provider_telemetry = get_provider_telemetry_snapshot() or (
        storage.parameters.peek_parameter("provider_transport_telemetry_snapshot", default_value={}) or {}
    )
    promoted_state = storage.parameters.peek_parameter(
        "promoted_eval_candidates",
        default_value={"current": None, "history": []},
    ) or {"current": None, "history": []}
    report_rows = (
        storage.session.query(ParameterModel)
        .filter(ParameterModel.key.like("experiment_report:%"))
        .order_by(ParameterModel.updated_at.desc())
        .limit(limit)
        .all()
    )
    normalized_reports = iter_experiment_reports(report_rows)
    reports = []
    weak_promotions = 0
    strong_promotions = 0
    for current in normalized_reports:
        metadata = current.get("proposal_metadata") or {}
        if current.get("recommendation") == "promote":
            if metadata.get("proposer_tier") == "weak":
                weak_promotions += 1
            elif metadata.get("proposer_tier") == "strong":
                strong_promotions += 1
        reports.append(
            {
                "candidate_change_id": current.get("candidate_change_id"),
                "evaluation_kind": current.get("evaluation_kind"),
                "recommendation": current.get("recommendation"),
                "recorded_at": current.get("recorded_at"),
                "proposal_metadata": metadata,
                "promotion_readiness": current.get("promotion_readiness") or {},
            }
        )
    recent_failures = [
        metric for metric in telemetry.get("recent_metrics", [])
        if metric.get("metric_name") == "task_failure"
    ]
    research_failures = [
        metric for metric in recent_failures
        if metric.get("task_type") == "RESEARCH"
    ]
    ignition = None
    for current in normalized_reports:
        metadata = current.get("proposal_metadata") or {}
        weak_gain = report_has_weak_gain(current)
        if metadata.get("proposer_tier") == "weak" and current.get("recommendation") == "promote" and weak_gain:
            ignition = {
                "detected": True,
                "candidate_change_id": current.get("candidate_change_id"),
                "proposal_metadata": metadata,
                "recorded_at": current.get("recorded_at"),
            }
            break
    if ignition is None:
        ignition = {"detected": False}
    return {
        "generated_at": telemetry.get("generated_at"),
        "overview": telemetry.get("overview", {}),
        "ignition": ignition,
        "current_promoted_candidate": promoted_state.get("current"),
        "promotion_counts": {
            "weak": weak_promotions,
            "strong": strong_promotions,
            "total_history": len(promoted_state.get("history", [])),
        },
        "failure_pressure": {
            "recent_failures": len(recent_failures),
            "recent_research_failures": len(research_failures),
        },
        "reports": reports,
        "provider_telemetry": provider_telemetry,
    }

async def _perform_web_search(query: str) -> str:
    """Synchronous fallback web search using duckduckgo HTML for simple facts."""
    import httpx
    import re
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": str(query)},
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"},
                timeout=5.0
            )
            resp.raise_for_status()
            match_list = re.findall(r'<a class="result__snippet[^>]*>(.*?)</a>', resp.text, re.IGNORECASE | re.DOTALL)
            snippets = list(match_list)
            results = []
            for s in snippets[:3]:
                clean = re.sub('<[^<]+>', '', str(s)).strip()
                results.append(clean)
            if not results:
                return "The web search returned no immediate snippets. Consider using 'kickoff_background_research' with scope 'web' for a deeper search if this was a complex query."
            return "\n".join(f"- {res}" for res in results)
    except Exception as e:
        return f"Web search tool encountered an error: {e}"

# ── App lifecycle ──────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    from strata.storage.models import Base
    from strata.storage.services.main import _engine
    Base.metadata.create_all(_engine)
    storage = StorageManager()
    try:
        persisted_settings = storage.parameters.get_parameter(
            key=SETTINGS_PARAMETER_KEY,
            default_value=dict(GLOBAL_SETTINGS),
            description=SETTINGS_PARAMETER_DESCRIPTION,
        ) or {}
        GLOBAL_SETTINGS.update(_normalized_settings(persisted_settings))
        storage.commit()
    finally:
        storage.close()
    await _worker.start()
    logger.info("Strata API started")
    yield
    await _worker.stop()
    logger.info("Strata API stopped")

app = FastAPI(title="Strata API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_storage():
    storage = StorageManager()
    try:
        yield storage
    finally:
        storage.close()


# ── Standard endpoints ──────────────────────────────────────────────────────────

@app.get("/tasks", response_model=List[Dict[str, Any]])
async def list_tasks(storage: StorageManager = Depends(get_storage)):
    from sqlalchemy.orm import selectinload
    tasks = storage.session.query(TaskModel).options(selectinload(TaskModel.attempts)).all()
    return [{
        "id": t.task_id,
        "parent_id": t.parent_task_id,
        "title": t.title,
        "description": t.description,
        "status": t.state.value.lower(),
        "type": t.type.value.lower(),
        "depth": t.depth,
        "attempts": [
            {
                "id": a.attempt_id,
                "outcome": a.outcome.value.lower() if a.outcome else None,
                "resolution": a.resolution.value.lower() if a.resolution else None,
                "started_at": a.started_at.isoformat(),
                "ended_at": a.ended_at.isoformat() if a.ended_at else None,
                "reason": a.reason
            } for a in t.attempts
        ]
    } for t in tasks]


@app.get("/messages")
async def get_messages(session_id: Optional[str] = None, storage: StorageManager = Depends(get_storage)):
    history = storage.messages.get_all(session_id=session_id)
    return [{
        "id": m.message_id,
        "session_id": m.session_id,
        "role": m.role,
        "content": m.content,
        "is_intervention": m.is_intervention,
        "created_at": m.created_at.isoformat()
    } for m in history]


@app.get("/sessions")
async def get_sessions(storage: StorageManager = Depends(get_storage)):
    return storage.messages.get_sessions()


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str, storage: StorageManager = Depends(get_storage)):
    storage.messages.archive_session(session_id)
    storage.commit()
    return {"status": "ok"}


@app.post("/chat")
async def post_chat(payload: Dict[str, Any], storage: StorageManager = Depends(get_storage)):
    """
    @summary Process a user chat message.
    @inputs payload: { role: 'user', content: '...', session_id: '...' }
    @outputs assistant response acknowledgement
    @side_effects may create background tasks
    """
    session_id = payload.get("session_id", "default")
    content = payload.get("content", "")

    # 1. Persist user message immediately so polling sees it
    storage.messages.create(role=payload["role"], content=content, session_id=session_id)
    storage.commit()

    # 2. Re-construct conversation history for the LLM
    # Use Semantic Memory to retrieve similar past tasks/decisions
    past_memories = _memory.query_memory(content, n_results=5)
    memory_context = ""
    if isinstance(past_memories, dict) and past_memories.get("documents") and past_memories["documents"][0]:
        memory_context = "\n\nRELEVANT PAST CONTEXT:\n" + "\n".join(past_memories["documents"][0])

    active_tools = load_dynamic_tools()
    tool_summaries = []
    for tool in active_tools:
        function = tool.get("function", {})
        name = function.get("name")
        description = function.get("description")
        if name and description:
            tool_summaries.append(f"- {name}: {description}")
    tool_summary_text = "\n".join(tool_summaries) if tool_summaries else "- No active tools are currently exposed."

    messages = [
        {
            "role": "system", 
            "content": f"""You are Strata, a unified AI engineering system. You manage a swarm of background agents, but present yourself as a single, first-person entity.
If internal processes hit a BLOCKED state, explain the issue to the USER directly.
{memory_context}

Available Tools:
{tool_summary_text}
"""
        }
    ]
    
    # Keep the immediate dialogue context (last 5 messages)
    history_records = storage.messages.get_all(session_id=session_id)
    for m in history_records[-5:]:
        messages.append({"role": m.role, "content": m.content})

    # 3. Handle tools with iterative looping
    max_iters = GLOBAL_SETTINGS.get("max_sync_tool_iterations", 3)
    iteration = 0
    final_reply = ""
    
    while iteration < max_iters:
        # Use dynamic tool registry
        model_response = await _model.chat(messages, tools=active_tools)
        
        tool_calls = model_response.get("tool_calls")
        content_val = model_response.get("content")
        chain_of_thought = str(content_val) if content_val else ""
        
        # Save interpretation if available
        if chain_of_thought and chain_of_thought.strip() and not tool_calls:
            # Done! Plain chat fallback, answered without tools
            storage.messages.create(role="assistant", content=chain_of_thought.strip(), session_id=session_id)
            storage.commit()
            await _broadcast_event({"type": "message", "session_id": session_id})
            return {"status": "ok", "reply": chain_of_thought.strip()}
            
        if tool_calls and isinstance(tool_calls, list) and len(tool_calls) > 0:
            import json
            tool_outputs_generated = False
            async_task_ids = []
            
            # Save the human-readable CoT if provided
            if chain_of_thought and chain_of_thought.strip():
                 storage.messages.create(role="assistant", content=chain_of_thought.strip(), session_id=session_id)
                 storage.commit()
            else:
                 # Generate a fallback CoT message if the model was silent
                 names = [c.get("function", {}).get("name") for c in tool_calls]
                 chain_of_thought = f"Invoking system tools: {', '.join(names)}"
                 storage.messages.create(role="assistant", content=chain_of_thought, session_id=session_id)
                 storage.commit()

            # Record the tool calls in the message history for the LLM's next turn
            messages.append({
                "role": "assistant",
                "content": chain_of_thought if chain_of_thought and chain_of_thought.strip() else None,
                "tool_calls": tool_calls
            })

            for call in tool_calls:
                func_name = call.get("function", {}).get("name")
                tool_call_id = call.get("id", "call_xyz")
                try:
                    args = json.loads(call.get("function", {}).get("arguments", "{}"))
                except:
                    args = {}

                if func_name == "kickoff_background_research":
                    desc = args.get("description", content)
                    scope = args.get("target_scope", "codebase")
                    task = storage.tasks.create(
                        title=f"Research [{scope.upper()}]: {desc[:50]}",
                        description=desc,
                        session_id=session_id,
                        state=TaskState.PENDING,
                        constraints={"target_scope": scope}
                    )
                    task.type = TaskType.RESEARCH
                    storage.commit()
                    await _worker.enqueue(task.task_id)
                    async_task_ids.append(task.task_id)
                    tool_content = f"Successfully enqueued background research task {task.task_id}."

                elif func_name == "kickoff_swarm_task":
                    title = args.get("title", f"Auto-Task: {content[:30]}...")
                    desc = args.get("description", content)
                    task = storage.tasks.create(
                        title=title,
                        description=desc,
                        session_id=session_id,
                        state=TaskState.PENDING,
                    )
                    storage.commit()
                    await _worker.enqueue(task.task_id)
                    async_task_ids.append(task.task_id)
                    tool_content = f"Successfully enqueued swarm implementation task {task.task_id}."

                elif func_name == "search_web":
                    query = args.get("query")
                    # ASYNC FIX: Offload web search to background
                    logger.info(f"Offloading web search to Strata worker: {query}")
                    task = storage.tasks.create(
                        title=f"Web Search: {query}",
                        description=f"Perform a targeted web search for: {query}. Synthesize the results and provide a concise answer.",
                        session_id=session_id,
                        state=TaskState.PENDING,
                        constraints={"target_scope": "web"}
                    )
                    task.type = TaskType.RESEARCH
                    storage.commit()
                    await _worker.enqueue(task.task_id)
                    
                    tool_content = f"I am searching the web for '{query}'. I will synthesize the findings and post them here shortly."
                    tool_outputs_generated = True

                elif func_name == "check_swarm_status":
                    target_id = args.get("task_id")
                    if target_id:
                        tasks = storage.session.query(TaskModel).filter(TaskModel.task_id == target_id).all()
                    else:
                        tasks = storage.session.query(TaskModel).filter(TaskModel.state != TaskState.COMPLETE).all()
                    
                    if not tasks:
                        tool_content = "No active or matching tasks found in the database."
                    else:
                        lines = [f"- {t.title} ({t.task_id}): {t.state.value}" for t in tasks]
                        tool_content = "Current Swarm Status:\n" + "\n".join(lines)
                    tool_outputs_generated = True

                elif func_name == "amend_project_spec":
                    amendment = args.get("amendment")
                    spec_path = ".knowledge/specs/project_spec.md"
                    from datetime import datetime
                    os.makedirs(".knowledge/specs", exist_ok=True)
                    with open(spec_path, "a") as f:
                        f.write(f"\n- [Update {datetime.utcnow().isoformat()}]: {amendment}")
                    tool_content = "Project specification successfully amended."
                    tool_outputs_generated = True
                
                else:
                    tool_content = f"Error: Tool '{func_name}' not implemented."

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": func_name,
                    "content": tool_content
                })

            if tool_outputs_generated:
                # If we have immediate data (search or status), loop back so the LLM can respond to them
                iteration += 1
                continue
            else:
                # If only async tasks were kicked off, we can finish immediately
                return {"status": "ok", "reply": chain_of_thought.strip(), "task_ids": async_task_ids}
                
        else:
            # No tool calls, we either got an answer or a fallback error
            final_reply = model_response.get("content", "I encountered an error processing that.")
            storage.messages.create(role="assistant", content=final_reply, session_id=session_id)
            storage.commit()
            return {"status": "ok", "reply": final_reply}
            
    # If the loop exhausted its iterations, force synthesis
    messages.append({
        "role": "system",
        "content": "You have reached the tool call limit. You MUST synthesize the data gathered so far and reply to the user immediately. Do not attempt further tool calls."
    })
    final_response = await _model.chat(messages) # Strip the tools, force an answer
    reply = final_response.get("content", "I hit the maximum iteration limit for synchronous tool usage without reaching a conclusion.")
    
    # Rare fallback
    if not reply or not reply.strip():
        reply = "I couldn't synthesize the final results."
        
    storage.messages.create(role="assistant", content=reply, session_id=session_id)
    storage.commit()
    return {"status": "ok", "reply": reply}


@app.post("/tasks")
async def create_task(task_data: Dict[str, Any], storage: StorageManager = Depends(get_storage)):
    task = storage.tasks.create(**task_data)
    storage.commit()
    return {"id": task.task_id, "status": task.state.value}


@app.post("/tasks/{task_id}/intervene")
async def task_intervene(task_id: str, payload: Dict[str, Any], storage: StorageManager = Depends(get_storage)):
    """
    @summary Resolve a blocked task with human override context.
    """
    task = storage.tasks.get_by_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    override = payload.get("override")
    if not override:
        raise HTTPException(status_code=400, detail="Override content required")
        
    # Append intervention to description to provide context to the agent
    task.description += f"\n\n[USER INTERVENTION]: {override}"
    task.state = TaskState.PENDING
    task.human_intervention_required = False
    storage.commit()
    
    # Re-enqueue the task for the background worker
    await _worker.enqueue(task.task_id)
    
    # Log it to the chat for transparency
    storage.messages.create(
        role="user",
        content=f"Sub-agent intervention for task '{task.title}': {override}",
        session_id=task.session_id or "default",
        is_intervention=True,
        task_id=task.task_id
    )
    storage.commit()
    
    return {"status": "ok"}


# ── Models ─────────────────────────────────────────────────────────────────────

@app.get("/models")
async def list_models():
    import httpx
    base = _model.endpoint.rsplit("/v1/", 1)[0]
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{base}/v1/models", timeout=5.0)
            resp.raise_for_status()
            data = resp.json()
            models = [m.get("id", m.get("name", "unknown")) for m in data.get("data", [])]
            return {"status": "ok", "models": models, "current": _model.active_model}
    except Exception as e:
        return {"status": "error", "models": [], "message": str(e)}


@app.post("/models/select")
async def select_model(payload: Dict[str, Any]):
    model_id = payload.get("model")
    if not model_id:
        raise HTTPException(status_code=400, detail="model field required")
    _model.active_model = model_id
    return {"status": "ok", "model": model_id}


# ── Admin / Diagnostics ────────────────────────────────────────────────────────

@app.get("/admin/test")
async def test_connectivity():
    from datetime import datetime
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "llm_endpoint": _model.endpoint
    }

@app.get("/admin/settings")
async def get_settings(storage: StorageManager = Depends(get_storage)):
    persisted_settings = storage.parameters.get_parameter(
        key=SETTINGS_PARAMETER_KEY,
        default_value=dict(GLOBAL_SETTINGS),
        description=SETTINGS_PARAMETER_DESCRIPTION,
    ) or {}
    merged_settings = _normalized_settings(persisted_settings)
    GLOBAL_SETTINGS.update(merged_settings)
    storage.commit()
    return {"status": "ok", "settings": merged_settings}

@app.post("/admin/settings")
async def update_settings(payload: Dict[str, Any], storage: StorageManager = Depends(get_storage)):
    merged_settings = _normalized_settings(payload)
    GLOBAL_SETTINGS.update(merged_settings)
    storage.parameters.set_parameter(
        key=SETTINGS_PARAMETER_KEY,
        value=merged_settings,
        description=SETTINGS_PARAMETER_DESCRIPTION,
    )
    storage.commit()
    return {"status": "ok", "settings": merged_settings}

@app.get("/admin/registry")
async def get_registry():
    from strata.models.registry import registry
    return {"status": "ok", "config": registry.to_dict()}

@app.get("/admin/registry/presets")
async def get_registry_presets():
    from strata.models.registry import registry
    return {"status": "ok", "presets": registry.presets()}

@app.post("/admin/registry")
async def update_registry(payload: Dict[str, Any]):
    from strata.models.registry import registry
    registry._load_config(payload)
    return {"status": "ok"}

@app.get("/admin/health")
async def health_check():
    """
    @summary Deep health check of the orchestrator substrate.
    """
    from sqlalchemy import text
    storage = StorageManager()
    try:
        # Check DB
        db = storage.session
        db.execute(text("SELECT 1"))
        
        # Check Worker
        worker_alive = _worker._running_task is not None and not _worker._running_task.done()
        
        return {
            "status": "ok",
            "database": "connected",
            "worker": "running" if worker_alive else "dead",
        }
    except Exception as e:
        return {
            "status": "degraded",
            "error": str(e)
        }
    finally:
        storage.close()

@app.get("/admin/telemetry")
async def get_telemetry(limit: int = 25, storage: StorageManager = Depends(get_storage)):
    """
    @summary Return bootstrap-oriented telemetry for the UI and agent introspection.
    """
    safe_limit = max(1, min(limit, 100))
    return {"status": "ok", "telemetry": build_telemetry_snapshot(storage, limit=safe_limit)}

@app.get("/admin/dashboard")
async def get_dashboard(limit: int = 10, storage: StorageManager = Depends(get_storage)):
    safe_limit = max(1, min(limit, 50))
    return {"status": "ok", "dashboard": _build_dashboard_snapshot(storage, limit=safe_limit)}

@app.get("/admin/providers/telemetry")
async def get_provider_telemetry(storage: StorageManager = Depends(get_storage)):
    providers = get_provider_telemetry_snapshot()
    if providers:
        storage.parameters.set_parameter(
            key="provider_transport_telemetry_snapshot",
            value=providers,
            description="Last persisted provider transport telemetry snapshot."
        )
        storage.commit()
        return {"status": "ok", "providers": providers, "source": "live"}

    persisted = storage.parameters.get_parameter(
        key="provider_transport_telemetry_snapshot",
        default_value={},
        description="Last persisted provider transport telemetry snapshot."
    ) or {}
    return {"status": "ok", "providers": persisted, "source": "persisted"}

@app.post("/admin/benchmark/run")
async def run_benchmark_suite(payload: Dict[str, Any] | None = None, storage: StorageManager = Depends(get_storage)):
    payload = payload or {}
    candidate_change_id = payload.get("candidate_change_id", "baseline")
    api_url = payload.get("api_url", "http://127.0.0.1:8000")
    run_count = max(1, int(payload.get("run_count", 1) or 1))
    eval_harness_config_override = payload.get("eval_harness_config_override")
    reports = []
    for run_index in range(run_count):
        report = await run_benchmark(
            api_url=api_url,
            run_label=f"{candidate_change_id}-benchmark-{run_index + 1}",
            eval_harness_config_override=eval_harness_config_override,
        )
        persist_benchmark_report(
            storage,
            report,
            candidate_change_id=candidate_change_id,
            run_mode="weak_eval" if candidate_change_id != "baseline" else "baseline",
            model_id="benchmark/harness",
        )
        reports.append(report)
    return {"status": "ok", "reports": reports, "candidate_change_id": candidate_change_id, "run_count": run_count}

@app.post("/admin/experiments/benchmark")
async def run_benchmark_experiment(payload: Dict[str, Any], storage: StorageManager = Depends(get_storage)):
    candidate_change_id = payload.get("candidate_change_id")
    if not candidate_change_id:
        raise HTTPException(status_code=400, detail="candidate_change_id field required")
    api_url = payload.get("api_url", "http://127.0.0.1:8000")
    baseline_change_id = payload.get("baseline_change_id", "baseline")
    run_count = max(1, int(payload.get("run_count", 1) or 1))
    eval_harness_config_override = payload.get("eval_harness_config_override")
    proposal_metadata = payload.get("proposal_metadata")
    runner = ExperimentRunner(storage, _model)
    result = await runner.run_benchmark_gate(
        candidate_change_id,
        api_url=api_url,
        baseline_change_id=baseline_change_id,
        run_count=run_count,
        eval_harness_config_override=eval_harness_config_override,
        proposal_metadata=proposal_metadata,
    )
    return {"status": "ok", "result": result.model_dump()}

@app.post("/admin/evals/run")
async def run_structured_eval_suite(payload: Dict[str, Any] | None = None, storage: StorageManager = Depends(get_storage)):
    payload = payload or {}
    candidate_change_id = payload.get("candidate_change_id", "baseline")
    suite_name = payload.get("suite_name", "bootstrap_mcq_v1")
    api_url = payload.get("api_url", "http://127.0.0.1:8000")
    cases = payload.get("cases")
    run_count = max(1, int(payload.get("run_count", 1) or 1))
    eval_harness_config_override = payload.get("eval_harness_config_override")
    reports = []
    for run_index in range(run_count):
        report = await run_structured_eval(
            api_url=api_url,
            suite_name=suite_name,
            cases=cases,
            run_label=f"{candidate_change_id}-structured-{run_index + 1}",
            eval_harness_config_override=eval_harness_config_override,
        )
        persist_structured_eval_report(
            storage,
            report,
            candidate_change_id=candidate_change_id,
            run_mode="weak_eval" if candidate_change_id != "baseline" else "baseline",
            model_id="structured_eval/harness",
        )
        reports.append(report)
    return {"status": "ok", "reports": reports, "candidate_change_id": candidate_change_id, "run_count": run_count}

@app.post("/admin/experiments/full_eval")
async def run_full_eval_experiment(payload: Dict[str, Any], storage: StorageManager = Depends(get_storage)):
    candidate_change_id = payload.get("candidate_change_id")
    if not candidate_change_id:
        raise HTTPException(status_code=400, detail="candidate_change_id field required")
    api_url = payload.get("api_url", "http://127.0.0.1:8000")
    baseline_change_id = payload.get("baseline_change_id", "baseline")
    suite_name = payload.get("suite_name", "bootstrap_mcq_v1")
    run_count = max(1, int(payload.get("run_count", 1) or 1))
    eval_harness_config_override = payload.get("eval_harness_config_override")
    proposal_metadata = payload.get("proposal_metadata")
    runner = ExperimentRunner(storage, _model)
    result = await runner.run_full_eval_gate(
        candidate_change_id,
        api_url=api_url,
        baseline_change_id=baseline_change_id,
        suite_name=suite_name,
        run_count=run_count,
        eval_harness_config_override=eval_harness_config_override,
        proposal_metadata=proposal_metadata,
    )
    return {"status": "ok", "result": result.model_dump()}

@app.get("/admin/experiments/compare")
async def compare_experiment_metrics(
    candidate_change_id: str,
    baseline_change_id: str = "baseline",
    storage: StorageManager = Depends(get_storage),
):
    runner = ExperimentRunner(storage, _model)
    persisted_report = runner.get_persisted_experiment_report(candidate_change_id)
    if persisted_report and persisted_report.get("baseline_change_id") == baseline_change_id:
        return {
            "status": "ok",
            "source": "persisted_report",
            **persisted_report,
        }
    candidate_metrics = runner._gather_metrics(candidate_change_id)
    baseline_metrics = runner._gather_metrics(baseline_change_id)
    deltas = runner._calculate_deltas(baseline_metrics, candidate_metrics)
    recommendation = runner._decide_benchmark_promotion(deltas)
    return {
        "status": "ok",
        "source": "aggregate_metrics",
        "candidate_change_id": candidate_change_id,
        "baseline_change_id": baseline_change_id,
        "baseline_metrics": baseline_metrics,
        "candidate_metrics": candidate_metrics,
        "deltas": deltas,
        "recommendation": recommendation,
    }

@app.get("/admin/experiments/report")
async def get_experiment_report(
    candidate_change_id: str,
    storage: StorageManager = Depends(get_storage),
):
    runner = ExperimentRunner(storage, _model)
    report = runner.get_persisted_experiment_report(candidate_change_id)
    if not report:
        raise HTTPException(status_code=404, detail="No persisted experiment report found for candidate_change_id")
    return {"status": "ok", "report": report}

@app.get("/admin/experiments/history")
async def get_experiment_history(limit: int = 25, storage: StorageManager = Depends(get_storage)):
    safe_limit = max(1, min(limit, 100))
    rows = (
        storage.session.query(ParameterModel)
        .filter(ParameterModel.key.like("experiment_report:%"))
        .order_by(ParameterModel.updated_at.desc())
        .limit(safe_limit)
        .all()
    )
    history = []
    for current in iter_experiment_reports(rows):
        readiness = current.get("promotion_readiness") or {}
        history.append(
            {
                "candidate_change_id": current.get("candidate_change_id"),
                "evaluation_kind": current.get("evaluation_kind"),
                "recommendation": current.get("recommendation"),
                "recorded_at": current.get("recorded_at"),
                "proposal_metadata": current.get("proposal_metadata") or {},
                "promotion_readiness": readiness,
                "has_eval_harness_override": bool(current.get("eval_harness_config_override")),
                "has_code_validation": bool(current.get("code_validation")),
            }
        )
    promoted_state = storage.parameters.peek_parameter(
        "promoted_eval_candidates",
        default_value={"current": None, "history": []},
    ) or {"current": None, "history": []}
    return {
        "status": "ok",
        "current_promoted_candidate": promoted_state.get("current"),
        "promotion_history": promoted_state.get("history", []),
        "reports": history,
    }

@app.get("/admin/evals/config")
async def get_eval_harness_config(storage: StorageManager = Depends(get_storage)):
    active_config = storage.parameters.peek_parameter(
        EVAL_HARNESS_CONFIG_KEY,
        default_value=default_eval_harness_config(),
    ) or default_eval_harness_config()
    return {"status": "ok", "config": active_config}

@app.post("/admin/evals/config")
async def set_eval_harness_config(payload: Dict[str, Any], storage: StorageManager = Depends(get_storage)):
    system_prompt = payload.get("system_prompt")
    context_files = payload.get("context_files")
    current_config = get_active_eval_harness_config()
    if system_prompt:
        current_config["system_prompt"] = str(system_prompt)
    if context_files:
        current_config["context_files"] = [str(path) for path in context_files]
    storage.parameters.set_parameter(
        EVAL_HARNESS_CONFIG_KEY,
        current_config,
        description=EVAL_HARNESS_CONFIG_DESCRIPTION,
    )
    storage.commit()
    return {"status": "ok", "config": current_config}

@app.post("/admin/experiments/promote")
async def promote_experiment_candidate(payload: Dict[str, Any], storage: StorageManager = Depends(get_storage)):
    candidate_change_id = payload.get("candidate_change_id")
    if not candidate_change_id:
        raise HTTPException(status_code=400, detail="candidate_change_id field required")
    force = bool(payload.get("force", False))
    result = _apply_experiment_promotion(storage, candidate_change_id, force=force)
    return {"status": "ok", **result}

@app.post("/admin/experiments/bootstrap_cycle")
async def run_bootstrap_cycle(payload: Dict[str, Any] | None = None, storage: StorageManager = Depends(get_storage)):
    payload = payload or {}
    proposer_tiers = [str(tier).lower() for tier in payload.get("proposer_tiers", ["weak", "strong"])]
    proposer_tiers = [tier for tier in proposer_tiers if tier in {"weak", "strong"}]
    if not proposer_tiers:
        raise HTTPException(status_code=400, detail="At least one proposer tier must be 'weak' or 'strong'")

    auto_promote = bool(payload.get("auto_promote", True))
    suite_name = payload.get("suite_name", "bootstrap_mcq_v1")
    run_count = max(1, int(payload.get("run_count", 2) or 2))
    baseline_change_id = payload.get("baseline_change_id", "baseline")
    current_config = get_active_eval_harness_config()

    proposals = await asyncio.gather(*[
        _generate_eval_candidate_from_tier(tier, current_config)
        for tier in proposer_tiers
    ])

    runner = ExperimentRunner(storage, _model)
    evaluated = []
    promoted = []

    for proposal in proposals:
        result = await runner.run_full_eval_gate(
            proposal["candidate_change_id"],
            api_url="http://127.0.0.1:8000",
            baseline_change_id=baseline_change_id,
            suite_name=suite_name,
            run_count=run_count,
            eval_harness_config_override=proposal["eval_harness_config_override"],
            proposal_metadata={
                "proposer_tier": proposal["proposer_tier"],
                "rationale": proposal["rationale"],
                "expected_gain": proposal["expected_gain"],
                "source": "bootstrap_cycle",
            },
        )
        result_payload = result.model_dump()
        evaluated.append({
            "proposal": proposal,
            "result": result_payload,
        })
        if auto_promote and result.recommendation == "promote":
            promoted.append(_apply_experiment_promotion(storage, proposal["candidate_change_id"]))

    return {
        "status": "ok",
        "current_eval_harness_config": current_config,
        "evaluated": evaluated,
        "promoted": promoted,
        "auto_promote": auto_promote,
    }

@app.post("/admin/experiments/tool_cycle")
async def run_tool_bootstrap_cycle(payload: Dict[str, Any] | None = None, storage: StorageManager = Depends(get_storage)):
    payload = payload or {}
    proposer_tiers = [str(tier).lower() for tier in payload.get("proposer_tiers", ["weak"])]
    proposer_tiers = [tier for tier in proposer_tiers if tier in {"weak", "strong"}]
    if not proposer_tiers:
        raise HTTPException(status_code=400, detail="At least one proposer tier must be 'weak' or 'strong'")
    tool_name = _slugify_candidate_suffix(str(payload.get("tool_name", "bootstrap_history_tool")))
    task_description = str(
        payload.get(
            "task_description",
            "Create a read-only dynamic tool that helps operators inspect bootstrap history and promotion readiness.",
        )
    )
    proposals = await asyncio.gather(*[
        _generate_tool_candidate_from_tier(
            tier,
            tool_name=tool_name,
            task_description=task_description,
        )
        for tier in proposer_tiers
    ])
    pipeline = ToolsPromotionPipeline(storage)
    evaluated = []
    for proposal in proposals:
        os.makedirs("strata/tools", exist_ok=True)
        os.makedirs("strata/tools/manifests", exist_ok=True)
        os.makedirs("strata/tools/tests", exist_ok=True)
        experimental_path = os.path.join("strata/tools", f"{proposal['tool_name']}.experimental.py")
        manifest_path = os.path.join("strata/tools/manifests", f"{proposal['tool_name']}.json")
        smoke_path = os.path.join("strata/tools/tests", f"test_{proposal['tool_name']}_smoke.py")
        with open(experimental_path, "w", encoding="utf-8") as handle:
            handle.write(proposal["source"])
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "validator": (proposal["manifest"] or {}).get("validator", "python_import_only"),
                    "smoke_test": (proposal["manifest"] or {}).get("smoke_test", smoke_path),
                    "proposer_tier": proposal["proposer_tier"],
                },
                handle,
                indent=2,
            )
        with open(smoke_path, "w", encoding="utf-8") as handle:
            handle.write(proposal["smoke_test"])
        validation = await pipeline.validate_and_promote(proposal["tool_name"])
        result = ExperimentRunner(storage, _model).record_tool_promotion_result(
            candidate_change_id=proposal["candidate_change_id"],
            validation_result=validation.model_dump(),
            proposal_metadata={
                "proposer_tier": proposal["proposer_tier"],
                "tool_name": proposal["tool_name"],
                "rationale": proposal["rationale"],
                "expected_gain": proposal["expected_gain"],
                "source": "tool_cycle",
            },
        )
        evaluated.append(
            {
                "proposal": proposal,
                "validation": validation.model_dump(),
                "result": result.model_dump(),
            }
        )
    return {"status": "ok", "evaluated": evaluated}

@app.get("/admin/experiments/secondary_ignition")
async def get_secondary_ignition_status(storage: StorageManager = Depends(get_storage)):
    promoted_state = storage.parameters.peek_parameter(
        "promoted_eval_candidates",
        default_value={"current": None, "history": []},
    ) or {"current": None, "history": []}
    current_candidate = promoted_state.get("current")
    runner = ExperimentRunner(storage, _model)
    report_rows = (
        storage.session.query(ParameterModel)
        .filter(ParameterModel.key.like("experiment_report:%"))
        .order_by(ParameterModel.updated_at.desc())
        .all()
    )
    matching_report = None
    for report in iter_experiment_reports(report_rows):
        proposal_metadata = report.get("proposal_metadata") or {}
        recommendation = report.get("recommendation")
        weak_gain = report_has_weak_gain(report)
        if proposal_metadata.get("proposer_tier") == "weak" and recommendation == "promote" and weak_gain:
            matching_report = report
            break

    if current_candidate:
        current_report = runner.get_persisted_experiment_report(current_candidate)
    else:
        current_report = None

    if matching_report:
        return {
            "status": "ok",
            "detected": True,
            "candidate_change_id": matching_report.get("candidate_change_id"),
            "current_promoted_candidate": current_candidate,
            "recommendation": matching_report.get("recommendation"),
            "proposal_metadata": matching_report.get("proposal_metadata") or {},
            "weak_gain_detected": True,
            "reason": "Weak-originated candidate was promoted after improving weak-tier eval metrics.",
        }

    if not current_candidate:
        return {
            "status": "ok",
            "detected": False,
            "reason": "No promoted eval candidate is currently active.",
        }

    report = current_report
    if not report:
        return {
            "status": "ok",
            "detected": False,
            "candidate_change_id": current_candidate,
            "reason": "No persisted experiment report found for the current promoted candidate.",
        }

    proposal_metadata = report.get("proposal_metadata") or {}
    recommendation = report.get("recommendation")
    weak_gain = report_has_weak_gain(report)
    return {
        "status": "ok",
        "detected": False,
        "candidate_change_id": current_candidate,
        "recommendation": recommendation,
        "proposal_metadata": proposal_metadata,
        "weak_gain_detected": weak_gain,
        "reason": "Secondary ignition has not been detected yet for the current promoted candidate.",
    }

@app.get("/admin/logs")
async def get_logs(limit: int = 50):
    """
    @summary Fetch the tail of the backend log for UI debugging.
    """
    log_path = "/tmp/strata_backend.log"
    if not os.path.exists(log_path):
        return {"logs": ["Log file not found."]}
    
    with open(log_path, "r") as f:
        lines = f.readlines()
        return {"logs": [line.strip() for line in lines[-limit:]]}

@app.post("/admin/reboot")
async def reboot_api():
    """
    @summary Restart the backend process by replacing itself.
    """
    import sys
    print("REBOOTING API PROCESS...")
    # Give the response a moment to send
    async def restart_soon():
        await asyncio.sleep(0.5)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    
    asyncio.create_task(restart_soon())
    return {"status": "rebooting"}

@app.get("/admin/files")
async def list_experimental_files():
    """List all .experimental.py files available for promotion."""
    return {"files": _hotreloader.list_experimental()}


@app.post("/admin/promote")
async def promote_file(payload: Dict[str, Any]):
    """
    @summary Validate and promote an experimental module to live.
    @inputs { "module": "strata.api.main" }
    @side_effects replaces live file, triggers SIGHUP, rolls back on failure
    """
    module = payload.get("module")
    if not module:
        raise HTTPException(status_code=400, detail="module field required")
    result = await _hotreloader.promote(module)
    return {
        "success": result.success,
        "module": result.module,
        "rolled_back": result.rolled_back,
        "message": result.message,
        "validation": result.validation.stages if result.validation else None,
    }


@app.post("/admin/rollback")
async def rollback_file(payload: Dict[str, Any]):
    """Manually restore the .live.bak for a module."""
    module = payload.get("module")
    if not module:
        raise HTTPException(status_code=400, detail="module field required")
    result = _hotreloader.rollback(module)
    return {"success": result.success, "module": result.module, "message": result.message}


@app.post("/admin/reset")
async def reset_database(storage: StorageManager = Depends(get_storage)):
    from strata.storage.models import Base
    storage.session.close()
    Base.metadata.drop_all(storage.engine)
    Base.metadata.create_all(storage.engine)
    storage.session = storage.SessionLocal()
    storage.tasks.session = storage.session
    storage.messages.session = storage.session
    storage.attempts.session = storage.session
    storage.parameters.session = storage.session
    return {"status": "ok", "message": "Database reset complete."}


@app.get("/admin/worker/status")
async def get_worker_status():
    return {"status": _worker.status}

@app.post("/admin/worker/pause")
async def pause_worker():
    _worker.pause()
    return {"status": "paused"}

@app.post("/admin/worker/resume")
async def resume_worker():
    _worker.resume()
    return {"status": "running"}

@app.post("/admin/worker/stop")
async def stop_worker():
    aborted = _worker.stop_current()
    return {"status": "stopped", "aborted": aborted}


@app.get("/events")
async def sse_events():
    """Stream events to the UI for real-time reactivity."""
    async def event_generator():
        while True:
            try:
                data = await _event_queue.get()
                yield f"data: {json.dumps(data)}\n\n"
            except Exception as e:
                logger.error(f"SSE disconnection: {e}")
                break
    return StreamingResponse(event_generator(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("strata.api.main:app", host="0.0.0.0", port=8000, reload=True)
