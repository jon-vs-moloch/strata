from __future__ import annotations

from types import SimpleNamespace

from strata.knowledge import pages as knowledge_pages


class DummyParameterRepo:
    def __init__(self):
        self.values = {}

    def peek_parameter(self, key, default_value=None):
        return self.values.get(key, default_value)

    def set_parameter(self, key, value, description=""):
        self.values[key] = value


class DummyTaskRepo:
    def __init__(self):
        self.created = []

    def create(self, **kwargs):
        task = SimpleNamespace(task_id=f"task-{len(self.created) + 1}", **kwargs)
        self.created.append(task)
        return task


class DummyStorage:
    def __init__(self):
        self.parameters = DummyParameterRepo()
        self.tasks = DummyTaskRepo()

    def commit(self):
        return None


def test_upsert_and_metadata_list(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge_pages, "KNOWLEDGE_PAGE_MIRROR_DIR", tmp_path)
    storage = DummyStorage()
    store = knowledge_pages.KnowledgePageStore(storage)

    page = store.upsert_page(
        slug="seahorses",
        title="Seahorses",
        body="# Seahorses\n\nSeahorses are fish.\n\n## Reproduction\n\nMale seahorses brood young.",
        tags=["Marine", "Biology"],
        aliases=["Sea Horses"],
        related_pages=["syngnathidae"],
        provenance=[{"label": ".knowledge/seahorses_note.md"}],
        confidence=0.8,
        created_by="test",
        updated_reason="seed",
        domain="world",
        visibility_policy="shareable",
        disclosure_rules={"can_quote": True, "allowed_audiences": ["shareable", "user_visible"]},
    )

    assert page["slug"] == "seahorses"
    assert page["summary"] == "Seahorses are fish."
    assert page["toc"][0]["title"] == "Seahorses"
    assert (tmp_path / "seahorses.md").exists()

    pages = store.list_pages(query="marine")
    assert len(pages) == 1
    assert pages[0]["slug"] == "seahorses"
    assert "body" not in pages[0]

    metadata = store.get_page_metadata("seahorses")
    assert metadata["source_count"] == 1
    assert metadata["domain"] == "world"
    assert metadata["visibility_policy"] == "shareable"
    assert metadata["disclosure_rules"]["can_quote"] is True
    assert "body" not in metadata


def test_section_lookup_and_missing_page(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge_pages, "KNOWLEDGE_PAGE_MIRROR_DIR", tmp_path)
    storage = DummyStorage()
    store = knowledge_pages.KnowledgePageStore(storage)
    store.upsert_page(
        slug="seahorses",
        title="Seahorses",
        body="# Seahorses\n\nIntro.\n\n## Reproduction\n\nMale seahorses brood young.",
    )

    section = store.get_page_section("seahorses", "Reproduction")
    assert "Male seahorses brood young." in section["content"]

    assert store.get_page("missing-page") == {}
    assert store.get_page_metadata("missing-page") == {}


def test_enqueue_update_task_marks_knowledge_operation(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge_pages, "KNOWLEDGE_PAGE_MIRROR_DIR", tmp_path)
    storage = DummyStorage()
    store = knowledge_pages.KnowledgePageStore(storage)

    task = store.enqueue_update_task(
        slug="seahorses",
        reason="page is stale after new marine biology finding",
        session_id="demo",
        target_scope="web",
        evidence=["new study on brood pouch oxygenation"],
        domain="world",
    )

    assert task.title == "Update Knowledge: Seahorses"
    assert task.type.value == "RESEARCH"
    assert task.constraints["knowledge_operation"] == "update_page"
    assert task.constraints["knowledge_slug"] == "seahorses"
    assert task.constraints["knowledge_domain"] == "world"
    assert task.constraints["target_scope"] == "web"


def test_enqueue_update_task_supports_maintenance_operations(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge_pages, "KNOWLEDGE_PAGE_MIRROR_DIR", tmp_path)
    storage = DummyStorage()
    store = knowledge_pages.KnowledgePageStore(storage)

    task = store.enqueue_update_task(
        slug="constitution",
        reason="[merge] overlapping page detected",
        target_scope="codebase",
        operation="knowledge_merge",
        related_slugs=["system-constitution"],
    )

    assert task.constraints["knowledge_operation"] == "knowledge_merge"
    assert task.constraints["related_knowledge_slugs"] == ["system-constitution"]


