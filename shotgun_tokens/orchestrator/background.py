"""
@module orchestrator.background
@purpose Async background worker that processes RESEARCH tasks without blocking the chat endpoint.
@owns task queue, task lifecycle (QUEUED → RUNNING → COMPLETED/FAILED)
@does_not_own LLM inference directly (delegates to ResearchModule), DB session creation
@key_exports BackgroundWorker
"""

import asyncio
import logging
from typing import Optional

from shotgun_tokens.storage.models import TaskModel, TaskState, TaskType, AttemptModel, AttemptOutcome, AttemptResolution
from shotgun_tokens.orchestrator.research import ResearchModule
from shotgun_tokens.orchestrator.decomposition import DecompositionModule
from shotgun_tokens.orchestrator.implementation import ImplementationModule

logger = logging.getLogger(__name__)


def _format_research_report(task_description: str, report) -> str:
    """Render a ResearchReport as a human-readable markdown string."""
    lines = [
        f"🔬 **Research Complete** — *{task_description[:80]}*\n",
        f"**Context**\n{report.context_gathered}\n",
    ]
    if report.key_constraints_discovered:
        lines.append("**Constraints**")
        for c in report.key_constraints_discovered:
            lines.append(f"- {c}")
        lines.append("")
    if report.suggested_approach:
        lines.append(f"**Suggested Approach**\n{report.suggested_approach}\n")
    if report.reference_urls:
        lines.append("**References**")
        for url in report.reference_urls:
            lines.append(f"- `{url}`")
    return "\n".join(lines)


