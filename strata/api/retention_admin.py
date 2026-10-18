"""
@module api.retention_admin
@purpose Register storage-retention admin endpoints separately from the main API assembly.

Retention maintenance is operational plumbing, not chat or orchestration logic.
Keeping it isolated makes the retention policy easier for small-context models to
inspect without dragging in unrelated API behavior.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import Depends


def register_retention_admin_routes(
    app,
    *,
    get_storage,
    get_retention_policy,
    get_retention_runtime,
    run_retention_maintenance,
) -> Dict[str, Any]:
    exported: Dict[str, Any] = {}

    @app.get("/admin/storage/retention")
    async def get_storage_retention(storage=Depends(get_storage)):
        return {
            "status": "ok",
            "policy": get_retention_policy(storage),
            "runtime": get_retention_runtime(storage),
        }

    @app.post("/admin/storage/retention/run")
    async def run_storage_retention(payload: Dict[str, Any] | None = None, storage=Depends(get_storage)):
        force = bool((payload or {}).get("force", False))
        summary = run_retention_maintenance(storage, force=force)
        return {"status": "ok", "summary": summary}

    exported.update(
        {
            "get_storage_retention": get_storage_retention,
            "run_storage_retention": run_storage_retention,
        }
    )
    return exported
