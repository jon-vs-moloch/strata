"""Shared pytest setup.

Knowledge pages mirror themselves to docs/spec/kb/*.md. Without this fixture,
any test that writes a page rewrites the tracked files' last_updated stamps.
"""

import pytest

from strata.knowledge import pages as knowledge_pages


@pytest.fixture(autouse=True)
def _knowledge_mirror_in_tmp(tmp_path, monkeypatch):
    mirror = tmp_path / "kb-mirror"
    monkeypatch.setattr(knowledge_pages, "KNOWLEDGE_PAGE_MIRROR_DIR", mirror)
    yield mirror
