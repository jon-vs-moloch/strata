"""
@module core.policy
@purpose Shared business rules and task constraints for the Strata system.
"""

from strata.storage.models import TaskModel, TaskType

def requires_validator(task: TaskModel) -> bool:
    """
    @summary Determine if a task is architectural/risky enough to MANDATE a validator.
    @inputs task: TaskModel instance
    @outputs boolean True if a validator is required
    """
    # 1. Implementation and self-improvement tasks are always required to be validated
    t = task.type.value if hasattr(task.type, "value") else str(task.type)
    
    mandatory_types = {
        TaskType.IMPL.value, 
        TaskType.BUG_FIX.value, 
        TaskType.REFACTOR.value, 
        "improve_tooling", # custom type string if used in some places
        "feature"
    }
    
    if t in mandatory_types:
        return True
        
    # 2. High-risk tasks always require it
    if task.risk == "high":
        return True
        
    return False

def validator_policy_for_task(task: TaskModel) -> str:
    """
    @summary Helper for consistent policy messaging in UI and traces.
    """
    return "required" if requires_validator(task) else "optional"
