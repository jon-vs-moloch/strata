"""Tests for the IMPL validator gate and implementation research handoff.

No network, no model calls: storage is in-memory sqlite and heavy collaborators
are monkeypatched away.
"""

import asyncio
import pytest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from strata.orchestrator.evaluation import extract_python_source, ValidatorRegistry
from strata.orchestrator.implementation import ImplementationModule
from strata.orchestrator.research import ResearchModule
from strata.orchestrator.step_outcomes import TerminalToolCallOutcome
from strata.orchestrator.worker import attempt_runner
from strata.orchestrator.worker.attempt_runner import (
    ImplementationValidationError,
    _run_implementation,
)
from strata.storage.models import Base, CandidateModel, TaskType
from strata.storage.services.main import StorageManager


TASK_DESCRIPTION = "Create workspace/mod.py with an add() helper so the declared test passes."


def make_storage():
    """In-memory sqlite StorageManager, matching tests/test_eval_jobs.py."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    return StorageManager(session=session)


def make_impl_task(storage, constraints):
    task = storage.tasks.create(
        title="Implement add helper",
        description=TASK_DESCRIPTION,
        session_id="agent:test-session",
        constraints=dict(constraints),
        flush=True,
    )
    task.type = TaskType.IMPL
    storage.commit()
    return task


def add_candidate(storage, task, content):
    candidate_id = f"cand-{storage.session.query(CandidateModel).count()}"
    candidate = CandidateModel(
        candidate_id=candidate_id,
        task_id=task.task_id,
        stage="impl",
        prompt_version="v1",
        model="test/model",
        artifact_type="python_file",
        content_path=f"strata/experimental/candidates/{candidate_id}.py",
        summary=f"Implementation for {task.title}",
        proposed_files=list(task.constraints.get("target_files") or []),
    )
    storage.session.add(candidate)
    storage.commit()
    path = Path(candidate.content_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return candidate_id


# --- extract_python_source ---------------------------------------------------


def test_extract_python_source_prefers_fenced_python_block():
    content = (
        "Here is the fix:\n"
        "```python\n"
        "def add(a, b):\n"
        "    return a + b\n"
        "```\n"
        "Prose trailing the block."
    )
    assert extract_python_source(content).strip() == "def add(a, b):\n    return a + b"


def test_extract_python_source_plain_fence():
    content = "Note:\n```\nX = 1\n```\nTrailing prose."
    assert extract_python_source(content).strip() == "X = 1"


def test_extract_python_source_no_fence_returns_content():
    content = "just plain text, not fenced"
    assert extract_python_source(content) == content


# --- ValidatorRegistry._run_pytest -------------------------------------------


@pytest.fixture()
def pytest_project(tmp_path, monkeypatch):
    """Temporary project dir with workspace/mod.py and tests/test_mod.py."""
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "mod.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_mod.py").write_text(
        "import importlib.util\n"
        "from pathlib import Path\n"
        "\n"
        "_mod_path = Path(__file__).resolve().parent.parent / 'workspace' / 'mod.py'\n"
        "_spec = importlib.util.spec_from_file_location('mod', _mod_path)\n"
        "mod = importlib.util.module_from_spec(_spec)\n"
        "_spec.loader.exec_module(mod)\n"
        "\n"
        "def test_add():\n"
        "    assert mod.add(2, 3) == 5\n"
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _pytest_task_constraints():
    return {
        "target_files": ["workspace/mod.py"],
        "test_file": "tests/test_mod.py",
        "validator": "python_pytest",
        "edit_type": "create",
    }


def test_run_pytest_passes_fenced_candidate(pytest_project):
    storage = make_storage()
    task = make_impl_task(storage, _pytest_task_constraints())
    registry = ValidatorRegistry(storage)
    # Candidate is prose plus a ```python fence, exactly what the impl prompt asks for.
    content = (
        "Here is the implementation:\n"
        "```python\n"
        "def add(a, b):\n"
        "    return a + b\n"
        "```\n"
        "Trailing prose."
    )
    result = registry.run("python_pytest", task, content)
    assert result.success is True


def test_run_pytest_fails_wrong_candidate(pytest_project):
    storage = make_storage()
    task = make_impl_task(storage, _pytest_task_constraints())
    registry = ValidatorRegistry(storage)
    content = (
        "Here is the implementation:\n"
        "```python\n"
        "def add(a, b):\n"
        "    return a - b\n"
        "```\n"
        "Trailing prose."
    )
    result = registry.run("python_pytest", task, content)
    assert result.success is False


# --- _run_implementation validator gate --------------------------------------


class _StubModelAdapter:
    last_response = None


def test_run_implementation_raises_when_validator_rejects_candidates(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = make_storage()
    task = make_impl_task(
        storage,
        {
            "target_files": ["workspace/x.py"],
            "validator": "python_import_only",
            "edit_type": "create",
        },
    )
    candidate_id = add_candidate(storage, task, "Here is my answer: def add(a, b) return a + b")

    async def fake_implement_task(self, task_id, *args, **kwargs):
        return [candidate_id]

    monkeypatch.setattr(ImplementationModule, "implement_task", fake_implement_task)
    # Avoid real communication side effects; the test only asserts the gate raise.
    monkeypatch.setattr(
        attempt_runner,
        "_emit_task_communication",
        lambda *args, **kwargs: None,
    )

    with pytest.raises(ImplementationValidationError) as excinfo:
        asyncio.run(_run_implementation(task, storage, _StubModelAdapter()))

    message = str(excinfo.value)
    assert message.startswith("Declared validator rejected every implementation candidate:")
    # The message carries the checks_failed text of the last candidate.
    assert "Validator (python_import_only) failed" in message
    assert excinfo.value.failure_kind == "validator_failed"
    assert excinfo.value.autopsy["candidate_ids"] == [candidate_id]
    assert any(
        "Validator (python_import_only) failed" in item
        for item in excinfo.value.autopsy["checks_failed"][-1]
    )


def test_run_implementation_returns_ids_without_validator(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = make_storage()
    task = make_impl_task(storage, {"target_files": ["workspace/x.py"]})
    candidate_id = add_candidate(storage, task, "def broken():    return")  # never evaluated

    captured_calls = []

    async def fake_implement_task(self, task_id, *args, **kwargs):
        captured_calls.append(task_id)
        return [candidate_id]

    monkeypatch.setattr(ImplementationModule, "implement_task", fake_implement_task)
    monkeypatch.setattr(
        attempt_runner,
        "_emit_task_communication",
        lambda *args, **kwargs: None,
    )

    result = asyncio.run(_run_implementation(task, storage, _StubModelAdapter()))
    assert result == [candidate_id]
    assert captured_calls == [task.task_id]


def test_run_implementation_passes_when_valid_candidate_exists(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = make_storage()
    task = make_impl_task(
        storage,
        {
            "target_files": ["workspace/x.py"],
            "validator": "python_import_only",
            "edit_type": "create",
        },
    )
    bad_id = add_candidate(storage, task, "JUNK prose not python")
    good_id = add_candidate(storage, task, "x = valid_python = 1")

    async def fake_implement_task(self, task_id, *args, **kwargs):
        return [bad_id, good_id]

    monkeypatch.setattr(ImplementationModule, "implement_task", fake_implement_task)
    monkeypatch.setattr(
        attempt_runner,
        "_emit_task_communication",
        lambda *args, **kwargs: None,
    )

    assert asyncio.run(_run_implementation(task, storage, _StubModelAdapter())) == [bad_id, good_id]


# --- implement_task research handoff -----------------------------------------


def test_implement_task_repoints_research_handoff_to_impl(monkeypatch):
    storage = make_storage()
    task = make_impl_task(
        storage,
        {"target_files": ["workspace/x.py"], "edit_type": "create"},
    )

    async def fake_conduct_research(self, **kwargs):
        return TerminalToolCallOutcome(
            tool_name="read_file",
            tool_result_preview="some file contents",
            next_step_hint="consume the result",
            source_module="research",
            continuation_task_type="RESEARCH",
            continuation_title="Do more research",
            continuation_description="Generic research follow-up that would never come back.",
        )

    module = ImplementationModule(
        model_adapter=_StubModelAdapter(),
        storage_manager=storage,
        research_module=ResearchModule(_StubModelAdapter(), storage),
    )
    monkeypatch.setattr(ResearchModule, "conduct_research", fake_conduct_research)

    outcome = asyncio.run(
        module.implement_task(task.task_id)
    )
    assert isinstance(outcome, TerminalToolCallOutcome)
    assert outcome.continuation_task_type == "IMPL"
    assert outcome.continuation_title == f"Continue implementation: {task.title}"[:200]
    assert outcome.continuation_description == TASK_DESCRIPTION
    # The rest of the outcome is untouched.
    assert outcome.tool_name == "read_file"
    assert outcome.tool_result_preview == "some file contents"


def test_run_pytest_sandbox_skips_venv_and_runtime_dirs(pytest_project, monkeypatch):
    # The sandbox copy must not drag venv/, .git/ or runtime state along on every run.
    import shutil
    seen = {}
    real_copytree = shutil.copytree

    def recording_copytree(src, dst, *args, **kwargs):
        # Record the top-level call only, then restore the real function, because
        # shutil recurses through the module attribute with positional arguments.
        monkeypatch.setattr(shutil, "copytree", real_copytree)
        ignore = kwargs["ignore"]
        seen["root"] = ignore(src, ["venv", ".venv", ".git", "strata", "memory", "workspace", "tests"])
        seen["strata"] = ignore(f"{src}/strata", ["runtime", "experimental", "orchestrator"])
        seen["experimental"] = ignore(f"{src}/strata/experimental", ["candidates", "verifier.py"])
        seen["memory"] = ignore(f"{src}/memory", ["vector_db"])
        return real_copytree(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "copytree", recording_copytree)
    storage = make_storage()
    task = make_impl_task(storage, _pytest_task_constraints())
    ValidatorRegistry(storage).run("python_pytest", task, "```python\ndef add(a, b):\n    return a + b\n```")
    assert seen["root"] == {"venv", ".venv", ".git"}
    assert seen["strata"] == {"runtime"}
    assert seen["experimental"] == {"candidates"}
    assert seen["memory"] == {"vector_db"}
