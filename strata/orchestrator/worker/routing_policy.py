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
    @rule Light-First: All tasks default to the Weak tier unless explicitly escalated.
    """
    run_id = f"run_{task.task_id}"
    
    # 1. Check for manual escalation or high-risk overrides
    is_risky = getattr(task, 'risk', 'low') == "high"
    
    # FUTURE: Escalation logic will check Attempt history for repeating failures
    # For now, we only escalate if it is explicitly marked as high risk.
    if is_risky:
        return StrongExecutionContext(run_id=run_id)
        
    # 2. Default to Weak (Local) for ALL tasks (Research, Impl, Decomp)
    # This enforces the "Harnessing the Weak" philosophy where we improve the 
    # weak model until it can handle these.
    return WeakExecutionContext(run_id=run_id)
