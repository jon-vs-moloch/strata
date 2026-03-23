"""
@module api.main
@purpose Expose internal storage and orchestrator state to the UI via REST.
@owns API routing, JSON serialization, StorageManager lifecycle
@does_not_own business logic orchestration, database schema definitions
@key_exports app
"""

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Any
from shotgun_tokens.storage.services.main import StorageManager
from shotgun_tokens.storage.models import TaskModel

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
