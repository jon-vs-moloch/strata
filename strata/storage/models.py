"""
@module storage.models
@purpose Define SQLAlchemy ORM models for the Strata orchestrator.
@owns database schemas, relationship mappings
@does_not_own database engine creation, querying logic, or migrations
@key_exports TaskModel, CandidateModel, EvaluationModel, PromptModel, MessageModel, TaskStatus
@side_effects none
"""

from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Table, Enum as SQLEnum, JSON, Float, Index
from sqlalchemy.orm import relationship, Mapped, mapped_column, DeclarativeBase
from enum import Enum as PyEnum
from typing import List, Optional, Dict
from datetime import datetime
from uuid import uuid4

class Base(DeclarativeBase):
    pass

class TaskState(PyEnum):
    PENDING = "pending"
    WORKING = "working"
    BLOCKED = "blocked"
    PUSHED = "pushed"
    COMPLETE = "complete"
    ABANDONED = "abandoned"
    CANCELLED = "cancelled"

class TaskType(PyEnum):
    RESEARCH = "RESEARCH"
    IMPL = "IMPL"
    BUG_FIX = "BUG_FIX"
    REFACTOR = "REFACTOR"
    DECOMP = "DECOMP"
    JUDGE = "JUDGE"


def task_state_api_value(state: object) -> str:
    raw = getattr(state, "value", state)
    normalized = str(raw or "").strip().lower()
    if normalized == "pushed":
        return "decomposed"
    return normalized


def task_state_display_label(state: object) -> str:
    normalized = task_state_api_value(state)
    if normalized == "decomposed":
        return "Children in progress"
    return normalized.replace("_", " ").title()

