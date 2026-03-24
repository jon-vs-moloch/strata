"""
@module storage.services.main
@purpose Coordinate multiple database domains into a unified storage entrypoint.
@owns session lifecycle, task/candidate/prompt repository instantiation
@does_not_own specific SQL logic (delegates to repositories)
@key_exports StorageManager
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from strata.storage.models import Base
from strata.storage.repositories.tasks import TaskRepository
from strata.storage.repositories.messages import MessageRepository
from strata.storage.repositories.parameters import ParameterRepository
from strata.storage.repositories.attempts import AttemptRepository

class StorageManager:
    """
    @summary Central entrypoint for database access.
    @inputs db_url: SQLite path or other SQLAlchemy URL
    @outputs side-effect driven (DB initialization)
    @side_effects creates tables, manages DB connections
    @depends storage.repositories.tasks, storage.models
    @invariants does not expose raw sessions to the orchestrator layer directly.
    """
    def __init__(self, db_url: str = None):
        """
        @summary Initialize the StorageManager and repositories.
        @inputs connection string (default local sqlite or DATABASE_URL env)
        @outputs none
        """
        import os
        db_url = db_url or os.getenv("DATABASE_URL", "sqlite:///strata.db")
        self.engine = create_engine(db_url)
        
        # SQLite-specific performance tuning: Enable Write-Ahead Logging (WAL)
        if db_url.startswith("sqlite"):
            from sqlalchemy import event
            @event.listens_for(self.engine, "connect")
            def set_sqlite_pragma(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.close()

        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        Base.metadata.create_all(self.engine)
        self.session: Session = self.SessionLocal()
        
        # Repositories for domain-specific logic
        self.tasks = TaskRepository(self.session)
        self.messages = MessageRepository(self.session)
        self.parameters = ParameterRepository(self.session)
        self.attempts = AttemptRepository(self.session)
        # self.candidates = CandidateRepository(self.session)
        # self.prompts = PromptRepository(self.session)

    def commit(self):
        """
        @summary Permanently persist current session changes.
        @inputs none
        """
        self.session.commit()

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
