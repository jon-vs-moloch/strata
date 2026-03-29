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

from fastapi import Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from strata.core.lanes import normalize_lane
from strata.context.loaded_files import list_loaded_context_files, load_context_file, unload_context_file
from strata.observability.context import get_context_load_telemetry, scan_codebase_context_pressure
from strata.procedures.registry import get_procedure, list_procedures, queue_procedure, save_procedure
from strata.schemas.execution import TrainerExecutionContext, AgentExecutionContext


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
    event_broadcaster,
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
                pool = model_adapter.registry.pools.get(context.mode)
                effective_allow_cloud = (
                    pool.allow_cloud
                    if pool is not None and context.allow_cloud is None
                    else context.allow_cloud
                )
                effective_allow_local = (
                    pool.allow_local
                    if pool is not None and context.allow_local is None
                    else context.allow_local
                )
                return {
                    "label": label,
                    "mode": context.mode,
                    "provider": endpoint.provider,
                    "model": endpoint.model,
                    "transport": endpoint.transport,
                    "endpoint_url": endpoint.endpoint_url,
                    "selected_model": preferred_model or endpoint.model,
                    "allow_cloud": effective_allow_cloud,
                    "allow_local": effective_allow_local,
                    "preferred_transport": getattr(pool, "preferred_transport", None),
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
            job_kind = str(system_job.get("kind") or "")
            if job_kind not in {"bootstrap_cycle", "eval_matrix"}:
                continue
            supervision_jobs.append(
                {
                    "task_id": task.task_id,
                    "title": task.title,
                    "kind": job_kind,
                    "state": task.state.value.lower(),
                    "updated_at": task.updated_at.isoformat() if task.updated_at else None,
                }
            )

        trainer_route = resolve_route("trainer", TrainerExecutionContext(run_id="admin-chat-routing"))
        agent_route = resolve_route("agent", AgentExecutionContext(run_id="admin-agent-routing"))

        return {
            "status": "ok",
            "routing": {
                "chat": {
                    **trainer_route,
                    "description": "Chat requests use the trainer route by default and fall back to the agent route if the cloud endpoint rejects the instruction or tool format.",
                },
                "trainer": trainer_route,
                "agent": agent_route,
                "supervision": {
                    "launcher_default_enabled": True,
                    "active_jobs": supervision_jobs,
                    "description": "Continuous self-improvement runs bootstrap cycles and sampled evals through the background worker.",
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

    @app.get("/admin/procedures")
    async def get_procedure_list(storage=Depends(get_storage)):
        return {"status": "ok", "procedures": list_procedures(storage)}

    @app.get("/admin/procedures/{procedure_id}")
    async def get_procedure_detail(procedure_id: str, storage=Depends(get_storage)):
        try:
            procedure = get_procedure(storage, procedure_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"status": "ok", "procedure": procedure}

    @app.post("/admin/procedures")
    async def upsert_procedure(payload: Dict[str, Any], storage=Depends(get_storage)):
        procedure = save_procedure(storage, payload)
        storage.commit()
        return {"status": "ok", "procedure": procedure}

    @app.post("/admin/procedures/{procedure_id}/queue")
    async def enqueue_procedure(procedure_id: str, payload: Dict[str, Any] | None = None, storage=Depends(get_storage)):
        payload = dict(payload or {})
        try:
            task = queue_procedure(
                storage,
                worker,
                procedure_id=procedure_id,
                session_id=payload.get("session_id"),
                lane=payload.get("lane"),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        await worker.enqueue(task.task_id)
        return {"status": "queued", "task_id": task.task_id, "procedure_id": procedure_id}

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

    async def perform_fresh_start(storage):
        from strata.storage.models import Base

        preserved_settings = dict(global_settings)
        engine = storage.engine
        worker.pause()
        aborted = worker.stop_current()
        await worker.wait_until_idle(timeout=10.0)
        cleared_queue = worker.clear_queue()

        storage.close()
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)

        restored = storage.__class__()
        onboarding_task_id = None
        try:
            restored.parameters.set_parameter(
                key=settings_parameter_key,
                value=preserved_settings,
                description=settings_parameter_description,
            )
            onboarding_task = None
            try:
                onboarding_task = queue_procedure(
                    restored,
                    worker,
                    procedure_id="operator_onboarding",
                )
                onboarding_task_id = str(getattr(onboarding_task, "task_id", "") or "") or None
            except Exception:
                onboarding_task = None
            restored.commit()
        finally:
            restored.close()

        return {
            "status": "ok",
            "message": "Fresh start complete. Runtime state was cleared and the worker remains paused.",
            "fresh_start": True,
            "worker_paused": True,
            "aborted_active_task": bool(aborted),
            "cleared_queue": cleared_queue,
            "preserved_settings": True,
            "seeded_onboarding": bool(onboarding_task),
            "onboarding_task_id": onboarding_task_id,
        }

    @app.post("/admin/fresh-start")
    async def fresh_start(storage=Depends(get_storage)):
        return await perform_fresh_start(storage)

    @app.post("/admin/reset")
    async def reset_database(storage=Depends(get_storage)):
        return await perform_fresh_start(storage)

    @app.get("/admin/worker/status")
    async def get_worker_status():
        return {"status": worker.status}

    @app.post("/admin/worker/pause")
    async def pause_worker(lane: str | None = None):
        normalized_lane = normalize_lane(lane)
        if lane is not None and normalized_lane is None:
            raw_lane = str(lane or "").strip().lower()
            if raw_lane:
                raise HTTPException(status_code=400, detail="lane must be 'trainer' or 'agent'")
        worker.pause(normalized_lane)
        return {"status": "paused", "lane": normalized_lane}

    @app.post("/admin/worker/resume")
    async def resume_worker(lane: str | None = None):
        normalized_lane = normalize_lane(lane)
        if lane is not None and normalized_lane is None:
            raw_lane = str(lane or "").strip().lower()
            if raw_lane:
                raise HTTPException(status_code=400, detail="lane must be 'trainer' or 'agent'")
        worker.resume(normalized_lane)
        replayed = await worker.enqueue_runnable_tasks(normalized_lane)
        return {"status": "running", "lane": normalized_lane, "replayed": replayed}

    @app.post("/admin/worker/stop")
    async def stop_worker(lane: str | None = None):
        normalized_lane = normalize_lane(lane)
        if lane is not None and normalized_lane is None:
            raw_lane = str(lane or "").strip().lower()
            if raw_lane:
                raise HTTPException(status_code=400, detail="lane must be 'trainer' or 'agent'")
        aborted = worker.stop_current(normalized_lane)
        return {"status": "stopped", "aborted": aborted, "lane": normalized_lane}

    @app.post("/admin/tasks/{task_id}/pause")
    async def pause_task(task_id: str):
        paused = worker.pause_task(task_id)
        if not paused:
            raise HTTPException(status_code=404, detail="task not found or not pausable")
        return {"status": "paused", "task_id": task_id}

    @app.post("/admin/tasks/{task_id}/resume")
    async def resume_task(task_id: str):
        resumed = await worker.resume_task(task_id)
        if not resumed:
            raise HTTPException(status_code=404, detail="task not found or not resumable")
        return {"status": "running", "task_id": task_id}

    @app.post("/admin/tasks/{task_id}/stop")
    async def stop_task(task_id: str):
        stopped = worker.stop_task(task_id)
        if not stopped:
            raise HTTPException(status_code=404, detail="task not found or not stoppable")
        return {"status": "cancelled", "task_id": task_id}

    @app.get("/events")
    async def sse_events(request: Request):
        async def event_generator():
            queue = await event_broadcaster.subscribe()
            try:
                while True:
                    try:
                        if await request.is_disconnected():
                            break
                        data = await asyncio.wait_for(queue.get(), timeout=15.0)
                        yield f"data: {json.dumps(data)}\n\n"
                    except asyncio.TimeoutError:
                        # Keep the SSE connection warm without retaining server-side backlog.
                        yield ": keepalive\n\n"
                    except Exception:
                        break
            finally:
                await event_broadcaster.unsubscribe(queue)

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
