"""
@module schemas.core
@purpose Define Pydantic schemas for structured LLM communication.
@owns TaskFraming, TaskDecomposition, LeafTaskPrototypes, CandidateSchema
@does_not_own Database models (TaskModel), DB storage logic
@key_exports TaskFraming, TaskDecomposition, LeafTaskPrototype
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from enum import Enum

class TaskFraming(BaseModel):
    """
    @summary Structured framing of a coding task context.
    @inputs none (Pydantic model)
    @outputs none (Pydantic model)
    @invariants ensures the agent understands the repository and goals.
    """
    repository_context: str = Field(description="Summary of the relevant code files and structure.")
    problem_statement: str = Field(description="Concrete goal being addressed.")
    constraints: List[str] = Field(description="Strict rules for implementation.")
    success_criteria: List[str] = Field(description="How to verify the fix.")

class LeafTaskPrototype(BaseModel):
    """
    @summary Blueprint for a single atom of work (leaf task).
    @inputs none (Pydantic model)
    @outputs none (Pydantic model)
    """
    title: str
    description: str
    target_files: List[str]
    dependencies: List[str] = Field(default_factory=list, description="IDs of other leaf tasks in the same decomposition.")

class TaskDecomposition(BaseModel):
    """
    @summary Complete decomposition of a root task into parallelizable steps.
    @inputs none (Pydantic model)
    @outputs none (Pydantic model)
    """
    framing: TaskFraming
    subtasks: Dict[str, LeafTaskPrototype] = Field(description="Mapping of task IDs to their prototypes.")
    total_estimated_budget: float = Field(description="Tokens or seconds.")
