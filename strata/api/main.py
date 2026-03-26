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
import time
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
from strata.eval.matrix import run_eval_matrix
from strata.eval.harness_eval import (
    EVAL_HARNESS_CONFIG_DESCRIPTION,
    EVAL_HARNESS_CONFIG_KEY,
    default_eval_harness_config,
    get_active_eval_harness_config,
)
from strata.knowledge.pages import KnowledgePageStore, slugify_page_title
from strata.specs.bootstrap import (
    create_spec_proposal,
    ensure_spec_files,
    get_spec_proposal,
    list_spec_proposals,
    load_specs,
    resolve_spec_proposal,
    resubmit_spec_proposal_with_clarification,
)
from strata.experimental.experiment_runner import (
    ExperimentRunner,
    iter_experiment_reports,
    normalize_experiment_report,
    report_has_weak_gain,
)
from strata.orchestrator.tools_pipeline import ToolsPromotionPipeline
from strata.orchestrator.user_questions import (
    enqueue_user_question,
    get_active_question,
    get_question_for_source,
    mark_question_asked,
    resolve_question,
)
import importlib.util
import glob
import re
import subprocess

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
            "name": "list_knowledge_pages",
            "description": "List synthesized knowledge pages by metadata only. Use this before loading a full page when you need to find relevant existing knowledge.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Optional query to filter pages by title, summary, alias, or tag."},
                    "tag": {"type": "string", "description": "Optional tag filter."},
                    "domain": {"type": "string", "description": "Optional domain filter.", "enum": ["system", "agent", "user", "contacts", "project", "world"]},
                    "limit": {"type": "integer", "description": "Maximum number of page metadata results to return."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_knowledge_page_metadata",
            "description": "Fetch metadata for a synthesized knowledge page without loading the full page body.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string", "description": "Knowledge page slug."}
                },
                "required": ["slug"]
            }
        }
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
                    "heading": {"type": "string", "description": "Optional heading to fetch a specific section only."}
                },
                "required": ["slug"]
            }
        }
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
                    "domain": {"type": "string", "description": "Knowledge domain for the target page.", "enum": ["system", "agent", "user", "contacts", "project", "world"]},
                    "target_scope": {"type": "string", "description": "Whether to inspect the codebase or the public web.", "enum": ["codebase", "web"]},
                    "evidence_hints": {"type": "array", "items": {"type": "string"}, "description": "Optional hints about missing, stale, or contradictory evidence."}
                },
                "required": ["slug", "reason"]
            }
        }
    },
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
            "name": "read_spec",
            "description": "Read one of the durable Strata spec files. Use this before proposing changes to the system's long-term intent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "description": "Which spec to read.", "enum": ["global", "project"]}
                },
                "required": ["scope"]
            }
        }
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
                    "user_signal": {"type": "string", "description": "The user statement or intent that triggered this proposal."}
                },
                "required": ["scope", "proposed_change", "rationale"]
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
    ensure_spec_files()
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


def _find_pending_spec_clarification(storage: StorageManager, session_id: str) -> Optional[Dict[str, Any]]:
    pending = get_active_question(storage, session_id)
    if not pending or pending.get("source_type") != "spec_clarification":
        return None
    proposal_id = str(pending.get("source_id") or "")
    if not proposal_id:
        return None
    proposal = get_spec_proposal(storage, proposal_id)
    if proposal and proposal.get("status") == "needs_clarification":
        proposal = dict(proposal)
        proposal["_pending_question"] = pending
        return proposal
    return None


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
        "human_intervention_required": t.human_intervention_required,
        "pending_question": (
            get_question_for_source(storage, source_type="task_blocked", source_id=t.task_id).get("question")
            if t.human_intervention_required else None
        ),
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


@app.get("/admin/specs")
async def get_specs():
    return {"status": "ok", "specs": load_specs()}


@app.get("/admin/spec_proposals")
async def get_spec_proposals(status: Optional[str] = None, limit: int = 50, storage: StorageManager = Depends(get_storage)):
    proposals = list_spec_proposals(storage, status=status, limit=limit)
    return {"status": "ok", "proposals": proposals}


