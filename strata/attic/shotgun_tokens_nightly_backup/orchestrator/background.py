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

from shotgun_tokens.storage.models import TaskModel, TaskStatus, TaskType
from shotgun_tokens.orchestrator.research import ResearchModule

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

    async def start(self):
        """
        @summary Launch the background loop. Call once on app startup.
        """
        if self._running:
            return
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
            try:
                task_id = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                idle_ticks = 0
                await self._run_task(task_id)
                self._queue.task_done()
            except asyncio.TimeoutError:
                idle_ticks += 1
                # Trigger autonomous behavior after ~30 seconds of idle time
                if idle_ticks >= 30:
                    await self._generate_autonomous_task()
                    idle_ticks = 0
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Unexpected error in background worker: {e}")

    async def _generate_autonomous_task(self):
        """
        @summary When idle, the system puts itself through its paces by generating maintainence tasks.
        """
        storage = self._storage_factory()
        try:
            logger.info("System is idle. Generating autonomous maintenance task.")
            
            # Target any recently failed tasks to prioritize autonomous self-repair
            from shotgun_tokens.storage.models import TaskModel, TaskStatus
            failed_task = storage.session.query(TaskModel).filter(TaskModel.status == TaskStatus.FAILED).order_by(TaskModel.id.desc()).first()
            
            if failed_task:
                sys_prompt = f"You are an autonomous AI swarm orchestrator. The system is currently idle, but a previous task failed: '{failed_task.title}' (Description: {failed_task.description[:100]}). Propose exactly ONE task you should research or perform to investigate why this task failed or how to fix the underlying issue. Reply with ONLY a single sentence describing the task."
            else:
                sys_prompt = "You are an autonomous AI swarm orchestrator. The system is currently idle. Propose exactly ONE task you should research or maintain in the current codebase (e.g. finding bugs, test gaps, architecture flaws, or performance bottlenecks). Reply with ONLY a single sentence describing the task."
                
            messages = [{"role": "system", "content": sys_prompt}]
            response = await self._model.chat(messages)
            task_desc = response.get("content", "").strip()
            if not task_desc:
                task_desc = "Autonomously investigate errors in the orchestrator pipeline."
                
            task = storage.tasks.create(
                title=f"Auto-Maintenance: {task_desc[:40]}...",
                description=task_desc,
                session_id="default",
                constraints={"target_scope": "codebase"}
            )
            task.type = TaskType.RESEARCH
            storage.commit()
            
            storage.messages.create(
                role="assistant",
                content=f"🧠 **Idle Policy Activated**\\nI noticed the swarm was idle, so I'm putting myself to work. I've autonomously spawned a background task to investigate:\\n*{task_desc}*",
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
        @side_effects: updates task status, writes message to DB
        """
        # Each task gets a fresh session to avoid cross-request contamination
        storage = self._storage_factory()
        try:
            task = storage.session.query(TaskModel).filter(
                TaskModel.task_id == task_id
            ).first()

            if task is None:
                logger.warning(f"Task {task_id} not found — skipping")
                return

            # Mark running
            task.status = TaskStatus.RUNNING
            storage.commit()
            logger.info(f"Running task {task_id} ({task.type.value})")

            if task.type == TaskType.RESEARCH:
                await self._run_research(task, storage)
            else:
                logger.warning(f"BackgroundWorker: unsupported task type {task.type}")
                task.status = TaskStatus.FAILED
                storage.commit()

        except Exception as e:
            import traceback
            with open("err.log", "w") as f:
                f.write(traceback.format_exc())
            logger.exception(f"Task {task_id} failed: {e}")
            # Best-effort failure marking
            try:
                task = storage.session.query(TaskModel).filter(
                    TaskModel.task_id == task_id
                ).first()
                if task:
                    task.status = TaskStatus.FAILED
                    storage.commit()
            except Exception:
                pass
        finally:
            storage.session.close()

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

        report = await research.conduct_research(
            task_description=task.description,
            repo_path=task.repo_path,
            target_scope=scope
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
        task.status = TaskStatus.COMPLETED
        storage.commit()
        logger.info(f"Research task {task.task_id} completed")
