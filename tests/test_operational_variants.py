import asyncio
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from strata.experimental.variants import (
    build_stage_scope,
    classify_pool_pruning,
    ensure_variant,
    get_variant_rating_snapshot,
    list_variants_for_scope,
)
from strata.orchestrator.implementation import ImplementationModule
from strata.orchestrator.judge import JudgeModule
from strata.orchestrator.synthesis import SynthesisModule
from strata.storage.models import Base, CandidateModel, TaskType
from strata.storage.services.main import StorageManager


def make_storage():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    return StorageManager(session=session)


class FakeResearchModule:
    async def conduct_research(self, *args, **kwargs):
        return SimpleNamespace(context_gathered="local context", key_constraints_discovered=["constraint"])


class FakeImplementationModel:
    def __init__(self):
        self.calls = []
        self.responses = [
            {"content": "```python\nprint('candidate_a')\n```", "provider": "test", "model": "model-a"},
            {"content": "```python\nprint('candidate_b')\n```", "provider": "test", "model": "model-b"},
        ]

    async def chat(self, messages, tools=None):
        self.calls.append(messages)
        return self.responses[len(self.calls) - 1]


class FakeSynthesisModel:
    def __init__(self):
        self.calls = 0

    async def chat(self, messages):
        self.calls += 1
        if self.calls == 1:
            return {"content": "merged output a"}
        if self.calls == 2:
            return {"content": "merged output b"}
        return {"content": "unparseable preference"}


def test_scope_resolution_prefers_exact_then_generic():
    storage = make_storage()
    exact_scope = build_stage_scope(component="synthesis", process="subtasks", step="default")
    ensure_variant(
        storage,
        kind="prompt_bundle",
        payload={"instruction_suffix": "generic"},
        family="synthesis_prompt",
        label="generic",
        metadata={"stage_scope": "synthesis.generic.default"},
    )
    ensure_variant(
        storage,
        kind="prompt_bundle",
        payload={"instruction_suffix": "exact"},
        family="synthesis_prompt",
        label="exact",
        metadata={"stage_scope": exact_scope},
    )

    variants = list_variants_for_scope(
        storage,
        family="synthesis_prompt",
        stage_scope=exact_scope,
        domain=f"ops:{exact_scope}",
    )

    assert variants[0]["scope_match"] == "exact"
    assert variants[1]["scope_match"] == "generic"


def test_pool_pruning_waits_for_larger_pass_pool():
    storage = make_storage()
    storage.parameters.set_parameter(
        "variant_registry_operational_policy",
        {
            "min_pool_size_for_pruning": 5,
            "drop_bottom_count": 1,
            "keep_top_k": 3,
        },
    )

    assert classify_pool_pruning(storage, pool_size=3)["drop_count"] == 0
    assert classify_pool_pruning(storage, pool_size=5)["drop_count"] == 1


def test_implementation_module_generates_candidates_for_scoped_variants():
    storage = make_storage()
    stage_scope = build_stage_scope(component="implementation", process="impl", step="default")
    variant_a = ensure_variant(
        storage,
        kind="prompt_bundle",
        payload={"instruction_suffix": "favor minimal edits"},
        family="implementation_prompt",
        label="variant-a",
        metadata={"stage_scope": stage_scope},
    )
    variant_b = ensure_variant(
        storage,
        kind="prompt_bundle",
        payload={"instruction_suffix": "favor explicit comments"},
        family="implementation_prompt",
        label="variant-b",
        metadata={"stage_scope": stage_scope},
    )
    task = storage.tasks.create(
        title="Implement feature",
        description="Do the thing.",
        constraints={"pass_at": 2, "target_files": ["example.py"]},
    )
    task.type = TaskType.IMPL
    storage.commit()

    model = FakeImplementationModel()
    module = ImplementationModule(model_adapter=model, storage_manager=storage, research_module=FakeResearchModule())
    candidate_ids = asyncio.run(module.implement_task(task.task_id))

    assert len(candidate_ids) == 2
    candidates = [storage.session.get(CandidateModel, candidate_id) for candidate_id in candidate_ids]
    assert {candidate.prompt_version for candidate in candidates} == {variant_a["variant_id"], variant_b["variant_id"]}
    refreshed = storage.tasks.get_by_id(task.task_id)
    assert refreshed.constraints["candidate_generation"]["generated_count"] == 2


def test_judge_records_ranked_variant_matchups_for_normal_ops(monkeypatch):
    storage = make_storage()
    task = storage.tasks.create(title="Judge task", description="Rank candidates.")
    task.type = TaskType.IMPL
    storage.commit()
    candidate_a = CandidateModel(
        candidate_id="cand-a",
        task_id=task.task_id,
        stage="impl",
        prompt_version="implementation_prompt.variant_a",
        model="test/model",
        artifact_type="python_file",
        content_path="tests/fixtures/cand_a.py",
        summary="a",
        proposed_files=[],
    )
    candidate_b = CandidateModel(
        candidate_id="cand-b",
        task_id=task.task_id,
        stage="impl",
        prompt_version="implementation_prompt.variant_b",
        model="test/model",
        artifact_type="python_file",
        content_path="tests/fixtures/cand_b.py",
        summary="b",
        proposed_files=[],
    )
    storage.session.add(candidate_a)
    storage.session.add(candidate_b)
    storage.commit()

    judge = JudgeModule(model_adapter=None, storage_manager=storage)

    async def fake_eval(_task, candidate):
        score = 9.0 if candidate.candidate_id == "cand-a" else 7.0
        return SimpleNamespace(
            score=score,
            valid=True,
            reasoning="ok",
            checks_passed=["pass"],
            checks_failed=[],
            diff_summary="diff",
        )

    monkeypatch.setattr(judge.evaluator, "evaluate_candidate", fake_eval)
    rankings = asyncio.run(judge.judge_candidates(task.task_id, ["cand-a", "cand-b"]))

    assert rankings[0]["candidate_id"] == "cand-a"
    refreshed = storage.tasks.get_by_id(task.task_id)
    assert refreshed.constraints["candidate_ranking"]["matchup_count"] == 1
    snapshot = get_variant_rating_snapshot(storage)
    assert snapshot["recent_matchups"][-1]["domain"] == "ops:implementation:impl"


def test_synthesis_variants_are_ranked_and_recorded():
    storage = make_storage()
    stage_scope = build_stage_scope(component="synthesis", process="subtasks", step="default")
    ensure_variant(
        storage,
        kind="prompt_bundle",
        payload={"instruction_suffix": "optimize for minimal conflicts"},
        family="synthesis_prompt",
        label="variant-a",
        metadata={"stage_scope": stage_scope},
    )
    ensure_variant(
        storage,
        kind="prompt_bundle",
        payload={"instruction_suffix": "optimize for readability"},
        family="synthesis_prompt",
        label="variant-b",
        metadata={"stage_scope": stage_scope},
    )

    module = SynthesisModule(model_adapter=FakeSynthesisModel(), storage_manager=storage)
    result = asyncio.run(module.synthesize_subtasks("parent-task", {"child-a": "patch a", "child-b": "patch b"}))

    assert result == "merged output a"
    snapshot = get_variant_rating_snapshot(storage)
    assert snapshot["recent_matchups"][-1]["domain"] == f"ops:{stage_scope}"
