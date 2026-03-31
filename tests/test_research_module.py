from strata.orchestrator.research import (
    DEFAULT_RESEARCH_ITERATION_POLICY,
    TaskBoundaryViolationError,
    _build_iteration_limit_autopsy,
    _build_research_system_prompt,
    _should_return_raw_file,
    load_research_iteration_policy,
    ResearchModule,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from strata.storage.models import Base
from strata.storage.services.main import StorageManager


def test_research_prompt_includes_local_exploration_guidance_for_codebase_tasks():
    prompt = _build_research_system_prompt(
        target_scope="codebase",
        task_description="Identify a spec alignment gap in the repository implementation.",
        repo_snapshot="DIR strata/api: main.py, chat_runtime.py",
        spec_paths=[".knowledge/specs/global_spec.md", ".knowledge/specs/project_spec.md"],
    )

    assert "list_directory" in prompt
    assert "read_file" in prompt
    assert "Do not say you need \"access to the codebase\"" in prompt
    assert "Observed repository snapshot" in prompt
    assert ".knowledge/specs/project_spec.md" in prompt


def test_research_prompt_omits_codebase_nudge_for_non_codebase_scope():
    prompt = _build_research_system_prompt(
        target_scope="world",
        task_description="Research seahorse habitats and summarize recent findings.",
        repo_snapshot="",
        spec_paths=[],
    )

    assert "[CODEBASE-FIRST BEHAVIOR]" not in prompt


def test_research_prompt_includes_handoff_context_and_avoid_repeat_guidance():
    prompt = _build_research_system_prompt(
        target_scope="codebase",
        task_description="Confirm the core spec files are present.",
        preferred_start_paths=[".knowledge/specs/constitution.md"],
        handoff_context={
            "tool_call": {"name": "list_directory", "arguments": '{"path":"."}'},
            "tool_result_full": "README.md\n.knowledge/\nstrata/\ndocs/",
            "tool_result_preview": "README.md\nstrata/\n.knowledge/",
            "next_step_hint": "Read the canonical spec files directly.",
            "avoid_repeating_first_tool": {"name": "list_directory"},
        },
    )

    assert "Prior handoff evidence from the parent step" in prompt
    assert "Prior tool call already executed: list_directory" in prompt
    assert "Prior full tool result:\nREADME.md\n.knowledge/\nstrata/\ndocs/" in prompt
    assert "Prior next-step hint: Read the canonical spec files directly." in prompt
    assert "Do not repeat list_directory as your first move" in prompt


def test_canonical_spec_reads_bypass_progressive_summary_cache():
    assert _should_return_raw_file(
        filepath=".knowledge/specs/project_spec.md",
        target_scope="codebase",
        task_description="Identify an alignment gap in the repository.",
        spec_paths=[".knowledge/specs/constitution.md", ".knowledge/specs/project_spec.md"],
    )

    assert not _should_return_raw_file(
        filepath="docs/spec/codemap.md",
        target_scope="world",
        task_description="Research public best practices for agent telemetry.",
        spec_paths=[],
    )


class _DummyParameters:
    def __init__(self, payload):
        self.payload = payload

    def get_parameter(self, key, default_value, description=""):
        return self.payload


class _DummyStorage:
    def __init__(self, payload):
        self.parameters = _DummyParameters(payload)


def test_load_research_iteration_policy_sanitizes_values():
    storage = _DummyStorage(
        {
            "max_iterations": "9",
            "warm_history_count": 0,
            "research_reattempt_limit": "bad",
            "default_reattempt_limit": 7,
            "recovery_shell_reattempt_limit": -2,
            "lineage_iteration_limit": "11",
        }
    )

    policy = load_research_iteration_policy(storage)

    assert policy["max_iterations"] == 9
    assert policy["warm_history_count"] == DEFAULT_RESEARCH_ITERATION_POLICY["warm_history_count"]
    assert policy["research_reattempt_limit"] == DEFAULT_RESEARCH_ITERATION_POLICY["research_reattempt_limit"]
    assert policy["default_reattempt_limit"] == 7
    assert policy["recovery_shell_reattempt_limit"] == DEFAULT_RESEARCH_ITERATION_POLICY["recovery_shell_reattempt_limit"]
    assert policy["lineage_iteration_limit"] == 11


def test_iteration_limit_autopsy_keeps_warm_history_and_archive_pointer():
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "function": {"name": "list_directory", "arguments": "{\"path\":\".\"}"}}]},
        {"role": "tool", "content": "README.md\nstrata/", "tool_call_id": "call_1"},
        {"role": "assistant", "content": "observed repo structure"},
        {"role": "user", "content": "continue"},
    ]

    autopsy = _build_iteration_limit_autopsy(
        task_description="Investigate the repository",
        target_scope="codebase",
        task_context={"task_id": "task-1", "title": "Error Recover"},
        policy={"max_iterations": 6, "warm_history_count": 3},
        messages=messages,
        wip_file="./.knowledge/wip_research_demo.md",
    )

    assert autopsy["failure_kind"] == "iteration_budget_exhausted"
    assert autopsy["archived_transcript"]["path"] == "./.knowledge/wip_research_demo.md"
    assert autopsy["archived_transcript"]["message_count"] == len(messages)
    assert len(autopsy["warm_history"]) == 3
    assert autopsy["warm_history"][0]["role"] == "tool"
    assert autopsy["warm_history"][-1]["role"] == "user"


def _make_runtime_storage():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    return StorageManager(session=session)


class _SingleTurnModel:
    def __init__(self, response):
        self.response = response

    async def chat(self, *_args, **_kwargs):
        return self.response


def test_research_single_tool_turn_raises_task_boundary_violation(monkeypatch):
    storage = _make_runtime_storage()
    model = _SingleTurnModel(
        {
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {"name": "list_directory", "arguments": '{"path":"."}'},
                }
            ],
        }
    )
    module = ResearchModule(model, storage)
    monkeypatch.setattr("strata.orchestrator.research.record_tool_execution", lambda *args, **kwargs: None)
    monkeypatch.setattr("strata.orchestrator.research.should_throttle_tool", lambda *args, **kwargs: {"throttle": False})

    try:
        __import__("asyncio").run(module.conduct_research("Inspect the repo root", repo_path="."))
        assert False, "expected TaskBoundaryViolationError"
    except TaskBoundaryViolationError as exc:
        assert exc.failure_kind == "task_boundary_violation"
        assert exc.autopsy["tool_call"]["name"] == "list_directory"
        assert "oneshottable" in exc.public_message.lower() or "variance-bearing" in exc.public_message.lower()
