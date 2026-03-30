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

from strata.core.lanes import infer_lane_from_task, normalize_lane
from strata.feedback.signals import register_feedback_signal
from strata.storage.models import TaskModel, TaskState, AttemptOutcome
from strata.orchestrator.worker.queue_recovery import recover_tasks
from strata.orchestrator.worker.idle_policy import run_idle_tasks
from strata.orchestrator.worker.telemetry import synthesize_model_performance
from strata.orchestrator.worker.attempt_runner import run_attempt
from strata.orchestrator.worker.resolution_policy import determine_resolution, apply_resolution
from strata.orchestrator.worker.plan_review import generate_plan_review
from strata.observability.writer import enqueue_attempt_observability_artifact, flush_observability_writes
from strata.orchestrator.worker.routing_policy import select_model_tier
from strata.eval.job_runner import run_eval_job_task
from strata.experimental.verifier import emit_verifier_attention_signal, verify_task_output

logger = logging.getLogger(__name__)


def resolution_from_plan_review(review: Optional[dict]):
    recommendation = str((review or {}).get("recommendation") or "").strip().lower()
    if recommendation not in {"decompose", "internal_replan", "abandon_to_parent"}:
        return None
    from strata.schemas.core import AttemptResolutionSchema

    return AttemptResolutionSchema(
        reasoning=str((review or {}).get("rationale") or f"Plan review recommended {recommendation}.").strip(),
        resolution=recommendation,
        new_subtasks=[],
    )


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


async def queue_task_attention_review(storage, *, task, signal: dict) -> Optional[dict]:
    prioritization = dict((signal or {}).get("prioritization") or {})
    priority = str(prioritization.get("priority") or "").strip().lower()
    if priority not in {"review_soon", "urgent"}:
        return None
    try:
        from strata.api.main import _queue_eval_system_job
    except Exception as exc:
        logger.warning("Unable to import system-job queue helper for task attention review: %s", exc)
        return None

    session_id = str(task.session_id or "").strip() or None
    return await _queue_eval_system_job(
        storage,
        kind="trace_review",
        title=f"Task Attention Review: {str(task.title or task.task_id)[:80]}",
        description=f"Queued task trace review after {priority} task-execution attention signal.",
        payload={
            "trace_kind": "task_trace",
            "task_id": task.task_id,
            "reviewer_tier": "trainer",
            "emit_followups": True,
            "persist_to_task": True,
            "spec_scope": "project",
            "attention_signal_id": signal.get("signal_id"),
            "prioritization": prioritization,
        },
        session_id=session_id,
        dedupe_signature={
            "trace_kind": "task_trace",
            "reviewer_tier": "trainer",
            "task_id": task.task_id,
        },
    )


async def ensure_continuous_supervision_job(
    storage_factory,
    *,
    queue_system_job=None,
    get_proposal_config=None,
    enabled: bool = True,
    minimum_run_count: int = 1,
) -> Optional[dict]:
    if not enabled:
        return None

    if queue_system_job is None:
        from strata.api.main import _queue_eval_system_job as queue_system_job
    if get_proposal_config is None:
        from strata.api.experiment_runtime import get_active_eval_proposal_config as get_proposal_config

    proposal_config = dict(get_proposal_config() or {})
    bootstrap_policy = dict(proposal_config.get("bootstrap") or {})
    proposer_tiers = [
        str(tier).lower()
        for tier in bootstrap_policy.get("continuous_proposer_tiers", ["agent", "trainer"])
        if str(tier).lower() in {"agent", "trainer"}
    ] or ["agent", "trainer"]
    run_count = max(max(1, int(minimum_run_count or 1)), int(bootstrap_policy.get("continuous_run_count", 1) or 1))
    suite_name = "bootstrap_mcq_v1"

    storage = storage_factory()
    try:
        return await queue_system_job(
            storage,
            kind="bootstrap_cycle",
            title="Bootstrap Cycle",
            description="Queued trainer-over-agent bootstrap cycle.",
            payload={
                "proposer_tiers": proposer_tiers,
                "auto_promote": True,
                "suite_name": suite_name,
                "run_count": run_count,
                "baseline_change_id": "baseline",
            },
            session_id="trainer:default",
            dedupe_signature={
                "suite_name": suite_name,
                "run_count": run_count,
                "proposer_tiers": proposer_tiers,
            },
        )
    finally:
        storage.close()


