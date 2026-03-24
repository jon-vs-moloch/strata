"""
@module api.main
@purpose Expose internal storage and Strata orchestrator state to the UI via REST.
@owns API routing, JSON serialization, StorageManager lifecycle, background worker
@does_not_own business logic orchestration, database schema definitions
@key_exports app
"""

import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Any, Optional, Union
import json
import asyncio
from strata.storage.services.main import StorageManager
from strata.storage.models import TaskModel, TaskType, TaskState
from strata.models.adapter import ModelAdapter
from strata.orchestrator.background import BackgroundWorker
from strata.api.hotreload import HotReloader
from strata.schemas.core import ResearchReport, ResearchReport as LocalResearchReport, TaskDecomposition, AttemptResolutionSchema
from strata.memory.semantic import SemanticMemory
import importlib.util
import glob

logger = logging.getLogger(__name__)

# ── Module-level singletons ────────────────────────────────────────────────────
GLOBAL_SETTINGS = {
    "max_sync_tool_iterations": 3
}

_storage = StorageManager()
_model = ModelAdapter()
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_hotreloader = HotReloader(_BASE_DIR)
_memory = SemanticMemory()
_worker = BackgroundWorker(
    storage_factory=StorageManager,   # each task gets a fresh session
    model_adapter=_model,
    memory=_memory
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
CORE_TOOLS = [
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
    dynamic_tools = []
    tools_dir = os.path.join(_BASE_DIR, "strata", "tools")
    
    # Core tools are always present
    dynamic_tools.extend(CORE_TOOLS)
    
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

    messages = [
        {
            "role": "system", 
            "content": f"""You are Strata, a unified AI engineering system. You manage a swarm of background agents, but present yourself as a single, first-person entity.
If internal processes hit a BLOCKED state, explain the issue to the USER directly.
{memory_context}

Available Tools:
- search_web: Get facts/docs.
- create_task: Spawn background work.
- list_tasks: Check status.
- get_task: View details.
- list_active_tools: See what implementation agents can do.
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
        active_tools = load_dynamic_tools()
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
async def get_settings():
    return {"status": "ok", "settings": GLOBAL_SETTINGS}

@app.post("/admin/settings")
async def update_settings(payload: Dict[str, Any]):
    GLOBAL_SETTINGS.update(payload)
    return {"status": "ok"}

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
