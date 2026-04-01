import os
from typing import Optional
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy import event
from strata.storage.repositories.tasks import TaskRepository
from strata.storage.repositories.messages import MessageRepository
from strata.storage.repositories.parameters import ParameterRepository
from strata.storage.repositories.attempts import AttemptRepository
from strata.storage.sqlite_write import commit_with_write_lock

# ── Module level shared resources ──────────────────────────────────────────────
_DB_URL = os.getenv("DATABASE_URL", "sqlite:///strata/runtime/strata.db")
_engine_kwargs = {}
if _DB_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {
        "timeout": 2,
        "check_same_thread": False,
    }
_engine = create_engine(_DB_URL, **_engine_kwargs)
_sqlite_write_enabled = _DB_URL.startswith("sqlite")

# SQLite-specific performance tuning: Enable Write-Ahead Logging (WAL)
if _DB_URL.startswith("sqlite"):
    @event.listens_for(_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=2000")
        cursor.close()

_SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

class StorageManager:
    """
    @summary Central entrypoint for database access.

    Strata keeps state outside the model on purpose. Durable storage is one of the
    main ways the harness compensates for small context windows and lets the
    system learn from prior outcomes instead of re-deriving everything in-prompt.
    """
    def __init__(self, session: Optional[Session] = None):
        """
        @summary Initialize the StorageManager and repositories.
        """
        if session:
            self.session = session
        else:
            self.session = _SessionLocal()

        # Repositories for domain-specific logic
        self.tasks = TaskRepository(self.session)
        self.messages = MessageRepository(self.session)
        self.parameters = ParameterRepository(self.session)
        self.attempts = AttemptRepository(self.session)
        # self.candidates = CandidateRepository(self.session)
        # self.prompts = PromptRepository(self.session)

    @property
    def engine(self):
        return _engine

    @property
    def SessionLocal(self):
        return _SessionLocal

    def commit(self):
        """
        @summary Permanently persist current session changes.
        @inputs none
        """
        commit_with_write_lock(self.session, enabled=_sqlite_write_enabled)

    def rollback(self):
        """
        @summary Revert current session changes.
        @inputs none
        """
        self.session.rollback()

    def close(self):
        """
        @summary Cleanup DB connection.
        @inputs none
        """
        self.session.close()

    async def get_resource_summary(self, resource_id: str, raw_content_callback, model_adapter) -> str:
        """
        @summary Practical Progressive Disclosure: Returns a summary of a resource by default.
        @inputs resource_id: key for cache, raw_content_callback: async function to fetch raw data if needed, model_adapter: for summarization
        @outputs a concise summary + drill-down instructions
        """
        cache_key = f"summary_{resource_id}"
        # If parameter doesn't exist, it returns None as default
        summary_text = self.parameters.get_parameter(cache_key, default_value=None)
        
        if summary_text:
            return summary_text + f"\n[Note: This is a cached summary. If you require the exact raw data, call the 'fetch_raw_artifact' tool with ID: {resource_id}]"

        # Fetch raw data
        raw_data = await raw_content_callback()
        
        # Summarize using a cheap model (Tier-0)
        summary_prompt = f"Summarize the following data concisely for a technical agent:\n\n{raw_data[:2000]}"
        response = await model_adapter.chat([{"role": "user", "content": summary_prompt}])
        summary_text = response.get("content", "Summary failed.")
        
        # Cache and return (or use mutate_parameter if it exists)
        self.parameters.mutate_parameter(key=cache_key, new_value=summary_text, rationale=f"Auto-generated summary for {resource_id}")
        self.commit()
        
        return summary_text + f"\n[Note: This is a cached summary. If you require the exact raw data, call the 'fetch_raw_artifact' tool with ID: {resource_id}]"

    def apply_dependency_cascade(self):
        """
        @summary Practical Cascade Rule: If a task is ABANDONED or CANCELLED, cancel all its dependents recursively.
        @owns gridlock resolution, dependency safety
        """
        from strata.storage.models import TaskModel, TaskState, task_dependencies
        
        # 1. Find all tasks currently in a terminal failure state
        failed_tasks = self.session.query(TaskModel).filter(
            TaskModel.state.in_([TaskState.ABANDONED, TaskState.CANCELLED])
        ).all()
        
        # Use a queue to handle recursive invalidation
        queue = [t.task_id for t in failed_tasks]
        processed = set()
        
        cascades = 0
        while queue:
            current_id = queue.pop(0)
            if current_id in processed:
                continue
            processed.add(current_id)
            
            # Find tasks that hold this current_id in their 'depends_on_id' (dependencies)
            dependents = (
                self.session.query(TaskModel)
                .join(task_dependencies, TaskModel.task_id == task_dependencies.c.task_id)
                .filter(task_dependencies.c.depends_on_id == current_id)
                .all()
            )
            
            for dep in dependents:
                if dep.state not in [TaskState.CANCELLED, TaskState.ABANDONED]:
                    print(f"GRIDLOCK RESOLUTION: Cancelling {dep.task_id} (dependent on failed {current_id})")
                    dep.state = TaskState.CANCELLED
                    cascades += 1
                    queue.append(dep.task_id)
        
        if cascades > 0:
            self.commit()
            return cascades
        return 0
