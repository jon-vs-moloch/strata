"""
@module knowledge.pages
@purpose Manage synthesized knowledge pages separately from raw `.knowledge/` notes.
@owns metadata-first retrieval, page normalization, provenance-aware upserts
@does_not_own raw note archival, background research execution
@key_exports KnowledgePageStore, slugify_page_title
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from strata.knowledge.page_access import build_access_state, sanitize_for_audience
from strata.storage.models import TaskState, TaskType


KNOWLEDGE_PAGE_INDEX_KEY = "knowledge_pages:index"
KNOWLEDGE_PAGE_KEY_PREFIX = "knowledge_page:"
KNOWLEDGE_PAGE_MIRROR_DIR = Path("docs/spec/kb")
DEFAULT_DOMAIN = "project"
MAX_INLINE_PROVENANCE = 20
DEFAULT_VISIBILITY_BY_DOMAIN = {
    "system": "agent_internal",
    "agent": "agent_internal",
    "user": "restricted",
    "contacts": "restricted",
    "project": "project_scoped",
    "world": "shareable",
}

def _normalize_domain(raw: Optional[str]) -> str:
    cleaned = str(raw or DEFAULT_DOMAIN).strip().lower()
    if cleaned in DEFAULT_VISIBILITY_BY_DOMAIN:
        return cleaned
    return DEFAULT_DOMAIN


def _default_disclosure_rules(domain: str, visibility_policy: str) -> Dict[str, Any]:
    return {
        "can_quote": visibility_policy in {"user_visible", "shareable", "project_scoped"},
        "can_summarize": True,
        "usable_for_personalization": domain in {"agent", "user"},
        "allowed_audiences": [visibility_policy],
        "tool_access": "restricted" if domain in {"user", "contacts"} else "allowed",
    }


def _normalize_disclosure_rules(raw: Any, *, domain: str, visibility_policy: str) -> Dict[str, Any]:
    base = _default_disclosure_rules(domain, visibility_policy)
    if not isinstance(raw, dict):
        return base
    normalized = dict(base)
    normalized.update(raw)
    normalized["allowed_audiences"] = [str(item) for item in normalized.get("allowed_audiences") or [visibility_policy]]
    normalized["can_quote"] = bool(normalized.get("can_quote", base["can_quote"]))
    normalized["can_summarize"] = bool(normalized.get("can_summarize", True))
    normalized["usable_for_personalization"] = bool(
        normalized.get("usable_for_personalization", base["usable_for_personalization"])
    )
    normalized["tool_access"] = str(normalized.get("tool_access") or base["tool_access"])
    return normalized


def slugify_page_title(raw: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (raw or "").lower()).strip("-")
    return slug[:80] or "untitled"


def _extract_toc(body: str) -> List[Dict[str, Any]]:
    toc: List[Dict[str, Any]] = []
    for line in body.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            continue
        level = len(match.group(1))
        title = match.group(2).strip()
        toc.append(
            {
                "title": title,
                "level": level,
                "anchor": slugify_page_title(title),
            }
        )
    return toc


def _build_summary(body: str, fallback_title: str) -> str:
    for block in re.split(r"\n\s*\n", body.strip()):
        cleaned = " ".join(block.strip().split())
        if not cleaned or cleaned.startswith("#"):
            continue
        return cleaned[:280]
    return fallback_title[:280]


def _normalize_tags(tags: Optional[List[str]]) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for tag in tags or []:
        cleaned = str(tag).strip().lower()
        if not cleaned or cleaned in seen:
            continue
        normalized.append(cleaned)
        seen.add(cleaned)
    return normalized


def _normalize_aliases(aliases: Optional[List[str]]) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for alias in aliases or []:
        cleaned = str(alias).strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        normalized.append(cleaned)
        seen.add(key)
    return normalized


def _normalize_links(slugs: Optional[List[str]]) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for slug in slugs or []:
        cleaned = slugify_page_title(str(slug))
        if cleaned in seen:
            continue
        normalized.append(cleaned)
        seen.add(cleaned)
    return normalized


def _page_key(slug: str) -> str:
    return f"{KNOWLEDGE_PAGE_KEY_PREFIX}{slugify_page_title(slug)}"


def _compact_provenance(provenance: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if len(provenance) <= MAX_INLINE_PROVENANCE:
        return provenance, {}
    kept = provenance[-MAX_INLINE_PROVENANCE:]
    archived = provenance[:-MAX_INLINE_PROVENANCE]
    return kept, {
        "archived_count": len(archived),
        "latest_archived_at": archived[-1].get("recorded_at") or archived[-1].get("modified_at") or "",
        "summary": f"{len(archived)} older provenance entries compacted out of the hot thread.",
    }


def normalize_page_payload(payload: Any, *, slug: Optional[str] = None) -> Dict[str, Any]:
    if not isinstance(payload, dict) or not payload:
        return {}
    normalized_slug = slugify_page_title(slug or str(payload.get("slug") or payload.get("title") or "untitled"))
    title = str(payload.get("title") or normalized_slug.replace("-", " ").title())
    body = str(payload.get("body") or "")
    summary = str(payload.get("summary") or _build_summary(body, title))
    toc = payload.get("toc") if isinstance(payload.get("toc"), list) else _extract_toc(body)
    provenance = payload.get("provenance") if isinstance(payload.get("provenance"), list) else []
    provenance, archived_provenance_summary = _compact_provenance(provenance)
    domain = _normalize_domain(payload.get("domain"))
    visibility_policy = str(payload.get("visibility_policy") or DEFAULT_VISIBILITY_BY_DOMAIN[domain])
    return {
        "slug": normalized_slug,
        "title": title,
        "summary": summary,
        "body": body,
        "toc": toc,
        "tags": _normalize_tags(payload.get("tags")),
        "aliases": _normalize_aliases(payload.get("aliases")),
        "related_pages": _normalize_links(payload.get("related_pages")),
        "provenance": provenance,
        "archived_provenance_summary": payload.get("archived_provenance_summary") or archived_provenance_summary,
        "source_count": len(provenance),
        "confidence": float(payload.get("confidence", 0.5) or 0.5),
        "created_by": str(payload.get("created_by") or "system"),
        "updated_reason": str(payload.get("updated_reason") or "upsert"),
        "domain": domain,
        "visibility_policy": visibility_policy,
        "disclosure_rules": _normalize_disclosure_rules(
            payload.get("disclosure_rules"),
            domain=domain,
            visibility_policy=visibility_policy,
        ),
        "scope_id": str(payload.get("scope_id") or ""),
        "project_id": str(payload.get("project_id") or ""),
        "owner_id": str(payload.get("owner_id") or ""),
        "retention_policy": str(payload.get("retention_policy") or "persistent"),
        "last_updated": str(payload.get("last_updated") or datetime.now(timezone.utc).isoformat()),
        "word_count": len(body.split()),
    }


def _split_sections(body: str) -> Dict[str, str]:
    sections: Dict[str, List[str]] = {}
    current_heading = "introduction"
    sections[current_heading] = []
    for line in body.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            current_heading = slugify_page_title(match.group(2))
            sections.setdefault(current_heading, [])
        sections.setdefault(current_heading, []).append(line)
    return {key: "\n".join(lines).strip() for key, lines in sections.items() if any(line.strip() for line in lines)}


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

    def get_page_view(self, slug: str, *, audience: str = "operator") -> Dict[str, Any]:
        payload = self.storage.parameters.peek_parameter(_page_key(slug), default_value={}) or {}
        page = normalize_page_payload(payload, slug=slug)
        return build_access_state(page=page, audience=audience, include_body=True)

    def get_page_metadata_view(self, slug: str, *, audience: str = "operator") -> Dict[str, Any]:
        payload = self.storage.parameters.peek_parameter(_page_key(slug), default_value={}) or {}
        page = normalize_page_payload(payload, slug=slug)
        return build_access_state(page=page, audience=audience, include_body=False)

    def get_page_section_view(self, slug: str, heading: str, *, audience: str = "operator") -> Dict[str, Any]:
        view = self.get_page_view(slug, audience=audience)
        if view.get("status") not in {"ok", "redacted"}:
            return view
        page = view.get("page") or {}
        sections = _split_sections(page.get("body", ""))
        section_key = slugify_page_title(heading)
        return {
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
        lowered_domain = _normalize_domain(domain) if domain else ""
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
        scope_id: str = "",
        project_id: str = "",
        owner_id: str = "",
        retention_policy: str = "persistent",
    ) -> Dict[str, Any]:
        normalized_slug = slugify_page_title(slug or title)
        existing = self.get_page(normalized_slug)
        normalized_domain = _normalize_domain(domain or existing.get("domain"))
        payload = normalize_page_payload(
            {
                "slug": normalized_slug,
                "title": title or existing.get("title") or normalized_slug.replace("-", " ").title(),
                "body": body,
                "summary": summary or existing.get("summary"),
                "tags": tags if tags is not None else existing.get("tags"),
                "aliases": aliases if aliases is not None else existing.get("aliases"),
                "related_pages": related_pages if related_pages is not None else existing.get("related_pages"),
                "provenance": provenance if provenance is not None else existing.get("provenance"),
                "confidence": confidence if confidence is not None else existing.get("confidence", 0.5),
                "created_by": created_by or existing.get("created_by", "system"),
                "updated_reason": updated_reason,
                "domain": normalized_domain,
                "visibility_policy": visibility_policy or existing.get("visibility_policy"),
                "disclosure_rules": disclosure_rules if disclosure_rules is not None else existing.get("disclosure_rules"),
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
    ):
        normalized_slug = slugify_page_title(slug)
        page = self.get_page(normalized_slug)
        title = page.get("title") or normalized_slug.replace("-", " ").title()
        resolved_domain = _normalize_domain(domain or page.get("domain"))
        description = (
            f"Update the knowledge page '{title}' ({normalized_slug}).\n"
            f"Reason: {reason}\n"
            f"Target scope: {target_scope}\n"
            f"Domain: {resolved_domain}\n"
            "First inspect existing knowledge metadata/body and provenance, then gather only the missing or stale evidence.\n"
            "Produce an updated synthesized page with summary, headings, related pages, and provenance candidates."
        )
        if evidence:
            description += "\nEvidence hints:\n" + "\n".join(f"- {hint}" for hint in evidence[:10])
        task = self.storage.tasks.create(
            title=f"Update Knowledge: {title}",
            description=description,
            session_id=session_id,
            state=TaskState.PENDING,
            constraints={
                "target_scope": target_scope,
                "knowledge_operation": "update_page",
                "knowledge_slug": normalized_slug,
                "knowledge_domain": resolved_domain,
                "reason": reason,
                "evidence_hints": evidence or [],
            },
        )
        task.type = TaskType.RESEARCH
        return task
