"""
@module schemas.execution
@purpose Define Pydantic schemas for runtime execution contexts and tiers.
@key_exports ExecutionContext, TrainerExecutionContext, AgentExecutionContext
"""

from pydantic import BaseModel, Field
from typing import Literal, Optional, List

class ExecutionContext(BaseModel):
    """
    @summary Base runtime context for STRATA tasks.
    """
    mode: Literal["trainer", "agent"] = Field(..., description="The context tier.")
    allow_cloud: Optional[bool] = Field(
        None,
        description="Optional override for whether cloud transport is permitted. If unset, the pool policy decides.",
    )
    allow_local: Optional[bool] = Field(
        None,
        description="Optional override for whether local transport is permitted. If unset, the pool policy decides.",
    )
    evaluation_run: bool = Field(default=False, description="Flag for runs that are part of an experiment.")
    run_id: str = Field(..., description="Unique ID for this specific run.")
    candidate_change_id: Optional[str] = Field(None, description="The ID of the candidate change being evaluated.")

class TrainerExecutionContext(ExecutionContext):
    """
    @summary Runtime optimized for complex planning and supervision.
    """
    mode: Literal["trainer"] = "trainer"
    allow_cloud: Optional[bool] = None
    allow_local: Optional[bool] = None

class AgentExecutionContext(ExecutionContext):
    """
    @summary Runtime optimized for constrained or lower-cost execution.
    """
    mode: Literal["agent"] = "agent"
    allow_cloud: Optional[bool] = None
    allow_local: Optional[bool] = None
