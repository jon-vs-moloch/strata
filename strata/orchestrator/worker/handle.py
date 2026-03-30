"""
@module orchestrator.worker.handle
@purpose API-facing worker control surface backed by external worker IPC.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from strata.core.lanes import normalize_lane
from strata.orchestrator.worker.runtime_ipc import (
    append_worker_command,
    default_worker_status,
    read_worker_status,
)


class ExternalWorkerHandle:
    def __init__(self, *, stale_after_s: float = 15.0):
        self._stale_after_s = max(1.0, float(stale_after_s))
        self._on_update_callback = None
        self._running_task = None

    def set_on_update(self, callback):
        self._on_update_callback = callback

    def _status_payload(self) -> Dict[str, Any]:
        payload = read_worker_status()
        if not payload:
            return default_worker_status()
        status = dict(payload.get("status") or {})
        updated_at_raw = payload.get("updated_at")
        try:
            updated_at = datetime.fromisoformat(str(updated_at_raw).replace("Z", "+00:00"))
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
        except Exception:
            updated_at = None
        if updated_at is not None:
            age_s = max(0.0, (datetime.now(timezone.utc) - updated_at).total_seconds())
            if age_s > self._stale_after_s:
                stale = default_worker_status("Worker status heartbeat is stale.")
                stale["last_updated_at"] = updated_at.isoformat()
                stale["heartbeat_age_s"] = round(age_s, 2)
                return stale
        status["last_updated_at"] = updated_at.isoformat() if updated_at is not None else None
        return status or default_worker_status()

    @property
    def status(self):
        return self._status_payload()

    async def enqueue(self, task_id: str):
        append_worker_command("enqueue", task_id=str(task_id))

    def pause(self, lane: str | None = None):
        append_worker_command("pause_worker", lane=normalize_lane(lane))

    def resume(self, lane: str | None = None):
        append_worker_command("resume_worker", lane=normalize_lane(lane))

    def stop_current(self, lane: str | None = None):
        append_worker_command("stop_worker", lane=normalize_lane(lane))
        return True

    async def wait_until_idle(self, timeout: float = 5.0, lane: Optional[str] = None) -> bool:
        normalized_lane = normalize_lane(lane)
        deadline = asyncio.get_running_loop().time() + max(0.1, float(timeout))
        while asyncio.get_running_loop().time() < deadline:
            status = self.status
            if normalized_lane:
                details = dict((status.get("lane_details") or {}).get(normalized_lane) or {})
                if not details.get("current_task_id") and not int(details.get("queue_depth") or 0):
                    return True
            else:
                lane_details = dict(status.get("lane_details") or {})
                if all(
                    not dict(lane_details.get(name) or {}).get("current_task_id")
                    and not int(dict(lane_details.get(name) or {}).get("queue_depth") or 0)
                    for name in ("trainer", "agent")
                ):
                    return True
            await asyncio.sleep(0.1)
        return False

    def clear_queue(self, lane: str | None = None) -> int:
        append_worker_command("clear_queue", lane=normalize_lane(lane))
        return 0

    async def enqueue_runnable_tasks(self, lane: Optional[str] = None) -> int:
        append_worker_command("replay_pending", lane=normalize_lane(lane))
        return 0

    def pause_task(self, task_id: str) -> bool:
        append_worker_command("pause_task", task_id=str(task_id))
        return True

    async def resume_task(self, task_id: str) -> bool:
        append_worker_command("resume_task", task_id=str(task_id))
        return True

    def stop_task(self, task_id: str) -> bool:
        append_worker_command("stop_task", task_id=str(task_id))
        return True

