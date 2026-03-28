"""
@module storage.repositories.messages
@purpose High-level CRUD operations for the MessageModel (Chat history).
@owns chat message persistence, intervention tracking
@does_not_own business logic orchestration, database connection lifecycle
@key_exports MessageRepository
"""

from typing import List, Dict, Any, Optional
import time
from sqlalchemy import select, desc, distinct, func
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError
from strata.storage.models import MessageModel

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
        last_error = None
        for attempt in range(5):
            msg = MessageModel(
                role=role,
                content=content,
                session_id=session_id,
                is_intervention=is_intervention,
                associated_task_id=task_id
            )
            try:
                self.session.add(msg)
                self.session.flush()
                return msg
            except OperationalError as e:
                last_error = e
                if "database is locked" not in str(e).lower() or attempt == 4:
                    raise
                self.session.rollback()
                time.sleep(0.2 * (attempt + 1))
        raise last_error

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

    def get_by_id(self, message_id: str) -> Optional[MessageModel]:
        """
        @summary Fetch a single active message by id.
        @inputs message_id: stable message identifier
        @outputs matching MessageModel or None
        """
        stmt = (
            select(MessageModel)
            .filter(MessageModel.is_archived == False, MessageModel.message_id == message_id)
            .limit(1)
        )
        return self.session.scalars(stmt).first()

    def get_sessions(self) -> List[str]:
        """
        @summary Fetch a list of all unique session IDs.
        @outputs list of session strings
        """
        stmt = select(distinct(MessageModel.session_id))
        results = self.session.scalars(stmt).all()
        return [str(r) for r in results if r is not None] # Ensure non-None and convert to string

    def get_session_summaries(self) -> List[Dict[str, Any]]:
        rows = (
            self.session.query(
                MessageModel.session_id,
                func.max(MessageModel.created_at).label("last_message_at"),
                func.min(MessageModel.created_at).label("first_message_at"),
                func.count(MessageModel.message_id).label("message_count"),
            )
            .filter(MessageModel.is_archived == False)
            .group_by(MessageModel.session_id)
            .all()
        )
        summaries: List[Dict[str, Any]] = []
        for row in rows:
            session_id = str(row.session_id)
            last_message = (
                self.session.query(MessageModel)
                .filter(MessageModel.is_archived == False, MessageModel.session_id == session_id)
                .order_by(MessageModel.created_at.desc())
                .first()
            )
            summaries.append(
                {
                    "session_id": session_id,
                    "message_count": int(row.message_count or 0),
                    "first_message_at": row.first_message_at.isoformat() if row.first_message_at else None,
                    "last_message_at": row.last_message_at.isoformat() if row.last_message_at else None,
                    "last_message_preview": str(getattr(last_message, "content", "") or "").strip()[:120],
                    "last_message_role": getattr(last_message, "role", None),
                }
            )
        summaries.sort(key=lambda item: item.get("last_message_at") or "", reverse=True)
        return summaries

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
