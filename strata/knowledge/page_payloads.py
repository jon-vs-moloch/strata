"""
@module knowledge.page_payloads
@purpose Normalize and shape synthesized knowledge page payloads.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


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


def normalize_domain(raw: Optional[str]) -> str:
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


def extract_toc(body: str) -> List[Dict[str, Any]]:
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


def build_summary(body: str, fallback_title: str) -> str:
    for block in re.split(r"\n\s*\n", body.strip()):
        cleaned = " ".join(block.strip().split())
        if not cleaned or cleaned.startswith("#"):
            continue
        return cleaned[:280]
    return fallback_title[:280]


def normalize_tags(tags: Optional[List[str]]) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for tag in tags or []:
        cleaned = str(tag).strip().lower()
        if not cleaned or cleaned in seen:
            continue
        normalized.append(cleaned)
        seen.add(cleaned)
    return normalized


def normalize_aliases(aliases: Optional[List[str]]) -> List[str]:
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


def normalize_links(slugs: Optional[List[str]]) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for slug in slugs or []:
        cleaned = slugify_page_title(str(slug))
        if cleaned in seen:
            continue
        normalized.append(cleaned)
        seen.add(cleaned)
    return normalized


def compact_provenance(provenance: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
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
    summary = str(payload.get("summary") or build_summary(body, title))
    toc = payload.get("toc") if isinstance(payload.get("toc"), list) else extract_toc(body)
    provenance = payload.get("provenance") if isinstance(payload.get("provenance"), list) else []
    provenance, archived_provenance_summary = compact_provenance(provenance)
    domain = normalize_domain(payload.get("domain"))
    visibility_policy = str(payload.get("visibility_policy") or DEFAULT_VISIBILITY_BY_DOMAIN[domain])
    return {
        "slug": normalized_slug,
        "title": title,
        "summary": summary,
        "body": body,
        "toc": toc,
        "tags": normalize_tags(payload.get("tags")),
        "aliases": normalize_aliases(payload.get("aliases")),
        "related_pages": normalize_links(payload.get("related_pages")),
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


def split_sections(body: str) -> Dict[str, str]:
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