@app.get("/admin/spec_proposals/{proposal_id}")
async def get_spec_proposal_detail(proposal_id: str, storage: StorageManager = Depends(get_storage)):
    proposal = get_spec_proposal(storage, proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Spec proposal not found")
    return {"status": "ok", "proposal": proposal}


@app.post("/admin/spec_proposals")
async def create_spec_proposal_endpoint(payload: Dict[str, Any], storage: StorageManager = Depends(get_storage)):
    scope = str(payload.get("scope") or "project")
    proposed_change = str(payload.get("proposed_change") or "").strip()
    rationale = str(payload.get("rationale") or "").strip()
    if not proposed_change or not rationale:
        raise HTTPException(status_code=400, detail="proposed_change and rationale are required")
    proposal = create_spec_proposal(
        storage,
        scope=scope,
        proposed_change=proposed_change,
        rationale=rationale,
        user_signal=str(payload.get("user_signal") or ""),
        session_id=payload.get("session_id"),
        source=str(payload.get("source") or "api"),
        review_task_id=payload.get("review_task_id"),
    )
    storage.commit()
    return {"status": "ok", "proposal": proposal}


@app.post("/admin/spec_proposals/{proposal_id}/resolve")
async def resolve_spec_proposal_endpoint(proposal_id: str, payload: Dict[str, Any], storage: StorageManager = Depends(get_storage)):
    resolution = str(payload.get("resolution") or "").strip()
    if not resolution:
        raise HTTPException(status_code=400, detail="resolution is required")
    try:
        proposal = resolve_spec_proposal(
            storage,
            proposal_id=proposal_id,
            resolution=resolution,
            reviewer_notes=str(payload.get("reviewer_notes") or ""),
            clarification_request=str(payload.get("clarification_request") or ""),
            reviewer=str(payload.get("reviewer") or "operator"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not proposal:
        raise HTTPException(status_code=404, detail="Spec proposal not found")
    if proposal.get("status") == "needs_clarification" and proposal.get("session_id"):
        enqueue_user_question(
            storage,
            session_id=proposal.get("session_id") or "default",
            question=proposal.get("clarification_request") or "More detail is required before this spec change can be reviewed.",
            source_type="spec_clarification",
            source_id=proposal_id,
            context={
                "scope": proposal.get("scope"),
                "proposed_change": proposal.get("proposed_change"),
            },
        )
    storage.commit()
    return {"status": "ok", "proposal": proposal}


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
    knowledge_pages = KnowledgePageStore(storage)

    # 1. Persist user message immediately so polling sees it
    storage.messages.create(role=payload["role"], content=content, session_id=session_id)
    storage.commit()

    pending_spec_proposal = _find_pending_spec_clarification(storage, session_id)
    if pending_spec_proposal and payload.get("role") == "user" and pending_spec_proposal.get("_pending_question", {}).get("status") == "asked":
        updated_proposal = resubmit_spec_proposal_with_clarification(
            storage,
            proposal_id=pending_spec_proposal["proposal_id"],
            clarification_response=content,
            source="user",
        )
        current_specs = load_specs()
        scope = updated_proposal.get("scope", "project")
        current_spec = current_specs.get("global_spec" if scope == "global" else "project_spec", "")
        task = storage.tasks.create(
            title=f"Spec Clarification Review ({scope.title()}): {updated_proposal.get('proposal_id')}",
            description=(
                f"Re-review a spec proposal after user clarification.\n\n"
                f"Current {scope} spec:\n{current_spec}\n\n"
                f"Proposed change:\n{updated_proposal.get('proposed_change')}\n\n"
                f"Rationale:\n{updated_proposal.get('rationale')}\n\n"
                f"Accumulated user signal:\n{updated_proposal.get('user_signal')}\n\n"
                "Tasks:\n"
                "1. Re-check contradictions or ambiguity.\n"
                "2. If still unresolved, produce a tighter clarification request.\n"
                "3. If resolved, recommend a clean spec delta without directly editing the spec.\n"
            ),
            session_id=session_id,
            state=TaskState.PENDING,
            constraints={
                "target_scope": "codebase",
                "spec_operation": "review_proposal",
                "spec_scope": scope,
                "spec_proposal_id": updated_proposal["proposal_id"],
                "proposed_change": updated_proposal.get("proposed_change"),
                "rationale": updated_proposal.get("rationale"),
                "user_signal": updated_proposal.get("user_signal"),
            },
        )
        task.type = TaskType.RESEARCH
        storage.commit()
        await _worker.enqueue(task.task_id)
        resolve_question(
            storage,
            pending_spec_proposal["_pending_question"]["question_id"],
            resolution="resolved",
            response=content,
        )
        storage.messages.create(
            role="assistant",
            content=(
                f"I’ve attached your clarification to spec proposal {updated_proposal['proposal_id']} "
                f"and kicked off a fresh review task ({task.task_id})."
            ),
            session_id=session_id,
        )
        storage.commit()
        return {
            "status": "ok",
            "reply": (
                f"I’ve attached your clarification to spec proposal {updated_proposal['proposal_id']} "
                f"and kicked off a fresh review."
            ),
            "spec_proposal_id": updated_proposal["proposal_id"],
            "task_id": task.task_id,
        }

    pending_question = get_active_question(storage, session_id)
    if pending_question and payload.get("role") == "user" and pending_question.get("status") == "asked":
        if pending_question.get("source_type") == "task_blocked":
            task_id = str(pending_question.get("source_id") or "")
            task = storage.tasks.get_by_id(task_id)
            if task:
                task.description = (task.description or "") + f"\n\nUser clarification:\n{content.strip()}"
                task.human_intervention_required = False
                task.state = TaskState.PENDING
                storage.commit()
                await _worker.enqueue(task.task_id)
                resolve_question(storage, pending_question["question_id"], resolution="resolved", response=content)
                storage.messages.create(
                    role="assistant",
                    content=f"I’ve attached your clarification to task {task.task_id} and re-queued it.",
                    session_id=session_id,
                )
                storage.commit()
                return {
                    "status": "ok",
                    "reply": f"I’ve attached your clarification to task {task.task_id} and re-queued it.",
                    "task_id": task.task_id,
                }

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
If the user expresses a durable desired future state, persistent preference, or architectural constraint, prefer `propose_spec_update` over casual implementation drift. Durable intent should be reviewed against the spec, not silently improvised.
{memory_context}

Available Tools:
{tool_summary_text}
"""
        }
    ]
    if pending_question and payload.get("role") == "user" and pending_question.get("status") == "pending":
        messages.append(
            {
                "role": "system",
                "content": (
                    "Internal pending user question:\n"
                    f"- source_type: {pending_question.get('source_type')}\n"
                    f"- question: {pending_question.get('question')}\n\n"
                    "Before doing anything else, ask the user this question naturally and briefly. "
                    "Do not mention internal queues or implementation details."
                ),
            }
        )
        mark_question_asked(storage, pending_question["question_id"])
        storage.commit()
    
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

                elif func_name == "list_knowledge_pages":
                    pages = knowledge_pages.list_pages(
                        query=args.get("query"),
                        tag=args.get("tag"),
                        domain=args.get("domain"),
                        audience="agent",
                        limit=int(args.get("limit") or 8),
                    )
                    if not pages:
                        tool_content = "No synthesized knowledge pages matched that query."
                    else:
                        lines = []
                        for page in pages:
                            lines.append(
                                f"- {page.get('slug')}: {page.get('title')} | summary={page.get('summary')} | "
                                f"domain={page.get('domain')} | visibility={page.get('visibility_policy')} | "
                                f"last_updated={page.get('last_updated')} | tags={page.get('tags') or []}"
                            )
                        tool_content = "Knowledge Page Metadata:\n" + "\n".join(lines)
                    tool_outputs_generated = True

                elif func_name == "get_knowledge_page_metadata":
                    slug = str(args.get("slug") or "")
                    metadata_view = knowledge_pages.get_page_metadata_view(slug, audience="agent")
                    if metadata_view.get("status") == "missing":
                        tool_content = f"No synthesized knowledge page found for '{slug}'."
                    elif metadata_view.get("status") == "restricted":
                        meta = metadata_view.get("page_metadata") or {}
                        tool_content = (
                            f"Knowledge page '{slug}' exists but is permission-restricted for the current audience. "
                            f"Domain={meta.get('domain')} visibility={meta.get('visibility_policy')}. "
                            "Use a summarized, consent-aware response or request operator intervention if direct disclosure is needed."
                        )
                    else:
                        tool_content = json.dumps(metadata_view.get("page") or {}, indent=2)
                    tool_outputs_generated = True

                elif func_name == "read_knowledge_page":
                    slug = str(args.get("slug") or "")
                    heading = args.get("heading")
                    if heading:
                        section_view = knowledge_pages.get_page_section_view(slug, str(heading), audience="agent")
                        if section_view.get("status") == "missing":
                            tool_content = f"No synthesized knowledge page found for '{slug}'."
                        elif section_view.get("status") == "restricted":
                            meta = section_view.get("page_metadata") or {}
                            tool_content = (
                                f"Knowledge page '{slug}' exists but section access is permission-restricted. "
                                f"Domain={meta.get('domain')} visibility={meta.get('visibility_policy')}. "
                                "If the user needs this, provide only a safe high-level explanation or ask for consent/operator review."
                            )
                        else:
                            section = section_view.get("section") or {}
                            prefix = "Summary-only view due to disclosure rules:\n" if section.get("content_redacted") else ""
                            tool_content = prefix + (section.get("content") or f"No section '{heading}' found in knowledge page '{slug}'.")
                    else:
                        page_view = knowledge_pages.get_page_view(slug, audience="agent")
                        if page_view.get("status") == "missing":
                            tool_content = f"No synthesized knowledge page found for '{slug}'."
                        elif page_view.get("status") == "restricted":
                            meta = page_view.get("page_metadata") or {}
                            tool_content = (
                                f"Knowledge page '{slug}' exists but is permission-restricted. "
                                f"Domain={meta.get('domain')} visibility={meta.get('visibility_policy')}. "
                                "Do not quote it directly; summarize cautiously or ask for consent/operator intervention if needed."
                            )
                        else:
                            page = page_view.get("page") or {}
                            prefix = "Summary-only view due to disclosure rules:\n" if page.get("content_redacted") else ""
                            tool_content = prefix + (page.get("body") or "")
                    tool_outputs_generated = True

                elif func_name == "update_knowledge":
                    slug = str(args.get("slug") or "")
                    reason = str(args.get("reason") or "knowledge gap detected")
                    target_scope = str(args.get("target_scope") or "codebase")
                    evidence_hints = [str(item) for item in (args.get("evidence_hints") or [])]
                    task = knowledge_pages.enqueue_update_task(
                        slug=slug,
                        reason=reason,
                        session_id=session_id,
                        target_scope=target_scope,
                        evidence=evidence_hints,
                        domain=args.get("domain"),
                    )
                    storage.commit()
                    await _worker.enqueue(task.task_id)
                    async_task_ids.append(task.task_id)
                    tool_content = f"Queued knowledge update task {task.task_id} for page '{slugify_page_title(slug)}'."

                elif func_name == "read_spec":
                    scope = str(args.get("scope") or "project")
                    specs = load_specs()
                    if scope == "global":
                        tool_content = specs.get("global_spec", "")
                    else:
                        tool_content = specs.get("project_spec", "")
                    tool_outputs_generated = True

                elif func_name == "propose_spec_update":
                    scope = str(args.get("scope") or "project")
                    proposed_change = str(args.get("proposed_change") or "").strip()
                    rationale = str(args.get("rationale") or "").strip()
                    user_signal = str(args.get("user_signal") or content).strip()
                    current_specs = load_specs()
                    current_spec = current_specs.get("global_spec" if scope == "global" else "project_spec", "")
                    title = f"Spec Review ({scope.title()}): {proposed_change[:48] or rationale[:48] or 'pending proposal'}"
                    review_prompt = (
                        f"Review a proposed {scope} spec update.\n\n"
                        f"Current {scope} spec:\n{current_spec}\n\n"
                        f"Proposed change:\n{proposed_change}\n\n"
                        f"Rationale:\n{rationale}\n\n"
                        f"Triggering user signal:\n{user_signal}\n\n"
                        "Tasks:\n"
                        "1. Compare the proposal against the current spec.\n"
                        "2. Identify contradictions, ambiguity, or missing details.\n"
                        "3. Draft clarification questions if needed.\n"
                        "4. Suggest a clean spec delta without directly editing the spec file.\n"
                        "5. Treat the spec as durable gospel unless the user explicitly wants to change it.\n"
                    )
                    task = storage.tasks.create(
                        title=title,
                        description=review_prompt,
                        session_id=session_id,
                        state=TaskState.PENDING,
                        constraints={
                            "target_scope": "codebase",
                            "spec_operation": "review_proposal",
                            "spec_scope": scope,
                            "proposed_change": proposed_change,
                            "rationale": rationale,
                            "user_signal": user_signal,
                        },
                    )
                    task.type = TaskType.RESEARCH
                    proposal = create_spec_proposal(
                        storage,
                        scope=scope,
                        proposed_change=proposed_change,
                        rationale=rationale,
                        user_signal=user_signal,
                        session_id=session_id,
                        source="chat_agent",
                        review_task_id=task.task_id,
                    )
                    task.constraints["spec_proposal_id"] = proposal["proposal_id"]
                    storage.commit()
                    await _worker.enqueue(task.task_id)
                    async_task_ids.append(task.task_id)
                    tool_content = (
                        f"Queued reviewed spec proposal task {task.task_id} for the {scope} spec "
                        f"(proposal_id={proposal['proposal_id']}). "
                        "I will treat this as durable intent under review rather than editing the spec directly."
                    )

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
    queued_question = get_question_for_source(storage, source_type="task_blocked", source_id=task_id)
    if queued_question:
        resolve_question(storage, queued_question["question_id"], resolution="resolved", response=override)
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

@app.post("/admin/evals/matrix")
async def run_eval_matrix_suite(payload: Dict[str, Any] | None = None, storage: StorageManager = Depends(get_storage)):
    payload = payload or {}
    suite_name = payload.get("suite_name", "mmlu_mini_v1")
    include_context = bool(payload.get("include_context", True))
    include_strong = bool(payload.get("include_strong", True))
    include_weak = bool(payload.get("include_weak", True))
    profiles = payload.get("profiles")
    sample_size = payload.get("sample_size")
    random_seed = payload.get("random_seed")
    report = await run_eval_matrix(
        suite_name=suite_name,
        include_context=include_context,
        include_strong=include_strong,
        include_weak=include_weak,
        profiles=profiles,
        sample_size=int(sample_size) if sample_size is not None else None,
        random_seed=int(random_seed) if random_seed is not None else None,
    )
    for variant in report.get("variants", []):
        details = {
            "suite_name": suite_name,
            "variant_id": variant.get("variant_id"),
            "mode": variant.get("mode"),
            "profile": variant.get("profile"),
            "include_context": include_context,
            "case_count": report.get("case_count"),
        }
        for metric_name, value in (
            ("eval_matrix_accuracy", float(variant.get("accuracy", 0.0) or 0.0)),
            ("eval_matrix_latency_s", float(variant.get("avg_latency_s", 0.0) or 0.0)),
            ("eval_matrix_prompt_tokens", float(variant.get("prompt_tokens", 0) or 0.0)),
            ("eval_matrix_completion_tokens", float(variant.get("completion_tokens", 0) or 0.0)),
            ("eval_matrix_total_tokens", float(variant.get("total_tokens", 0) or 0.0)),
        ):
            from strata.orchestrator.worker.telemetry import record_metric
            record_metric(
                storage,
                metric_name=metric_name,
                value=value,
                model_id=variant.get("variant_id"),
                task_type="EVAL_MATRIX",
                run_mode="eval_matrix",
                execution_context=variant.get("mode"),
                details=details,
            )
    storage.commit()
    return {"status": "ok", "report": report}

@app.post("/admin/evals/sample_tick")
async def run_sampled_eval_tick(payload: Dict[str, Any] | None = None, storage: StorageManager = Depends(get_storage)):
    payload = payload or {}
    suite_name = payload.get("suite_name", "mmlu_mini_v1")
    sample_size = max(1, int(payload.get("sample_size", 2) or 2))
    include_context = bool(payload.get("include_context", False))
    profiles = payload.get(
        "profiles",
        ["raw_model", "harness_no_capes", "harness_tools_no_web", "harness_web_no_tools", "harness_tools_web"],
    )
    report = await run_eval_matrix(
        suite_name=suite_name,
        include_context=include_context,
        include_strong=bool(payload.get("include_strong", True)),
        include_weak=bool(payload.get("include_weak", True)),
        profiles=profiles,
        sample_size=sample_size,
        random_seed=int(time.time()),
    )
    for variant in report.get("variants", []):
        details = {
            "suite_name": suite_name,
            "variant_id": variant.get("variant_id"),
            "mode": variant.get("mode"),
            "profile": variant.get("profile"),
            "include_context": include_context,
            "case_count": report.get("case_count"),
            "sampled": True,
        }
        from strata.orchestrator.worker.telemetry import record_metric
        record_metric(
            storage,
            metric_name="eval_sample_tick_accuracy",
            value=float(variant.get("accuracy", 0.0) or 0.0),
            model_id=variant.get("variant_id"),
            task_type="EVAL_SAMPLE_TICK",
            run_mode="eval_sample_tick",
            execution_context=variant.get("mode"),
            details=details,
        )
        record_metric(
            storage,
            metric_name="eval_sample_tick_latency_s",
            value=float(variant.get("avg_latency_s", 0.0) or 0.0),
            model_id=variant.get("variant_id"),
            task_type="EVAL_SAMPLE_TICK",
            run_mode="eval_sample_tick",
            execution_context=variant.get("mode"),
            details=details,
        )
        record_metric(
            storage,
            metric_name="eval_sample_tick_total_tokens",
            value=float(variant.get("total_tokens", 0.0) or 0.0),
            model_id=variant.get("variant_id"),
            task_type="EVAL_SAMPLE_TICK",
            run_mode="eval_sample_tick",
            execution_context=variant.get("mode"),
            details=details,
        )
    storage.commit()
    return {"status": "ok", "report": report}

@app.post("/admin/knowledge/compact")
async def compact_knowledge_base():
    script_path = os.path.join(_BASE_DIR, "scripts", "compact_knowledge.py")
    result = subprocess.run(
        ["./venv/bin/python", script_path],
        cwd=_BASE_DIR,
        capture_output=True,
        text=True,
        check=True,
    )
    return {"status": "ok", "report": json.loads(result.stdout)}

@app.get("/admin/knowledge/pages")
async def list_knowledge_pages(
    query: Optional[str] = None,
    tag: Optional[str] = None,
    domain: Optional[str] = None,
    audience: str = "user",
    limit: int = 50,
    storage: StorageManager = Depends(get_storage),
):
    pages = KnowledgePageStore(storage).list_pages(query=query, tag=tag, domain=domain, audience=audience, limit=limit)
    return {"status": "ok", "pages": pages}

@app.get("/admin/knowledge/pages/{slug}/metadata")
async def get_knowledge_page_metadata(slug: str, audience: str = "user", storage: StorageManager = Depends(get_storage)):
    page = KnowledgePageStore(storage).get_page_metadata(slug, audience=audience)
    if not page:
        raise HTTPException(status_code=404, detail="Knowledge page not found")
    return {"status": "ok", "page": page}

@app.get("/admin/knowledge/pages/{slug}")
async def get_knowledge_page(slug: str, audience: str = "user", storage: StorageManager = Depends(get_storage)):
    page = KnowledgePageStore(storage).get_page(slug, audience=audience)
    if not page:
        raise HTTPException(status_code=404, detail="Knowledge page not found")
    return {"status": "ok", "page": page}

@app.get("/admin/knowledge/pages/{slug}/section")
async def get_knowledge_page_section(slug: str, heading: str, audience: str = "user", storage: StorageManager = Depends(get_storage)):
    section = KnowledgePageStore(storage).get_page_section(slug, heading, audience=audience)
    if not section:
        raise HTTPException(status_code=404, detail="Knowledge page not found")
    return {"status": "ok", "section": section}

@app.post("/admin/knowledge/pages")
async def upsert_knowledge_page(payload: Dict[str, Any], storage: StorageManager = Depends(get_storage)):
    title = str(payload.get("title") or "").strip()
    body = str(payload.get("body") or "").strip()
    if not title or not body:
        raise HTTPException(status_code=400, detail="title and body are required")
    page = KnowledgePageStore(storage).upsert_page(
        slug=payload.get("slug"),
        title=title,
        body=body,
        summary=payload.get("summary"),
        tags=payload.get("tags"),
        aliases=payload.get("aliases"),
        related_pages=payload.get("related_pages"),
        provenance=payload.get("provenance"),
        confidence=float(payload.get("confidence", 0.5) or 0.5),
        created_by=str(payload.get("created_by") or "api"),
        updated_reason=str(payload.get("updated_reason") or "manual_upsert"),
        domain=str(payload.get("domain") or "project"),
        visibility_policy=payload.get("visibility_policy"),
        disclosure_rules=payload.get("disclosure_rules"),
        scope_id=str(payload.get("scope_id") or ""),
        project_id=str(payload.get("project_id") or ""),
        owner_id=str(payload.get("owner_id") or ""),
        retention_policy=str(payload.get("retention_policy") or "persistent"),
    )
    storage.commit()
    return {"status": "ok", "page": page}

@app.post("/admin/knowledge/update")
async def queue_knowledge_update(payload: Dict[str, Any], storage: StorageManager = Depends(get_storage)):
    slug = str(payload.get("slug") or payload.get("title") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    if not slug or not reason:
        raise HTTPException(status_code=400, detail="slug/title and reason are required")
    task = KnowledgePageStore(storage).enqueue_update_task(
        slug=slug,
        reason=reason,
        session_id=payload.get("session_id"),
        target_scope=str(payload.get("target_scope") or "codebase"),
        evidence=[str(item) for item in (payload.get("evidence_hints") or [])],
        domain=payload.get("domain"),
    )
    storage.commit()
    await _worker.enqueue(task.task_id)
    return {
        "status": "ok",
        "task_id": task.task_id,
        "knowledge_slug": slugify_page_title(slug),
    }

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
