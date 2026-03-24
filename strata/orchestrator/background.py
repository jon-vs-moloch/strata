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

from strata.storage.models import TaskModel, TaskState, TaskType, AttemptModel, AttemptOutcome, AttemptResolution
from strata.orchestrator.research import ResearchModule
from strata.orchestrator.decomposition import DecompositionModule
from strata.orchestrator.implementation import ImplementationModule
from strata.schemas.core import AttemptResolutionSchema

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

    def __init__(self, storage_factory, model_adapter, memory=None):
        """
        @summary Initialise worker (does not start it yet).
        @inputs storage_factory: zero-arg callable → StorageManager
        @inputs model_adapter: ModelAdapter instance
        @inputs memory: Optional SemanticMemory instance
        """
        self._storage_factory = storage_factory
        self._model = model_adapter
        self._memory = memory
        self._queue: asyncio.Queue = asyncio.Queue()
        self._running_task: Optional[asyncio.Task] = None
        self._on_update_callback = None
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
            from strata.storage.models import TaskModel, TaskState
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

    def set_on_update(self, callback):
        """Register a callback for task status updates."""
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
            from strata.storage.models import ModelTelemetry
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
            from strata.storage.models import TaskModel, TaskState, TaskType
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
            task.state = TaskState.RUNNING
            storage.commit()
            await self._notify(task_id, task.state.value)
            
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
                task.state = TaskState.COMPLETED
                storage.commit()
                await self._notify(task_id, task.state.value)
                logger.info(f"Task {task_id} completed successfully.")

            except Exception as e:
                storage.rollback() # Rollback any partial progress in repositories if possible
                
                # Determine resolution using SLM Structured Outputs
                resolution_data = await self._determine_resolution(task, e, storage)
                storage.attempts.set_resolution(attempt.attempt_id, AttemptResolution(resolution_data.resolution))
                
                await self._apply_resolution(task, resolution_data, e, storage)
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

    async def _determine_resolution(self, task: "TaskModel", error: Exception, storage) -> AttemptResolutionSchema:
        """
        @summary Classify the failure and choose a resolution strategy using Structured SLM Output.
        """
        import json
        
        prompt = f"""You are a failure analysis agent for the Strata Swarm.
A background task has failed. Evaluate the error and determine the structural fix.

TASK: {task.title}
DESCRIPTION: {task.description}
ERROR: {str(error)}

RESOLUTIONS:
- reattempt: Use for transient/random errors.
- decompose: The goal is too large; break it into simpler subtasks.
- internal_replan: The current approach/method is flawed; replan at this level.
- abandon_to_parent: This leaf task is impossible OR requires architectural decisions beyond this scope.
- blocked: Use when the task is blocked by a missing physical requirement that ONLY a human can provide (e.g. missing API keys, manual environment setup, clarification on ambiguous user intent, access to a private resource).

Respond with structured reasoning first, then the resolution choice.
"""
        response = await self._model.chat(
            messages=[{"role": "system", "content": prompt}],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "attempt_resolution",
                    "strict": True,
                    "schema": AttemptResolutionSchema.model_json_schema()
                }
            }
        )
        
        try:
            raw_content = response.get("content", "{}")
            # Handle potential markdown fence
            if "```json" in raw_content:
                raw_content = raw_content.split("```json")[1].split("```")[0]
            
            data = json.loads(raw_content)
            return AttemptResolutionSchema(**data)
        except Exception as e:
            logger.error(f"Failed to parse structured SLM resolution: {e}. Falling back to REATTEMPT.")
            return AttemptResolutionSchema(
                reasoning=f"Resolution analysis failed: {e}",
                resolution="reattempt",
                new_subtasks=[]
            )

    async def _apply_resolution(self, task: "TaskModel", resolution_data: AttemptResolutionSchema, error: Exception, storage):
        """
        @summary Apply the SLM's structural resolution to the task graph.
        """
        from strata.storage.models import TaskState, TaskType
        # 5. Index into long-term semantic memory for future RAG retrieval
        if self._memory:
            try:
                self._memory.upsert_task_memory(
                    task_id=task.task_id,
                    content=f"TASK: {task.title}\nDESCRIPTION: {task.description}\nRESOLUTION: {resolution_data.resolution}\nRATIONALE: {resolution_data.reasoning}",
                    metadata={"type": task.type.value, "status": task.state.value}
                )
            except Exception as e:
                logger.error(f"Failed to index task memory: {e}")
        res = resolution_data.resolution
        logger.info(f"Applying SLM Resolution: {res.upper()} for task {task.task_id} ({resolution_data.reasoning})")
        
        if res == "reattempt":
            task.state = TaskState.PENDING
            await self.enqueue(task.task_id)
            
        elif res == "decompose" or res == "internal_replan":
            if resolution_data.new_subtasks:
                for sub_proto in resolution_data.new_subtasks:
                    sub = storage.tasks.create(
                        parent_task_id=task.task_id,
                        title=f"Recovery: {sub_proto.title}",
                        description=sub_proto.description,
                        session_id=task.session_id,
                        state=TaskState.PENDING,
                        depth=task.depth + 1
                    )
                    sub.type = TaskType.IMPL
                    storage.commit()
                    await self.enqueue(sub.task_id)
                task.state = TaskState.WORKING
            else:
                # Fallback to generic decomposition task
                failover = storage.tasks.create(
                    title=f"Recovery Plan for {task.title}",
                    description=f"Automated recovery from failover analysis: {resolution_data.reasoning}. Original error: {error}",
                    parent_task_id=task.task_id,
                    type=TaskType.DECOMP,
                    state=TaskState.PENDING,
                    depth=task.depth + 1
                )
                storage.commit()
                await self.enqueue(failover.task_id)
                task.state = TaskState.WORKING
                
        elif res == "abandon_to_parent":
            task.state = TaskState.ABANDONED
            # Solve the Gridlock: Trigger the Dependency Cascade
            storage.apply_dependency_cascade()

        elif res == "improve_tooling":
            target = resolution_data.tool_modification_target or "unknown_tool"
            logger.info(f"Resolution: IMPROVE_TOOLING for target {target}")
            
            # 1. Block current task
            task.state = TaskState.BLOCKED
            
            # 2. Spawn a MAXIMUM priority Implementation task
            repair_task = storage.tasks.create(
                title=f"Tool Repair: {target}",
                description=f"The Orchestrator failed an objective because a tool is inadequate or missing. Target: {target}. Reasoning: {resolution_data.reasoning}. Read the tool, fix the logic, and promote it.",
                session_id=task.session_id,
                state=TaskState.PENDING,
                type=TaskType.IMPL,
                depth=task.depth + 1,
                priority=100.0 # Maximum priority
            )
            storage.commit()
            
            # 3. Add dependency link
            storage.tasks.add_dependency(task.task_id, repair_task.task_id)
            storage.commit()
            
            # 4. Enqueue the repair task
            await self.enqueue(repair_task.task_id)

        elif res == "blocked":
            import json
            task.state = TaskState.BLOCKED
            task.human_intervention_required = True
            
            # Post a structured system intervention message to the chat
            storage.messages.create(
                role="system",
                content=json.dumps({
                    "error": "Task Blocked",
                    "reasoning": resolution_data.reasoning,
                    "task_id": task.task_id,
                    "title": task.title
                }),
                session_id=task.session_id or "default",
                is_intervention=True,
                task_id=task.task_id
            )
            storage.commit()
            logger.info(f"Task {task.task_id} is now BLOCKED. Awaiting human intervention.")

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
