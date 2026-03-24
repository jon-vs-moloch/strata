"""
@module schemas.execution
@purpose Define Pydantic schemas for runtime execution contexts and tiers.
@key_exports ExecutionContext, StrongExecutionContext, WeakExecutionContext
"""

from pydantic import BaseModel, Field
from typing import Literal, Optional, List

class ExecutionContext(BaseModel):
    """
    @summary Base runtime context for STRATA tasks.
    """
    mode: Literal["strong", "weak"] = Field(..., description="The context tier.")
    allow_cloud: bool = Field(..., description="Whether cloud transport is permitted.")
    allow_local: bool = Field(..., description="Whether local transport is permitted.")
    evaluation_run: bool = Field(default=False, description="Flag for runs that are part of an experiment.")
    run_id: str = Field(..., description="Unique ID for this specific run.")
    candidate_change_id: Optional[str] = Field(None, description="The ID of the candidate change being evaluated.")

class StrongExecutionContext(ExecutionContext):
    """
    @summary Runtime optimized for complex planning and supervision via cloud.
    """
    mode: Literal["strong"] = "strong"
    allow_cloud: bool = True
    allow_local: bool = False

class WeakExecutionContext(ExecutionContext):
    """
    @summary Runtime restricted for local evaluation of task agents.
    """
    mode: Literal["weak"] = "weak"
    allow_cloud: bool = False
    allow_local: bool = True
