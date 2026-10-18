#!/usr/bin/env python3
"""
Separate Strata worker daemon.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from sqlalchemy.exc import OperationalError

from strata.env import load_local_env
from strata.memory.semantic import SemanticMemory
from strata.models.adapter import ModelAdapter
from strata.models.providers import GenericOpenAICompatibleProvider
from strata.observability.writer import flush_observability_writes
from strata.orchestrator.background import BackgroundWorker
from strata.orchestrator.worker.runtime_ipc import (
    default_worker_status,
    ensure_runtime_dir,
    read_worker_commands,
    worker_command_cursor,
    write_worker_status,
)
from strata.runtime_config import (
    GLOBAL_SETTINGS,
    SETTINGS_PARAMETER_DESCRIPTION,
    SETTINGS_PARAMETER_KEY,
    normalized_settings,
)
from strata.storage.services.main import StorageManager


logger = logging.getLogger("strata.worker_daemon")
load_local_env(Path(__file__).resolve().parents[1])


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _load_runtime_settings() -> dict:
    storage = StorageManager()
    try:
        try:
            persisted = storage.parameters.get_parameter(
                key=SETTINGS_PARAMETER_KEY,
                default_value=dict(GLOBAL_SETTINGS),
                description=SETTINGS_PARAMETER_DESCRIPTION,
            ) or {}
        except OperationalError as exc:
            if "no such table" not in str(exc).lower():
                raise
            logger.warning("Worker daemon started before runtime tables existed; using default settings for bootstrap.")
            storage.rollback()
            return normalized_settings(dict(GLOBAL_SETTINGS))
        return normalized_settings(persisted)
    finally:
        storage.close()


async def _dispatch_command(worker: BackgroundWorker, command: dict) -> None:
    action = str(command.get("action") or "").strip().lower()
    payload = dict(command.get("payload") or {})
    lane = payload.get("lane")
    work_pool = payload.get("work_pool")
    task_id = payload.get("task_id")
    if action == "enqueue" and task_id:
        await worker.enqueue(str(task_id))
    elif action == "pause_worker":
        worker.pause(lane, work_pool=work_pool)
    elif action == "resume_worker":
        worker.resume(lane, work_pool=work_pool)
        await worker.enqueue_runnable_tasks(work_pool or lane)
    elif action == "stop_worker":
        worker.stop_current(lane, work_pool=work_pool)
    elif action == "clear_queue":
        worker.clear_queue(lane)
    elif action == "replay_pending":
        await worker.enqueue_runnable_tasks(lane)
    elif action == "pause_task" and task_id:
        worker.pause_task(str(task_id))
    elif action == "resume_task" and task_id:
        await worker.resume_task(str(task_id))
    elif action == "stop_task" and task_id:
        worker.stop_task(str(task_id))


async def _status_publisher(worker: BackgroundWorker) -> None:
    while True:
        write_worker_status(
            {
                "pid": os.getpid(),
                "status": worker.status,
            }
        )
        await asyncio.sleep(0.5)


async def _command_consumer(worker: BackgroundWorker) -> None:
    cursor = worker_command_cursor()
    while True:
        cursor, commands = read_worker_commands(cursor)
        for command in commands:
            try:
                await _dispatch_command(worker, command)
            except Exception as exc:
                logger.warning("Failed to apply worker command %s: %s", command.get("action"), exc)
        await asyncio.sleep(0.25)


async def main() -> None:
    _configure_logging()
    ensure_runtime_dir()
    write_worker_status({"pid": os.getpid(), "status": default_worker_status("Worker daemon is starting.")})

    settings = _load_runtime_settings()
    GenericOpenAICompatibleProvider.set_runtime_policy(
        settings.get("inference_throttle_policy") or {}
    )

    model = ModelAdapter()
    memory = SemanticMemory()
    worker = BackgroundWorker(
        storage_factory=StorageManager,
        model_adapter=model,
        memory=memory,
        settings_provider=lambda: _load_runtime_settings(),
    )
    await worker.start()
    publisher = asyncio.create_task(_status_publisher(worker), name="worker-status-publisher")
    commands = asyncio.create_task(_command_consumer(worker), name="worker-command-consumer")
    logger.info("Worker daemon started")
    try:
        await asyncio.gather(publisher, commands)
    finally:
        publisher.cancel()
        commands.cancel()
        await worker.stop()
        flush_observability_writes()
        write_worker_status({"pid": os.getpid(), "status": default_worker_status("Worker daemon stopped.")})


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        _configure_logging()
        logger.exception("Worker daemon crashed: %s", exc)
        write_worker_status({"pid": os.getpid(), "status": default_worker_status(f"Worker daemon crashed: {exc}")})
        raise
