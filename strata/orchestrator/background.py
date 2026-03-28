"""
@module orchestrator.background
@purpose Thin control loop for the Strata background worker.
@owns orchestrator.worker.*

This loop exists to keep execution discipline outside the model itself.
Instead of asking one model call to be reliable, Strata wraps work in
routing, retries, evaluation, telemetry, and resolution policies that a
small local model can benefit from.
"""

import asyncio
import logging
from typing import Callable, Optional

from strata.feedback.signals import register_feedback_signal
from strata.storage.models import TaskModel, TaskState, AttemptOutcome
from strata.orchestrator.worker.queue_recovery import recover_tasks
from strata.orchestrator.worker.idle_policy import run_idle_tasks
from strata.orchestrator.worker.telemetry import synthesize_model_performance
from strata.orchestrator.worker.attempt_runner import run_attempt
from strata.orchestrator.worker.resolution_policy import determine_resolution, apply_resolution
from strata.orchestrator.worker.plan_review import generate_plan_review
from strata.orchestrator.worker.routing_policy import select_model_tier
from strata.eval.job_runner import run_eval_job_task

logger = logging.getLogger(__name__)


def emit_task_execution_attention_signal(
    storage,
    *,
    task,
    attempt,
    context,
    plan_review: Optional[dict] = None,
    error: Optional[BaseException] = None,
):
    prior_attempts = [
        row for row in (storage.attempts.get_by_task_id(task.task_id) or [])
        if str(getattr(row, "attempt_id", "")) != str(getattr(attempt, "attempt_id", ""))
    ]
    prior_failed = [row for row in prior_attempts if row.outcome == AttemptOutcome.FAILED]
    prior_succeeded = [row for row in prior_attempts if row.outcome == AttemptOutcome.SUCCEEDED]
    plan_review = dict(plan_review or {})
    plan_health = str(plan_review.get("plan_health") or "").strip().lower()
    recommendation = str(plan_review.get("recommendation") or "").strip().lower()
    outcome = getattr(attempt.outcome, "value", "").strip().lower() if attempt.outcome else ""

    signal_kind = None
    signal_value = outcome or "unknown"
    expected_outcome = "progress"
    observed_outcome = outcome or "unknown"
    note_bits = []

    if attempt.outcome == AttemptOutcome.FAILED and not prior_failed:
        signal_kind = "unexpected_failure"
        signal_value = "first_failure"
        observed_outcome = "failed"
        note_bits.append("Task failed on its first recorded failed attempt.")
    elif attempt.outcome == AttemptOutcome.SUCCEEDED and prior_failed:
        signal_kind = "unexpected_success"
        signal_value = "recovered_after_failures"
        expected_outcome = "continued_struggle"
        observed_outcome = "succeeded"
        note_bits.append(f"Task succeeded after {len(prior_failed)} prior failed attempt(s).")
    elif attempt.outcome == AttemptOutcome.FAILED and len(prior_failed) >= 2:
        signal_kind = "importance"
        signal_value = "repeated_failures"
        observed_outcome = "failed_repeatedly"
        note_bits.append(f"Task has {len(prior_failed) + 1} failed attempts.")
    elif attempt.outcome == AttemptOutcome.SUCCEEDED and (
        plan_health in {"degraded", "invalid"} or recommendation in {"decompose", "internal_replan", "abandon_to_parent"}
    ):
        signal_kind = "surprise"
        signal_value = "success_but_plan_degraded"
        expected_outcome = "stable_success"
        observed_outcome = "succeeded_but_needs_restructure"
        note_bits.append("Attempt succeeded, but plan review says the branch remains unhealthy.")

    if not signal_kind:
        return None

    usage = dict(getattr(attempt, "artifacts", {}) or {})
    model_id = f"{usage.get('provider', 'unknown')}/{usage.get('model', 'unknown')}"
    return register_feedback_signal(
        storage,
        source_type="task_execution",
        source_id=str(task.task_id),
        signal_kind=signal_kind,
        signal_value=signal_value,
        source_actor="background_worker",
        session_id=str(task.session_id or "").strip(),
        source_preview=str(task.title or task.description or f"Task {task.task_id}")[:220],
        note=" ".join(
            [
                part
                for part in [
                    *note_bits,
                    f"task_type={getattr(task.type, 'value', str(task.type))}",
                    f"context_mode={getattr(context, 'mode', 'unknown')}",
                    f"model_id={model_id}",
                    f"plan_health={plan_health or 'unknown'}",
                    f"recommendation={recommendation or 'unknown'}",
                    f"error={str(error)[:180] if error else ''}",
                ]
                if str(part).strip()
            ]
        )[:500],
        expected_outcome=expected_outcome,
        observed_outcome=observed_outcome,
        metadata={
            "task_id": task.task_id,
            "attempt_id": getattr(attempt, "attempt_id", None),
            "task_type": getattr(task.type, "value", str(task.type)),
            "context_mode": getattr(context, "mode", "unknown"),
            "candidate_change_id": getattr(context, "candidate_change_id", None),
            "run_mode": getattr(context, "run_mode", "weak_eval" if getattr(context, "evaluation_run", False) else "normal"),
            "plan_health": plan_health,
            "recommendation": recommendation,
            "prior_failed_attempts": len(prior_failed),
            "prior_succeeded_attempts": len(prior_succeeded),
            "error": str(error)[:220] if error else "",
        },
    )