async def ensure_blocked_weak_task_review(
    storage_factory,
    *,
    queue_system_job=None,
    enabled: bool = True,
) -> Optional[dict]:
    if not enabled:
        return None

    if queue_system_job is None:
        from strata.api.main import _queue_eval_system_job as queue_system_job

    storage = storage_factory()
    try:
        weak_blocked_tasks = (
            storage.session.query(TaskModel)
            .filter(
                TaskModel.state == TaskState.BLOCKED,
                TaskModel.human_intervention_required == True,
            )
            .order_by(TaskModel.updated_at.desc())
            .all()
        )
        for candidate in weak_blocked_tasks:
            if normalize_lane(infer_lane_from_task(candidate)) != "agent":
                continue
            return await queue_system_job(
                storage,
                kind="trace_review",
                title=f"Agent Supervision Review: {str(candidate.title or candidate.task_id)[:72]}",
                description="Queued trainer-agent review for blocked agent-lane work before another bootstrap cycle.",
                payload={
                    "trace_kind": "task_trace",
                    "task_id": candidate.task_id,
                    "reviewer_tier": "trainer",
                    "emit_followups": True,
                    "persist_to_task": True,
                    "spec_scope": "project",
                    "supervision_reason": "weak_blocked_task",
                },
                session_id="trainer:default",
                dedupe_signature={
                    "trace_kind": "task_trace",
                    "reviewer_tier": "trainer",
                    "task_id": candidate.task_id,
                    "supervision_reason": "weak_blocked_task",
                },
            )
        return None
    finally:
        storage.close()

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
        self._paused_lanes: set[str] = set()
        self._current_process: Optional[asyncio.Task] = None
        self._current_task_lane: Optional[str] = None
        self._current_task_id: Optional[str] = None
        self._active_experiment_id: Optional[str] = None # Added for bootstrap experiments
        self._tier_health = {"trainer": "unknown", "agent": "unknown"}

    def _settings(self) -> dict:
        try:
            return dict(self._settings_provider() or {})
        except Exception as exc:
            logger.error(f"Failed to read worker settings; using defaults. ({exc})")
            return {}

    async def start(self):
        if self._running:
            return
            
        # 1. Deep preflight model check (trainer-agent + agent)
        logger.info("Performing deep preflight check (trainer-agent + agent tiers)...")
        from strata.schemas.execution import TrainerExecutionContext, AgentExecutionContext
        settings = self._settings()
        allow_cloud_only_boot = bool(settings.get("allow_cloud_only_boot", False))
        
        contexts = [
            ("trainer", TrainerExecutionContext(run_id="preflight")),
            ("agent", AgentExecutionContext(run_id="preflight"))
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
                # Special case: if the trainer-agent tier is missing a cloud key,
                # we do not necessarily want to hard-fail if the local agent tier is alive.
                # We still log it loudly so the operator knows bootstrap quality is degraded.
                logger.error(f"  -> {name} tier FAILED check: {e}")
                self._tier_health[name] = "error"
                if name == "agent":
                    if allow_cloud_only_boot and self._tier_health.get("trainer") == "ok":
                        logger.warning("Worker proceeding in cloud-only mode because the agent tier is unavailable.")
                    else:
                        raise Exception(f"Worker cannot start: the local agent tier is unreachable. ({e})")
                else:
                    # Trainer-agent is optional but critical for high-level reasoning.
                    logger.warning("Worker proceeding without the trainer-agent tier (unreachable or misconfigured).")

        # 2. Start Recovery Sweep
        await recover_tasks(
            self._storage_factory,
            self._queue,
            recover_orphaned_running=not settings.get("testing_mode", False),
            requeue_existing_pending=settings.get("replay_pending_tasks_on_startup", False),
        )
        if not settings.get("testing_mode", False):
            replayed = await self.enqueue_runnable_tasks()
            if replayed:
                logger.info("Seeded %s runnable task(s) into the worker queue during startup.", replayed)

        # 3. Start Loop
        self._running = True
        if not settings.get("testing_mode", False):
            await self._ensure_lane_idle_policies(settings)
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

    async def wait_until_idle(self, timeout: float = 5.0) -> bool:
        deadline = asyncio.get_running_loop().time() + max(0.1, float(timeout))
        while asyncio.get_running_loop().time() < deadline:
            if self._current_process is None:
                return True
            await asyncio.sleep(0.05)
        return self._current_process is None

    def clear_queue(self) -> int:
        cleared = 0
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            else:
                self._queue.task_done()
                cleared += 1
        if cleared:
            logger.info("Cleared %s queued task(s) from the worker backlog.", cleared)
        return cleared

    def _lane_for_task_id(self, task_id: str) -> Optional[str]:
        storage = self._storage_factory()
        try:
            task = storage.session.query(TaskModel).filter_by(task_id=task_id).first()
            if not task:
                return None
            return normalize_lane(infer_lane_from_task(task))
        finally:
            storage.session.close()

    def _task_is_paused(self, task_id: str) -> bool:
        storage = self._storage_factory()
        try:
            task = storage.session.query(TaskModel).filter_by(task_id=task_id).first()
            if not task:
                return False
            return bool((task.constraints or {}).get("paused"))
        finally:
            storage.session.close()

    def _lane_has_runnable_or_active_work(self, lane: str) -> bool:
        normalized_lane = normalize_lane(lane)
        if not normalized_lane:
            return False
        if self._current_task_lane == normalized_lane:
            return True
        storage = self._storage_factory()
        try:
            query = storage.session.query(TaskModel).filter(TaskModel.state.in_([TaskState.PENDING, TaskState.WORKING]))
            for task in query.all():
                task_lane = normalize_lane(infer_lane_from_task(task))
                if task_lane != normalized_lane:
                    continue
                constraints = dict(task.constraints or {})
                if constraints.get("paused") or task.human_intervention_required:
                    continue
                return True
            return False
        finally:
            storage.session.close()

    async def enqueue_runnable_tasks(self, lane: Optional[str] = None) -> int:
        normalized_lane = normalize_lane(lane)
        storage = self._storage_factory()
        try:
            query = storage.session.query(TaskModel).filter(TaskModel.state.in_([TaskState.PENDING, TaskState.WORKING]))
            candidates = query.all()
            enqueued = 0
            for task in candidates:
                if task.human_intervention_required:
                    continue
                if task.state == TaskState.BLOCKED:
                    continue
                constraints = dict(task.constraints or {})
                if constraints.get("paused"):
                    continue
                task_lane = normalize_lane(infer_lane_from_task(task))
                if normalized_lane and task_lane != normalized_lane:
                    continue
                if task.dependencies and any(dep.state != TaskState.COMPLETE for dep in task.dependencies):
                    continue
                if task.task_id == self._current_task_id:
                    continue
                await self.enqueue(task.task_id)
                enqueued += 1
            return enqueued
        finally:
            storage.session.close()

    async def _ensure_lane_idle_policies(self, settings: Optional[dict] = None):
        settings = settings or self._settings()
        if settings.get("testing_mode", False) or self._paused:
            return

        if (
            self._tier_health.get("trainer") == "ok"
            and "trainer" not in self._paused_lanes
            and not self._lane_has_runnable_or_active_work("trainer")
        ):
            try:
                review_seed = await ensure_blocked_weak_task_review(
                    self._storage_factory,
                    enabled=True,
                )
                if review_seed and review_seed.get("status") == "queued":
                    logger.info("Queued blocked agent-task supervision review %s for idle trainer lane.", review_seed.get("task_id"))
                else:
                    seeded = await ensure_continuous_supervision_job(
                        self._storage_factory,
                        enabled=True,
                        minimum_run_count=3 if settings.get("heavy_reflection_mode", False) else 1,
                    )
                    if seeded and seeded.get("status") == "queued":
                        logger.info("Queued continuous supervision job %s for idle trainer lane.", seeded.get("task_id"))
            except Exception as exc:
                logger.warning("Unable to ensure continuous supervision job for the trainer lane: %s", exc)

        if (
            settings.get("automatic_task_generation", False)
            and self._tier_health.get("agent") != "error"
            and "agent" not in self._paused_lanes
        ):
            if not self._lane_has_runnable_or_active_work("agent"):
                await run_idle_tasks(self._storage_factory, self._model, self._queue)

    def _update_task_control_state(
        self,
        task_id: str,
        *,
        paused: Optional[bool] = None,
        state: Optional[TaskState] = None,
        attempt_outcome: Optional[AttemptOutcome] = None,
        reason: Optional[str] = None,
    ) -> bool:
        storage = self._storage_factory()
        try:
            task = storage.session.query(TaskModel).filter_by(task_id=task_id).first()
            if not task:
                return False
            if task.state in {TaskState.COMPLETE, TaskState.CANCELLED, TaskState.ABANDONED}:
                return False
            constraints = dict(task.constraints or {})
            if paused is True:
                constraints["paused"] = True
            elif paused is False:
                constraints.pop("paused", None)
            task.constraints = constraints
            if state is not None and task.state not in {TaskState.COMPLETE, TaskState.CANCELLED, TaskState.ABANDONED}:
                task.state = state
            if attempt_outcome is not None:
                open_attempt = next((row for row in storage.attempts.get_by_task_id(task_id) if row.outcome is None), None)
                if open_attempt:
                    storage.attempts.update_outcome(open_attempt.attempt_id, attempt_outcome, reason=reason)
            storage.commit()
            return True
        finally:
            storage.session.close()

    async def _loop(self):
        idle_ticks = 0
        while self._running:
            if self._paused:
                await asyncio.sleep(0.5)
                continue
                
            try:
                task_id = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                idle_ticks = 0
                task_lane = self._lane_for_task_id(task_id)
                if task_lane and task_lane in self._paused_lanes:
                    await self._queue.put(task_id)
                    self._queue.task_done()
                    await asyncio.sleep(0.1)
                    continue
                if self._task_is_paused(task_id):
                    self._queue.task_done()
                    continue
                
                self._current_task_lane = task_lane
                self._current_task_id = task_id
                self._current_process = asyncio.create_task(self._run_task_cycle(task_id))
                try:
                    await self._current_process
                except asyncio.CancelledError:
                    logger.info(f"Task process {task_id} was forced STOPPED.")
                finally:
                    self._current_process = None
                    self._current_task_lane = None
                    self._current_task_id = None
                    await self._ensure_lane_idle_policies()
                    
                self._queue.task_done()
            except asyncio.TimeoutError:
                idle_ticks += 1
                if idle_ticks >= 30:
                    settings = self._settings()
                    if settings.get("testing_mode", False):
                        logger.info("Testing mode active; skipping autonomous idle task generation.")
                    else:
                        replayed = await self.enqueue_runnable_tasks()
                        if replayed:
                            logger.info("Worker idle with runnable backlog; re-enqueued %s task(s).", replayed)
                            idle_ticks = 0
                            continue
                        await self._ensure_lane_idle_policies(settings)
                        if not settings.get("automatic_task_generation", False):
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
            if self._tier_health.get("agent") != "ok" and self._tier_health.get("trainer") == "ok":
                from strata.schemas.execution import TrainerExecutionContext
                context = TrainerExecutionContext(run_id=f"run_{task_id}_cloud_only")
            
            # Application of experimental override if active
            if self._active_experiment_id:
                from strata.schemas.execution import AgentExecutionContext
                context = AgentExecutionContext(
                    run_id=f"exp_{task_id}",
                    candidate_change_id=self._active_experiment_id,
                    evaluation_run=True
                )
            
            self._model.bind_execution_context(context)
            logger.info(f"Routing task {task_id} to {context.mode} execution context [Exp: {self._active_experiment_id}]")

            constraints = dict(task.constraints or {})
            if constraints.get("lane") != context.mode:
                constraints["lane"] = context.mode
                task.constraints = constraints

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
                # Update attempt outcome to FAILED before review so the reviewer sees the actual failure state.
                storage.attempts.update_outcome(attempt.attempt_id, AttemptOutcome.FAILED, reason=str(error))

                review = None
                try:
                    review = await generate_plan_review(task, attempt, self._model, storage)
                    storage.attempts.set_plan_review(attempt.attempt_id, review)
                    storage.commit()
                    should_flush = enqueue_attempt_observability_artifact(
                        {
                            "task_id": task.task_id,
                            "attempt_id": attempt.attempt_id,
                            "session_id": task.session_id,
                            "artifact_kind": "plan_review",
                            "payload": dict(review or {}),
                        }
                    )
                    if should_flush:
                        flush_observability_writes()
                except Exception as review_err:
                    logger.error(f"Failed to generate failure-time plan review for attempt {attempt.attempt_id}: {review_err}")

                # --- RESOLUTION ---
                resolution_data = resolution_from_plan_review(review) or await determine_resolution(task, error, self._model, storage)
                
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

            # --- LIGHTWEIGHT VERIFICATION ---
            try:
                verification = await verify_task_output(
                    storage,
                    task=task,
                    attempt=attempt,
                    model_adapter=self._model,
                    context=context,
                )
                if verification:
                    verifier_signal = emit_verifier_attention_signal(
                        storage,
                        task=task,
                        verification=verification,
                    )
                    if verifier_signal:
                        queued_review = await queue_task_attention_review(
                            storage,
                            task=task,
                            signal=verifier_signal,
                        )
                        logger.info(
                            "Verifier flagged task %s as %s%s",
                            task.task_id,
                            verification.get("verdict"),
                            f" and queued review {queued_review.get('task_id')}" if queued_review else "",
                        )
                    storage.commit()
            except Exception as verifier_err:
                logger.error(f"Failed to verify attempt {attempt.attempt_id}: {verifier_err}")
            
            # --- REVIEW ---
            try:
                existing_review = dict(getattr(attempt, "plan_review", {}) or {})
                review = existing_review
                if not review or not str(review.get("recommendation") or "").strip():
                    review = await generate_plan_review(task, attempt, self._model, storage)
                    storage.attempts.set_plan_review(attempt.attempt_id, review)
                    should_flush = enqueue_attempt_observability_artifact(
                        {
                            "task_id": task.task_id,
                            "attempt_id": attempt.attempt_id,
                            "session_id": task.session_id,
                            "artifact_kind": "plan_review",
                            "payload": dict(review or {}),
                        }
                    )
                    if should_flush:
                        flush_observability_writes()
                attention_signal = emit_task_execution_attention_signal(
                    storage,
                    task=task,
                    attempt=attempt,
                    context=context,
                    plan_review=review,
                    error=error,
                )
                if attention_signal:
                    queued_review = await queue_task_attention_review(
                        storage,
                        task=task,
                        signal=attention_signal,
                    )
                    logger.info(
                        "Emitted task execution attention signal %s for task %s%s",
                        attention_signal.get("signal_kind"),
                        task.task_id,
                        f" and queued review {queued_review.get('task_id')}" if queued_review else "",
                    )
                storage.commit()
            except Exception as review_err:
                logger.error(f"Failed to generate plan review for attempt {attempt.attempt_id}: {review_err}")

        except Exception as e:
            logger.exception(f"Fatal error in _run_task_cycle for {task_id}: {e}")
        finally:
            storage.session.close()

    def pause(self, lane: Optional[str] = None):
        normalized_lane = normalize_lane(lane)
        if normalized_lane:
            self._paused_lanes.add(normalized_lane)
            return
        self._paused = True

    def resume(self, lane: Optional[str] = None):
        normalized_lane = normalize_lane(lane)
        if normalized_lane:
            self._paused_lanes.discard(normalized_lane)
            return
        self._paused = False

    def stop_current(self, lane: Optional[str] = None):
        normalized_lane = normalize_lane(lane)
        if normalized_lane and self._current_task_lane != normalized_lane:
            return False
        if self._current_process:
            self._current_process.cancel()
            return True
        return False

    def pause_task(self, task_id: str) -> bool:
        updated = self._update_task_control_state(
            task_id,
            paused=True,
            state=TaskState.PENDING,
            attempt_outcome=AttemptOutcome.CANCELLED if self._current_task_id == task_id else None,
            reason="Paused by operator.",
        )
        if not updated:
            return False
        if self._current_task_id == task_id and self._current_process:
            self._current_process.cancel()
        return True

    async def resume_task(self, task_id: str) -> bool:
        updated = self._update_task_control_state(
            task_id,
            paused=False,
            state=TaskState.PENDING,
        )
        if not updated:
            return False
        await self.enqueue(task_id)
        return True

    def stop_task(self, task_id: str) -> bool:
        updated = self._update_task_control_state(
            task_id,
            paused=False,
            state=TaskState.CANCELLED,
            attempt_outcome=AttemptOutcome.CANCELLED if self._current_task_id == task_id else None,
            reason="Cancelled by operator.",
        )
        if not updated:
            return False
        if self._current_task_id == task_id and self._current_process:
            self._current_process.cancel()
        storage = self._storage_factory()
        try:
            cascades = storage.apply_dependency_cascade()
            if cascades:
                logger.info("Cancelled task %s triggered %s dependent cancellation(s).", task_id, cascades)
        finally:
            storage.close()
        return True

    def lane_status(self, lane: str) -> str:
        normalized_lane = normalize_lane(lane)
        if not normalized_lane:
            return "UNKNOWN"
        if not self._running:
            return "STOPPED"
        if self._paused or normalized_lane in self._paused_lanes:
            return "PAUSED"
        if self._current_task_lane == normalized_lane:
            return "RUNNING"
        return "IDLE"

    @property
    def status(self):
        return {
            "worker": "STOPPED" if not self._running else ("PAUSED" if self._paused else "RUNNING"),
            "global_paused": self._paused,
            "paused_lanes": sorted(self._paused_lanes),
            "tiers": self._tier_health,
            "lanes": {
                "trainer": self.lane_status("trainer"),
                "agent": self.lane_status("agent"),
            },
        }
