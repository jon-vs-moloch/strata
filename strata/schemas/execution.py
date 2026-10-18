"""
@module schemas.execution
@purpose Define Pydantic schemas for runtime execution contexts and tiers.
@key_exports ExecutionContext, TrainerExecutionContext, AgentExecutionContext, LocalAgentExecutionContext, RemoteAgentExecutionContext
"""

from pydantic import BaseModel, Field
from typing import Optional

class ExecutionContext(BaseModel):
    """
    @summary Base runtime context for STRATA tasks.
    """
    mode: str = Field(..., description="The execution profile or tier.")
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
    mode: str = "trainer"
    allow_cloud: Optional[bool] = None
    allow_local: Optional[bool] = None

class LocalAgentExecutionContext(ExecutionContext):
    """
    @summary Local worker runtime optimized for constrained or lower-cost execution.
    """
    mode: str = "local_agent"
    allow_cloud: Optional[bool] = None
    allow_local: Optional[bool] = None


class RemoteAgentExecutionContext(ExecutionContext):
    """
    @summary Cloud worker runtime used to compare scaffold quality against stronger remote inference.
    """
    mode: str = "remote_agent"
    allow_cloud: Optional[bool] = None
    allow_local: Optional[bool] = None


class AgentExecutionContext(LocalAgentExecutionContext):
    """
    @summary Backward-compatible alias for the default local worker profile.
    """
