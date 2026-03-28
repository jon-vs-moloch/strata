#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from sqlalchemy.exc import OperationalError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strata.knowledge.page_payloads import build_content_fingerprint, slugify_page_title
from strata.knowledge.pages import KnowledgePageStore
from strata.storage.services.main import StorageManager


KNOWLEDGE_DIR = ROOT / ".knowledge"
SPEC_DIR = ROOT / "docs" / "spec"
CURRENT_KB_PATH = SPEC_DIR / "current_knowledge_base.md"
PROVENANCE_PATH = KNOWLEDGE_DIR / "provenance_index.json"
ARCHIVE_DIR = KNOWLEDGE_DIR / "archive"
MAX_HOT_FINAL = 8
MAX_HOT_WIP = 12

SOURCE_BLUEPRINTS = [
    {
        "path": Path(".knowledge/specs/constitution.md"),
        "slug": "constitution",
        "title": "Constitution",
        "domain": "system",
        "visibility_policy": "project_scoped",
        "tags": ["constitution", "durable", "alignment"],
        "related_pages": ["project-spec", "strata-system-philosophy", "codemap"],
    },
    {
        "path": Path(".knowledge/specs/project_spec.md"),
        "slug": "project-spec",
        "title": "Project Spec",
        "domain": "project",
        "visibility_policy": "project_scoped",
        "tags": ["project", "durable", "alignment"],
        "related_pages": ["constitution", "strata-system-philosophy", "codemap"],
    },
    {
        "path": Path("docs/spec/project-philosophy.md"),
        "slug": "strata-system-philosophy",
        "title": "Strata System Philosophy",
        "domain": "system",
        "visibility_policy": "project_scoped",
        "tags": ["philosophy", "bootstrap", "system"],
        "related_pages": ["constitution", "project-spec", "codemap"],
    },
    {
        "path": Path("docs/spec/codemap.md"),
        "slug": "codemap",
        "title": "Codemap",
        "domain": "project",
        "visibility_policy": "project_scoped",
        "tags": ["architecture", "navigation", "repo"],
        "related_pages": ["project-spec", "strata-system-philosophy", "ui-operator-audit"],
    },
    {
        "path": Path("docs/spec/eval-brief.md"),
        "slug": "eval-brief",
        "title": "Eval Brief",
        "domain": "project",
        "visibility_policy": "project_scoped",
        "tags": ["eval", "telemetry"],
        "related_pages": ["eval-catalog", "project-spec", "model-performance-intel"],
    },
    {
        "path": Path("docs/spec/eval-catalog.md"),
        "slug": "eval-catalog",
        "title": "Eval Catalog",
        "domain": "project",
        "visibility_policy": "project_scoped",
        "tags": ["eval", "catalog"],
        "related_pages": ["eval-brief", "model-performance-intel"],
    },
    {
        "path": Path("docs/spec/task-attempt-ontology.md"),
        "slug": "task-attempt-ontology",
        "title": "Task Attempt Ontology",
        "domain": "project",
        "visibility_policy": "project_scoped",
        "tags": ["tasks", "ontology", "storage"],
        "related_pages": ["codemap", "project-spec"],
    },
    {
        "path": Path("docs/spec/ui-operator-audit.md"),
        "slug": "ui-operator-audit",
        "title": "UI Operator Audit",
        "domain": "project",
        "visibility_policy": "project_scoped",
        "tags": ["ui", "operator", "audit"],
        "related_pages": ["codemap", "project-spec", "current-knowledge-base"],
    },
    {
        "path": Path(".knowledge/model_performance_intel.md"),
        "slug": "model-performance-intel",
        "title": "Model Performance Intel",
        "domain": "system",
        "visibility_policy": "project_scoped",
        "tags": ["telemetry", "performance", "models"],
        "related_pages": ["eval-brief", "eval-catalog", "current-knowledge-base"],
    },
]


