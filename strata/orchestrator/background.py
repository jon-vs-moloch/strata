"""
@module orchestrator.background
@purpose Thin control loop for the Strata background worker.
@owns orchestrator.worker.*
"""

import asyncio
import logging
from typing import Optional

from strata.storage.models import TaskModel, TaskState, AttemptOutcome
from strata.orchestrator.worker.queue_recovery import recover_tasks
from strata.orchestrator.worker.idle_policy import run_idle_tasks
from strata.orchestrator.worker.telemetry import synthesize_model_performance
from strata.orchestrator.worker.attempt_runner import run_attempt
from strata.orchestrator.worker.resolution_policy import determine_resolution, apply_resolution
from strata.orchestrator.worker.plan_review import generate_plan_review
from strata.orchestrator.worker.routing_policy import select_model_tier, is_canary_eligible

logger = logging.getLogger(__name__)

class BackgroundWorker:
    """
    @summary Managed background loop for asynchronous task execution.
    """

    def __init__(self, storage_factory, model_adapter, memory=None):
        self._storage_factory = storage_factory
        self._model = model_adapter
        self._memory = memory
        self._queue: asyncio.Queue = asyncio.Queue()
        self._running_task: Optional[asyncio.Task] = None
        self._on_update_callback = None
        self._running = False
        self._paused = False
        self._current_process: Optional[asyncio.Task] = None

    async def start(self):
        if self._running:
            return
            
        # 1. Start Recovery Sweep
        await recover_tasks(self._storage_factory, self._queue)

        # 2. Start Loop
        self._running = True
        self._running_task = asyncio.create_task(self._loop(), name="background-worker")
        logger.info("BackgroundWorker started (Refactored)")

    async def stop(self):
        self._running = False
        if self._running_task:
            self._running_task.cancel()
            try:
                await self._running_task
            except asyncio.CancelledError:
                pass
        logger.info("BackgroundWorker stopped")

    def set_on_update(self, callback):
        self._on_update_callback = callback
        
    async def _notify(self, task_id: str, state: str):
        if self._on_update_callback:
            try:
                if asyncio.iscoroutinefunction(self._on_update_callback):
                    await self._on_update_callback(task_id, state)
                else:
                    self._on_update_callback(task_id, state)
            except Exception as e:
                logger.error(f"Failed to notify update: {e}")

    async def enqueue(self, task_id: str):
        await self._queue.put(task_id)
        logger.info(f"Enqueued task {task_id}")

    async def _loop(self):
        idle_ticks = 0
        while self._running:
            if self._paused:
                await asyncio.sleep(0.5)
                continue
                
            try:
                task_id = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                idle_ticks = 0
                
                self._current_process = asyncio.create_task(self._run_task_cycle(task_id))
                try:
                    await self._current_process
                except asyncio.CancelledError:
                    logger.info(f"Task process {task_id} was forced STOPPED.")
                finally:
                    self._current_process = None
                    
                self._queue.task_done()
            except asyncio.TimeoutError:
                idle_ticks += 1
                if idle_ticks >= 30:
                    # 3. Idle Policies
                    await run_idle_tasks(self._storage_factory, self._model, self._queue)
                    await synthesize_model_performance(self._storage_factory)
                    idle_ticks = 0
            except asyncio.CancelledError:
                break

    async def _run_task_cycle(self, task_id: str):
        """
        @summary The orchestrator cycle for a single task: Run -> Resolve -> Review.
        """
        storage = self._storage_factory()
        try:
            task = storage.session.query(TaskModel).filter_by(task_id=task_id).first()
            if not task or task.state in [TaskState.COMPLETE, TaskState.CANCELLED]:
                return

            # Mark RUNNING
            task.state = TaskState.RUNNING
            
            # --- ROUTING ---
            tier = select_model_tier(task)
            if tier == "weak" and is_canary_eligible(task):
                # Canary logic: already weak or forced to weak
                pass
            self._model.set_tier(tier)
            logger.info(f"Routing task {task_id} to {tier} model tier")

            storage.commit()
            await self._notify(task_id, task.state.value)
            
            # --- ATTEMPT ---
            success, error, attempt = await run_attempt(
                task, storage, self._model, self._notify, self.enqueue
            )
            
            if not success:
                # --- RESOLUTION ---
                resolution_data = await determine_resolution(task, error, self._model, storage)
                
                # Update attempt outcome to FAILED
                storage.attempts.update_outcome(attempt.attempt_id, AttemptOutcome.FAILED, reason=str(error))
                
                # Map string resolution to Enum
                from strata.storage.models import AttemptResolution
                try:
                    res_enum = AttemptResolution(resolution_data.resolution)
                    storage.attempts.set_resolution(attempt.attempt_id, res_enum)
                except ValueError:
                    logger.error(f"Invalid resolution choice: {resolution_data.resolution}")
                
                await apply_resolution(task, resolution_data, error, storage, self.enqueue)
                
                # --- RECORD METRICS ---
                from strata.storage.models import MetricModel
                metric = MetricModel(
                    metric_name="task_failure",
                    value=1.0,
                    model_id=self._model.active_model,
                    task_type=task.type.value if hasattr(task.type, 'value') else str(task.type),
                    details={"error": str(error), "resolution": resolution_data.resolution, "task_id": task_id}
                )
                storage.session.add(metric)
                storage.commit()
            
            # --- REVIEW ---
            try:
                review = await generate_plan_review(task, attempt, self._model, storage)
                storage.attempts.set_plan_review(attempt.attempt_id, review)
                storage.commit()
            except Exception as review_err:
                logger.error(f"Failed to generate plan review for attempt {attempt.attempt_id}: {review_err}")

        except Exception as e:
            logger.exception(f"Fatal error in _run_task_cycle for {task_id}: {e}")
        finally:
            storage.session.close()

    def pause(self):
        self._paused = True
    def resume(self):
        self._paused = False
    def stop_current(self):
        if self._current_process:
            self._current_process.cancel()
            return True
        return False
    @property
    def status(self):
        if not self._running: return "STOPPED"
        if self._paused: return "PAUSED"
        return "RUNNING"
