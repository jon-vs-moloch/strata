"""
@module storage.services.main
@purpose Coordinate multiple database domains into a unified storage entrypoint.
@owns session lifecycle, task/candidate/prompt repository instantiation
@does_not_own specific SQL logic (delegates to repositories)
@key_exports StorageManager
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from shotgun_tokens.storage.models import Base
from shotgun_tokens.storage.repositories.tasks import TaskRepository
from shotgun_tokens.storage.repositories.messages import MessageRepository
from shotgun_tokens.storage.repositories.parameters import ParameterRepository
from shotgun_tokens.storage.repositories.attempts import AttemptRepository

class StorageManager:
    """
    @summary Central entrypoint for database access.
    @inputs db_url: SQLite path or other SQLAlchemy URL
    @outputs side-effect driven (DB initialization)
    @side_effects creates tables, manages DB connections
    @depends storage.repositories.tasks, storage.models
    @invariants does not expose raw sessions to the orchestrator layer directly.
    """
    def __init__(self, db_url: str = "sqlite:///shotgun.db"):
        """
        @summary Initialize the StorageManager and repositories.
        @inputs connection string (default local sqlite)
        @outputs none
        """
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
