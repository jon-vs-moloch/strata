from typing import Literal, Union
from strata.storage.models import TaskModel, TaskType
from strata.schemas.execution import ExecutionContext, StrongExecutionContext, WeakExecutionContext

def select_model_tier(task: TaskModel) -> ExecutionContext:
    """
    @summary routing logic for resolving the appropriate execution context.
    """
    run_id = f"run_{task.task_id}"
    
    # 1. Architectural tasks always go to strong models
    is_architectural = (hasattr(task, 'type') and task.type in [TaskType.DECOMP, TaskType.RESEARCH])
    is_risky = getattr(task, 'risk', 'low') == "high"
    
    if is_architectural or is_risky:
        return StrongExecutionContext(run_id=run_id)
        
    # 2. Check for well-formed constraints (Target for weak models)
    import json
    try:
        constraints = task.constraints if isinstance(task.constraints, dict) else json.loads(task.constraints or "{}")
        
        # If we have target files and a validator, it's a candidate for weak model
        if constraints.get("target_files") and constraints.get("validator"):
              # Canary logic could go here (e.g. 20% to weak model even if risky)
              return WeakExecutionContext(run_id=run_id)
    except:
        pass
        
    # 3. Default to strong for safety
    return StrongExecutionContext(run_id=run_id)
