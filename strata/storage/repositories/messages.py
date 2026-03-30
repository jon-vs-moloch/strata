"""
@module storage.repositories.messages
@purpose High-level CRUD operations for the MessageModel (Chat history).
@owns chat message persistence, intervention tracking
@does_not_own business logic orchestration, database connection lifecycle
@key_exports MessageRepository
"""

from typing import List, Dict, Any, Optional
import time
from datetime import datetime, timezone

from sqlalchemy import and_, case, distinct, func, select
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError
from strata.core.lanes import session_matches_lane
from strata.sessions.metadata import _session_metadata_key, get_session_metadata_from_value
from strata.storage.models import MessageModel, ParameterModel
from strata.storage.sqlite_write import flush_with_write_lock


def _parse_timestamp(raw: Any) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except Exception:
        return None

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
        bind = getattr(self.session, "bind", None)
        sqlite_enabled = str(getattr(getattr(bind, "url", None), "drivername", "") or "").startswith("sqlite")
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
                flush_with_write_lock(self.session, enabled=sqlite_enabled)
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

    def get_session_summaries(self, *, lane: Optional[str] = None) -> List[Dict[str, Any]]:
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
        session_rows = [row for row in rows if session_matches_lane(str(row.session_id), lane)]
        if not session_rows:
            return []
        session_ids = [str(row.session_id) for row in session_rows]
        metadata_rows = (
            self.session.query(ParameterModel)
            .filter(ParameterModel.key.in_([_session_metadata_key(session_id) for session_id in session_ids]))
            .all()
        )
        metadata_by_session = {
            str(row.key).removeprefix(_session_metadata_key("")): get_session_metadata_from_value(row.value.get("current"))
            for row in metadata_rows
            if isinstance(row.value, dict)
        }

        first_ranked = (
            self.session.query(
                MessageModel.session_id.label("session_id"),
                MessageModel.role.label("role"),
                func.row_number().over(
                    partition_by=MessageModel.session_id,
                    order_by=[MessageModel.created_at.asc(), MessageModel.message_id.asc()],
                ).label("row_rank"),
            )
            .filter(MessageModel.is_archived == False, MessageModel.session_id.in_(session_ids))
            .subquery()
        )
        first_rows = (
            self.session.query(first_ranked.c.session_id, first_ranked.c.role)
            .filter(first_ranked.c.row_rank == 1)
            .all()
        )
        first_role_by_session = {str(row.session_id): row.role for row in first_rows}

        last_ranked = (
            self.session.query(
                MessageModel.session_id.label("session_id"),
                MessageModel.role.label("role"),
                MessageModel.content.label("content"),
                func.row_number().over(
                    partition_by=MessageModel.session_id,
                    order_by=[MessageModel.created_at.desc(), MessageModel.message_id.desc()],
                ).label("row_rank"),
            )
            .filter(MessageModel.is_archived == False, MessageModel.session_id.in_(session_ids))
            .subquery()
        )
        last_rows = (
            self.session.query(last_ranked.c.session_id, last_ranked.c.role, last_ranked.c.content)
            .filter(last_ranked.c.row_rank == 1)
            .all()
        )
        last_message_by_session = {
            str(row.session_id): {
                "role": row.role,
                "preview": str(row.content or "").strip()[:120],
            }
            for row in last_rows
        }

        unread_threshold_by_session = {
            session_id: _parse_timestamp((metadata_by_session.get(session_id) or {}).get("last_read_at"))
            for session_id in session_ids
        }
        unread_conditions = [
            (
                and_(
                    MessageModel.session_id == session_id,
                    MessageModel.role.in_(["assistant", "system"]),
                    MessageModel.created_at > last_read_at,
                ),
                1,
            )
            for session_id, last_read_at in unread_threshold_by_session.items()
            if last_read_at is not None
        ]
        unread_fallback_session_ids = [
            session_id
            for session_id, last_read_at in unread_threshold_by_session.items()
            if last_read_at is None
        ]
        unread_fallback_case = (
            case(
                (
                    and_(
                        MessageModel.session_id.in_(unread_fallback_session_ids),
                        MessageModel.role.in_(["assistant", "system"]),
                    ),
                    1,
                ),
                else_=0,
            )
            if unread_fallback_session_ids
            else 0
        )
        unread_case = (
            case(*unread_conditions, else_=unread_fallback_case)
            if unread_conditions
            else unread_fallback_case
        )
        count_rows = (
            self.session.query(
                MessageModel.session_id,
                func.sum(case((MessageModel.role == "user", 1), else_=0)).label("user_message_count"),
                func.sum(case((MessageModel.role == "assistant", 1), else_=0)).label("assistant_message_count"),
                func.sum(case((MessageModel.role == "system", 1), else_=0)).label("system_message_count"),
                func.sum(unread_case).label("unread_count"),
            )
            .filter(MessageModel.is_archived == False, MessageModel.session_id.in_(session_ids))
            .group_by(MessageModel.session_id)
            .all()
        )
        count_by_session = {
            str(row.session_id): {
                "user_message_count": int(row.user_message_count or 0),
                "assistant_message_count": int(row.assistant_message_count or 0),
                "system_message_count": int(row.system_message_count or 0),
                "unread_count": int(row.unread_count or 0),
            }
            for row in count_rows
        }

        summaries: List[Dict[str, Any]] = []
        for row in session_rows:
            session_id = str(row.session_id)
            last_message = last_message_by_session.get(session_id) or {}
            role_counts = count_by_session.get(session_id) or {}
            summaries.append(
                {
                    "session_id": session_id,
                    "message_count": int(row.message_count or 0),
                    "user_message_count": int(role_counts.get("user_message_count", 0)),
                    "assistant_message_count": int(role_counts.get("assistant_message_count", 0)),
                    "system_message_count": int(role_counts.get("system_message_count", 0)),
                    "first_message_at": row.first_message_at.isoformat() if row.first_message_at else None,
                    "first_message_role": first_role_by_session.get(session_id),
                    "last_message_at": row.last_message_at.isoformat() if row.last_message_at else None,
                    "last_message_preview": str(last_message.get("preview") or ""),
                    "last_message_role": last_message.get("role"),
                    "unread_count": int(role_counts.get("unread_count", 0)),
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
