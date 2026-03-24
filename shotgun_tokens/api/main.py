"""
@module api.main
@purpose Expose internal storage and orchestrator state to the UI via REST.
@owns API routing, JSON serialization, StorageManager lifecycle, background worker
@does_not_own business logic orchestration, database schema definitions
@key_exports app
"""

import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Any, Optional
from shotgun_tokens.storage.services.main import StorageManager
from shotgun_tokens.storage.models import TaskModel, TaskType, TaskState
from shotgun_tokens.models.adapter import ModelAdapter
from shotgun_tokens.orchestrator.background import BackgroundWorker
from shotgun_tokens.api.hotreload import HotReloader

logger = logging.getLogger(__name__)

# ── Module-level singletons ────────────────────────────────────────────────────
GLOBAL_SETTINGS = {
    "max_sync_tool_iterations": 3
}

_storage = StorageManager()
_model = ModelAdapter()
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_hotreloader = HotReloader(_BASE_DIR)
_worker = BackgroundWorker(
    storage_factory=StorageManager,   # each task gets a fresh session
    model_adapter=_model
)

# ── True Tool Calling ──────────────────────────────────────────────────────────
# We no longer use string heuristics. Instead, we provide the LLM with explicitly 
# defined tools to route tasks or fetch facts.
TOOLS = [
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
            "description": "Perform an IMMEDIATE synchronous web search. Use ONLY for quick, immediate answers like facts, daily weather, or single-concept documentation lookups. Do NOT use this for deep or comprehensive research.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The simple search query."}
                },
                "required": ["query"]
            }
        }
    }
]

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
    logger.info("Shotgun Tokens API started")
    yield
    await _worker.stop()
    logger.info("Shotgun Tokens API stopped")

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
    # In a real system, we'd limit context window.
    history_records = storage.messages.get_all(session_id=session_id)
    messages = []
    # Optionally inject a system prompt here
    messages.append({
        "role": "system",
        "content": """You are the Strata Orchestrator. You are a STRICT ROUTING AGENT. 
Your ONLY goal is to route user requests to the correct subsystem. 

FOLLOW THESE HIERARCHICAL RULES:
1. NEVER attempt to research the web or explain complex codebase architecture yourself. 
2. If the user asks for DEEP research, broad context, or multi-source synthesis (web or codebase), YOU MUST CALL 'kickoff_background_research'.
3. If the user asks for a simple fact (weather, population, simple library syntax), YOU MUST CALL 'search_web'.
4. If the user asks to build, fix, refactor, or implement code, YOU MUST CALL 'kickoff_swarm_task'.
5. ONLY answer as a human if the user is just saying 'hello', asking how you are, or discussing the status of existing tasks.

CRITICAL: When you call a tool, you MUST simultaneously output a short human-readable message explaining what you are doing. For example: "I am kicking off a deep web research protocol to look into this." or "Let me query the web for the current weather." or "I am configuring a swarm task to implement that."

DO NOT output headers like '# Deep Web Research' without calling a tool. If you decide to research, CALL THE TOOL."""
    })
    for m in history_records[-10:]: # last 10
        messages.append({"role": m.role, "content": m.content})

    # 3. Handle tools with iterative looping
    max_iters = GLOBAL_SETTINGS.get("max_sync_tool_iterations", 3)
    iteration = 0
    final_reply = ""
    
    while iteration < max_iters:
        model_response = await _model.chat(messages, tools=TOOLS)
        
        tool_calls = model_response.get("tool_calls")
        content_val = model_response.get("content")
        chain_of_thought = str(content_val) if content_val else ""
        
        # Save interpretation if available
        if chain_of_thought and chain_of_thought.strip() and not tool_calls:
            # Done! Plain chat fallback, answered without tools
            storage.messages.create(role="assistant", content=chain_of_thought.strip(), session_id=session_id)
            storage.commit()
            return {"status": "ok", "reply": chain_of_thought.strip()}
            
        if tool_calls and isinstance(tool_calls, list) and len(tool_calls) > 0:
            call = tool_calls[0]
            func_name = call.get("function", {}).get("name")
            import json
            try:
                args = json.loads(call.get("function", {}).get("arguments", "{}"))
            except:
                args = {}
                
            # Fallback message if model silent
            if not chain_of_thought or not chain_of_thought.strip():
                if func_name == "search_web":
                    chain_of_thought = f"I'm going to quickly query the web for: `{args.get('query', '...')}`"
                elif func_name == "kickoff_background_research":
                    chain_of_thought = f"I am kicking off an asynchronous {args.get('target_scope', 'codebase')} research task to compile data on this."
                elif func_name == "kickoff_swarm_task":
                    chain_of_thought = f"Let me initialize an agent swarm to handle implementation for `{args.get('title', 'this task')}`."
                else:
                    chain_of_thought = f"Invoking tool: {func_name}"

            storage.messages.create(role="assistant", content=chain_of_thought, session_id=session_id)
            storage.commit()

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
                # This breaks the loop
                reply = chain_of_thought.strip() if chain_of_thought and chain_of_thought.strip() else f"Starting background research on: *{desc}*\\n\\nI've kicked off an asynchronous swarm task. I'll post the results here when done."
                return {"status": "ok", "reply": reply, "task_id": task.task_id}

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
                # Breaks the loop
                reply = chain_of_thought.strip() if chain_of_thought and chain_of_thought.strip() else f"Tool Call Executed: kickoff_swarm_task. Initialized implementation swarm for: *{title}*"
                return {"status": "ok", "reply": reply, "task_id": task.task_id}

            elif func_name == "search_web":
                query = args.get("query", content)
                search_results = await _perform_web_search(query)
                
                # Setup context for the next iteration of the loop!
                # We need to append the model's message that contained the tool call
                messages.append({
                    "role": "assistant",
                    "content": chain_of_thought if chain_of_thought and chain_of_thought.strip() else None,
                    "tool_calls": [call]
                })
                # We append the tool output
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", "call_xyz"),
                    "name": "search_web",
                    "content": f"Search Results for '{query}':\\n{search_results}\\n\\nCRITICAL INSTRUCTION: You MUST now synthesize the above results into a final answer for the user. Do " "NOT say 'Let me check again', do NOT output another tool call. Provide the final textual response now."
                })
                # Continue loop!
                iteration += 1
                continue
                
        else:
            # No tool calls, we either got an answer or a fallback error
            final_reply = model_response.get("content", "I encountered an error processing that.")
            storage.messages.create(role="assistant", content=final_reply, session_id=session_id)
            storage.commit()
            return {"status": "ok", "reply": final_reply}
            
    # If the loop exhausted its iterations
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
    @inputs { "module": "shotgun_tokens.api.main" }
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
    from shotgun_tokens.storage.models import Base
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("shotgun_tokens.api.main:app", host="0.0.0.0", port=8000, reload=True)