def _classify(path: Path) -> str:
    name = path.name
    if name.startswith("final_research_"):
        return "final_research"
    if name.startswith("wip_research_"):
        return "wip_research"
    if "performance" in name:
        return "telemetry"
    return "misc"


def _snippet(text: str, limit: int = 800) -> str:
    cleaned = " ".join(text.strip().split())
    return cleaned[:limit]


def _archive_plan(files: List[Path]) -> tuple[List[Path], List[Path]]:
    final_files = [path for path in files if _classify(path) == "final_research"]
    wip_files = [path for path in files if _classify(path) == "wip_research"]
    keep = set(final_files[-MAX_HOT_FINAL:] + wip_files[-MAX_HOT_WIP:])
    archived = [path for path in files if _classify(path) in {"final_research", "wip_research"} and path not in keep]
    hot = [path for path in files if path not in archived]
    return hot, archived


def _archive_file(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    target_dir = ARCHIVE_DIR / stamp
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    if not target.exists():
        shutil.move(str(path), str(target))
    return target


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _split_frontmatter(raw: str) -> tuple[Dict[str, str], str]:
    if not raw.startswith("---\n"):
        return {}, raw
    parts = raw.split("\n---\n", 1)
    if len(parts) != 2:
        return {}, raw
    meta_raw = parts[0].splitlines()[1:]
    meta: Dict[str, str] = {}
    for line in meta_raw:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip()
    return meta, parts[1].lstrip()


def _extract_title(body: str, fallback: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return fallback


def _provenance_entry(path: Path, *, kind: str, body: str, recorded_at: str) -> Dict[str, Any]:
    stat = path.stat()
    rel = str(path.relative_to(ROOT))
    return {
        "label": rel,
        "path": rel,
        "kind": kind,
        "recorded_at": recorded_at,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "content_fingerprint": build_content_fingerprint(body),
        "snippet": _snippet(body, limit=240),
    }


def _iter_blueprint_pages(recorded_at: str) -> List[Dict[str, Any]]:
    pages: List[Dict[str, Any]] = []
    for blueprint in SOURCE_BLUEPRINTS:
        path = ROOT / blueprint["path"]
        if not path.exists():
            continue
        raw = _read_text(path)
        frontmatter, body = _split_frontmatter(raw)
        title = str(frontmatter.get("title") or blueprint["title"] or _extract_title(body, path.stem.replace("_", " ").title()))
        slug = str(frontmatter.get("slug") or blueprint["slug"] or slugify_page_title(title))
        pages.append(
            {
                "slug": slug,
                "title": title,
                "body": body.strip(),
                "summary": frontmatter.get("summary"),
                "tags": blueprint.get("tags") or [],
                "aliases": blueprint.get("aliases") or [],
                "related_pages": blueprint.get("related_pages") or [],
                "domain": frontmatter.get("domain") or blueprint.get("domain") or "project",
                "visibility_policy": frontmatter.get("visibility_policy") or blueprint.get("visibility_policy"),
                "confidence": float(frontmatter.get("confidence") or 0.75),
                "created_by": "knowledge_compactor",
                "updated_reason": "knowledge_compaction",
                "project_id": "strata",
                "retention_policy": "persistent",
                "provenance": [_provenance_entry(path, kind="durable_doc", body=body, recorded_at=recorded_at)],
            }
        )
    return pages


def _iter_hot_research_pages(raw_files: List[Path], recorded_at: str) -> List[Dict[str, Any]]:
    pages: List[Dict[str, Any]] = []
    for path in raw_files:
        if path.parent != KNOWLEDGE_DIR:
            continue
        if path.name == "model_performance_intel.md":
            continue
        raw = _read_text(path)
        frontmatter, body = _split_frontmatter(raw)
        title = str(frontmatter.get("title") or _extract_title(body, path.stem.replace("_", " ").title()))
        kind = _classify(path)
        if kind == "wip_research":
            continue
        pages.append(
            {
                "slug": str(frontmatter.get("slug") or slugify_page_title(title)),
                "title": title,
                "body": body.strip(),
                "summary": frontmatter.get("summary"),
                "tags": [kind, "research"],
                "aliases": [],
                "related_pages": ["current-knowledge-base"],
                "domain": "project",
                "visibility_policy": "project_scoped",
                "confidence": 0.55 if kind == "wip_research" else 0.65,
                "created_by": "knowledge_compactor",
                "updated_reason": "knowledge_compaction",
                "project_id": "strata",
                "retention_policy": "ephemeral" if kind == "wip_research" else "persistent",
                "provenance": [_provenance_entry(path, kind=kind, body=body, recorded_at=recorded_at)],
            }
        )
    return pages


def _build_index_page(page_specs: List[Dict[str, Any]], archived_entries: List[Dict[str, str]], recorded_at: str) -> Dict[str, Any]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for page in page_specs:
        grouped[str(page.get("domain") or "project")].append(page)
    lines = [
        "# Current Knowledge Base",
        "",
        "This page is the current-facing knowledge index for Strata.",
        "Durable docs and synthesized pages are kept here so research and operations can cite a stable wiki instead of scraping transient note dumps.",
        "",
        f"Generated: {recorded_at}",
        "",
        "## Index",
    ]
    for domain, entries in sorted(grouped.items()):
        lines.append(f"### {domain.title()}")
        for page in sorted(entries, key=lambda item: str(item.get('title') or item.get('slug') or '')):
            summary = str(page.get("summary") or _snippet(str(page.get("body") or ""), limit=180))
            lines.append(f"- **{page['title']}** (`{page['slug']}`): {summary}")
        lines.append("")
    if archived_entries:
        lines.extend(
            [
                "## Archived Research",
                "",
                f"- {len(archived_entries)} older research notes were archived after synthesis to keep the hot knowledge thread bounded.",
                "",
            ]
        )
    provenance = []
    for page in page_specs:
        provenance.extend(page.get("provenance") or [])
    return {
        "slug": "current-knowledge-base",
        "title": "Current Knowledge Base",
        "body": "\n".join(lines).strip(),
        "tags": ["knowledge", "index", "wiki"],
        "aliases": ["knowledge base", "wiki"],
        "related_pages": [page["slug"] for page in page_specs[:20]],
        "domain": "project",
        "visibility_policy": "project_scoped",
        "confidence": 0.9,
        "created_by": "knowledge_compactor",
        "updated_reason": "knowledge_compaction",
        "project_id": "strata",
        "retention_policy": "persistent",
        "provenance": provenance,
    }


def _build_maintenance_page(pages: List[Dict[str, Any]], recorded_at: str) -> Dict[str, Any]:
    duplicate_lines = []
    stale_lines = []
    for page in pages:
        maintenance = page.get("maintenance") or {}
        candidates = maintenance.get("duplicate_candidates") or []
        if candidates:
            preview = ", ".join(f"`{item['slug']}` ({item['reason']})" for item in candidates[:3])
            duplicate_lines.append(f"- `{page['slug']}` -> {preview}")
        if maintenance.get("freshness_status") == "stale":
            stale_lines.append(f"- `{page['slug']}`")
    lines = [
        "# Knowledge Maintenance Report",
        "",
        f"Generated: {recorded_at}",
        "",
        f"- Pages tracked: {len(pages)}",
        f"- Pages with duplicate candidates: {len(duplicate_lines)}",
        f"- Pages marked stale: {len(stale_lines)}",
        "",
        "## Duplicate Candidates",
        "",
    ]
    lines.extend(duplicate_lines or ["- No duplicate candidates detected."])
    lines.extend(["", "## Freshness", ""])
    lines.extend(stale_lines or ["- No stale pages detected."])
    provenance = []
    for page in pages:
        provenance.extend(page.get("provenance") or [])
    return {
        "slug": "knowledge-maintenance-report",
        "title": "Knowledge Maintenance Report",
        "body": "\n".join(lines).strip(),
        "tags": ["knowledge", "maintenance", "dedupe"],
        "aliases": ["maintenance report"],
        "related_pages": [page["slug"] for page in pages[:20]],
        "domain": "project",
        "visibility_policy": "project_scoped",
        "confidence": 0.85,
        "created_by": "knowledge_compactor",
        "updated_reason": "knowledge_compaction",
        "project_id": "strata",
        "retention_policy": "persistent",
        "provenance": provenance,
    }


def _persist_page_with_retry(page: Dict[str, Any], *, attempts: int = 4, delay_s: float = 0.5) -> Dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(attempts):
        storage = StorageManager()
        try:
            store = KnowledgePageStore(storage)
            persisted = store.upsert_page(
                slug=page["slug"],
                title=page["title"],
                body=page["body"],
                summary=page.get("summary"),
                tags=page.get("tags"),
                aliases=page.get("aliases"),
                related_pages=page.get("related_pages"),
                provenance=page.get("provenance"),
                confidence=float(page.get("confidence", 0.5) or 0.5),
                created_by=str(page.get("created_by") or "knowledge_compactor"),
                updated_reason=str(page.get("updated_reason") or "knowledge_compaction"),
                domain=str(page.get("domain") or "project"),
                visibility_policy=page.get("visibility_policy"),
                disclosure_rules=page.get("disclosure_rules"),
                scope_id=str(page.get("scope_id") or ""),
                project_id=str(page.get("project_id") or "strata"),
                owner_id=str(page.get("owner_id") or ""),
                retention_policy=str(page.get("retention_policy") or "persistent"),
                maintenance=page.get("maintenance"),
            )
            storage.commit()
            return persisted
        except OperationalError as exc:
            storage.rollback()
            last_error = exc
            if "database is locked" not in str(exc).lower() or attempt == attempts - 1:
                raise
            time.sleep(delay_s * (attempt + 1))
        finally:
            storage.close()
    if last_error:
        raise last_error
    raise RuntimeError("Failed to persist knowledge page")


def _load_persisted_pages() -> List[Dict[str, Any]]:
    storage = StorageManager()
    try:
        store = KnowledgePageStore(storage)
        pages = []
        for metadata in store.list_pages(audience="operator", limit=200):
            slug = str(metadata.get("slug") or "")
            if not slug:
                continue
            page = store._load_page_payload(slug)
            if page:
                pages.append(page)
        return pages
    finally:
        storage.close()


def _refresh_maintenance_report_with_retry(*, attempts: int = 4, delay_s: float = 0.5) -> Dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(attempts):
        storage = StorageManager()
        try:
            report = KnowledgePageStore(storage).refresh_maintenance_report()
            storage.commit()
            return report
        except OperationalError as exc:
            storage.rollback()
            last_error = exc
            if "database is locked" not in str(exc).lower() or attempt == attempts - 1:
                raise
            time.sleep(delay_s * (attempt + 1))
        finally:
            storage.close()
    if last_error:
        raise last_error
    raise RuntimeError("Failed to refresh maintenance report")


def _prune_stale_compacted_pages(valid_slugs: List[str], *, attempts: int = 4, delay_s: float = 0.5) -> List[str]:
    desired = {slugify_page_title(slug) for slug in valid_slugs}
    last_error: Exception | None = None
    for attempt in range(attempts):
        storage = StorageManager()
        try:
            store = KnowledgePageStore(storage)
            removed = []
            for metadata in store.list_pages(audience="operator", limit=500):
                slug = slugify_page_title(str(metadata.get("slug") or ""))
                if not slug or slug in desired:
                    continue
                if str(metadata.get("updated_reason") or "") != "knowledge_compaction":
                    continue
                if store.delete_page(slug):
                    removed.append(slug)
            storage.commit()
            return removed
        except OperationalError as exc:
            storage.rollback()
            last_error = exc
            if "database is locked" not in str(exc).lower() or attempt == attempts - 1:
                raise
            time.sleep(delay_s * (attempt + 1))
        finally:
            storage.close()
    if last_error:
        raise last_error
    raise RuntimeError("Failed to prune stale compacted pages")


def build_knowledge_snapshot() -> Dict[str, object]:
    recorded_at = datetime.now(timezone.utc).isoformat()
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    SPEC_DIR.mkdir(parents=True, exist_ok=True)

    raw_files = sorted([path for path in KNOWLEDGE_DIR.glob("*.md") if path.is_file()])
    hot_files, archived_candidates = _archive_plan(raw_files)
    archived_entries: List[Dict[str, str]] = []
    for path in archived_candidates:
        text = _read_text(path)
        target = _archive_file(path)
        archived_entries.append(
            {
                "filename": target.name,
                "kind": _classify(target),
                "archived_path": str(target.relative_to(ROOT)),
                "snippet": _snippet(text),
            }
        )

    page_specs = _iter_blueprint_pages(recorded_at)
    page_specs.extend(_iter_hot_research_pages(hot_files, recorded_at))
    page_specs.append(_build_index_page(page_specs, archived_entries, recorded_at))

    mirrored_pages: List[Dict[str, Any]] = []
    for page in page_specs:
        mirrored_pages.append(_persist_page_with_retry(page))
    _prune_stale_compacted_pages([page["slug"] for page in page_specs])
    _refresh_maintenance_report_with_retry()
    mirrored_pages = _load_persisted_pages()
    maintenance_page = _build_maintenance_page(mirrored_pages, recorded_at)
    maintenance_page["maintenance"] = {
        "freshness_status": "fresh",
        "review_status": "unreviewed",
        "evidence_status": "maintenance_report",
        "last_compacted_at": recorded_at,
    }
    mirrored_pages.append(_persist_page_with_retry(maintenance_page))
    _refresh_maintenance_report_with_retry()
    mirrored_pages = _load_persisted_pages()

    current_page = next((page for page in mirrored_pages if page.get("slug") == "current-knowledge-base"), None)
    if current_page:
        CURRENT_KB_PATH.write_text(
            "\n".join(
                [
                    f"# {current_page['title']}",
                    "",
                    current_page["body"],
                    "",
                ]
            ),
            encoding="utf-8",
        )

    PROVENANCE_PATH.write_text(
        json.dumps(
            {
                "generated_at": recorded_at,
                "current_kb_path": str(CURRENT_KB_PATH.relative_to(ROOT)),
                "page_count": len(mirrored_pages),
                "pages": {
                    page["slug"]: {
                        "title": page["title"],
                        "domain": page.get("domain"),
                        "source_count": page.get("source_count", len(page.get("provenance") or [])),
                        "provenance": page.get("provenance") or [],
                        "archived_provenance_summary": page.get("archived_provenance_summary") or {},
                        "maintenance": page.get("maintenance") or {},
                    }
                    for page in mirrored_pages
                },
                "archived_files": archived_entries,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    sections: Dict[str, int] = defaultdict(int)
    for page in mirrored_pages:
        sections[str(page.get("domain") or "project")] += 1

    return {
        "generated_at": recorded_at,
        "page_count": len(mirrored_pages),
        "archived_file_count": len(archived_entries),
        "current_kb_path": str(CURRENT_KB_PATH.relative_to(ROOT)),
        "provenance_path": str(PROVENANCE_PATH.relative_to(ROOT)),
        "sections": dict(sections),
        "page_slugs": [page["slug"] for page in mirrored_pages],
    }


def main() -> None:
    print(json.dumps(build_knowledge_snapshot(), indent=2))


if __name__ == "__main__":
    main()
