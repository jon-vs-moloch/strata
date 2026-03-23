"""
@module storage.repositories.messages
@purpose High-level CRUD operations for the MessageModel (Chat history).
@owns chat message persistence, intervention tracking
@does_not_own business logic orchestration, database connection lifecycle
@key_exports MessageRepository
"""

from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select
from shotgun_tokens.storage.models import MessageModel

class MessageRepository:
    """
    @summary Manages the chat history and user intervention messages in SQL.
    @inputs session: SQLAlchemy Session
    @outputs side-effect driven (DB mutations) or MessageModel objects
    @side_effects writes to 'messages' table
    @depends storage.models.MessageModel
    @invariants does not commit the session.
    """
    def __init__(self, session: Session):
        """
        @summary Initialize the MessageRepository.
        @inputs session: active DB session
        """
        self.session = session

    def create(self, role: str, content: str, is_intervention: bool = False, task_id: str = None) -> MessageModel:
        """
        @summary Add a new message to the chat trace.
        @inputs role: ('user', 'assistant', 'system'), content: message text
        @outputs the created MessageModel
        """
        msg = MessageModel(
            role=role, 
            content=content, 
            is_intervention=is_intervention,
            associated_task_id=task_id
        )
        self.session.add(msg)
        self.session.flush()
        return msg

    def get_history(self, limit: int = 50) -> List[MessageModel]:
        """
        @summary Retrieve the most recent chat log.
        @outputs list of MessageModel objects
        """
        stmt = select(MessageModel).order_by(MessageModel.created_at.desc()).limit(limit)
        return list(self.session.scalars(stmt).all()[::-1])
