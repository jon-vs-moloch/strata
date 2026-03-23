"""
@module storage.models
@purpose Define SQLAlchemy ORM models for the Shotgun Tokens orchestrator.
@owns database schemas, relationship mappings
@does_not_own database engine creation, querying logic, or migrations
@key_exports TaskModel, CandidateModel, EvaluationModel, PromptModel, MessageModel, TaskStatus
@side_effects none
"""

from sqlalchemy import Column, Integer, String, Boolean, DateTime, JSON, ForeignKey, Table, Enum as SQLEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from enum import Enum as PyEnum
from typing import List, Optional, Dict
from datetime import datetime
from uuid import uuid4

class Base(DeclarativeBase):
    pass

class TaskStatus(PyEnum):
    QUEUED = "QUEUED"
    WAITING_DEPENDENCIES = "WAITING_DEPENDENCIES"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED_FOR_INPUT = "BLOCKED_FOR_INPUT"

class TaskType(PyEnum):
    RESEARCH = "RESEARCH"
    IMPL = "IMPL"
    BUG_FIX = "BUG_FIX"
    REFACTOR = "REFACTOR"

# Association table for task dependencies
task_dependencies = Table(
    "task_dependencies",
    Base.metadata,
    Column("task_id", String, ForeignKey("tasks.task_id"), primary_key=True),
    Column("depends_on_id", String, ForeignKey("tasks.task_id"), primary_key=True),
)

class TaskModel(Base):
    """
    @summary Represents a root or leaf coding task in the dependency graph.
    @inputs none (ORM entity)
    @outputs none (ORM entity)
    @side_effects none
    @invariants Every task has a unique task_id and valid TaskStatus.
    """
    __tablename__ = "tasks"
    
    task_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid4()))
    parent_task_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String)
    type: Mapped[TaskStatus] = mapped_column(SQLEnum(TaskType), default=TaskType.IMPL)
    status: Mapped[TaskStatus] = mapped_column(SQLEnum(TaskStatus), default=TaskStatus.QUEUED)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    repo_path: Mapped[str] = mapped_column(String)
    
    constraints: Mapped[dict] = mapped_column(JSON, default=dict)
    success_criteria: Mapped[dict] = mapped_column(JSON, default=dict)
    budget: Mapped[dict] = mapped_column(JSON, default=dict)
    prompt_registry: Mapped[dict] = mapped_column(JSON, default=dict)
    sequence_id: Mapped[str] = mapped_column(String, default="default_v1")
    human_intervention_required: Mapped[bool] = mapped_column(Boolean, default=False)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    candidates: Mapped[List["CandidateModel"]] = relationship(back_populates="task")
    dependencies: Mapped[List["TaskModel"]] = relationship(
        "TaskModel",
        secondary=task_dependencies,
        primaryjoin=task_id == task_dependencies.c.task_id,
        secondaryjoin=task_id == task_dependencies.c.depends_on_id,
        backref="blocked_tasks"
    )

class CandidateModel(Base):
    """
    @summary Represents a single generated code candidate for a task.
    @inputs none (ORM entity)
    @outputs none (ORM entity)
    @side_effects none
    @invariants Always maps to exactly one TaskModel via task_id.
    """
    __tablename__ = "candidates"
    
    candidate_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid4()))
    task_id: Mapped[str] = mapped_column(String, ForeignKey("tasks.task_id"))
    stage: Mapped[str] = mapped_column(String) # framing, impl, repair, synth
    prompt_version: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    temperature: Mapped[float] = mapped_column(Integer)
    artifact_type: Mapped[str] = mapped_column(String) # python_file, patch, markdown
    content_path: Mapped[str] = mapped_column(String) # Path to the actually generated file
    summary: Mapped[str] = mapped_column(String)
    proposed_files: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    task: Mapped[TaskModel] = relationship(back_populates="candidates")
    evaluations: Mapped[List["EvaluationModel"]] = relationship(back_populates="candidate")

class EvaluationModel(Base):
    """
    @summary Represents a test/eval execution result for a single candidate.
    @inputs none (ORM entity)
    @outputs none (ORM entity)
    @side_effects none
    @invariants Always maps to exactly one CandidateModel.
    """
    __tablename__ = "evaluations"
    
    evaluation_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid4()))
    candidate_id: Mapped[str] = mapped_column(String, ForeignKey("candidates.candidate_id"))
    status: Mapped[str] = mapped_column(String) # passed, failed_tests, failed_lint, etc.
    compile_success: Mapped[bool] = mapped_column(Boolean)
    lint_success: Mapped[bool] = mapped_column(Boolean)
    tests: Mapped[dict] = mapped_column(JSON) # {passed: 10, failed: 2, duration: 1.5s}
    benchmarks: Mapped[dict] = mapped_column(JSON)
    artifacts: Mapped[dict] = mapped_column(JSON) # {stdout: "", stderr: ""}
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    candidate: Mapped[CandidateModel] = relationship(back_populates="evaluations")

class PromptModel(Base):
    """
    @summary Represents a versioned system prompt used for agent stages.
    @inputs none (ORM entity)
    @outputs none (ORM entity)
    @side_effects none
    @invariants Tracking usage_count helps meta-evaluation.
    """
    __tablename__ = "prompts"
    
    prompt_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid4()))
    stage: Mapped[str] = mapped_column(String)
    version: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(String)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class MessageModel(Base):
    """
    @summary Represents a chat message in the Orchestrator Chat log.
    @inputs none (ORM entity)
    @outputs none (ORM entity)
    @side_effects none
    @invariants role must be 'user', 'assistant', or 'system'.
    """
    __tablename__ = "messages"
    
    message_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid4()))
    role: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(String)
    is_intervention: Mapped[bool] = mapped_column(Boolean, default=False)
    associated_task_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
