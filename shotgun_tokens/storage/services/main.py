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