class BackgroundWorker:
    """
    @summary Managed background loop for asynchronous task execution.
    """

    def __init__(self, storage_factory, model_adapter, memory=None, settings_provider: Optional[Callable[[], dict]] = None):
        self._storage_factory = storage_factory
        self._model = model_adapter
        self._memory = memory
        self._settings_provider = settings_provider or (lambda: {})
        self._queue: asyncio.Queue = asyncio.Queue()
        self._running_task: Optional[asyncio.Task] = None
        self._on_update_callback = None
        self._running = False
        self._paused = False
        self._current_process: Optional[asyncio.Task] = None
        self._active_experiment_id: Optional[str] = None # Added for bootstrap experiments
        self._tier_health = {"Strong": "unknown", "Weak": "unknown"}

    def _settings(self) -> dict:
        try:
            return dict(self._settings_provider() or {})
        except Exception as exc:
            logger.error(f"Failed to read worker settings; using defaults. ({exc})")
            return {}

    async def start(self):
        if self._running:
            return
            
        # 1. Deep Preflight Model Check (Strong + Weak)
        logger.info("Performing Deep Preflight Check (Strong + Weak tiers)...")
        from strata.schemas.execution import StrongExecutionContext, WeakExecutionContext
        
        contexts = [
            ("Strong", StrongExecutionContext(run_id="preflight")),
            ("Weak", WeakExecutionContext(run_id="preflight"))
        ]
        
        for name, ctx in contexts:
            logger.info(f"Checking {name} model tier...")
            self._model.bind_execution_context(ctx)
            try:
                # Ping with a simple no-op (Max 5s timeout)
                # If it's a Cloud transport without an API key, bind_execution_context may throw
                # or the chat call will throw.
                await self._model.chat([{"role": "user", "content": "ping"}], timeout=5.0)
                logger.info(f"  -> {name} tier is reachable.")
                self._tier_health[name] = "ok"
            except Exception as e:
                # Special Case: If it's the Strong tier failing because of a missing API key,
                # we don't necessarily want to HARD fail if the Weak tier (local) is alive
                # and the user hasn't provided a key yet. BUT we should log it loudly.
                logger.error(f"  -> {name} tier FAILED check: {e}")
                self._tier_health[name] = "error"
                if name == "Weak":
                    # Weak (Local) is the mandatory baseline for this dev session.
                    raise Exception(f"Worker cannot start: Local (Weak) model is unreachable. ({e})")
                else:
                    # Strong (Cloud) is optional but critical for high-level reasoning.
                    logger.warning(f"Worker proceeding without {name} model tier (unreachable/misconfigured).")

        # 2. Start Recovery Sweep
        settings = self._settings()
        await recover_tasks(
            self._storage_factory,
            self._queue,
            recover_orphaned_running=not settings.get("testing_mode", False),
            requeue_existing_pending=settings.get("replay_pending_tasks_on_startup", False),
        )

        # 3. Start Loop
        self._running = True
        self._running_task = asyncio.create_task(self._loop(), name="background-worker")
        logger.info("BackgroundWorker started (Hardened Startup)")

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
                    settings = self._settings()
                    if settings.get("testing_mode", False):
                        logger.info("Testing mode active; skipping autonomous idle task generation.")
                    elif settings.get("automatic_task_generation", False):
                        await run_idle_tasks(self._storage_factory, self._model, self._queue)
                    else:
                        logger.info("Automatic task generation disabled; worker remains idle without spawning new tasks.")
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
            if (task.constraints or {}).get("system_job"):
                logger.info(f"Running queued system job for task {task_id}")
                await run_eval_job_task(task, storage, self._model)
                await self._notify(task_id, task.state.value)
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

            def _record_attempt_efficiency_metrics(outcome: str):
                from strata.orchestrator.worker.telemetry import record_metric
                duration_s = float(attempt.artifacts.get("duration_s", 0.0) or 0.0)
                usage = attempt.artifacts.get("usage") or {}
                base_kwargs = {
                    "storage": storage,
                    "model_id": f"{attempt.artifacts.get('provider', 'unknown')}/{attempt.artifacts.get('model', 'unknown')}",
                    "task_type": task.type.value if hasattr(task.type, 'value') else str(task.type),
                    "task_id": task_id,
                    "run_mode": run_mode,
                    "execution_context": ctx_mode,
                    "candidate_change_id": change_id,
                    "details": {"outcome": outcome},
                }
                if duration_s > 0.0:
                    record_metric(
                        base_kwargs["storage"],
                        metric_name="task_attempt_duration_s",
                        value=duration_s,
                        model_id=base_kwargs["model_id"],
                        task_type=base_kwargs["task_type"],
                        task_id=base_kwargs["task_id"],
                        run_mode=base_kwargs["run_mode"],
                        execution_context=base_kwargs["execution_context"],
                        candidate_change_id=base_kwargs["candidate_change_id"],
                        details=base_kwargs["details"],
                    )
                for key, metric_name in (
                    ("prompt_tokens", "task_prompt_tokens"),
                    ("completion_tokens", "task_completion_tokens"),
                    ("total_tokens", "task_total_tokens"),
                ):
                    if usage.get(key) is not None:
                        record_metric(
                            base_kwargs["storage"],
                            metric_name=metric_name,
                            value=float(usage.get(key) or 0.0),
                            model_id=base_kwargs["model_id"],
                            task_type=base_kwargs["task_type"],
                            task_id=base_kwargs["task_id"],
                            run_mode=base_kwargs["run_mode"],
                            execution_context=base_kwargs["execution_context"],
                            candidate_change_id=base_kwargs["candidate_change_id"],
                            details=base_kwargs["details"],
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
                _record_attempt_efficiency_metrics("failed")
                
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
                _record_attempt_efficiency_metrics("succeeded")
                storage.commit()
            
            # --- REVIEW ---
            try:
                review = await generate_plan_review(task, attempt, self._model, storage)
                storage.attempts.set_plan_review(attempt.attempt_id, review)
                attention_signal = emit_task_execution_attention_signal(
                    storage,
                    task=task,
                    attempt=attempt,
                    context=context,
                    plan_review=review,
                    error=error,
                )
                if attention_signal:
                    logger.info(
                        "Emitted task execution attention signal %s for task %s",
                        attention_signal.get("signal_kind"),
                        task.task_id,
                    )
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
        return {
            "worker": "STOPPED" if not self._running else ("PAUSED" if self._paused else "RUNNING"),
            "tiers": self._tier_health
        }
