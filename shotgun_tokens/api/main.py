"""
@module api.main
@purpose Expose internal storage and orchestrator state to the UI via REST.
@owns API routing, JSON serialization, StorageManager lifecycle
@does_not_own business logic orchestration, database schema definitions
@key_exports app
"""

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Any, Optional
from shotgun_tokens.storage.services.main import StorageManager
from shotgun_tokens.storage.models import TaskModel
from shotgun_tokens.models.adapter import ModelAdapter
from shotgun_tokens.orchestrator.command import SwarmCommand, CreateTaskAction, PrioritizeAction

app = FastAPI(title="Shotgun Tokens API")

# Enable CORS for local React development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global storage instance
_storage = StorageManager()
_model = ModelAdapter() # Defaults to local Ollama

def get_storage():
    """
    @summary Dependency provider for the shared StorageManager.
    @inputs none
    @outputs StorageManager instance
    """
    return _storage

@app.get("/tasks", response_model=List[Dict[str, Any]])
async def list_tasks(storage: StorageManager = Depends(get_storage)):
    """
    @summary Retrieve all active work contexts from the swarm.
    @inputs storage: injected StorageManager
    @outputs List of task objects in JSON format
    """
    # Simple projection for the UI
    tasks = storage.session.query(TaskModel).all()
    return [{
        "id": t.task_id,
        "title": t.title,
        "description": t.description,
        "status": t.status.value,
        "depth": t.depth
    } for t in tasks]

@app.get("/messages")
async def get_messages(session_id: Optional[str] = None, storage: StorageManager = Depends(get_storage)):
    """
    @summary Retrieve chat history, optionally filtered by session.
    """
    history = storage.messages.get_all(session_id=session_id)
    return [
        {
            "id": m.id,
            "session_id": m.session_id,
            "role": m.role,
            "content": m.content,
            "is_intervention": m.is_intervention,
            "created_at": m.created_at.isoformat()
        } for m in history
    ]

@app.get("/sessions")
async def get_sessions(storage: StorageManager = Depends(get_storage)):
    """
    @summary Retrieve unique session list.
    """
    return storage.messages.get_sessions()

@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str, storage: StorageManager = Depends(get_storage)):
    """
    @summary Archive a session.
    """
    storage.messages.archive_session(session_id)
    storage.commit()
    return {"status": "ok"}


@app.post("/chat")
async def post_chat(payload: Dict[str, Any], storage: StorageManager = Depends(get_storage)):
    """
    @summary Process a user chat message.
    @inputs payload: { role: 'user', content: '...', session_id: '...' }
    @outputs assistant response acknowledgement
    @side_effects triggers orchestrator if task-related
    """
    # 1. Store the user message
    session_id = payload.get('session_id', 'default')
    storage.messages.create(role=payload['role'], content=payload['content'], session_id=session_id)
    
    # 2. Call the Real Brain
    # Note: Using a simplified prompt for the bootstrap
    prompt = f"User said: {payload['content']}. If this is a task, respond with a tool call. Otherwise, respond naturally."
    model_response = await _model.chat([{"role": "user", "content": prompt}])
    
    # 3. Handle Tool Logic (Simplified for Genesis)
    response_content = model_response.get("content", "I encountered an error processing that.")
    
    # HEURISTIC: Does it look like a task request?
    lower_content = payload['content'].lower()
    if any(word in lower_content for word in ["build", "create", "refactor", "task", "implement"]):
        # Automatic task creation for the Genesis instruction
        task = storage.tasks.create(
            title=f"Auto-Task: {payload['content'][:30]}...",
            description=payload['content']
        )
        response_content = f"Tool Call Executed: create_task. I've initialized the swarm for: '{task.title}'."

    storage.messages.create(role="assistant", content=response_content, session_id=session_id)

    
    storage.commit()
    return {"status": "ok", "reply": response_content}



@app.post("/tasks")
async def create_task(task_data: Dict[str, Any], storage: StorageManager = Depends(get_storage)):
    """
    @summary Manually enqueue a new task into the swarm.
    @inputs task_data: JSON payload with title/description
    @outputs The created task record
    @side_effects writes a new row to the database
    """
    task = storage.tasks.create(**task_data)
    storage.commit()
    return {"id": task.task_id, "status": task.status.value}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
