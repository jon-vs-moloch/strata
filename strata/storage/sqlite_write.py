from __future__ import annotations

import time
from threading import Lock
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError


_sqlite_write_lock = Lock()
_SQLITE_WRITE_RETRIES = 8
_SQLITE_WRITE_BACKOFF_S = 0.15
_SQLITE_WRITE_MAX_TOTAL_WAIT_S = 6.0


def is_sqlite_locked_error(exc: BaseException) -> bool:
    return "database is locked" in str(exc or "").lower()


def _retry_locked(fn, session: Session, *, enabled: bool):
    attempts = 1 if not enabled else _SQLITE_WRITE_RETRIES
    started_at = time.monotonic()
    for index in range(attempts):
        try:
            return fn()
        except OperationalError as exc:
            if not enabled or not is_sqlite_locked_error(exc) or index >= attempts - 1:
                raise
            if (time.monotonic() - started_at) >= _SQLITE_WRITE_MAX_TOTAL_WAIT_S:
                raise OperationalError(
                    str(getattr(exc, "statement", "") or "sqlite write timeout"),
                    getattr(exc, "params", None),
                    Exception(
                        f"database is locked and exceeded bounded write wait of {_SQLITE_WRITE_MAX_TOTAL_WAIT_S:.1f}s"
                    ),
                ) from exc
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
