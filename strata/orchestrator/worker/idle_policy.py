"""
@module orchestrator.worker.idle_policy
@purpose Alignment policy to be run when the system is idle.
"""

import logging
from datetime import datetime
from pathlib import Path
from strata.storage.models import TaskModel, TaskState, TaskType
from strata.specs.bootstrap import load_specs, spec_is_bootstrap_placeholder

logger = logging.getLogger(__name__)
def _build_repo_snapshot() -> str:
    root = Path(__file__).resolve().parents[3]
    interesting = [
        "README.md",
        "docs/spec/project-philosophy.md",
        "docs/spec/codemap.md",
        "strata/api",
        "strata/eval",
        "strata/orchestrator",
        "strata/knowledge",
        "strata/storage",
        "strata/specs",
        "strata_ui/src",
    ]
    parts = []
    for rel in interesting:
        path = root / rel
        if not path.exists():
            continue
        if path.is_file():
            parts.append(f"FILE {rel}")
            continue
        children = sorted(child.name for child in path.iterdir() if not child.name.startswith("."))
        preview = ", ".join(children[:12])
        if len(children) > 12:
            preview += ", ..."
        parts.append(f"DIR {rel}: {preview}")
    return "\n".join(parts)

async def run_idle_tasks(storage_factory, model_adapter, queue):
    """
    @summary Handle autonomous gap analysis when the worker is idle.
    """
    logger.info("System is idle. Triggering Constitutional Alignment Task.")
    storage = storage_factory()
    try:
        # 1. Read the user specifications from a guaranteed bootstrap location
        specs = load_specs(storage=storage)
        global_spec = specs.get("global_spec", "None.")
        project_spec = specs.get("project_spec", "None.")

        has_usable_specs = any(
            spec.strip() and spec.strip().lower() != "none."
            for spec in [global_spec, project_spec]
        )
        if not has_usable_specs:
            logger.info("Idle alignment skipped because no usable specs are present.")
            return

        active_alignment_rows = (
            storage.session.query(TaskModel)
            .filter(
                TaskModel.type == TaskType.RESEARCH,
                TaskModel.state.in_([TaskState.PENDING, TaskState.WORKING, TaskState.BLOCKED]),
                TaskModel.title.like("Alignment:%"),
            )
            .all()
        )
        active_alignment = []
        for candidate in active_alignment_rows:
            constraints = dict(candidate.constraints or {})
            description = str(candidate.description or "").lower()
            if (
                not constraints.get("alignment_source")
                and "cannot identify" in description
                and (
                    "no codebase" in description
                    or "no vision" in description
                    or "vision document" in description
                    or "no goals" in description
                    or "desired end state are unknown" in description
                )
            ):
                candidate.state = TaskState.CANCELLED
                constraints["superseded_by_alignment_fix"] = datetime.utcnow().isoformat()
                candidate.constraints = constraints
                continue
            active_alignment.append(candidate)
        if len(active_alignment_rows) != len(active_alignment):
            storage.commit()
        if active_alignment:
            logger.info("Idle alignment skipped because an alignment-style research task already exists.")
            return

        spec_paths = [
            ".knowledge/specs/global_spec.md",
            ".knowledge/specs/project_spec.md",
            "docs/spec/project-philosophy.md",
            "docs/spec/codemap.md",
        ]
        repo_snapshot = _build_repo_snapshot()
        project_spec_is_thin = spec_is_bootstrap_placeholder(project_spec)
        global_spec_is_thin = spec_is_bootstrap_placeholder(global_spec)

        if project_spec_is_thin and global_spec_is_thin:
            task_desc = (
                "Review docs/spec/project-philosophy.md, README.md, and .knowledge/specs/project_spec.md, "
                "then prepare a reviewed spec proposal that turns the current project vision into durable spec language."
            )
        else:
            # 2. Prompt for Alignment
            from strata.schemas.execution import WeakExecutionContext
            model_adapter.bind_execution_context(WeakExecutionContext(run_id="idle_alignment"))
            
            sys_prompt = f"""You are the Alignment Module for Strata.
The system is currently IDLE. Your job is to identify ONE concrete alignment gap between the durable spec and the current repo, then propose exactly one bounded task.

Canonical spec paths:
- .knowledge/specs/global_spec.md
- .knowledge/specs/project_spec.md

Supporting references you may assume exist:
- README.md
- docs/spec/project-philosophy.md
- docs/spec/codemap.md

Observed repository snapshot:
{repo_snapshot}

Current global spec:
{global_spec}

Current project spec:
{project_spec}

Rules:
- Do not claim the vision or current state is unknown; the spec paths above are the source of truth.
- Use the repository snapshot above as concrete codebase state.
- If the spec is still thin, propose a spec-hardening or alignment-review task rather than giving up.
- Prefer a task that is specific, bounded, and checkable.
- Reply with ONLY a single sentence describing the task.
"""
            messages = [{"role": "system", "content": sys_prompt}]
            response = await model_adapter.chat(messages)
            task_desc = response.get("content", "").strip()
            if not task_desc:
                task_desc = "Review the project spec and philosophy docs, then propose one bounded alignment task for the current codebase."
            
        task = storage.tasks.create(
            title=f"Alignment: {task_desc[:40]}...",
            description=task_desc,
            session_id="default",
            state=TaskState.PENDING,
            constraints={
                "target_scope": "codebase",
                "spec_paths": spec_paths,
                "repo_snapshot": repo_snapshot,
                "alignment_source": "idle_policy",
                "spec_bootstrap_fallback": project_spec_is_thin and global_spec_is_thin,
            }
        )
        task.type = TaskType.RESEARCH
        storage.commit()
        
        storage.messages.create(
            role="assistant",
            content=(
                "🧠 **Constitutional Alignment Policy Active**\n"
                "I've analyzed the project specs and identified a gap. "
                f"The alignment task is grounded in {', '.join(spec_paths[:2])}.\n"
                f"*{task_desc}*"
            ),
            session_id="default"
        )
        storage.commit()
        await queue.put(task.task_id)
        
    except Exception as e:
        logger.error(f"Failed to generate autonomous task: {e}")
    finally:
        storage.session.close()
