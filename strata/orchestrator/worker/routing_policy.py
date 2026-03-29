"""
@module orchestrator.worker.routing_policy
@purpose Route tasks so the weak tier carries as much useful work as possible.

The point of this module is philosophical as much as technical: Strata is trying
to teach smaller local models to succeed inside a disciplined harness, not hide
all meaningful work behind a stronger remote model.
"""

from typing import Literal, Union
from strata.storage.models import TaskModel, TaskType
from strata.schemas.execution import ExecutionContext, StrongExecutionContext, WeakExecutionContext

def select_model_tier(task: TaskModel) -> ExecutionContext:
    """
    @summary Routing logic for resolving the appropriate execution context.
    @rule Light-First: Background task execution defaults to the Weak tier.

    Strong is reserved for bootstrap/supervision flows unless a future policy
    explicitly introduces cross-pool escalation. In-pool escalation should be
    handled inside the selected pool's mutable config, not by silently jumping
    from weak to strong here.
    """
    run_id = f"run_{task.task_id}"

    # Default to Weak for normal system work. Future cross-pool escalation, if any,
    # should be an explicit policy with its own telemetry rather than an implicit
    # risk-based shortcut.
    return WeakExecutionContext(run_id=run_id)
