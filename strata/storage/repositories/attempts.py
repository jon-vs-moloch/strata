"""
@module storage.repositories.attempts
@purpose High-level CRUD operations for the AttemptModel entity.
@owns attempt creation, outcome updates, resolution management
@key_exports AttemptRepository
"""

from typing import List, Optional
import time
from uuid import uuid4
from sqlalchemy.orm import Session
from datetime import datetime
from sqlalchemy.exc import OperationalError
from strata.storage.models import AttemptModel, AttemptOutcome, AttemptResolution
from strata.storage.sqlite_write import flush_with_write_lock, is_sqlite_locked_error

class AttemptRepository:
    """
    @summary Manages structured attempt storage and retrieval using SQLAlchemy.
    """
    def __init__(self, session: Session):
        self.session = session

    def create(self, **kwargs) -> AttemptModel:
        """
        @summary Instantiate and persist a new AttemptModel.
        """
        if not kwargs.get("attempt_id"):
            kwargs["attempt_id"] = str(uuid4())
        bind = getattr(self.session, "bind", None)
        sqlite_enabled = str(getattr(getattr(bind, "url", None), "drivername", "") or "").startswith("sqlite")
        retries = 8 if sqlite_enabled else 1
        for index in range(retries):
            attempt = AttemptModel(**kwargs)
            self.session.add(attempt)
            try:
                flush_with_write_lock(self.session, enabled=sqlite_enabled)
                return attempt
            except OperationalError as exc:
                self.session.rollback()
                if not sqlite_enabled or not is_sqlite_locked_error(exc) or index >= retries - 1:
                    raise
                time.sleep(0.15 * (index + 1))
        raise RuntimeError("Attempt creation retries exhausted.")

    def get_by_id(self, attempt_id: str) -> Optional[AttemptModel]:
        """
        @summary Retrieve an attempt by its primary key.
        """
        return self.session.get(AttemptModel, attempt_id)

    def get_by_task_id(self, task_id: str) -> List[AttemptModel]:
        """
        @summary Retrieve all attempts for a given task.
        """
        from sqlalchemy import select
        stmt = select(AttemptModel).where(AttemptModel.task_id == task_id).order_by(AttemptModel.started_at.desc())
        return list(self.session.scalars(stmt).all())

    def update_outcome(self, attempt_id: str, outcome: AttemptOutcome, reason: Optional[str] = None):
        """
        @summary Record the outcome of an attempt.
        """
        attempt = self.get_by_id(attempt_id)
        if attempt:
            attempt.outcome = outcome
            attempt.reason = reason
            attempt.ended_at = datetime.utcnow()

    def set_resolution(self, attempt_id: str, resolution: AttemptResolution):
        """
        @summary Set the resolution for a failed attempt.
        """
        attempt = self.get_by_id(attempt_id)
        if attempt:
            attempt.resolution = resolution

    def set_plan_review(self, attempt_id: str, plan_review: dict):
        """
        @summary Update the plan review for an attempt.
        """
        attempt = self.get_by_id(attempt_id)
        if attempt:
            attempt.plan_review = plan_review
