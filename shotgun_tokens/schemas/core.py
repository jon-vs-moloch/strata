"""
@module schemas.core
@purpose Define Pydantic schemas for structured Strata LLM communication.
@owns TaskFraming, TaskDecomposition, LeafTaskPrototypes, CandidateSchema, AttemptResolution
@does_not_own Database models (TaskModel), DB storage logic
@key_exports TaskFraming, TaskDecomposition, LeafTaskPrototype, ResearchReport, AttemptResolutionSchema
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Literal
from enum import Enum

class SubtaskDraft(BaseModel):
    title: str = Field(
        ..., 
        description="A short, clear title for the new subtask."
    )
    description: str = Field(
        ..., 
        description="Detailed instructions on what this subtask must accomplish."
    )

class AttemptResolutionSchema(BaseModel):
    reasoning: str = Field(
        ..., 
        description="Explain exactly why the attempt failed and evaluate the best path forward before choosing a resolution."
    )
    resolution: Literal["reattempt", "decompose", "internal_replan", "abandon_to_parent"] = Field(
        ...,
        description="The structural decision for how to handle this failure."
    )
    new_subtasks: List[SubtaskDraft] = Field(
        default_factory=list,
        description="If resolution is 'decompose' or 'internal_replan', provide the new child tasks here. If 'reattempt' or 'abandon_to_parent', leave this array empty."
    )

class ResearchReport(BaseModel):
    """
    @summary Structured output of a research phase, summarizing findings.
    @inputs none (Pydantic model)
    @outputs none (Pydantic model)
    @invariants Findings must be actionable for the next agent stage.
    """
    context_gathered: str = Field(description="Summary of documentation or code analyzed.")
    key_constraints_discovered: List[str] = Field(description="Hidden rules or limitations found during research.")
    suggested_approach: str = Field(description="High-level recommendation based on research.")
    reference_urls: List[str] = Field(default_factory=list, description="URLs or local file paths referenced.")


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
    research_summary: Optional[ResearchReport] = Field(default=None, description="Prior research context if available.")

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
