"""
@module orchestrator.worker.idle_policy
@purpose Alignment policy to be run when the system is idle.
"""

import os
import logging
from datetime import datetime
from strata.storage.models import TaskModel, TaskState, TaskType

logger = logging.getLogger(__name__)

async def run_idle_tasks(storage_factory, model_adapter, queue):
    """
    @summary Handle autonomous gap analysis when the worker is idle.
    """
    logger.info("System is idle. Triggering Constitutional Alignment Task.")
    storage = storage_factory()
    try:
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
        response = await model_adapter.chat(messages)
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
        await queue.put(task.task_id)
        
    except Exception as e:
        logger.error(f"Failed to generate autonomous task: {e}")
    finally:
        storage.session.close()
