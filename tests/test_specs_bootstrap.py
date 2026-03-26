from __future__ import annotations

from pathlib import Path

from strata.specs import bootstrap


def test_ensure_spec_files_creates_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "SPECS_DIR", tmp_path / ".knowledge" / "specs")
    monkeypatch.setattr(bootstrap, "GLOBAL_SPEC_PATH", bootstrap.SPECS_DIR / "global_spec.md")
    monkeypatch.setattr(bootstrap, "PROJECT_SPEC_PATH", bootstrap.SPECS_DIR / "project_spec.md")

    result = bootstrap.ensure_spec_files()

    assert Path(result["global_spec_path"]).exists()
    assert Path(result["project_spec_path"]).exists()
    assert "Global Spec" in Path(result["global_spec_path"]).read_text(encoding="utf-8")
    assert "Project Spec" in Path(result["project_spec_path"]).read_text(encoding="utf-8")


class DummyParameterRepo:
    def __init__(self):
        self.values = {}

    def peek_parameter(self, key, default_value=None):
        return self.values.get(key, default_value)

    def set_parameter(self, key, value, description=""):
        self.values[key] = value


class DummyStorage:
    def __init__(self):
        self.parameters = DummyParameterRepo()
        self.messages = DummyMessageRepo()


class DummyMessage:
    def __init__(self, message_id, role, content, created_at):
        self.message_id = message_id
        self.role = role
        self.content = content
        self.created_at = created_at


class DummyMessageRepo:
    def __init__(self):
        from datetime import datetime, timezone
        self.items = [
            DummyMessage("m1", "user", "I want durable measurable outcomes.", datetime.now(timezone.utc)),
            DummyMessage("m2", "assistant", "That sounds like spec material.", datetime.now(timezone.utc)),
        ]

    def get_all(self, session_id=None):
        return list(self.items)


def test_spec_proposal_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "SPECS_DIR", tmp_path / ".knowledge" / "specs")
    monkeypatch.setattr(bootstrap, "GLOBAL_SPEC_PATH", bootstrap.SPECS_DIR / "global_spec.md")
    monkeypatch.setattr(bootstrap, "PROJECT_SPEC_PATH", bootstrap.SPECS_DIR / "project_spec.md")

    storage = DummyStorage()
    bootstrap.ensure_spec_files()

    proposal = bootstrap.create_spec_proposal(
        storage,
        scope="project",
        proposed_change="Add a section that says eval outcomes should become product-facing metrics.",
        rationale="The user wants measurable outcomes exposed clearly.",
        user_signal="we should measure this",
        session_id="demo",
        source="test",
    )
    assert proposal["status"] == "pending_review"

    listed = bootstrap.list_spec_proposals(storage)
    assert listed[0]["proposal_id"] == proposal["proposal_id"]

    resolved = bootstrap.resolve_spec_proposal(
        storage,
        proposal_id=proposal["proposal_id"],
        resolution="approved",
        reviewer_notes="Looks consistent with current direction.",
        reviewer="test",
    )
    assert resolved["status"] == "approved"
    assert resolved["applied_at"] is not None
    assert proposal["attribution"]["message_citations"][0]["message_id"] == "m1"
    assert proposal["proposal_id"] in bootstrap.PROJECT_SPEC_PATH.read_text(encoding="utf-8")
    assert "User Message Citations" in bootstrap.PROJECT_SPEC_PATH.read_text(encoding="utf-8")


def test_spec_proposal_resubmission_clears_clarification(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "SPECS_DIR", tmp_path / ".knowledge" / "specs")
    monkeypatch.setattr(bootstrap, "GLOBAL_SPEC_PATH", bootstrap.SPECS_DIR / "global_spec.md")
    monkeypatch.setattr(bootstrap, "PROJECT_SPEC_PATH", bootstrap.SPECS_DIR / "project_spec.md")

    storage = DummyStorage()
    bootstrap.ensure_spec_files()
    proposal = bootstrap.create_spec_proposal(
        storage,
        scope="project",
        proposed_change="Add a new persistent objective.",
        rationale="The user clarified a durable target.",
        user_signal="we should do x",
    )
    bootstrap.resolve_spec_proposal(
        storage,
        proposal_id=proposal["proposal_id"],
        resolution="needs_clarification",
        clarification_request="Can you be more specific about the scope?",
    )
    updated = bootstrap.resubmit_spec_proposal_with_clarification(
        storage,
        proposal_id=proposal["proposal_id"],
        clarification_response="Yes, this applies only to the current repo bootstrap loop.",
    )
    assert updated["status"] == "pending_review"
    assert updated["clarification_request"] == ""
    assert "Clarification Response" in updated["user_signal"]


def test_spec_proposal_index_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "SPECS_DIR", tmp_path / ".knowledge" / "specs")
    monkeypatch.setattr(bootstrap, "GLOBAL_SPEC_PATH", bootstrap.SPECS_DIR / "global_spec.md")
    monkeypatch.setattr(bootstrap, "PROJECT_SPEC_PATH", bootstrap.SPECS_DIR / "project_spec.md")
    monkeypatch.setattr(bootstrap, "MAX_TERMINAL_SPEC_PROPOSALS", 3)

    storage = DummyStorage()
    bootstrap.ensure_spec_files()
    for idx in range(5):
        proposal = bootstrap.create_spec_proposal(
            storage,
            scope="project",
            proposed_change=f"change {idx}",
            rationale="test",
        )
        bootstrap.resolve_spec_proposal(
            storage,
            proposal_id=proposal["proposal_id"],
            resolution="rejected",
            reviewer_notes="test",
        )

    listed = bootstrap.list_spec_proposals(storage, limit=10)
    assert len(listed) == 3
