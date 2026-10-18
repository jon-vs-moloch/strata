"""
@module api.api_token
@purpose Own the per-launch API token: create it, publish it to a local file, read it back.
@owns token value for this process, token file location, safe token-file IO
@does_not_own request checks (see strata.api.local_guard)
@key_exports TOKEN_HEADER, current_token, token_file_path, write_token_file, read_token_file

Every state-changing API request must carry the token in the ``X-Strata-Token``
header. The workbench gets it from the API's bootstrap endpoint; local scripts
(for example ``scripts/bootstrap_supervisor.py``) read it from the token file,
which only the operator's own account can read.

This module is deliberately standard-library only, so stdlib-only scripts can
import it.
"""

from __future__ import annotations

import os
import secrets
import stat
import tempfile
from pathlib import Path
from typing import Optional

TOKEN_HEADER = "X-Strata-Token"
TOKEN_ENV = "STRATA_API_TOKEN"
TOKEN_FILE_ENV = "STRATA_API_TOKEN_FILE"
DEFAULT_TOKEN_FILE = "strata/runtime/api-token"
_MAX_TOKEN_FILE_BYTES = 4096

# One token per API process. A restart (or a uvicorn reload) issues a new one;
# the workbench fetches it again when the API answers 401.
_TOKEN = str(os.environ.get(TOKEN_ENV) or "").strip() or secrets.token_urlsafe(32)


def current_token() -> str:
    return _TOKEN


def token_file_path() -> Path:
    return Path(os.environ.get(TOKEN_FILE_ENV) or DEFAULT_TOKEN_FILE)


def write_token_file(path: Optional[Path] = None) -> Path:
    """Publish the token to a 0600 file, atomically and without following a planted symlink."""
    target = Path(path or token_file_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".api-token-", dir=str(target.parent))  # created 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(_TOKEN + "\n")
        os.replace(tmp_name, target)  # replaces a symlink at target rather than writing through it
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return target


def read_token_file(path: Optional[Path] = None) -> Optional[str]:
    """Read a token file written by write_token_file. Returns None if it is missing or not a regular file.

    Opens with O_NONBLOCK and O_NOFOLLOW and checks the opened file is regular, so a
    FIFO or symlink planted at the path can neither hang nor redirect the read.
    """
    target = Path(path or token_file_path())
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(target), flags)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        raw = os.read(fd, _MAX_TOKEN_FILE_BYTES)
    finally:
        os.close(fd)
    value = raw.decode("utf-8", errors="replace").strip()
    return value or None


def resolve_client_token(path: Optional[Path] = None) -> Optional[str]:
    """Token for a local client: STRATA_API_TOKEN if set, else the token file."""
    return str(os.environ.get(TOKEN_ENV) or "").strip() or read_token_file(path)
