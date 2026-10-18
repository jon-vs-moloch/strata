"""
@module orchestrator.command
@purpose Pydantic schemas and logic for tool-calling from the Chat Agent to the swarm.
@owns task_tool_definitions, command_schemas, prioritization_logic
@does_not_own specific LLM inference, DB session management
@key_exports TaskActionRequest, CreateTaskAction, UpdateStatusAction, PrioritizeAction
"""

from typing import Optional, List, Union, Literal
from pydantic import BaseModel, Field

class CreateTaskAction(BaseModel):
    """
    @summary Instruct the swarm to initialize and frame a new coding objective.
    """
    title: str = Field(description="Short semantic name for the task.")
    description: str = Field(description="Detailed natural language goal for the swarm.")
    priority: int = Field(default=1, description="Higher numbers indicate earlier processing.")

class UpdateStatusAction(BaseModel):
    """
    @summary Manually change a task's progress state (e.g. to archive or block).
    """
    task_id: str
    new_status: str = Field(description="QUEUED, WAITING_DEPENDENCIES, RUNNING, COMPLETED, FAILED, BLOCKED_FOR_INPUT")

class PrioritizeAction(BaseModel):
    """
    @summary Steer the swarm by shifting relative task importance.
    """
    task_id: str
    new_priority: int

class ArchiveAction(BaseModel):
    """
    @summary Mark a task as inactive and hide from the primary swarm dashboard.
    """
    task_id: str

class SwarmCommand(BaseModel):
    """
    @summary Unified wrapper for a tool-call emitted by the Chat Agent.
    """
    action_type: Literal["create", "status", "prioritize", "archive"]
    action_data: Union[CreateTaskAction, UpdateStatusAction, PrioritizeAction, ArchiveAction]
