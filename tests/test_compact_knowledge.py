from __future__ import annotations

from pathlib import Path

from scripts import compact_knowledge


class DummyParameterRepo:
    def __init__(self):
        self.values = {}

    def peek_parameter(self, key, default_value=None):
        return self.values.get(key, default_value)

    def set_parameter(self, key, value, description=""):
        self.values[key] = value


class DummyTaskRepo:
    def create(self, **kwargs):
        return None


class DummyStorage:
    def __init__(self):
        self.parameters = DummyParameterRepo()
        self.tasks = DummyTaskRepo()

    def commit(self):
        return None

    def close(self):
        return None


def test_compaction_materializes_pages_and_provenance(tmp_path, monkeypatch):
    root = tmp_path
    (root / ".knowledge" / "specs").mkdir(parents=True)
    (root / "docs" / "spec" / "kb").mkdir(parents=True)

    (root / ".knowledge" / "specs" / "constitution.md").write_text(
        "# Constitution\n\nDurable rules for Strata.\n",
        encoding="utf-8",
    )
    (root / ".knowledge" / "specs" / "project_spec.md").write_text(
        "# Project Spec\n\nProject goals live here.\n",
        encoding="utf-8",
    )
    (root / ".knowledge" / "model_performance_intel.md").write_text(
        "# Model Performance Intel\n\nWeak accuracy is improving.\n",
        encoding="utf-8",
    )
    (root / "docs" / "spec" / "project-philosophy.md").write_text(
        "# Strata Project Philosophy\n\nPush rigor into the system.\n",
        encoding="utf-8",
    )
    (root / "docs" / "spec" / "codemap.md").write_text(
        "# Codemap\n\nWhere everything lives.\n",
        encoding="utf-8",
    )
    (root / "docs" / "spec" / "eval-brief.md").write_text(
        "# Eval Brief\n\nHow we measure progress.\n",
        encoding="utf-8",
    )
    (root / "docs" / "spec" / "eval-catalog.md").write_text(
        "# Eval Catalog\n\nAvailable evals.\n",
        encoding="utf-8",
    )
    (root / "docs" / "spec" / "task-attempt-ontology.md").write_text(
        "# Task Attempt Ontology\n\nTask schema notes.\n",
        encoding="utf-8",
    )
    (root / "docs" / "spec" / "ui-operator-audit.md").write_text(
        "# UI Operator Audit\n\nOperator gaps.\n",
        encoding="utf-8",
    )

    storage = DummyStorage()

    monkeypatch.setattr(compact_knowledge, "ROOT", root)
    monkeypatch.setattr(compact_knowledge, "KNOWLEDGE_DIR", root / ".knowledge")
    monkeypatch.setattr(compact_knowledge, "SPEC_DIR", root / "docs" / "spec")
    monkeypatch.setattr(compact_knowledge, "CURRENT_KB_PATH", root / "docs" / "spec" / "current_knowledge_base.md")
    monkeypatch.setattr(compact_knowledge, "PROVENANCE_PATH", root / ".knowledge" / "provenance_index.json")
    monkeypatch.setattr(compact_knowledge, "ARCHIVE_DIR", root / ".knowledge" / "archive")
    monkeypatch.setattr(compact_knowledge, "StorageManager", lambda: storage)

    snapshot = compact_knowledge.build_knowledge_snapshot()

    index = storage.parameters.values["knowledge_pages:index"]
    slugs = {row["slug"] for row in index}

    assert snapshot["page_count"] >= 5
    assert "constitution" in slugs
    assert "project-spec" in slugs
    assert "current-knowledge-base" in slugs
    assert (root / "docs" / "spec" / "current_knowledge_base.md").exists()
    assert (root / ".knowledge" / "provenance_index.json").exists()

    constitution = storage.parameters.values["knowledge_page:constitution"]
    assert constitution["provenance"][0]["path"] == ".knowledge/specs/constitution.md"

    current_kb = storage.parameters.values["knowledge_page:current-knowledge-base"]
    assert current_kb["source_count"] >= 3
    assert "stable wiki" in current_kb["body"]
