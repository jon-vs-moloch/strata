"""
@module knowledge.page_access
@purpose Access policy and audience-aware filtering for synthesized knowledge pages.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


USER_VISIBLE_POLICIES = {"shareable", "user_visible", "project_scoped"}
AGENT_VISIBLE_POLICIES = {"shareable", "user_visible", "project_scoped", "agent_internal", "restricted"}
TOOL_VISIBLE_POLICIES = {"shareable", "project_scoped", "agent_internal"}


def normalize_audience(raw: Optional[str]) -> str:
    cleaned = str(raw or "operator").strip().lower()
    if cleaned in {"operator", "agent", "user", "tool"}:
        return cleaned
    return "operator"


def has_metadata_access(page: Dict[str, Any], *, audience: str) -> bool:
    normalized_audience = normalize_audience(audience)
    if normalized_audience == "operator":
        return True
    visibility_policy = str(page.get("visibility_policy") or "")
    disclosure_rules = page.get("disclosure_rules") or {}
    allowed_audiences = {str(item).lower() for item in disclosure_rules.get("allowed_audiences") or []}
    if normalized_audience == "agent":
        return visibility_policy in AGENT_VISIBLE_POLICIES or "agent" in allowed_audiences
    if normalized_audience == "tool":
        return (
            disclosure_rules.get("tool_access") == "allowed"
            and (visibility_policy in TOOL_VISIBLE_POLICIES or "tool" in allowed_audiences)
        )
    return visibility_policy in USER_VISIBLE_POLICIES or "user" in allowed_audiences


def sanitize_for_audience(
    page: Dict[str, Any],
    *,
    audience: str,
    include_body: bool = False,
    heading: Optional[str] = None,
) -> Dict[str, Any]:
    if not page:
        return {}
    normalized_audience = normalize_audience(audience)
    if not has_metadata_access(page, audience=normalized_audience):
        return {}
    sanitized = dict(page)
    disclosure_rules = sanitized.get("disclosure_rules") or {}
    if not include_body:
        sanitized.pop("body", None)
        return sanitized
    if normalized_audience in {"operator", "agent"} or disclosure_rules.get("can_quote", True):
        return sanitized
    if disclosure_rules.get("can_summarize", True):
        restricted_body = sanitized.get("summary") or ""
        if heading:
            restricted_body = f"Summary only for section '{heading}': {restricted_body}"
        sanitized["body"] = restricted_body
        sanitized["content_redacted"] = True
        return sanitized
    return {}


def build_access_state(
    *,
    page: Dict[str, Any],
    audience: str,
    include_body: bool,
    heading: Optional[str] = None,
) -> Dict[str, Any]:
    if not page:
        return {
            "status": "missing",
            "reason": "knowledge page not found",
            "audience": normalize_audience(audience),
            "requires_consent": False,
        }
    normalized_audience = normalize_audience(audience)
    if not has_metadata_access(page, audience=normalized_audience):
        visibility_policy = str(page.get("visibility_policy") or "")
        disclosure_rules = page.get("disclosure_rules") or {}
        return {
            "status": "restricted",
            "reason": f"page is not available to audience '{normalized_audience}' under visibility policy '{visibility_policy}'",
            "audience": normalized_audience,
            "requires_consent": visibility_policy in {"restricted", "agent_internal"},
            "page_metadata": {
                "slug": page.get("slug"),
                "title": page.get("title"),
                "domain": page.get("domain"),
                "visibility_policy": visibility_policy,
                "summary": page.get("summary"),
                "disclosure_rules": {
                    "can_quote": disclosure_rules.get("can_quote"),
                    "can_summarize": disclosure_rules.get("can_summarize"),
                    "tool_access": disclosure_rules.get("tool_access"),
                },
            },
        }
    sanitized = sanitize_for_audience(page, audience=normalized_audience, include_body=include_body, heading=heading)
    if not sanitized:
        return {
            "status": "restricted",
            "reason": "page exists but cannot be disclosed in this form",
            "audience": normalized_audience,
            "requires_consent": True,
        }
    status = "redacted" if sanitized.get("content_redacted") else "ok"
    return {
        "status": status,
        "reason": "content redacted to summary due to disclosure rules" if status == "redacted" else "",
        "audience": normalized_audience,
        "requires_consent": False,
        "page": sanitized,
    }
