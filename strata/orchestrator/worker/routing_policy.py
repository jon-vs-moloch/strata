"""
@module orchestrator.worker.routing_policy
@purpose Route tasks so the agent tier carries as much useful work as possible.

The point of this module is philosophical as much as technical: Strata is trying
to teach the operator-facing agent to succeed inside a disciplined harness, not
hide all meaningful work behind the trainer.
"""

from typing import Literal, Union
from strata.storage.models import TaskModel, TaskType
from strata.schemas.execution import ExecutionContext, TrainerExecutionContext, AgentExecutionContext

def select_model_tier(task: TaskModel) -> ExecutionContext:
    """
    @summary Routing logic for resolving the appropriate execution context.
    @rule Light-First: Background task execution defaults to the agent tier.

    Trainer is reserved for bootstrap and supervision flows unless a future policy
    explicitly introduces cross-pool escalation. In-pool escalation should be
    handled inside the selected pool's mutable config, not by silently jumping
    from agent to trainer here.
    """
    run_id = f"run_{task.task_id}"

    # Default to the agent tier for normal system work. Future cross-pool escalation, if any,
    # should be an explicit policy with its own telemetry rather than an implicit
    # risk-based shortcut.
    return AgentExecutionContext(run_id=run_id)
