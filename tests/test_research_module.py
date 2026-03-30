from strata.orchestrator.research import (
    DEFAULT_RESEARCH_ITERATION_POLICY,
    _build_iteration_limit_autopsy,
    _build_research_system_prompt,
    _should_return_raw_file,
    load_research_iteration_policy,
)


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
