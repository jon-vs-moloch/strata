from strata.orchestrator.research import _build_research_system_prompt


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
