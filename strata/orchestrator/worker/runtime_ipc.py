"""
@module orchestrator.worker.runtime_ipc
@purpose File-backed control and status exchange between API and worker processes.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from uuid import uuid4


_ROOT_DIR = Path(__file__).resolve().parents[3]
_RUNTIME_DIR = _ROOT_DIR / "strata" / "runtime"
STATUS_PATH = _RUNTIME_DIR / "worker_status.json"
COMMAND_PATH = _RUNTIME_DIR / "worker_commands.jsonl"


def ensure_runtime_dir() -> None:
    _RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_worker_status(payload: Dict[str, Any]) -> None:
    ensure_runtime_dir()
    status_payload = {
        "updated_at": _now_iso(),
        **dict(payload or {}),
    }
    tmp_path = STATUS_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(status_payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp_path, STATUS_PATH)


def read_worker_status() -> Optional[Dict[str, Any]]:
    try:
        raw = STATUS_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def append_worker_command(action: str, **payload: Any) -> str:
    ensure_runtime_dir()
    command_id = str(uuid4())
    record = {
        "command_id": command_id,
        "action": str(action or "").strip(),
        "issued_at": _now_iso(),
        "payload": dict(payload or {}),
    }
    with COMMAND_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True))
        handle.write("\n")
    return command_id


def worker_command_cursor() -> int:
    ensure_runtime_dir()
    try:
        return COMMAND_PATH.stat().st_size
    except FileNotFoundError:
        return 0


def read_worker_commands(cursor: int) -> Tuple[int, List[Dict[str, Any]]]:
    ensure_runtime_dir()
    try:
        with COMMAND_PATH.open("r", encoding="utf-8") as handle:
            handle.seek(max(0, int(cursor or 0)))
            commands: List[Dict[str, Any]] = []
            while True:
                line = handle.readline()
                if not line:
                    return handle.tell(), commands
                text = line.strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    commands.append(payload)
    except FileNotFoundError:
        return 0, []


def default_worker_status(reason: str = "Worker daemon is offline.") -> Dict[str, Any]:
    return {
        "worker": "STOPPED",
        "global_paused": False,
        "paused_lanes": [],
        "tiers": {"trainer": "unknown", "agent": "unknown"},
        "lanes": {"trainer": "STOPPED", "agent": "STOPPED"},
        "lane_details": {
            "trainer": {
                "status": "STOPPED",
                "tier_health": "unknown",
                "activity_mode": "STOPPED",
                "activity_label": "Stopped",
                "activity_reason": reason,
                "queue_depth": 0,
                "current_task_id": None,
                "current_task_started_at": None,
                "current_task_title": "",
                "current_task_state": None,
                "active_attempt_id": None,
                "step": "",
                "step_label": "",
                "step_detail": "",
                "step_updated_at": None,
                "progress_label": "",
                "recent_steps": [],
                "ticker_items": [],
                "last_activity_at": None,
                "heartbeat_state": "unknown",
                "heartbeat_age_s": None,
            },
            "agent": {
                "status": "STOPPED",
                "tier_health": "unknown",
                "activity_mode": "STOPPED",
                "activity_label": "Stopped",
                "activity_reason": reason,
                "queue_depth": 0,
                "current_task_id": None,
                "current_task_started_at": None,
                "current_task_title": "",
                "current_task_state": None,
                "active_attempt_id": None,
                "step": "",
                "step_label": "",
                "step_detail": "",
                "step_updated_at": None,
                "progress_label": "",
                "recent_steps": [],
                "ticker_items": [],
                "last_activity_at": None,
                "heartbeat_state": "unknown",
                "heartbeat_age_s": None,
            },
        },
    }

