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
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from strata.core.lanes import normalize_lane, normalize_work_pool
from strata.context.loaded_files import list_loaded_context_files, load_context_file, unload_context_file
from strata.experimental.trace_review import list_attempt_observability_artifacts
from strata.models.providers import GenericOpenAICompatibleProvider
from strata.observability.context import get_context_load_telemetry, scan_codebase_context_pressure
from strata.observability.host import get_host_telemetry_snapshot
from strata.orchestrator.capability_incidents import list_capability_incidents
from strata.procedures.registry import get_procedure, list_procedures, queue_procedure, save_procedure
from strata.schemas.execution import TrainerExecutionContext, AgentExecutionContext, LocalAgentExecutionContext, RemoteAgentExecutionContext
from strata.storage.models import task_state_api_value


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

    def _workbench_adapter_for_target(lane: str | None, work_pool: str | None = None):
        normalized_lane = normalize_lane(lane) or "agent"
        normalized_work_pool = normalize_work_pool(work_pool) or normalize_work_pool("trainer" if normalized_lane == "trainer" else "local_agent")
        active_adapter = model_adapter
        try:
            lane_adapter = None
            if hasattr(worker, "_work_pool_model"):
                lane_adapter = worker._work_pool_model(normalized_work_pool)
            if lane_adapter is None and hasattr(worker, "_lane_model"):
                lane_adapter = worker._lane_model(normalized_lane)
            if lane_adapter is not None:
                active_adapter = lane_adapter
        except Exception:
            active_adapter = model_adapter
        try:
            if normalized_work_pool == "trainer":
                context = TrainerExecutionContext(run_id=f"workbench:{normalized_work_pool}")
            elif normalized_work_pool == "remote_agent":
                context = RemoteAgentExecutionContext(run_id=f"workbench:{normalized_work_pool}")
            elif normalized_work_pool == "local_agent":
                context = LocalAgentExecutionContext(run_id=f"workbench:{normalized_work_pool}")
            else:
                context = (
                    TrainerExecutionContext(run_id=f"workbench:{normalized_lane}")
                    if normalized_lane == "trainer"
                    else AgentExecutionContext(run_id=f"workbench:{normalized_lane}")
                )
            if hasattr(active_adapter, "bind_execution_context"):
                active_adapter.bind_execution_context(context)
        except Exception:
            pass
        return normalized_lane, normalized_work_pool, active_adapter

    def _resolve_workbench_target(
        *,
        lane: str | None,
        work_pool: str | None,
        procedure: Dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        effective_lane = normalize_lane(lane) or normalize_lane((procedure or {}).get("target_lane")) or "agent"
        effective_work_pool = (
            normalize_work_pool(work_pool)
            or normalize_work_pool((procedure or {}).get("target_work_pool"))
            or normalize_work_pool((procedure or {}).get("target_execution_profile"))
            or normalize_work_pool("trainer" if effective_lane == "trainer" else "local_agent")
        )
        return effective_lane, effective_work_pool

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
        GenericOpenAICompatibleProvider.set_runtime_policy(
            merged_settings.get("inference_throttle_policy") or {}
        )
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

    @app.get("/admin/registry/catalog")
    async def get_registry_catalog():
        import httpx
        from strata.models.registry import registry

        def _base_models_url(raw_url: str) -> str:
            value = str(raw_url or "").strip()
            if not value:
                return ""
            parsed = urlparse(value)
            path = parsed.path or ""
            if path.endswith("/chat/completions"):
                path = path[: -len("/chat/completions")]
            if not path.endswith("/models"):
                if path.endswith("/v1"):
                    path = f"{path}/models"
                else:
                    path = f"{path.rstrip('/')}/v1/models"
            return parsed._replace(path=path, params="", query="", fragment="").geturl()

        catalog: Dict[str, Any] = {}
        async with httpx.AsyncClient() as client:
            for pool_name, pool in (registry.pools or {}).items():
                entries = []
                for index, endpoint in enumerate(list(pool.endpoints or [])):
                    models_url = _base_models_url(endpoint.endpoint_url or "")
                    entry = {
                        "index": index,
                        "provider": endpoint.provider,
                        "model": endpoint.model,
                        "transport": endpoint.transport,
                        "endpoint_url": endpoint.endpoint_url,
                        "models_url": models_url,
                    }
                    if not models_url:
                        entry["status"] = "missing_endpoint"
                        entries.append(entry)
                        continue
                    try:
                        resp = await client.get(models_url, timeout=5.0)
                        resp.raise_for_status()
                        data = resp.json()
                        models = [
                            str(item.get("id") or item.get("name") or "").strip()
                            for item in list(data.get("data") or [])
                            if isinstance(item, dict)
                        ]
                        models = [item for item in models if item]
                        entry["status"] = "ok"
                        entry["models"] = models
                        entry["configured_model_present"] = str(endpoint.model or "") in set(models)
                    except Exception as exc:
                        entry["status"] = "error"
                        entry["error"] = str(exc)
                    entries.append(entry)
                catalog[pool_name] = {
                    "allow_local": bool(getattr(pool, "allow_local", True)),
                    "allow_cloud": bool(getattr(pool, "allow_cloud", True)),
                    "preferred_transport": getattr(pool, "preferred_transport", None),
                    "endpoints": entries,
                }
        return {"status": "ok", "catalog": catalog}

    @app.get("/admin/health")
    async def health_check():
        from strata.storage.services.main import StorageManager

        storage = StorageManager()
        try:
            with storage.engine.connect() as connection:
                connection.exec_driver_sql("SELECT 1")
            worker_status = dict(getattr(worker, "status", {}) or {})
            worker_alive = str(worker_status.get("worker") or "").upper() in {"RUNNING", "PAUSED"}
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

        def _selected_model_for_mode(mode: str) -> str | None:
            worker_lane_model = None
            try:
                worker_lane_model = worker._lane_model(mode) if hasattr(worker, "_lane_model") else None
            except Exception:
                worker_lane_model = None
            if worker_lane_model is not None:
                selected = getattr(worker_lane_model, "_selected_models", {}) or {}
                resolved = str(selected.get(mode) or "").strip()
                if resolved:
                    return resolved
            selected = getattr(model_adapter, "_selected_models", {}) or {}
            resolved = str(selected.get(mode) or "").strip()
            return resolved or None

        def resolve_route(label: str, context):
            try:
                preferred_model = _selected_model_for_mode(context.mode)
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
                    "state": task_state_api_value(task.state),
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

    @app.get("/admin/host/telemetry")
    async def get_host_telemetry():
        return {"status": "ok", "host": get_host_telemetry_snapshot()}

    @app.get("/admin/observability/attempts")
    async def get_attempt_observability(
        task_id: str | None = None,
        attempt_id: str | None = None,
        session_id: str | None = None,
        artifact_kind: str | None = None,
        limit: int = 25,
        storage=Depends(get_storage),
    ):
        artifacts = list_attempt_observability_artifacts(
            storage,
            task_id=task_id,
            attempt_id=attempt_id,
            session_id=session_id,
            artifact_kind=artifact_kind,
            limit=limit,
        )
        return {
            "status": "ok",
            "artifacts": artifacts,
            "filters": {
                "task_id": task_id,
                "attempt_id": attempt_id,
                "session_id": session_id,
                "artifact_kind": artifact_kind,
                "limit": max(1, min(limit, 200)),
            },
        }

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

    @app.post("/admin/context/clear")
    async def clear_loaded_context(storage=Depends(get_storage)):
        from strata.context.loaded_files import LOADED_CONTEXT_FILES_DESCRIPTION, LOADED_CONTEXT_FILES_KEY

        storage.parameters.set_parameter(
            LOADED_CONTEXT_FILES_KEY,
            {"files": [], "budget_tokens": 3200},
            description=LOADED_CONTEXT_FILES_DESCRIPTION,
        )
        storage.commit()
        return {"status": "ok", "cleared": "loaded_context"}

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
                work_pool=payload.get("work_pool"),
                execution_profile=payload.get("execution_profile"),
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
        preflight_task_id = None
        try:
            restored.parameters.set_parameter(
                key=settings_parameter_key,
                value=preserved_settings,
                description=settings_parameter_description,
            )
            preflight_task = None
            try:
                preflight_task = queue_procedure(
                    restored,
                    worker,
                    procedure_id="preflight",
                )
                preflight_task_id = str(getattr(preflight_task, "task_id", "") or "") or None
            except Exception:
                preflight_task = None
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
            "seeded_preflight": bool(preflight_task),
            "preflight_task_id": preflight_task_id,
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

    @app.post("/admin/worker/clear_queue")
    async def clear_worker_queue():
        cleared = worker.clear_queue()
        return {"status": "ok", "cleared_queue": cleared}

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
        return {"status": "stopped", "task_id": task_id}

    @app.post("/admin/tasks/{task_id}/replay")
    async def replay_task(task_id: str, payload: Dict[str, Any] | None = None):
        replayed = await worker.replay_task(task_id, overrides=payload)
        if not replayed:
            raise HTTPException(status_code=404, detail="task not found or not replayable")
        return {"status": "replayed", "task_id": task_id}

    @app.post("/admin/tasks/{task_id}/branch")
    async def branch_task(task_id: str, payload: Dict[str, Any], storage=Depends(get_storage)):
        from strata.storage.models import TaskModel
        original = storage.tasks.get_by_id(task_id)
        if not original:
            raise HTTPException(status_code=404, detail="original task not found")

        new_title = payload.get("title") or f"[BRANCH] {original.title}"
        new_desc = payload.get("description") or original.description
        new_constraints = dict(original.constraints or {})
        new_constraints.update(payload.get("constraints") or {})

        branch = storage.tasks.create(
            title=new_title,
            description=new_desc,
            type=original.type,
            session_id=original.session_id,
            parent_task_id=original.parent_task_id,
            depth=original.depth,
            constraints=new_constraints,
        )
        storage.commit()
        await worker.enqueue(branch.task_id)
        return {"status": "branched", "original": task_id, "branch": branch.task_id}

    @app.post("/admin/tasks/{task_id}/mutate")
    async def mutate_task(task_id: str, payload: Dict[str, Any], storage=Depends(get_storage)):
        task = storage.tasks.get_by_id(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="task not found")

        if "title" in payload: task.title = payload["title"]
        if "description" in payload: task.description = payload["description"]
        if "constraints" in payload:
            c = dict(task.constraints or {})
            c.update(payload["constraints"])
            task.constraints = c

        storage.commit()
        return {"status": "mutated", "task_id": task_id}

    @app.get("/admin/workbench/procedures/{procedure_id}/steps/{step_id}/preview")
    async def preview_procedure_step(
        procedure_id: str,
        step_id: str,
        lane: str | None = None,
        work_pool: str | None = None,
        storage=Depends(get_storage),
    ):
        from strata.procedures.registry import get_procedure
        try:
            procedure = get_procedure(storage, procedure_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        step = next((s for s in procedure.get("checklist", []) if s["id"] == step_id), None)
        if not step:
            raise HTTPException(status_code=404, detail=f"Step {step_id} not found in procedure {procedure_id}")

        system_prompt = (
            f"You are an Expert Agent executing a specific step of the procedure: {procedure['title']}.\n"
            f"Procedure Summary: {procedure.get('summary', '')}\n"
            f"Overall Instructions: {procedure.get('instructions', '')}\n\n"
            "Your goal is to satisfy the verification criteria for this step."
        )
        user_prompt = (
            f"STEP: {step['title']}\n"
            f"VERIFICATION: {step['verification']}\n\n"
            "Please analyze the current state and determine if this step is complete or if further action is required."
        )
        effective_lane, effective_work_pool = _resolve_workbench_target(
            lane=lane,
            work_pool=work_pool,
            procedure=procedure,
        )
        return {
            "status": "ok",
            "procedure_id": procedure_id,
            "step_id": step_id,
            "lane": effective_lane,
            "work_pool": effective_work_pool,
            "execution_profile": effective_work_pool,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

    @app.post("/admin/workbench/procedures/{procedure_id}/steps/{step_id}/execute")
    async def execute_procedure_step(
        procedure_id: str,
        step_id: str,
        payload: Dict[str, Any] | None = None,
        storage=Depends(get_storage),
    ):
        from strata.procedures.registry import get_procedure
        try:
            procedure = get_procedure(storage, procedure_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        step = next((s for s in procedure.get("checklist", []) if s["id"] == step_id), None)
        if not step:
            raise HTTPException(status_code=404, detail=f"Step {step_id} not found in procedure {procedure_id}")

        system_prompt = (
            f"You are an Expert Agent executing a specific step of the procedure: {procedure['title']}.\n"
            f"Procedure Summary: {procedure.get('summary', '')}\n"
            f"Overall Instructions: {procedure.get('instructions', '')}\n\n"
            "Your goal is to satisfy the verification criteria for this step."
        )
        user_prompt = (
            f"STEP: {step['title']}\n"
            f"VERIFICATION: {step['verification']}\n\n"
            "Please analyze the current state and determine if this step is complete or if further action is required."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        effective_lane, effective_work_pool, active_adapter = _workbench_adapter_for_target(
            (payload or {}).get("lane") or procedure.get("target_lane"),
            (payload or {}).get("work_pool") or procedure.get("target_work_pool") or procedure.get("target_execution_profile"),
        )
        response = await active_adapter.chat(messages)
        if str(response.get("status") or "").strip().lower() != "success":
            return {
                "status": "error",
                "procedure_id": procedure_id,
                "step_id": step_id,
                "lane": effective_lane,
                "work_pool": effective_work_pool,
                "execution_profile": effective_work_pool,
                "execution_mode": "dry_run",
                "messages": messages,
                "message": response.get("message") or "Dry-run execution failed",
                "response": response,
            }
        return {
            "status": "ok",
            "procedure_id": procedure_id,
            "step_id": step_id,
            "lane": effective_lane,
            "work_pool": effective_work_pool,
            "execution_profile": effective_work_pool,
            "execution_mode": "dry_run",
            "messages": messages,
            "response": response,
        }

    @app.post("/admin/registry/procedures/{id}/mutate")
    async def mutate_procedure(id: str, payload: Dict[str, Any], storage=Depends(get_storage)):
        from strata.procedures.registry import get_procedure, save_procedure
        try:
            procedure = get_procedure(storage, id)
        except KeyError:
            raise HTTPException(status_code=404, detail="procedure not found")

        updated = {**procedure, **payload}
        save_procedure(storage, updated)
        storage.commit()
        return {"status": "mutated", "procedure_id": id}

    @app.get("/admin/tasks/{task_id}/detail")
    async def get_task_detail(task_id: str, storage=Depends(get_storage)):
        from strata.storage.models import TaskModel, AttemptModel
        from strata.procedures.registry import get_procedure

        task = storage.session.query(TaskModel).filter(TaskModel.task_id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="task not found")

        attempts = storage.session.query(AttemptModel).filter(AttemptModel.task_id == task_id).all()
        children = storage.session.query(TaskModel).filter(TaskModel.parent_task_id == task_id).all()

        parent = None
        if task.parent_task_id:
            parent = storage.session.query(TaskModel).filter(TaskModel.task_id == task.parent_task_id).first()

        artifacts = list_attempt_observability_artifacts(storage, task_id=task_id)
        incidents = list_capability_incidents(
            storage,
            task_id=task_id,
            session_id=str(task.session_id or "").strip() or None,
            limit=20,
        )

        procedure = None
        procedure_id = (task.constraints or {}).get("procedure_id")
        if procedure_id:
            try:
                procedure = get_procedure(storage, procedure_id)
            except KeyError:
                pass

        return {
            "status": "ok",
            "task": {
                "id": task.task_id,
                "title": task.title,
                "description": task.description,
                "state": task_state_api_value(task.state),
                "type": task.type.value if task.type else None,
                "depth": task.depth,
                "lane": (task.constraints or {}).get("lane") or "trainer",
                "parent_id": task.parent_task_id,
                "active_child_ids": task.active_child_ids,
                "constraints": task.constraints,
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "updated_at": task.updated_at.isoformat() if task.updated_at else None,
            },
            "attempts": [
                {
                    "attempt_id": a.attempt_id,
                    "task_id": a.task_id,
                    "outcome": a.outcome.value if a.outcome else "working",
                    "resolution": a.resolution.value if a.resolution else None,
                    "started_at": a.started_at.isoformat() if a.started_at else None,
                    "ended_at": a.ended_at.isoformat() if a.ended_at else None,
                    "artifacts": a.artifacts,
                    "evidence": a.evidence,
                    "plan_review": a.plan_review,
                }
                for a in attempts
            ],
            "children": [
                {
                    "task_id": c.task_id,
                    "title": c.title,
                    "state": task_state_api_value(c.state),
                }
                for c in children
            ],
            "parent": {
                "task_id": parent.task_id,
                "title": parent.title,
                "state": task_state_api_value(parent.state),
            } if parent else None,
            "observability": artifacts,
            "capability_incidents": incidents,
            "procedure": procedure,
        }

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
            "pause_task": pause_task,
            "resume_task": resume_task,
            "stop_task": stop_task,
            "replay_task": replay_task,
            "branch_task": branch_task,
            "mutate_task": mutate_task,
            "mutate_procedure": mutate_procedure,
            "get_task_detail": get_task_detail,
            "preview_procedure_step": preview_procedure_step,
            "execute_procedure_step": execute_procedure_step,
            "sse_events": sse_events,
        }
    )
    return exported
