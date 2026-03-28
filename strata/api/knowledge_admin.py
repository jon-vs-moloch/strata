"""
@module api.knowledge_admin
@purpose Register knowledge-base maintenance endpoints separately from the main API assembly.

Knowledge compaction, page browsing, and knowledge-maintenance task creation are
their own operational surface. Keeping them isolated makes it easier for small
models to navigate the knowledge system without loading chat or worker control.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException


def register_knowledge_admin_routes(
    app,
    *,
    get_storage,
    base_dir: str,
    knowledge_page_store_cls,
    slugify_page_title,
    worker,
) -> Dict[str, Any]:
    exported: Dict[str, Any] = {}

    @app.post("/admin/knowledge/compact")
    async def compact_knowledge_base():
        script_path = os.path.join(base_dir, "scripts", "compact_knowledge.py")
        result = subprocess.run(
            ["./venv/bin/python", script_path],
            cwd=base_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        return {"status": "ok", "report": json.loads(result.stdout)}

    @app.get("/admin/knowledge/pages")
    async def list_knowledge_pages(
        query: Optional[str] = None,
        tag: Optional[str] = None,
        domain: Optional[str] = None,
        audience: str = "user",
        limit: int = 50,
        storage=Depends(get_storage),
    ):
        pages = knowledge_page_store_cls(storage).list_pages(
            query=query,
            tag=tag,
            domain=domain,
            audience=audience,
            limit=limit,
        )
        return {"status": "ok", "pages": pages}

    @app.get("/admin/knowledge/maintenance")
    async def get_knowledge_maintenance(storage=Depends(get_storage)):
        report = knowledge_page_store_cls(storage).get_maintenance_report()
        return {"status": "ok", "report": report}

    @app.get("/admin/knowledge/pages/{slug}/metadata")
    async def get_knowledge_page_metadata(slug: str, audience: str = "user", storage=Depends(get_storage)):
        page = knowledge_page_store_cls(storage).get_page_metadata(slug, audience=audience)
        if not page:
            raise HTTPException(status_code=404, detail="Knowledge page not found")
        return {"status": "ok", "page": page}

    @app.get("/admin/knowledge/pages/{slug}")
    async def get_knowledge_page(slug: str, audience: str = "user", storage=Depends(get_storage)):
        page = knowledge_page_store_cls(storage).get_page(slug, audience=audience)
        if not page:
            raise HTTPException(status_code=404, detail="Knowledge page not found")
        return {"status": "ok", "page": page}

    @app.get("/admin/knowledge/pages/{slug}/section")
    async def get_knowledge_page_section(slug: str, heading: str, audience: str = "user", storage=Depends(get_storage)):
        section = knowledge_page_store_cls(storage).get_page_section(slug, heading, audience=audience)
        if not section:
            raise HTTPException(status_code=404, detail="Knowledge page not found")
        return {"status": "ok", "section": section}

    @app.post("/admin/knowledge/pages")
    async def upsert_knowledge_page(payload: Dict[str, Any], storage=Depends(get_storage)):
        title = str(payload.get("title") or "").strip()
        body = str(payload.get("body") or "").strip()
        if not title or not body:
            raise HTTPException(status_code=400, detail="title and body are required")
        page = knowledge_page_store_cls(storage).upsert_page(
            slug=payload.get("slug"),
            title=title,
            body=body,
            summary=payload.get("summary"),
            tags=payload.get("tags"),
            aliases=payload.get("aliases"),
            related_pages=payload.get("related_pages"),
            provenance=payload.get("provenance"),
            confidence=float(payload.get("confidence", 0.5) or 0.5),
            created_by=str(payload.get("created_by") or "api"),
            updated_reason=str(payload.get("updated_reason") or "manual_upsert"),
            domain=str(payload.get("domain") or "project"),
            visibility_policy=payload.get("visibility_policy"),
            disclosure_rules=payload.get("disclosure_rules"),
            scope_id=str(payload.get("scope_id") or ""),
            project_id=str(payload.get("project_id") or ""),
            owner_id=str(payload.get("owner_id") or ""),
            retention_policy=str(payload.get("retention_policy") or "persistent"),
        )
        storage.commit()
        return {"status": "ok", "page": page}

    @app.post("/admin/knowledge/update")
    async def queue_knowledge_update(payload: Dict[str, Any], storage=Depends(get_storage)):
        slug = str(payload.get("slug") or payload.get("title") or "").strip()
        reason = str(payload.get("reason") or "").strip()
        if not slug or not reason:
            raise HTTPException(status_code=400, detail="slug/title and reason are required")
        task = knowledge_page_store_cls(storage).enqueue_update_task(
            slug=slug,
            reason=reason,
            session_id=payload.get("session_id"),
            target_scope=str(payload.get("target_scope") or "codebase"),
            evidence=[str(item) for item in (payload.get("evidence_hints") or [])],
            domain=payload.get("domain"),
        )
        storage.commit()
        await worker.enqueue(task.task_id)
        return {
            "status": "ok",
            "task_id": task.task_id,
            "knowledge_slug": slugify_page_title(slug),
        }

    exported.update(
        {
            "compact_knowledge_base": compact_knowledge_base,
            "list_knowledge_pages": list_knowledge_pages,
            "get_knowledge_maintenance": get_knowledge_maintenance,
            "get_knowledge_page_metadata": get_knowledge_page_metadata,
            "get_knowledge_page": get_knowledge_page,
            "get_knowledge_page_section": get_knowledge_page_section,
            "upsert_knowledge_page": upsert_knowledge_page,
            "queue_knowledge_update": queue_knowledge_update,
        }
    )
    return exported
