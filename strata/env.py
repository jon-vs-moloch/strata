"""
@module env
@purpose Lightweight local environment loading for direct Strata launches.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_local_env(root: str | Path | None = None, *, filename: str = ".env.local") -> bool:
    base = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    env_path = base / filename
    if not env_path.exists():
        return False

    loaded = False
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if not key or key in os.environ:
            continue
        os.environ[key] = value
        loaded = True
    return loaded
