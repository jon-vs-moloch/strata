import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from strata.experimental.verifier import (
    emit_verifier_attention_signal,
    repo_fact_contradictions,
    select_verification_policy,
    verify_artifact,
    verify_task_output,
)
from strata.schemas.execution import AgentExecutionContext, TrainerExecutionContext
from strata.storage.models import AttemptOutcome, Base, TaskState, TaskType
from strata.storage.services.main import StorageManager


def make_storage():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    return StorageManager(session=session)


class FailingIfCalledModelAdapter:
    def __init__(self):
        self.calls = 0

    async def chat(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("deterministic verifier path should not call the model")


class JsonModelAdapter:
    def __init__(self, content):
        self.content = content
        self.calls = 0

    async def chat(self, *_args, **_kwargs):
        self.calls += 1
        return {"status": "success", "content": self.content}


def test_verification_policy_anneals_from_observed_error_rate():
    storage = make_storage()
    for index in range(10):
        agent_task = storage.tasks.create(
            title=f"Agent task {index}",
            description="agent output",
            session_id=f"agent:session-{index}",
            state=TaskState.COMPLETE,
            type=TaskType.RESEARCH,
            constraints={"lane": "agent"},
        )
        agent_attempt = storage.attempts.create(task_id=agent_task.task_id)
        agent_attempt.outcome = AttemptOutcome.SUCCEEDED

        trainer_task = storage.tasks.create(
            title=f"Trainer task {index}",
            description="trainer output",
            session_id=f"trainer:session-{index}",
            state=TaskState.COMPLETE,
            type=TaskType.RESEARCH,
            constraints={"lane": "trainer"},
        )
        trainer_attempt = storage.attempts.create(task_id=trainer_task.task_id)
        trainer_attempt.outcome = AttemptOutcome.FAILED if index < 3 else AttemptOutcome.SUCCEEDED
    storage.commit()

    agent_policy = select_verification_policy(storage, mode="agent")
    trainer_policy = select_verification_policy(storage, mode="trainer")

    assert agent_policy["recent_error_rate"] < trainer_policy["recent_error_rate"]
    assert agent_policy["cadence"] >= trainer_policy["cadence"]


def test_verify_task_output_uses_deterministic_contradiction_without_model_call():
    storage = make_storage()
    task = storage.tasks.create(
        title="Alignment task",
        description="Investigate whether canonical spec files are missing.",
        session_id="agent:default",
        state=TaskState.BLOCKED,
        type=TaskType.RESEARCH,
        constraints={
            "lane": "agent",
            "spec_paths": [".knowledge/specs/constitution.md", ".knowledge/specs/project_spec.md"],
        },
    )
    attempt = storage.attempts.create(task_id=task.task_id)
    attempt.outcome = AttemptOutcome.FAILED
    attempt.reason = "Agent iteration limit reached. Partial context saved to durable `.knowledge` library at: ./.knowledge/wip_research_20260329_094324.md"
    storage.commit()

    adapter = FailingIfCalledModelAdapter()
    artifact = asyncio.run(
        verify_task_output(
            storage,
            task=task,
            attempt=attempt,
            model_adapter=adapter,
            context=AgentExecutionContext(run_id="verify-demo"),
        )
    )

    storage.session.expire_all()
    reloaded_attempt = storage.attempts.get_by_id(attempt.attempt_id)
    reloaded_task = storage.tasks.get_by_id(task.task_id)

    assert adapter.calls == 0
    assert artifact["verification_kind"] == "deterministic"
    assert artifact["verdict"] == "flawed"
    assert ".knowledge/specs/project_spec.md" in artifact["deterministic_contradictions"]
    assert reloaded_attempt.artifacts["verifier"]["verdict"] == "flawed"
    assert reloaded_task.constraints["verifier_reviews"][0]["verification_kind"] == "deterministic"


def test_repo_fact_contradictions_can_be_used_before_attempt_execution():
    contradictions = repo_fact_contradictions(
        text_fragments=[
            "The `.knowledge/` directory and `.knowledge/specs/project_spec.md` do not exist in the repository."
        ],
        repo_fact_checks=[
            {"path": ".knowledge/specs/constitution.md", "exists": True, "is_file": True},
            {"path": ".knowledge/specs/project_spec.md", "exists": True, "is_file": True},
        ],
    )

    assert ".knowledge/specs/project_spec.md" in contradictions


def test_verify_artifact_supports_general_step_level_verification():
    storage = make_storage()
    artifact = asyncio.run(
        verify_artifact(
            storage,
            mode="agent",
            model_adapter=FailingIfCalledModelAdapter(),
            artifact_kind="task_creation",
            text_fragments=[
                "The `.knowledge/specs/project_spec.md` file does not exist in the repository."
            ],
            repo_fact_checks=[
                {"path": ".knowledge/specs/project_spec.md", "exists": True, "is_file": True},
            ],
            metadata={"phase": "idle_generation"},
        )
    )

    assert artifact["artifact_kind"] == "task_creation"
    assert artifact["verification_kind"] == "deterministic"
    assert artifact["verdict"] == "flawed"


def test_verify_task_output_can_use_model_judgment_when_no_contradiction_exists():
    storage = make_storage()
    task = storage.tasks.create(
        title="Trainer summary",
        description="Summarize the latest repo state carefully.",
        session_id="trainer:default",
        state=TaskState.COMPLETE,
        type=TaskType.RESEARCH,
        constraints={"lane": "trainer"},
    )
    attempt = storage.attempts.create(task_id=task.task_id)
    attempt.outcome = AttemptOutcome.SUCCEEDED
    storage.commit()

    adapter = JsonModelAdapter(
        '{"verdict":"uncertain","confidence":0.61,"reasons":["The output is underspecified."],'
        '"failure_modes":["incomplete"],"recommended_action":"verify_more",'
        '"checks_run":["summary review"],"claims_examined":["repo state summary"],"residual_risk":["may omit key details"]}'
    )
    artifact = asyncio.run(
        verify_task_output(
            storage,
            task=task,
            attempt=attempt,
            model_adapter=adapter,
            context=TrainerExecutionContext(run_id="verify-demo"),
        )
    )

    signal = emit_verifier_attention_signal(storage, task=task, verification=artifact)

    assert adapter.calls == 1
    assert artifact["verification_kind"] == "model"
    assert artifact["verdict"] == "uncertain"
    assert signal is not None
