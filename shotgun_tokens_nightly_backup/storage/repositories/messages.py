"""
@module storage.repositories.messages
@purpose High-level CRUD operations for the MessageModel (Chat history).
@owns chat message persistence, intervention tracking
@does_not_own business logic orchestration, database connection lifecycle
@key_exports MessageRepository
"""

from typing import List, Dict, Any, Optional
from sqlalchemy import select, desc, distinct
from sqlalchemy.orm import Session
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

    def create(self, role: str, content: str, session_id: str = "default", is_intervention: bool = False, task_id: str = None) -> MessageModel:
        """
        @summary Add a new message to the chat trace.
        @inputs role: ('user', 'assistant', 'system'), content: message text
        @outputs the created MessageModel
        """
        msg = MessageModel(
            role=role, 
            content=content, 
            session_id=session_id,
            is_intervention=is_intervention,
            associated_task_id=task_id
        )
        self.session.add(msg)
        self.session.flush()
        return msg

    def get_all(self, session_id: Optional[str] = None) -> List[MessageModel]:

        """
        @summary Fetch all active (non-archived) messages, optionally filtered by session.
        @inputs session_id: optional group ID
        @outputs list of message models
        """
        stmt = select(MessageModel).filter(MessageModel.is_archived == False)
        if session_id:
            stmt = stmt.filter(MessageModel.session_id == session_id)
        stmt = stmt.order_by(MessageModel.created_at.asc())
        return list(self.session.scalars(stmt).all())

    def get_sessions(self) -> List[str]:
        """
        @summary Fetch a list of all unique session IDs.
        @outputs list of session strings
        """
        stmt = select(distinct(MessageModel.session_id))
        results = self.session.scalars(stmt).all()
        return [str(r) for r in results if r is not None] # Ensure non-None and convert to string

    def archive_session(self, session_id: str):
        """
        @summary Mark all messages in a session as archived.
        """
        stmt = select(MessageModel).filter(MessageModel.session_id == session_id)
        for msg in self.session.scalars(stmt):
            msg.is_archived = True
        # self.session.query(MessageModel).filter(MessageModel.session_id == session_id).update({"is_archived": True})
        # self.session.commit() # The repository should not commit, the unit of work should.

    def delete_session(self, session_id: str):
        """
        @summary Permanently remove all messages in a session.
        """
        stmt = select(MessageModel).filter(MessageModel.session_id == session_id)
        for msg in self.session.scalars(stmt):
            self.session.delete(msg)
        # self.session.query(MessageModel).filter(MessageModel.session_id == session_id).delete()
        # self.session.commit() # The repository should not commit, the unit of work should.
