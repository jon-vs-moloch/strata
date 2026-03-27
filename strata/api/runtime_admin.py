"""
@module api.runtime_admin
@purpose Register operator/runtime endpoints separately from the main API assembly.

These routes expose live controls, health, model selection, and process-level
operations. Keeping them separate reduces the amount of unrelated code a small
model must ingest to reason about app assembly or chat behavior.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from typing import Any, Dict

from fastapi import Depends, HTTPException
from fastapi.responses import StreamingResponse
from strata.context.loaded_files import list_loaded_context_files, load_context_file, unload_context_file
from strata.observability.context import get_context_load_telemetry, scan_codebase_context_pressure
from strata.schemas.execution import StrongExecutionContext, WeakExecutionContext


def register_runtime_admin_routes(
    app,
    *,
    get_storage,
    model_adapter,
    global_settings,
    normalized_settings,
    settings_parameter_key: str,
    settings_parameter_description: str,
    worker,
    event_queue,
    hotreloader,
    base_dir: str,
) -> Dict[str, Any]:
    exported: Dict[str, Any] = {}

    @app.get("/models")
    async def list_models():
        import httpx

        base = model_adapter.endpoint.rsplit("/v1/", 1)[0]
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{base}/v1/models", timeout=5.0)
                resp.raise_for_status()
                data = resp.json()
                models = [m.get("id", m.get("name", "unknown")) for m in data.get("data", [])]
                return {"status": "ok", "models": models, "current": model_adapter.active_model}
        except Exception as exc:
            return {"status": "error", "models": [], "message": str(exc)}

    @app.post("/models/select")
    async def select_model(payload: Dict[str, Any]):
        model_id = payload.get("model")
        if not model_id:
            raise HTTPException(status_code=400, detail="model field required")
        model_adapter.active_model = model_id
        return {"status": "ok", "model": model_id}

    @app.get("/admin/test")
    async def test_connectivity():
        return {
            "status": "ok",
            "timestamp": datetime.utcnow().isoformat(),
            "llm_endpoint": model_adapter.endpoint,
        }

    @app.get("/admin/settings")
    async def get_settings(storage=Depends(get_storage)):
        persisted_settings = storage.parameters.get_parameter(
            key=settings_parameter_key,
            default_value=dict(global_settings),
            description=settings_parameter_description,
        ) or {}
        merged_settings = normalized_settings(persisted_settings)
        global_settings.update(merged_settings)
        storage.commit()
        return {"status": "ok", "settings": merged_settings}

    @app.post("/admin/settings")
    async def update_settings(payload: Dict[str, Any], storage=Depends(get_storage)):
        merged_settings = normalized_settings(payload)
        global_settings.update(merged_settings)
        storage.parameters.set_parameter(
            key=settings_parameter_key,
            value=merged_settings,
            description=settings_parameter_description,
        )
        storage.commit()
        return {"status": "ok", "settings": merged_settings}

    @app.get("/admin/registry")
    async def get_registry():
        from strata.models.registry import registry

        return {"status": "ok", "config": registry.to_dict()}

    @app.get("/admin/registry/presets")
    async def get_registry_presets():
        from strata.models.registry import registry

        return {"status": "ok", "presets": registry.presets()}

    @app.post("/admin/registry")
    async def update_registry(payload: Dict[str, Any]):
        from strata.models.registry import registry

        registry._load_config(payload)
        return {"status": "ok"}

    @app.get("/admin/health")
    async def health_check():
        from sqlalchemy import text
        from strata.storage.services.main import StorageManager

        storage = StorageManager()
        try:
            db = storage.session
            db.execute(text("SELECT 1"))
            worker_alive = worker._running_task is not None and not worker._running_task.done()
            return {
                "status": "ok",
                "database": "connected",
                "worker": "running" if worker_alive else "dead",
            }
        except Exception as exc:
            return {"status": "degraded", "error": str(exc)}
        finally:
            storage.close()

    @app.get("/admin/routing")
    async def get_routing_summary(storage=Depends(get_storage)):
        from strata.storage.models import TaskModel, TaskState, TaskType

        def resolve_route(label: str, context):
            try:
                preferred_model = model_adapter._selected_models.get(context.mode)
                endpoint = model_adapter.registry.resolve_endpoint_for_context(
                    context,
                    preferred_model=preferred_model,
                )
                return {
                    "label": label,
                    "mode": context.mode,
                    "provider": endpoint.provider,
                    "model": endpoint.model,
                    "transport": endpoint.transport,
                    "endpoint_url": endpoint.endpoint_url,
                    "selected_model": preferred_model or endpoint.model,
                    "allow_cloud": context.allow_cloud,
                    "allow_local": context.allow_local,
                    "status": worker.status.get("tiers", {}).get(label, "unknown"),
                }
            except Exception as exc:
                return {
                    "label": label,
                    "mode": context.mode,
                    "status": "error",
                    "error": str(exc),
                }

        active_bootstrap_jobs = (
            storage.session.query(TaskModel)
            .filter(TaskModel.type == TaskType.JUDGE)
            .all()
        )
        supervision_jobs = []
        for task in active_bootstrap_jobs:
            if task.state not in {TaskState.PENDING, TaskState.WORKING}:
                continue
            system_job = dict((task.constraints or {}).get("system_job") or {})
            if str(system_job.get("kind") or "") != "bootstrap_cycle":
                continue
            supervision_jobs.append(
                {
                    "task_id": task.task_id,
                    "title": task.title,
                    "state": task.state.value.lower(),
                    "updated_at": task.updated_at.isoformat() if task.updated_at else None,
                }
            )

        chat_route = resolve_route("Strong", StrongExecutionContext(run_id="admin-chat-routing"))
        weak_route = resolve_route("Weak", WeakExecutionContext(run_id="admin-weak-routing"))

        return {
            "status": "ok",
            "routing": {
                "chat": {
                    **chat_route,
                    "description": "Chat requests use the strong tier by default and fall back to weak if the cloud endpoint rejects the instruction/tool format.",
                },
                "strong": chat_route,
                "weak": weak_route,
                "supervision": {
                    "launcher_default_enabled": False,
                    "active_jobs": supervision_jobs,
                    "description": "Continuous bootstrap cycles are healthiest when queued onto the background worker.",
                },
            },
        }

    @app.get("/admin/context/telemetry")
    async def get_context_telemetry(storage=Depends(get_storage)):
        return {"status": "ok", "context": get_context_load_telemetry(storage)}

    @app.get("/admin/context/loaded")
    async def get_loaded_context(storage=Depends(get_storage)):
        return {"status": "ok", "loaded": list_loaded_context_files(storage)}

    @app.post("/admin/context/load")
    async def admin_load_context(payload: Dict[str, Any], storage=Depends(get_storage)):
        path = str(payload.get("path") or "").strip()
        if not path:
            raise HTTPException(status_code=400, detail="path required")
        return {"status": "ok", "result": load_context_file(storage, path, source="admin.context.load", base_dir=base_dir)}

    @app.post("/admin/context/unload")
    async def admin_unload_context(payload: Dict[str, Any], storage=Depends(get_storage)):
        path = str(payload.get("path") or "").strip()
        if not path:
            raise HTTPException(status_code=400, detail="path required")
        return {"status": "ok", "result": unload_context_file(storage, path, base_dir=base_dir)}

    @app.post("/admin/context/scan")
    async def rescan_context_pressure(storage=Depends(get_storage)):
        return {"status": "ok", "scan": scan_codebase_context_pressure(storage, base_dir=base_dir)}

    @app.get("/admin/logs")
    async def get_logs(limit: int = 50):
        log_path = "/tmp/strata_backend.log"
        if not os.path.exists(log_path):
            return {"logs": ["Log file not found."]}
        with open(log_path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
        return {"logs": [line.strip() for line in lines[-limit:]]}

    @app.post("/admin/reboot")
    async def reboot_api():
        import sys

        async def restart_soon():
            await asyncio.sleep(0.5)
            os.execv(sys.executable, [sys.executable] + sys.argv)

        asyncio.create_task(restart_soon())
        return {"status": "rebooting"}

    @app.get("/admin/files")
    async def list_experimental_files():
        return {"files": hotreloader.list_experimental()}

    @app.post("/admin/promote")
    async def promote_file(payload: Dict[str, Any]):
        module = payload.get("module")
        if not module:
            raise HTTPException(status_code=400, detail="module field required")
        result = await hotreloader.promote(module)
        return {
            "success": result.success,
            "module": result.module,
            "rolled_back": result.rolled_back,
            "message": result.message,
            "validation": result.validation.stages if result.validation else None,
        }

    @app.post("/admin/rollback")
    async def rollback_file(payload: Dict[str, Any]):
        module = payload.get("module")
        if not module:
            raise HTTPException(status_code=400, detail="module field required")
        result = hotreloader.rollback(module)
        return {"success": result.success, "module": result.module, "message": result.message}

    @app.post("/admin/reset")
    async def reset_database(storage=Depends(get_storage)):
        from strata.storage.models import Base

        storage.session.close()
        Base.metadata.drop_all(storage.engine)
        Base.metadata.create_all(storage.engine)
        storage.session = storage.SessionLocal()
        storage.tasks.session = storage.session
        storage.messages.session = storage.session
        storage.attempts.session = storage.session
        storage.parameters.session = storage.session
        return {"status": "ok", "message": "Database reset complete."}

    @app.get("/admin/worker/status")
    async def get_worker_status():
        return {"status": worker.status}

    @app.post("/admin/worker/pause")
    async def pause_worker():
        worker.pause()
        return {"status": "paused"}

    @app.post("/admin/worker/resume")
    async def resume_worker():
        worker.resume()
        return {"status": "running"}

    @app.post("/admin/worker/stop")
    async def stop_worker():
        aborted = worker.stop_current()
        return {"status": "stopped", "aborted": aborted}

    @app.get("/events")
    async def sse_events():
        async def event_generator():
            while True:
                try:
                    data = await event_queue.get()
                    yield f"data: {json.dumps(data)}\n\n"
                except Exception:
                    break

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    exported.update(
        {
            "list_models": list_models,
            "select_model": select_model,
            "test_connectivity": test_connectivity,
            "get_settings": get_settings,
            "update_settings": update_settings,
            "get_registry": get_registry,
            "get_registry_presets": get_registry_presets,
            "update_registry": update_registry,
            "health_check": health_check,
            "get_routing_summary": get_routing_summary,
            "get_context_telemetry": get_context_telemetry,
            "rescan_context_pressure": rescan_context_pressure,
            "get_logs": get_logs,
            "reboot_api": reboot_api,
            "list_experimental_files": list_experimental_files,
            "promote_file": promote_file,
            "rollback_file": rollback_file,
            "reset_database": reset_database,
            "get_worker_status": get_worker_status,
            "pause_worker": pause_worker,
            "resume_worker": resume_worker,
            "stop_worker": stop_worker,
            "sse_events": sse_events,
        }
    )
    return exported