class AttemptOutcome(PyEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"

class AttemptResolution(PyEnum):
    REATTEMPT = "reattempt"
    DECOMPOSE = "decompose"
    INTERNAL_REPLAN = "internal_replan"
    ABANDON_TO_PARENT = "abandon_to_parent"
    IMPROVE_TOOLING = "improve_tooling"
    BLOCKED = "blocked"

# Association table for task dependencies
task_dependencies = Table(
    "task_dependencies",
    Base.metadata,
    Column("task_id", String, ForeignKey("tasks.task_id"), primary_key=True),
    Column("depends_on_id", String, ForeignKey("tasks.task_id"), primary_key=True),
)

class TaskModel(Base):
    """
    @summary Represents a root or leaf unit of work in the recursive task graph.
    @inputs none (ORM entity)
    @outputs none (ORM entity)
    @side_effects none
    @invariants Every task has a unique task_id and valid TaskState.
    """
    __tablename__ = "tasks"
    
    task_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid4()))
    parent_task_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("tasks.task_id"), nullable=True)
    title: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String)
    type: Mapped[TaskType] = mapped_column(SQLEnum(TaskType), default=TaskType.IMPL)
    state: Mapped[TaskState] = mapped_column(SQLEnum(TaskState), default=TaskState.PENDING)
    priority: Mapped[float] = mapped_column(Integer, default=0.0)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    repo_path: Mapped[str] = mapped_column(String, default=".")
    
    constraints: Mapped[dict] = mapped_column(JSON, default=dict)
    success_criteria: Mapped[dict] = mapped_column(JSON, default=dict)
    budget: Mapped[dict] = mapped_column(JSON, default=dict)
    prompt_registry: Mapped[dict] = mapped_column(JSON, default=dict)
    sequence_id: Mapped[str] = mapped_column(String, default="default_v1")
    session_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    human_intervention_required: Mapped[bool] = mapped_column(Boolean, default=False)
    
    active_child_ids: Mapped[list] = mapped_column(JSON, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    attempts: Mapped[List["AttemptModel"]] = relationship(back_populates="task", cascade="all, delete-orphan")
    candidates: Mapped[List["CandidateModel"]] = relationship(back_populates="task")
    dependencies: Mapped[List["TaskModel"]] = relationship(
        "TaskModel",
        secondary=task_dependencies,
        primaryjoin=task_id == task_dependencies.c.task_id,
        secondaryjoin=task_id == task_dependencies.c.depends_on_id,
        backref="blocked_tasks"
    )

class AttemptModel(Base):
    """
    @summary Represents a single execution instance of a task.
    """
    __tablename__ = "attempts"

    attempt_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid4()))
    task_id: Mapped[str] = mapped_column(String, ForeignKey("tasks.task_id"))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    outcome: Mapped[Optional[AttemptOutcome]] = mapped_column(SQLEnum(AttemptOutcome), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    artifacts: Mapped[dict] = mapped_column(JSON, default=dict)
    resolution: Mapped[Optional[AttemptResolution]] = mapped_column(SQLEnum(AttemptResolution), nullable=True)
    plan_review: Mapped[dict] = mapped_column(JSON, default=lambda: {
        "plan_health": "healthy",
        "recommendation": "continue",
        "confidence": 1.0,
        "rationale": "Initial state"
    })

    task: Mapped[TaskModel] = relationship(back_populates="attempts")

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
    temperature: Mapped[float] = mapped_column(Integer, default=0.7)  # TODO: Change column type to Float in migration
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

class ParameterModel(Base):
    """
    @summary Represents an intelligently tunable numerical or categorical parameter.
    @inputs none (ORM entity)
    @outputs none (ORM entity)
    @side_effects none
    @invariants Used for evolving orchestrator behaviors (like max deep research iterations).
    """
    __tablename__ = "parameters"
    
    key: Mapped[str] = mapped_column(String, primary_key=True)
    description: Mapped[str] = mapped_column(String)
    value: Mapped[dict] = mapped_column(JSON) # e.g. {"current": 20, "history": [6, 10, 20]}
    mutation_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class ContextLoadEventModel(Base):
    """
    @summary Append-only record of artifacts loaded into model context.
    """
    __tablename__ = "context_load_events"
    __table_args__ = (
        Index("ix_context_load_events_artifact_key", "artifact_type", "identifier"),
        Index("ix_context_load_events_artifact_loaded", "artifact_type", "identifier", "id"),
        Index("ix_context_load_events_estimated_tokens", "estimated_tokens"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    artifact_type: Mapped[str] = mapped_column(String, index=True)
    identifier: Mapped[str] = mapped_column(String, index=True)
    source: Mapped[str] = mapped_column(String, index=True)
    estimated_tokens: Mapped[int] = mapped_column(Integer, default=0)
    event_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

class MessageModel(Base):
    """
    @summary Represents a chat message in the Orchestrator Chat log.
    @inputs none (ORM entity)
    @outputs none (ORM entity)
    @side_effects none
    @invariants role must be 'user', 'assistant', or 'system'.
    """
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_session_archived_created", "session_id", "is_archived", "created_at"),
        Index("ix_messages_session_role_archived_created", "session_id", "role", "is_archived", "created_at"),
    )
    
    message_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(String, default="default", index=True)
    role: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(String)
    is_intervention: Mapped[bool] = mapped_column(Boolean, default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    associated_task_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class ModelTelemetry(Base):
    """
    @summary Persistent scoreboard of model performance by task type.
    """
    __tablename__ = "model_telemetry"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_id: Mapped[str] = mapped_column(String)
    task_type: Mapped[str] = mapped_column(String)
    score: Mapped[float] = mapped_column(Float) # 0-10.0
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class ProviderTelemetrySnapshotModel(Base):
    """
    @summary Durable snapshot of in-memory provider transport telemetry.
    """
    __tablename__ = "provider_telemetry_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

class AttemptObservabilityArtifactModel(Base):
    """
    @summary Queryable sidecar artifacts for attempt autopsies and reviews.
    """
    __tablename__ = "attempt_observability_artifacts"
    __table_args__ = (
        Index("ix_attempt_observability_task_attempt", "task_id", "attempt_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String, index=True)
    attempt_id: Mapped[str] = mapped_column(String, index=True)
    session_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    artifact_kind: Mapped[str] = mapped_column(String, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

class FeedbackSignalModel(Base):
    """
    @summary Append-only durable feedback and attention signals.
    """
    __tablename__ = "feedback_signals"
    __table_args__ = (
        Index("ix_feedback_signals_session_created", "session_id", "created_at"),
        Index("ix_feedback_signals_source_created", "source_type", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    source_type: Mapped[str] = mapped_column(String, index=True)
    source_id: Mapped[str] = mapped_column(String, index=True)
    signal_kind: Mapped[str] = mapped_column(String, index=True)
    signal_value: Mapped[str] = mapped_column(String)
    source_actor: Mapped[str] = mapped_column(String, default="system")
    session_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    source_preview: Mapped[str] = mapped_column(String, default="")
    note: Mapped[str] = mapped_column(String, default="")
    expected_outcome: Mapped[str] = mapped_column(String, default="")
    observed_outcome: Mapped[str] = mapped_column(String, default="")
    signal_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    prioritization: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String, default="logged", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

class ToolExecutionEventModel(Base):
    """
    @summary Append-only record of tool executions and derived health signals.
    """
    __tablename__ = "tool_execution_events"
    __table_args__ = (
        Index("ix_tool_execution_tool_created", "tool_name", "created_at"),
        Index("ix_tool_execution_scope_created", "tool_name", "lane", "task_type", "created_at"),
        Index("ix_tool_execution_outcome_created", "outcome", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tool_name: Mapped[str] = mapped_column(String, index=True)
    outcome: Mapped[str] = mapped_column(String, index=True)  # success, degraded, broken, blocked
    lane: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    task_type: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    task_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    session_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    failure_kind: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

class MetricModel(Base):
    """
    @summary Structured measurement record for optimization loops.
    """
    __tablename__ = "metrics"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    metric_name: Mapped[str] = mapped_column(String) # e.g. "valid_candidate_rate", "retry_depth"
    value: Mapped[float] = mapped_column(Float)
    model_id: Mapped[Optional[str]] = mapped_column(String, nullable=True) # If model-specific
    task_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    # New Experiment Attribution Fields
    run_mode: Mapped[Optional[str]] = mapped_column(String, nullable=True) # normal, weak_eval, strong_supervisor
    execution_context: Mapped[Optional[str]] = mapped_column(String, nullable=True) # strong/weak
    candidate_change_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True) # Extra info like task_id or specific error
