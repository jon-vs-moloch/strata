"""
@module orchestrator.worker.routing_policy
@purpose Explicit routing of tasks to strong or weak models based on risk and scope.
"""

from typing import Literal
from strata.storage.models import TaskModel, TaskType

ModelTier = Literal["strong", "weak"]

def select_model_tier(task: TaskModel) -> ModelTier:
    """
    @summary routing logic for strong->weak handoff.
    """
    # 1. Architectural tasks always go to strong models
    if task.type in [TaskType.DECOMP, TaskType.RESEARCH] or task.risk == "high":
        return "strong"
        
    # 2. Check for well-formed constraints (Target for weak models)
    import json
    try:
        constraints = task.constraints if isinstance(task.constraints, dict) else json.loads(task.constraints or "{}")
        
        # If we have target files and a validator, it's a candidate for weak model
        if constraints.get("target_files") and constraints.get("validator"):
              # Canary logic could go here (e.g. 20% to weak model even if risky)
              return "weak"
    except:
        pass
        
    # 3. Default to strong for safety
    return "strong"

def is_canary_eligible(task: TaskModel) -> bool:
    """
    @summary Determine if a task is eligible for canary routing to a weak model.
    """
    import random
    # Implementation of 20% canary for testing new harness components
    return random.random() < 0.2
