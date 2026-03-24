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

from strata.storage.models import TaskModel, TaskStatus, TaskType
from strata.orchestrator.research import ResearchModule

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
        while self._running:
            try:
                task_id = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._run_task(task_id)
                self._queue.task_done()
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Unexpected error in background worker: {e}")

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
        research = ResearchModule(self._model, storage)
        report = await research.conduct_research(
            task_description=task.description,
            repo_path=task.repo_path
        )
        result_text = _format_research_report(task.description, report)

        # Post result as an assistant message back to the originating session
        session_id = task.session_id or "default"
        storage.messages.create(
            role="assistant",
            content=result_text,
            session_id=session_id,
            associated_task_id=task.task_id
        )
        task.status = TaskStatus.COMPLETED
        storage.commit()
        logger.info(f"Research task {task.task_id} completed")
