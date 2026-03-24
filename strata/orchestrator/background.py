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
from strata.orchestrator.worker.routing_policy import select_model_tier

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
        self._active_experiment_id: Optional[str] = None # Added for bootstrap experiments

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

            # --- ROUTING ---
            from strata.orchestrator.worker.routing_policy import select_model_tier
            context = select_model_tier(task)
            
            # Application of experimental override if active
            if self._active_experiment_id:
                from strata.schemas.execution import WeakExecutionContext
                context = WeakExecutionContext(
                    run_id=f"exp_{task_id}",
                    candidate_change_id=self._active_experiment_id,
                    evaluation_run=True
                )
            
            self._model.bind_execution_context(context)
            logger.info(f"Routing task {task_id} to {context.mode} execution context [Exp: {self._active_experiment_id}]")

            storage.commit()
            await self._notify(task_id, task.state.value)
            
            # --- ATTEMPT ---
            success, error, attempt = await run_attempt(
                task, storage, self._model, self._notify, self.enqueue
            )
            
            # Determine execution context details for metrics
            run_mode = getattr(context, "run_mode", "normal") if hasattr(context, "run_mode") else "normal"
            if getattr(context, "evaluation_run", False):
                run_mode = "weak_eval"
            ctx_mode = context.mode
            change_id = getattr(context, "candidate_change_id", None)

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
                from strata.orchestrator.worker.telemetry import record_metric
                record_metric(
                    storage,
                    metric_name="task_failure",
                    value=1.0,
                    model_id=f"{attempt.artifacts.get('provider', 'unknown')}/{attempt.artifacts.get('model', 'unknown')}",
                    task_type=task.type.value if hasattr(task.type, 'value') else str(task.type),
                    task_id=task_id,
                    run_mode=run_mode,
                    execution_context=ctx_mode,
                    candidate_change_id=change_id,
                    details={"error": str(error), "resolution": resolution_data.resolution}
                )
                
                # Record valid candidate rate if applicable
                from strata.storage.models import CandidateModel
                candidates = storage.session.query(CandidateModel).filter_by(task_id=task_id).all()
                if candidates:
                    from strata.orchestrator.evaluation import EvaluationPipeline
                    evaluator = EvaluationPipeline(storage, context=context)
                    valid_count = 0
                    for c in candidates:
                        sc = await evaluator.evaluate_candidate(task, c)
                        if sc.valid:
                            valid_count += 1
                    
                    record_metric(
                        storage,
                        metric_name="valid_candidate_rate",
                        value=valid_count / len(candidates),
                        model_id=f"{attempt.artifacts.get('provider', 'unknown')}/{attempt.artifacts.get('model', 'unknown')}",
                        task_type=task.type.value if hasattr(task.type, 'value') else str(task.type),
                        task_id=task_id,
                        run_mode=run_mode,
                        execution_context=ctx_mode,
                        candidate_change_id=change_id,
                        details={"total": len(candidates), "valid": valid_count}
                    )
                
                storage.commit()
            else:
                # --- SUCCESS METRICS ---
                from strata.orchestrator.worker.telemetry import record_metric
                record_metric(
                    storage,
                    metric_name="task_success",
                    value=1.0,
                    model_id=f"{attempt.artifacts.get('provider', 'unknown')}/{attempt.artifacts.get('model', 'unknown')}",
                    task_type=task.type.value if hasattr(task.type, 'value') else str(task.type),
                    task_id=task_id,
                    run_mode=run_mode,
                    execution_context=ctx_mode,
                    candidate_change_id=change_id
                )
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