class BackgroundWorker:
    """
    @summary Asyncio-based background worker that drains a queue of task IDs.
    @inputs storage_factory: callable returning a fresh StorageManager
    @inputs model_adapter: shared ModelAdapter instance
    @side_effects writes MessageModel and updates TaskModel status in DB
    @invariants Each task is processed exactly once; failures are caught and logged.
    """

    def __init__(self, storage_factory, model_adapter):
        """
        @summary Initialise worker (does not start it yet).
        @inputs storage_factory: zero-arg callable → StorageManager (fresh session per task)
        @inputs model_adapter: ModelAdapter instance
        """
        self._storage_factory = storage_factory
        self._model = model_adapter
        self._queue: asyncio.Queue = asyncio.Queue()
        self._running_task: Optional[asyncio.Task] = None
        self._running = False
        self._paused = False
        self._current_process: Optional[asyncio.Task] = None # In-flight task processing

    async def start(self):
        """
        @summary Launch the background loop. Call once on app startup.
        """
        if self._running:
            return
            
        # Startup Sweep: Recover orphaned tasks interrupted by server hot-restarts
        storage = self._storage_factory()
        try:
            from shotgun_tokens.storage.models import TaskModel, TaskState
            orphaned = storage.session.query(TaskModel).filter(TaskModel.state == TaskState.WORKING).all()
            for task in orphaned:
                logger.warning(f"Re-queueing orphaned runtime task: {task.task_id}")
                task.state = TaskState.PENDING
                # Manually bypass enqueue because we don't want to double queue them if they were already queued.
            storage.commit()
            
            # Repopulate the active queue with all PENDING tasks
            queued = storage.session.query(TaskModel).filter(TaskModel.state == TaskState.PENDING).all()
            for task in queued:
                self._queue.put_nowait(task.task_id)
                logger.info(f"Loaded existing queued task: {task.task_id}")
                
        except Exception as e:
            logger.error(f"Queue recovery sweep failed: {e}")
        finally:
            storage.session.close()

        self._running = True
        self._running_task = asyncio.create_task(self._loop(), name="background-worker")
        logger.info("BackgroundWorker started")

    async def stop(self):
        """
        @summary Gracefully stop the worker loop. Call on app shutdown.
        """
        self._running = False
        if self._running_task:
            self._running_task.cancel()
            try:
                await self._running_task
            except asyncio.CancelledError:
                pass
        logger.info("BackgroundWorker stopped")

    async def enqueue(self, task_id: str):
        """
        @summary Add a task ID to the processing queue.
        @inputs task_id: UUID string of a TaskModel with status QUEUED
        """
        await self._queue.put(task_id)
        logger.info(f"Enqueued task {task_id}")

    async def _loop(self):
        """
        @summary Main worker loop — drains the queue forever.
        """
        idle_ticks = 0
        while self._running:
            if self._paused:
                await asyncio.sleep(0.5)
                continue
                
            try:
                task_id = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                idle_ticks = 0
                
                # Wrap _run_task in a cancelable future
                self._current_process = asyncio.create_task(self._run_task(task_id))
                try:
                    await self._current_process
                except asyncio.CancelledError:
                    logger.info(f"Task process {task_id} was forced STOPPED.")
                    # We might want to mark it as ABANDONED in DB? 
                    # For now just let it stop.
                finally:
                    self._current_process = None
                    
                self._queue.task_done()
            except asyncio.TimeoutError:
                idle_ticks += 1
                # Trigger autonomous behavior after ~30 seconds of idle time
                if idle_ticks >= 30:
                    await self._generate_autonomous_task()
                    await self._synthesize_model_performance()
                    idle_ticks = 0
            except asyncio.CancelledError:
                break

    async def _synthesize_model_performance(self):
        """
        @summary Periodically summarize model performance intelligence from telemetry to a markdown file.
        """
        import os
        from datetime import datetime
        storage = self._storage_factory()
        try:
            from shotgun_tokens.storage.models import ModelTelemetry
            from sqlalchemy import func
            results = (
                storage.session.query(
                    ModelTelemetry.model_id,
                    ModelTelemetry.task_type,
                    func.avg(ModelTelemetry.score).label("avg_score")
                )
                .group_by(ModelTelemetry.model_id, ModelTelemetry.task_type)
                .all()
            )
            
            if not results:
                return
                
            md = ["# Model Performance Intel", f"*Synthesized on: {datetime.utcnow().isoformat()}*\n"]
            for r in results:
                md.append(f"- **{r.model_id}** ({r.task_type}): {r.avg_score:.2f} avg score")
            
            os.makedirs(".knowledge", exist_ok=True)
            with open(".knowledge/model_performance_intel.md", "w") as f:
                f.write("\n".join(md))
                
            logger.info("Synthesized model performance report to .knowledge/model_performance_intel.md")
        except Exception as e:
            logger.error(f"Failed to synthesize model performance: {e}")
        finally:
            storage.session.close()
    def pause(self):
        self._paused = True
        logger.info("Worker PAUSED")

    def resume(self):
        self._paused = False
        logger.info("Worker RESUMED")

    def stop_current(self):
        if self._current_process:
            self._current_process.cancel()
            logger.info("Current task process ABORTED")
            return True
        return False

    @property
    def status(self):
        if not self._running: return "STOPPED"
        if self._paused: return "PAUSED"
        return "RUNNING"

    async def _generate_autonomous_task(self):
        """
        @summary When idle, the system aligns itself with the User Spec/Constitution.
        """
        storage = self._storage_factory()
        import os
        try:
            logger.info("System is idle. Triggering Constitutional Alignment Task.")
            
            # 1. Read the user specifications
            kb_dir = ".knowledge/specs"
            global_spec = "None."
            project_spec = "None."
            if os.path.exists(os.path.join(kb_dir, "global_spec.md")):
                with open(os.path.join(kb_dir, "global_spec.md"), "r") as f:
                    global_spec = f.read()
            if os.path.exists(os.path.join(kb_dir, "project_spec.md")):
                with open(os.path.join(kb_dir, "project_spec.md"), "r") as f:
                    project_spec = f.read()

            # 2. Prompt for Alignment
            from shotgun_tokens.storage.models import TaskModel, TaskState, TaskType
            sys_prompt = f"""You are the Alignment Module for the Strata Swarm.
The system is currently IDLE. You must identify gaps between the user's vision and the current codebase state.

USER GLOBAL PREFERENCES:
{global_spec}

PROJECT GOALS:
{project_spec}

TASK: Identify the LARGEST delta between the vision and the current implementation.
Propose exactly ONE task (maintenance, research, or refinement) to close that gap.
Reply with ONLY a single sentence describing the task.
"""
            messages = [{"role": "system", "content": sys_prompt}]
            response = await self._model.chat(messages)
            task_desc = response.get("content", "").strip()
            if not task_desc:
                task_desc = "Autonomously align codebase with user specifications."
                
            task = storage.tasks.create(
                title=f"Alignment: {task_desc[:40]}...",
                description=task_desc,
                session_id="default",
                state=TaskState.PENDING,
                constraints={"target_scope": "codebase"}
            )
            task.type = TaskType.RESEARCH
            storage.commit()
            
            storage.messages.create(
                role="assistant",
                content=f"🧠 **Constitutional Alignment Policy Active**\nI've analyzed the project specs and identified a gap. I've autonomously spawned an alignment task:\n*{task_desc}*",
                session_id="default"
            )
            storage.commit()
            await self.enqueue(task.task_id)
        except Exception as e:
            logger.error(f"Failed to generate autonomous task: {e}")
        finally:
            storage.session.close()

    async def _run_task(self, task_id: str):
        """
        @summary Execute a single task. Gets its own fresh DB session.
        @inputs task_id: UUID string
        @side_effects: updates task state, creates attempts, writes message to DB
        """
        storage = self._storage_factory()
        try:
            task = storage.session.query(TaskModel).filter(
                TaskModel.task_id == task_id
            ).first()

            if task is None:
                logger.warning(f"Task {task_id} not found — skipping")
                return

            # Skip if already complete or cancelled
            if task.state in [TaskState.COMPLETE, TaskState.CANCELLED]:
                logger.info(f"Task {task_id} is already {task.state.value} — skipping")
                return

            # Mark working
            task.state = TaskState.WORKING
            storage.commit()
            
            # Start a new Attempt
            attempt = storage.attempts.create(task_id=task.task_id)
            storage.commit()
            
            logger.info(f"Running task {task_id} ({task.type.value}), Attempt {attempt.attempt_id}")

            try:
                if task.type == TaskType.RESEARCH:
                    await self._run_research(task, storage)
                elif task.type == TaskType.DECOMP:
                    await self._run_decomposition(task, storage)
                elif task.type == TaskType.IMPL:
                    await self._run_implementation(task, storage)
                else:
                    logger.warning(f"BackgroundWorker: unsupported task type {task.type}")
                    raise NotImplementedError(f"Unsupported task type {task.type}")

                # If we got here without exception, it succeeded
                storage.attempts.update_outcome(attempt.attempt_id, AttemptOutcome.SUCCEEDED)
                task.state = TaskState.COMPLETE
                storage.commit()

            except Exception as e:
                storage.rollback() # Rollback any partial progress in repositories if possible
                
                logger.exception(f"Attempt {attempt.attempt_id} for task {task_id} failed: {e}")
                storage.attempts.update_outcome(attempt.attempt_id, AttemptOutcome.FAILED, reason=str(e))
                
                # Determine resolution
                resolution = await self._determine_resolution(task, e, storage)
                storage.attempts.set_resolution(attempt.attempt_id, resolution)
                
                await self._apply_resolution(task, resolution, e, storage)
                storage.commit()

            # Always perform plan review after an attempt finishes (success or failure)
            try:
                review = await self._generate_plan_review(task, attempt, storage)
                storage.attempts.set_plan_review(attempt.attempt_id, review)
                storage.commit()
            except Exception as review_err:
                logger.error(f"Failed to generate plan review for attempt {attempt.attempt_id}: {review_err}")

        except Exception as e:
            logger.exception(f"Fatal error in _run_task for {task_id}: {e}")
        finally:
            storage.session.close()

    async def _determine_resolution(self, task: TaskModel, error: Exception, storage) -> AttemptResolution:
        """
        @summary Classify the failure and choose a resolution strategy.
        """
        # Simple heuristic for now:
        # If it's a deep task, maybe abandon to parent.
        # If it's a root task, decompose or reattempt.
        if task.parent_task_id is None:
            # Root tasks cannot abandon to parent
            if task.depth < 2:
                return AttemptResolution.DECOMPOSE
            else:
                return AttemptResolution.REATTEMPT
        
        # Child tasks can abandon to parent
        if "timeout" in str(error).lower():
            return AttemptResolution.DECOMPOSE
        
        return AttemptResolution.ABANDON_TO_PARENT

    async def _apply_resolution(self, task: TaskModel, resolution: AttemptResolution, error: Exception, storage):
        """
        @summary Apply the chosen resolution to the task graph.
        """
        if resolution == AttemptResolution.REATTEMPT:
            logger.info(f"Resolution: REATTEMPT task {task.task_id}")
            task.state = TaskState.PENDING
            await self.enqueue(task.task_id)
            
        elif resolution == AttemptResolution.DECOMPOSE:
            logger.info(f"Resolution: DECOMPOSE task {task.task_id}")
            # We keep it as WORKING and create a specific decomposition task?
            # Or just update it to DECOMP type? 
            # The spec says: "Effect: create child tasks, keep current task working"
            # Here we'll spawn a "Failover Decomposition" task.
            failover_task = storage.tasks.create(
                title=f"Decomposition Recovery: {task.title}",
                description=f"The previous attempt failed with: {error}. Decompose this problem into smaller sub-tasks.",
                session_id=task.session_id,
                parent_task_id=task.task_id,
                state=TaskState.PENDING,
                type=TaskType.DECOMP,
                depth=task.depth + 1
            )
            storage.commit()
            await self.enqueue(failover_task.task_id)
            
        elif resolution == AttemptResolution.ABANDON_TO_PARENT:
            logger.info(f"Resolution: ABANDON_TO_PARENT task {task.task_id}")
            task.state = TaskState.ABANDONED
            # Notify parent is handled implicitly by the state change if the parent is monitoring.
            # In our current loop, the parent will see a child is abandoned and could react.
            
        elif resolution == AttemptResolution.INTERNAL_REPLAN:
            logger.info(f"Resolution: INTERNAL_REPLAN task {task.task_id}")
            task.state = TaskState.PENDING # Or some 'replanning' state?
            # Logic to change approach...

    async def _run_research(self, task: "TaskModel", storage):
        """
        @summary Run a RESEARCH task using ResearchModule and post results to chat.
        """
        import json
        research = ResearchModule(self._model, storage)
        
        scope = "codebase"
        try:
            constraints = json.loads(task.constraints) if isinstance(task.constraints, str) else dict(task.constraints or {})
            scope = constraints.get("target_scope", "codebase")
        except Exception:
            pass

        # Hard wall-clock deadline: even if every inner timeout somehow passes,
        # the task will be forcibly cancelled after this many seconds.
        task_deadline = storage.parameters.get_parameter(
            key="research_task_deadline_seconds",
            default_value=1800,
            description="Hard asyncio wall-clock limit (seconds) for a single background research task before it is forcibly cancelled."
        )
        try:
            storage.commit()
        except Exception:
            pass

        report = await asyncio.wait_for(
            research.conduct_research(
                task_description=task.description,
                repo_path=task.repo_path,
                target_scope=scope
            ),
            timeout=task_deadline
        )
        result_text = _format_research_report(task.description, report)

        # Post result as an assistant message back to the originating session
        session_id = task.session_id or "default"
        storage.messages.create(
            role="assistant",
            content=result_text,
            session_id=session_id,
            task_id=task.task_id
        )
        task.state = TaskState.WORKING # Redundant but safe
        storage.commit()
        logger.info(f"Research task {task.task_id} completed")

    async def _run_decomposition(self, task: "TaskModel", storage):
        """
        @summary Execute a DECOMP task by breaking it into leaf subtasks in the DB.
        """
        decomp_mod = DecompositionModule(self._model, storage)
        
        # In a real workflow, we might look for existing ResearchReport if this is a follow-up
        # For now, we assume root decomposition.
        decomp = await decomp_mod.decompose_task(task.title, task.description)
        
        # Spawn subtasks
        for tid, proto in decomp.subtasks.items():
            sub = storage.tasks.create(
                title=proto.title,
                description=proto.description,
                session_id=task.session_id,
                parent_task_id=task.task_id,
                state=TaskState.PENDING,
                constraints={"target_files": proto.target_files}
            )
            sub.type = TaskType.IMPL
            sub.depth = task.depth + 1
            storage.commit()
            await self.enqueue(sub.task_id)
            
        task.state = TaskState.WORKING # Task stays working while children are active
        storage.commit()
        logger.info(f"Decomposition of {task.task_id} into {len(decomp.subtasks)} subtasks complete.")

    async def _run_implementation(self, task: "TaskModel", storage):
        """
        @summary Execute an IMPL task by generating and staging code candidates.
        """
        research_mod = ResearchModule(self._model, storage)
        impl_mod = ImplementationModule(self._model, storage, research_mod)
        
        # Trigger the implementation pass (includes internal local research)
        candidate_ids = await impl_mod.implement_task(task.task_id)
        
        storage.messages.create(
            role="assistant",
            content=f"🛠️ **Implementation Staged**\\nI've generated {len(candidate_ids)} candidates for task: *{task.title}*.",
            session_id=task.session_id or "default",
            task_id=task.task_id
        )
        
        task.state = TaskState.WORKING
        storage.commit()
        logger.info(f"Implementation of {task.task_id} complete.")

    async def _generate_plan_review(self, task: "TaskModel", attempt: "AttemptModel", storage) -> dict:
        """
        @summary Use the LLM to review the current plan health and recommend next steps.
        @inputs task: the task being worked on, attempt: the recent performance record
        @outputs dict containing plan_health, recommendation, confidence, rationale
        """
        logger.info(f"Generating plan review for task {task.task_id}")
        
        # Gather context
        outcome_str = attempt.outcome.value if attempt.outcome else "unknown"
        reason_str = attempt.reason or "No specific reason provided."
        
        prompt = f"""You are a senior technical project manager reviewing an agent's progress.
An 'Attempt' has just finished for the following task:
Title: {task.title}
Description: {task.description}

Attempt Outcome: {outcome_str}
Attempt Reason/Error: {reason_str}

Evaluate if the current plan (pursuing this task and its subtasks) still makes sense or if it needs structural adjustment.

Output MUST be a YAML block with these fields:
plan_health: healthy | uncertain | degraded | invalid
recommendation: continue | reattempt | decompose | internal_replan | abandon_to_parent
confidence: <float 0.0 to 1.0>
rationale: <short explanation>

Rules:
- Even if the attempt SUCCEEDED, you may recommend 'decompose' or 'internal_replan' if the approach seems unsustainable.
- Even if the attempt FAILED, you may recommend 'continue' (if it was a transient error) or 'reattempt'.
- 'healthy' means the plan is working and on track.
- 'uncertain' means progress is slower than expected or small obstacles appeared.
- 'degraded' means significant issues occurred, but the goal is still viable.
- 'invalid' means this branch of the plan is no longer viable.
"""
        try:
            messages = [{"role": "system", "content": prompt}]
            response = await self._model.chat(messages)
            content = response.get("content", "")
            
            review = self._model.extract_yaml(content)
            
            # Basic validation/normalization
            defaults = {
                "plan_health": "healthy" if outcome_str == "succeeded" else "uncertain",
                "recommendation": "continue" if outcome_str == "succeeded" else "reattempt",
                "confidence": 0.8,
                "rationale": "Automated fallback"
            }
            
            if not isinstance(review, dict) or "plan_health" not in review:
                logger.warning("LLM produced invalid plan review YAML, using default.")
                return defaults
                
            return {
                "plan_health": review.get("plan_health", defaults["plan_health"]),
                "recommendation": review.get("recommendation", defaults["recommendation"]),
                "confidence": float(review.get("confidence", defaults["confidence"])),
                "rationale": review.get("rationale", defaults["rationale"])
            }
            
        except Exception as e:
            logger.error(f"Error generating LLM plan review: {e}")
            return {
                "plan_health": "uncertain",
                "recommendation": "reattempt",
                "confidence": 0.5,
                "rationale": f"Review system error: {str(e)}"
            }
