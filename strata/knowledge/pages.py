"""
@module knowledge.pages
@purpose Manage synthesized knowledge pages separately from raw `.knowledge/` notes.
@owns metadata-first retrieval, page normalization, provenance-aware upserts
@does_not_own raw note archival, background research execution
@key_exports KnowledgePageStore, slugify_page_title
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import desc

from strata.observability.context import record_context_load
from strata.knowledge.page_access import build_access_state, sanitize_for_audience
from strata.knowledge.page_payloads import (
    DEFAULT_DOMAIN,
    MAX_INLINE_PROVENANCE,
    build_content_fingerprint,
    normalize_domain,
    normalize_page_payload,
    slugify_page_title,
    split_sections,
)
from strata.storage.models import ParameterModel, TaskModel, TaskState, TaskType


KNOWLEDGE_PAGE_INDEX_KEY = "knowledge_pages:index"
KNOWLEDGE_PAGE_KEY_PREFIX = "knowledge_page:"
KNOWLEDGE_PAGE_MAINTENANCE_REPORT_KEY = "knowledge_pages:maintenance_report"
KNOWLEDGE_PAGE_MIRROR_DIR = Path("docs/spec/kb")


def _page_key(slug: str) -> str:
    return f"{KNOWLEDGE_PAGE_KEY_PREFIX}{slugify_page_title(slug)}"

class KnowledgePageStore:
    """
    Metadata-first synthesized page store layered on top of the parameter table.
    """

    def __init__(self, storage):
        self.storage = storage

    def _load_index(self) -> List[Dict[str, Any]]:
        value = self.storage.parameters.peek_parameter(KNOWLEDGE_PAGE_INDEX_KEY, default_value=[])
        if not isinstance(value, list):
            return []
        pages = []
        for row in value:
            if isinstance(row, dict) and row.get("slug"):
                pages.append(row)
        return pages

    def _save_index(self, pages: List[Dict[str, Any]]) -> None:
        self.storage.parameters.set_parameter(
            KNOWLEDGE_PAGE_INDEX_KEY,
            pages,
            description="Metadata-first index of synthesized knowledge pages.",
        )

    def _load_page_payload(self, slug: str) -> Dict[str, Any]:
        payload = self.storage.parameters.peek_parameter(_page_key(slug), default_value={}) or {}
        return normalize_page_payload(payload, slug=slug)

    def get_page_view(self, slug: str, *, audience: str = "operator") -> Dict[str, Any]:
        page = self._load_page_payload(slug)
        view = build_access_state(page=page, audience=audience, include_body=True)
        if view.get("status") in {"ok", "redacted"}:
            loaded_page = view.get("page") or {}
            record_context_load(
                artifact_type="knowledge_page",
                identifier=str(loaded_page.get("slug") or slug),
                content=str(loaded_page.get("body") or loaded_page.get("summary") or ""),
                source="knowledge.pages.get_page_view",
                metadata={"audience": audience, "status": view.get("status")},
                storage=self.storage,
            )
        return view

    def get_page_metadata_view(self, slug: str, *, audience: str = "operator") -> Dict[str, Any]:
        page = self._load_page_payload(slug)
        view = build_access_state(page=page, audience=audience, include_body=False)
        if view.get("status") in {"ok", "redacted"}:
            loaded_page = view.get("page") or {}
            record_context_load(
                artifact_type="knowledge_page_metadata",
                identifier=str(loaded_page.get("slug") or slug),
                content=str(loaded_page.get("summary") or loaded_page.get("title") or ""),
                source="knowledge.pages.get_page_metadata_view",
                metadata={"audience": audience, "status": view.get("status")},
                storage=self.storage,
            )
        return view

    def get_page_section_view(self, slug: str, heading: str, *, audience: str = "operator") -> Dict[str, Any]:
        view = self.get_page_view(slug, audience=audience)
        if view.get("status") not in {"ok", "redacted"}:
            return view
        page = view.get("page") or {}
        sections = split_sections(page.get("body", ""))
        section_key = slugify_page_title(heading)
        section_view = {
            "status": view.get("status"),
            "reason": view.get("reason", ""),
            "audience": view.get("audience"),
            "requires_consent": view.get("requires_consent", False),
            "section": {
                "slug": page.get("slug"),
                "title": page.get("title"),
                "heading": heading,
                "anchor": section_key,
                "content": sections.get(section_key, ""),
                "visibility_policy": page.get("visibility_policy"),
                "content_redacted": bool(page.get("content_redacted")),
            },
        }
        section = section_view.get("section") or {}
        record_context_load(
            artifact_type="knowledge_page_section",
            identifier=f"{slug}#{section_key}",
            content=str(section.get("content") or ""),
            source="knowledge.pages.get_page_section_view",
            metadata={"audience": audience, "status": view.get("status")},
            storage=self.storage,
        )
        return section_view

    def list_pages(
        self,
        *,
        query: Optional[str] = None,
        tag: Optional[str] = None,
        domain: Optional[str] = None,
        audience: str = "operator",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        pages = self._load_index()
        lowered_query = (query or "").strip().lower()
        lowered_tag = (tag or "").strip().lower()
        lowered_domain = normalize_domain(domain) if domain else ""
        filtered: List[Dict[str, Any]] = []
        for page in pages:
            if lowered_query:
                haystack = " ".join(
                    [
                        str(page.get("slug", "")),
                        str(page.get("title", "")),
                        str(page.get("summary", "")),
                        " ".join(page.get("tags") or []),
                        " ".join(page.get("aliases") or []),
                    ]
                ).lower()
                if lowered_query not in haystack:
                    continue
            if lowered_tag and lowered_tag not in [str(tag).lower() for tag in page.get("tags") or []]:
                continue
            if lowered_domain and str(page.get("domain") or "") != lowered_domain:
                continue
            sanitized = sanitize_for_audience(page, audience=audience, include_body=False)
            if sanitized:
                filtered.append(sanitized)
        filtered.sort(key=lambda page: page.get("last_updated", ""), reverse=True)
        return filtered[: max(1, limit)]

    def get_page(self, slug: str, *, audience: str = "operator") -> Dict[str, Any]:
        return self.get_page_view(slug, audience=audience).get("page", {})

    def get_page_metadata(self, slug: str, *, audience: str = "operator") -> Dict[str, Any]:
        return self.get_page_metadata_view(slug, audience=audience).get("page", {})

    def get_page_section(self, slug: str, heading: str, *, audience: str = "operator") -> Dict[str, Any]:
        return self.get_page_section_view(slug, heading, audience=audience).get("section", {})

    def _build_duplicate_candidates(
        self,
        *,
        normalized_slug: str,
        title: str,
        aliases: Optional[List[str]],
        body: str,
    ) -> List[Dict[str, Any]]:
        page_fingerprint = build_content_fingerprint(body)
        title_key = slugify_page_title(title)
        alias_keys = {slugify_page_title(alias) for alias in aliases or [] if str(alias).strip()}
        candidates: List[Dict[str, Any]] = []
        for row in self._load_index():
            candidate_slug = str(row.get("slug") or "")
            if not candidate_slug or candidate_slug == normalized_slug:
                continue
            candidate_title_key = slugify_page_title(str(row.get("title") or candidate_slug))
            candidate_alias_keys = {slugify_page_title(alias) for alias in row.get("aliases") or []}
            candidate_fingerprint = build_content_fingerprint(
                " ".join(
                    [
                        str(row.get("summary") or ""),
                        str(row.get("title") or ""),
                    ]
                )
            )
            score = 0.0
            reason = ""
            if candidate_title_key == title_key:
                score = 1.0
                reason = "matching_title"
            elif candidate_slug in alias_keys or candidate_title_key in alias_keys:
                score = 0.9
                reason = "alias_overlap"
            elif candidate_alias_keys & ({title_key} | alias_keys):
                score = 0.75
                reason = "alias_crosslink"
            elif candidate_fingerprint == page_fingerprint:
                score = 0.65
                reason = "similar_summary_fingerprint"
            if score > 0:
                candidates.append({"slug": candidate_slug, "reason": reason, "score": score})
        candidates.sort(key=lambda item: (-float(item.get("score", 0.0)), str(item.get("slug") or "")))
        return candidates[:12]

    def _write_maintenance_report(self, pages: List[Dict[str, Any]]) -> Dict[str, Any]:
        duplicates = []
        stale = []
        contested = []
        for page in pages:
            maintenance = page.get("maintenance") or {}
            duplicate_candidates = maintenance.get("duplicate_candidates") or []
            if duplicate_candidates:
                duplicates.append(
                    {
                        "slug": page.get("slug"),
                        "title": page.get("title"),
                        "candidates": duplicate_candidates[:5],
                    }
                )
            if maintenance.get("freshness_status") == "stale":
                stale.append(page.get("slug"))
            if maintenance.get("review_status") == "contested":
                contested.append(page.get("slug"))
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "page_count": len(pages),
            "duplicate_page_count": len(duplicates),
            "stale_page_count": len(stale),
            "contested_page_count": len(contested),
            "duplicates": duplicates[:20],
            "stale_pages": stale[:20],
            "contested_pages": contested[:20],
        }
        self.storage.parameters.set_parameter(
            KNOWLEDGE_PAGE_MAINTENANCE_REPORT_KEY,
            report,
            description="Maintenance report for synthesized knowledge pages, including freshness and deduplication signals.",
        )
        return report

    def refresh_maintenance_report(self) -> Dict[str, Any]:
        pages = []
        for row in self._load_index():
            slug = str(row.get("slug") or "")
            if not slug:
                continue
            page = self._load_page_payload(slug)
            if page:
                pages.append(page)
        return self._write_maintenance_report(pages)

    def get_maintenance_report(self) -> Dict[str, Any]:
        report = self.storage.parameters.peek_parameter(KNOWLEDGE_PAGE_MAINTENANCE_REPORT_KEY, default_value={}) or {}
        if not isinstance(report, dict):
            return {}
        return report

    def delete_page(self, slug: str) -> bool:
        normalized_slug = slugify_page_title(slug)
        existing = self._load_page_payload(normalized_slug)
        if not existing:
            return False
        row = self.storage.session.query(ParameterModel).filter_by(key=_page_key(normalized_slug)).first()
        if row is not None:
            self.storage.session.delete(row)
        index = [row for row in self._load_index() if row.get("slug") != normalized_slug]
        self._save_index(index)
        target = KNOWLEDGE_PAGE_MIRROR_DIR / f"{normalized_slug}.md"
        if target.exists():
            target.unlink()
        return True

    def upsert_page(
        self,
        *,
        slug: Optional[str],
        title: str,
        body: str,
        summary: Optional[str] = None,
        tags: Optional[List[str]] = None,
        aliases: Optional[List[str]] = None,
        related_pages: Optional[List[str]] = None,
        provenance: Optional[List[Dict[str, Any]]] = None,
        confidence: float = 0.5,
        created_by: str = "system",
        updated_reason: str = "upsert",
        domain: str = DEFAULT_DOMAIN,
        visibility_policy: Optional[str] = None,
        disclosure_rules: Optional[Dict[str, Any]] = None,
        maintenance: Optional[Dict[str, Any]] = None,
        scope_id: str = "",
        project_id: str = "",
        owner_id: str = "",
        retention_policy: str = "persistent",
    ) -> Dict[str, Any]:
        normalized_slug = slugify_page_title(slug or title)
        existing = self._load_page_payload(normalized_slug)
        normalized_domain = domain or existing.get("domain") or DEFAULT_DOMAIN
        next_aliases = aliases if aliases is not None else existing.get("aliases")
        next_body = body or existing.get("body") or ""
        default_maintenance = {
            "source_paths": [
                str(item.get("path") or item.get("label") or "")
                for item in (provenance if provenance is not None else existing.get("provenance") or [])
                if str(item.get("path") or item.get("label") or "").strip()
            ],
            "source_fingerprints": [
                str(item.get("content_fingerprint") or "")
                for item in (provenance if provenance is not None else existing.get("provenance") or [])
                if str(item.get("content_fingerprint") or "").strip()
            ],
            "freshness_status": "fresh",
            "stale_source_count": 0,
            "duplicate_candidates": self._build_duplicate_candidates(
                normalized_slug=normalized_slug,
                title=title or existing.get("title") or normalized_slug.replace("-", " ").title(),
                aliases=next_aliases,
                body=next_body,
            ),
            "review_status": "unreviewed",
            "last_compacted_at": datetime.now(timezone.utc).isoformat(),
            "evidence_status": "seeded",
        }
        if existing.get("maintenance"):
            default_maintenance.update(existing.get("maintenance") or {})
        if maintenance:
            default_maintenance.update(maintenance)
        payload = normalize_page_payload(
            {
                "slug": normalized_slug,
                "title": title or existing.get("title") or normalized_slug.replace("-", " ").title(),
                "body": next_body,
                "summary": summary or existing.get("summary"),
                "tags": tags if tags is not None else existing.get("tags"),
                "aliases": next_aliases,
                "related_pages": related_pages if related_pages is not None else existing.get("related_pages"),
                "provenance": provenance if provenance is not None else existing.get("provenance"),
                "confidence": confidence if confidence is not None else existing.get("confidence", 0.5),
                "created_by": created_by or existing.get("created_by", "system"),
                "updated_reason": updated_reason,
                "domain": normalized_domain,
                "visibility_policy": visibility_policy or existing.get("visibility_policy"),
                "disclosure_rules": disclosure_rules if disclosure_rules is not None else existing.get("disclosure_rules"),
                "maintenance": default_maintenance,
                "scope_id": scope_id or existing.get("scope_id", ""),
                "project_id": project_id or existing.get("project_id", ""),
                "owner_id": owner_id or existing.get("owner_id", ""),
                "retention_policy": retention_policy or existing.get("retention_policy", "persistent"),
                "last_updated": datetime.now(timezone.utc).isoformat(),
            },
            slug=normalized_slug,
        )
        self.storage.parameters.set_parameter(
            _page_key(normalized_slug),
            payload,
            description=f"Synthesized knowledge page for {normalized_slug}.",
        )
        index = self._load_index()
        index = [row for row in index if row.get("slug") != normalized_slug]
        metadata = dict(payload)
        metadata.pop("body", None)
        index.append(metadata)
        self._save_index(index)
        self._mirror_page_to_disk(payload)
        return payload

    def _mirror_page_to_disk(self, page: Dict[str, Any]) -> None:
        KNOWLEDGE_PAGE_MIRROR_DIR.mkdir(parents=True, exist_ok=True)
        target = KNOWLEDGE_PAGE_MIRROR_DIR / f"{page['slug']}.md"
        lines = [
            "---",
            f"title: {page['title']}",
            f"slug: {page['slug']}",
            f"last_updated: {page['last_updated']}",
            f"confidence: {page['confidence']}",
            f"domain: {page.get('domain')}",
            f"visibility_policy: {page.get('visibility_policy')}",
            f"scope_id: {page.get('scope_id')}",
            f"project_id: {page.get('project_id')}",
            f"owner_id: {page.get('owner_id')}",
            f"retention_policy: {page.get('retention_policy')}",
            f"tags: {page.get('tags') or []}",
            f"aliases: {page.get('aliases') or []}",
            f"related_pages: {page.get('related_pages') or []}",
            "---",
            "",
            f"# {page['title']}",
            "",
            f"> Summary: {page['summary']}",
            "",
        ]
        if page.get("provenance"):
            lines.extend(
                [
                    "## Provenance",
                    "",
                ]
            )
            for source in page["provenance"][:20]:
                label = str(source.get("label") or source.get("path") or source.get("source") or "source")
                lines.append(f"- {label}")
            lines.append("")
        if page.get("archived_provenance_summary"):
            lines.extend(
                [
                    "## Archived Provenance Summary",
                    "",
                    f"- {page['archived_provenance_summary'].get('summary')}",
                    "",
                ]
            )
        lines.append(page.get("body", ""))
        target.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

    def enqueue_update_task(
        self,
        *,
        slug: str,
        reason: str,
        session_id: Optional[str] = None,
        target_scope: str = "codebase",
        evidence: Optional[List[str]] = None,
        domain: Optional[str] = None,
        operation: str = "update_page",
        related_slugs: Optional[List[str]] = None,
        provenance: Optional[Dict[str, Any]] = None,
    ):
        normalized_slug = slugify_page_title(slug)
        page = self.get_page(normalized_slug)
        title = page.get("title") or normalized_slug.replace("-", " ").title()
        resolved_domain = normalize_domain(domain or page.get("domain"))
        description = (
            f"Update the knowledge page '{title}' ({normalized_slug}).\n"
            f"Reason: {reason}\n"
            f"Target scope: {target_scope}\n"
            f"Domain: {resolved_domain}\n"
            "First inspect existing knowledge metadata/body and provenance, then gather only the missing or stale evidence.\n"
            "Produce an updated synthesized page with summary, headings, related pages, and provenance candidates."
        )
        normalized_related = [slugify_page_title(item) for item in (related_slugs or []) if str(item).strip()]
        if normalized_related:
            description += "\nRelated pages:\n" + "\n".join(f"- {item}" for item in normalized_related[:10])
        if evidence:
            description += "\nEvidence hints:\n" + "\n".join(f"- {hint}" for hint in evidence[:10])
        existing = None
        if hasattr(self.storage, "session"):
            existing = (
                self.storage.session.query(TaskModel)
                .filter(TaskModel.title == f"Update Knowledge: {title}")
                .filter(TaskModel.type == TaskType.RESEARCH)
                .filter(TaskModel.state.in_([TaskState.PENDING, TaskState.WORKING, TaskState.PUSHED, TaskState.BLOCKED]))
                .order_by(desc(TaskModel.updated_at))
                .first()
            )
        elif hasattr(self.storage, "tasks") and hasattr(self.storage.tasks, "created"):
            for candidate in reversed(list(self.storage.tasks.created or [])):
                candidate_constraints = dict(getattr(candidate, "constraints", {}) or {})
                candidate_type = getattr(getattr(candidate, "type", None), "value", getattr(candidate, "type", ""))
                candidate_state = getattr(getattr(candidate, "state", None), "value", getattr(candidate, "state", ""))
                if (
                    str(getattr(candidate, "title", "") or "").strip() == f"Update Knowledge: {title}"
                    and str(candidate_type or "").strip() == TaskType.RESEARCH.value
                    and str(candidate_state or "").strip().lower() in {
                        TaskState.PENDING.value.lower(),
                        TaskState.WORKING.value.lower(),
                        TaskState.PUSHED.value.lower(),
                        TaskState.BLOCKED.value.lower(),
                    }
                    and str(candidate_constraints.get("knowledge_slug") or "").strip() == normalized_slug
                    and str(candidate_constraints.get("knowledge_operation") or "").strip() == str(operation or "").strip()
                ):
                    existing = candidate
                    break
        if existing is not None:
            existing_constraints = dict(existing.constraints or {})
            if (
                str(existing_constraints.get("knowledge_slug") or "").strip() == normalized_slug
                and str(existing_constraints.get("knowledge_operation") or "").strip() == str(operation or "").strip()
            ):
                merged_hints = list(dict.fromkeys([*(existing_constraints.get("evidence_hints") or []), *(evidence or [])]))
                merged_related = list(dict.fromkeys([*(existing_constraints.get("related_knowledge_slugs") or []), *normalized_related]))
                existing_constraints["evidence_hints"] = merged_hints
                existing_constraints["related_knowledge_slugs"] = merged_related
                existing_constraints["reason"] = reason
                existing.constraints = existing_constraints
                existing.description = description
                return existing
        task = self.storage.tasks.create(
            title=f"Update Knowledge: {title}",
            description=description,
            session_id=session_id,
            state=TaskState.PENDING,
            constraints={
                "target_scope": target_scope,
                "knowledge_operation": operation,
                "knowledge_slug": normalized_slug,
                "knowledge_domain": resolved_domain,
                "reason": reason,
                "evidence_hints": evidence or [],
                "related_knowledge_slugs": normalized_related,
                "provenance": dict(provenance or {}),
            },
        )
        task.type = TaskType.RESEARCH
        return task
