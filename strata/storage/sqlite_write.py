from __future__ import annotations

import time
from threading import Lock
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError


_sqlite_write_lock = Lock()
_SQLITE_WRITE_RETRIES = 8
_SQLITE_WRITE_BACKOFF_S = 0.15


def is_sqlite_locked_error(exc: BaseException) -> bool:
    return "database is locked" in str(exc or "").lower()


def _retry_locked(fn, session: Session, *, enabled: bool):
    attempts = 1 if not enabled else _SQLITE_WRITE_RETRIES
    for index in range(attempts):
        try:
            return fn()
        except OperationalError as exc:
            if not enabled or not is_sqlite_locked_error(exc) or index >= attempts - 1:
                raise
            session.rollback()
            time.sleep(_SQLITE_WRITE_BACKOFF_S * (index + 1))


def flush_with_write_lock(session: Session, *, enabled: bool) -> None:
    def _flush():
        if enabled:
            with _sqlite_write_lock:
                session.flush()
            return
        session.flush()
    _retry_locked(_flush, session, enabled=enabled)


def commit_with_write_lock(session: Session, *, enabled: bool) -> None:
    def _commit():
        if enabled:
            with _sqlite_write_lock:
                session.commit()
            return
        session.commit()
    _retry_locked(_commit, session, enabled=enabled)
