"""
@module storage.repositories.attempts
@purpose High-level CRUD operations for the AttemptModel entity.
@owns attempt creation, outcome updates, resolution management
@key_exports AttemptRepository
"""

from typing import List, Optional
from sqlalchemy.orm import Session
from datetime import datetime
from shotgun_tokens.storage.models import AttemptModel, AttemptOutcome, AttemptResolution

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
        attempt = AttemptModel(**kwargs)
        self.session.add(attempt)
        self.session.flush()
        return attempt

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