def test_user_access_respects_visibility_and_redaction(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge_pages, "KNOWLEDGE_PAGE_MIRROR_DIR", tmp_path)
    storage = DummyStorage()
    store = knowledge_pages.KnowledgePageStore(storage)

    store.upsert_page(
        slug="internal-agent-note",
        title="Internal Agent Note",
        body="# Internal Agent Note\n\nDo not disclose this directly.",
        domain="agent",
        visibility_policy="agent_internal",
    )
    store.upsert_page(
        slug="user-profile",
        title="User Profile",
        body="# User Profile\n\nThe user prefers concise answers.",
        domain="user",
        visibility_policy="user_visible",
        disclosure_rules={"can_quote": False, "can_summarize": True, "allowed_audiences": ["user"]},
    )

    user_pages = store.list_pages(audience="user")
    slugs = [page["slug"] for page in user_pages]
    assert "internal-agent-note" not in slugs
    assert "user-profile" in slugs

    assert store.get_page_metadata("internal-agent-note", audience="user") == {}

    redacted_page = store.get_page("user-profile", audience="user")
    assert redacted_page["content_redacted"] is True
    assert redacted_page["body"] == "The user prefers concise answers."

    operator_page = store.get_page("internal-agent-note", audience="operator")
    assert "Do not disclose this directly." in operator_page["body"]


def test_access_views_surface_missing_restricted_and_redacted_states(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge_pages, "KNOWLEDGE_PAGE_MIRROR_DIR", tmp_path)
    storage = DummyStorage()
    store = knowledge_pages.KnowledgePageStore(storage)

    store.upsert_page(
        slug="private-contact",
        title="Private Contact",
        body="# Private Contact\n\nSensitive contact details.",
        domain="contacts",
        visibility_policy="restricted",
        disclosure_rules={"can_quote": False, "can_summarize": False, "allowed_audiences": ["agent"], "tool_access": "restricted"},
    )
    store.upsert_page(
        slug="soft-redacted-user-profile",
        title="Soft Redacted User Profile",
        body="# Soft Redacted User Profile\n\nThe user likes concise answers and prefers examples.",
        domain="user",
        visibility_policy="user_visible",
        disclosure_rules={"can_quote": False, "can_summarize": True, "allowed_audiences": ["user"]},
    )

    missing = store.get_page_view("does-not-exist", audience="agent")
    assert missing["status"] == "missing"

    restricted = store.get_page_view("private-contact", audience="user")
    assert restricted["status"] == "restricted"
    assert restricted["requires_consent"] is True
    assert restricted["page_metadata"]["domain"] == "contacts"

    redacted = store.get_page_view("soft-redacted-user-profile", audience="user")
    assert redacted["status"] == "redacted"
    assert redacted["page"]["content_redacted"] is True
    assert redacted["page"]["body"] == "The user likes concise answers and prefers examples."


def test_provenance_thread_is_compacted(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge_pages, "KNOWLEDGE_PAGE_MIRROR_DIR", tmp_path)
    storage = DummyStorage()
    store = knowledge_pages.KnowledgePageStore(storage)
    provenance = [{"label": f"note-{idx}", "recorded_at": f"2026-03-26T00:00:{idx:02d}Z"} for idx in range(30)]
    page = store.upsert_page(
        slug="bounded-provenance",
        title="Bounded Provenance",
        body="# Bounded Provenance\n\nTest body.",
        provenance=provenance,
    )

    assert len(page["provenance"]) == knowledge_pages.MAX_INLINE_PROVENANCE
    assert page["archived_provenance_summary"]["archived_count"] == 10


def test_duplicate_candidates_and_maintenance_report(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge_pages, "KNOWLEDGE_PAGE_MIRROR_DIR", tmp_path)
    storage = DummyStorage()
    store = knowledge_pages.KnowledgePageStore(storage)

    store.upsert_page(
        slug="system-constitution",
        title="System Constitution",
        body="# System Constitution\n\nShared durable rules.",
        aliases=["constitution"],
        provenance=[{"label": ".knowledge/specs/constitution.md", "content_fingerprint": "abc123"}],
    )
    page = store.upsert_page(
        slug="constitution",
        title="Constitution",
        body="# Constitution\n\nShared durable rules.",
        aliases=["System Constitution"],
        provenance=[{"label": ".knowledge/specs/constitution.md", "content_fingerprint": "abc123"}],
    )

    duplicate_slugs = [item["slug"] for item in page["maintenance"]["duplicate_candidates"]]
    assert "system-constitution" in duplicate_slugs

    store.refresh_maintenance_report()
    report = storage.parameters.values[knowledge_pages.KNOWLEDGE_PAGE_MAINTENANCE_REPORT_KEY]
    assert report["duplicate_page_count"] >= 1
