"""
@module orchestrator.research
@purpose Gather and synthesize contextual information prior to task execution or decomposition.
@owns metadata retrieval, documentation search, context synthesis
@does_not_own LLM API interactions directly (uses ModelAdapter), or task state mutations
@key_exports ResearchModule
@side_effects none
"""

from pathlib import Path
from typing import Dict, Any, Optional
import json
from strata.knowledge.pages import KnowledgePageStore
from strata.feedback.signals import register_feedback_signal
from strata.schemas.core import ResearchReport


DEFAULT_REPO_ANCHORS = [
    "README.md",
    ".knowledge/specs/constitution.md",
    ".knowledge/specs/project_spec.md",
    "docs/spec/project-philosophy.md",
    "docs/spec/codemap.md",
    "strata/api",
    "strata/eval",
    "strata/orchestrator",
    "strata/knowledge",
    "strata/storage",
]


def _build_research_system_prompt(
    target_scope: str,
    task_description: str,
    repo_snapshot: str = "",
    spec_paths: Optional[list[str]] = None,
) -> str:
    spec_lines = "\n".join(f"- {path}" for path in (spec_paths or [])) or "- None provided"
    repo_hint_block = f"\nObserved repository snapshot:\n{repo_snapshot}\n" if repo_snapshot else ""
    codebase_nudge = ""
    lower_desc = (task_description or "").lower()
    if target_scope.lower() == "codebase" or any(
        keyword in lower_desc
        for keyword in ["codebase", "repo", "repository", "alignment", "spec", "implementation"]
    ):
        anchors = "\n".join(f"- {path}" for path in DEFAULT_REPO_ANCHORS)
        codebase_nudge = f"""
[CODEBASE-FIRST BEHAVIOR]
- You DO have access to the local repository through `list_directory` and `read_file`.
- For codebase, alignment, or spec-gap tasks, start by inspecting the local repo before concluding anything is missing.
- Use `list_directory` on "." or a likely subtree, then `read_file` on concrete anchors such as:
{anchors}
- Do not say you need "access to the codebase" when the task already provides repo paths or a snapshot. Inspect the files instead.
- If the snapshot or anchor files seem incomplete, say what you inspected and what is still missing.
"""

    return f"""You are an Expert Research Agent building a persistent knowledge library.
Your primary goal is to decompose the user's research task and iteratively gather data.

[CRITICAL - TOOL USE]
To gather information or save data, you MUST use the structured tool-calling format.
If you simply say "I will call a tool" in plain text without a structured tool call, the system will reject your response.
Your current tools: list_directory, read_file, search_web, write_library_file, list_knowledge_pages, read_knowledge_page, inspect_knowledge_maintenance, propose_knowledge_merge, propose_knowledge_correction, queue_knowledge_refresh, submit_feedback_signal.

[LIBRARY STRUCTURE]
- As you find complete atomic findings, you MUST use `write_library_file` to save them locally into the `.knowledge/` memory store.
- Enforce a clean library structure: small, atomic files. ALWAYS include YAML metadata (title, subjects, tags) at the top.
- Use [[Wikilinks]] to cross-reference other documents you create.

[KNOWLEDGE MAINTENANCE]
- Before creating a brand new research note or proposing a new durable page, inspect the synthesized knowledge pages when relevant.
- If you discover overlap, contradictions, or stale claims, use the maintenance tools to propose merge/correction/refresh work rather than silently duplicating the wiki.
- Prefer durable knowledge pages for retrieval, and raw `.knowledge/` notes for draft or intermediate findings.

[LOCAL CONTEXT]
- Repository paths are relative to the repo root.
- Canonical spec paths for this task:
{spec_lines}{repo_hint_block}{codebase_nudge}

When you have collected enough comprehensive information across all sources and saved your atomic notes, call 'finalize_research' with a high-level synthesized report to end the research phase.
Focus area: {target_scope.upper()} scope."""

class ResearchModule:
    """
    @summary Executes the research phase for a given task, querying repo metadata.
    @inputs model: ModelAdapter, storage: StorageManager
    @outputs ResearchReport containing synthesized context
    @side_effects requests completions from the LLM adapter
    @depends models.adapter, schemas.core.ResearchReport
    @invariants always returns a ResearchReport regardless of findings
    """
    def __init__(self, model_adapter, storage_manager, enqueue_task=None):
        """
        @summary Initialize the ResearchModule.
        @inputs model_adapter instance, storage_manager instance
        @outputs none
        """
        self.model = model_adapter
        self.storage = storage_manager
        self.enqueue_task = enqueue_task

    async def conduct_research(
        self,
        task_description: str,
        repo_path: Optional[str] = None,
        target_scope: str = "codebase",
        context_hints: Optional[Dict[str, Any]] = None,
    ) -> ResearchReport:
        """
        @summary Autonomous agent loop for research. Decomposes the task, queries the web/codebase iteratively, and synthesizes.
        """
        import os
        import json
        import httpx
        import re
        
        print(f"Starting autonomous research loop for: {task_description[:50]}...")
        root = repo_path or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        context_hints = context_hints or {}
        repo_snapshot = str(context_hints.get("repo_snapshot") or "").strip()
        spec_paths = context_hints.get("spec_paths") or []
        
        RESEARCH_TOOLS = [
            {
                "type": "function",
                "function": {
                    "name": "list_directory",
                    "description": "List files and folders in a local repository directory. Use this first when you need to inspect what exists.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Relative path to the directory. Use '.' for repo root."
                            }
                        },
                        "required": ["path"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "search_web",
                    "description": "Search DuckDuckGo for facts, documentation, or tutorials.",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string", "description": "The search query"}},
                        "required": ["query"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read the contents of a local codebase file.",
                    "parameters": {
                        "type": "object",
                        "properties": {"filepath": {"type": "string", "description": "Relative path to the file"}},
                        "required": ["filepath"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "finalize_research",
                    "description": "Call this ONLY when you have fully answered the research goal.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "context_gathered": {"type": "string", "description": "A long paragraph detailing findings."},
                            "key_constraints_discovered": {"type": "array", "items": {"type": "string"}},
                            "suggested_approach": {"type": "string"},
                            "reference_urls": {"type": "array", "items": {"type": "string"}}
                        },
                        "required": ["context_gathered", "key_constraints_discovered", "suggested_approach", "reference_urls"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "write_library_file",
                    "description": "Write a small, atomic markdown file to the `.knowledge/` library. Use this to continuously save finalized, bite-sized components of your research to disk with searchable metadata (title, subjects, tags). Use [[Wikilinks]] to cross-reference other atomic files you create.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filename": {"type": "string", "description": "The exact name of the file to save (e.g. pattern_routing.md)"},
                            "content": {"type": "string", "description": "The complete markdown content to write, including YAML frontmatter tags."}
                        },
                        "required": ["filename", "content"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "submit_feedback_signal",
                    "description": "Register a lightweight feedback, surprise, correction, or attention signal so the system can prioritize it.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "source_type": {"type": "string", "description": "What produced or received the signal."},
                            "source_id": {"type": "string", "description": "Stable identifier for the source item."},
                            "signal_kind": {"type": "string", "description": "Kind of signal being registered."},
                            "signal_value": {"type": "string", "description": "Short label or payload for the signal."},
                            "source_preview": {"type": "string", "description": "Short excerpt or summary of the source item."},
                            "expected_outcome": {"type": "string", "description": "Optional expectation that was violated."},
                            "observed_outcome": {"type": "string", "description": "Optional observed outcome."},
                            "note": {"type": "string", "description": "Optional short explanation of why the signal matters."}
                        },
                        "required": ["source_type", "source_id", "signal_kind", "signal_value"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "list_knowledge_pages",
                    "description": "List synthesized knowledge pages by metadata so you can reuse or inspect existing wiki pages before drafting new notes.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Optional query to filter pages by title, summary, alias, or tag."},
                            "tag": {"type": "string", "description": "Optional tag filter."},
                            "domain": {"type": "string", "description": "Optional domain filter."},
                            "limit": {"type": "integer", "description": "Maximum number of page metadata results to return."}
                        }
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "read_knowledge_page",
                    "description": "Read a synthesized knowledge page or a specific section when you need durable wiki context.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "slug": {"type": "string", "description": "Knowledge page slug."},
                            "heading": {"type": "string", "description": "Optional heading to fetch a specific section only."}
                        },
                        "required": ["slug"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "inspect_knowledge_maintenance",
                    "description": "Inspect the knowledge maintenance backlog, including duplicate candidates and stale pages.",
                    "parameters": {
                        "type": "object",
                        "properties": {}
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "propose_knowledge_merge",
                    "description": "Queue a merge/canonicalization proposal when two knowledge pages overlap or should be consolidated.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "canonical_slug": {"type": "string", "description": "The page that should remain canonical."},
                            "duplicate_slug": {"type": "string", "description": "The page that may be merged into the canonical page."},
                            "reason": {"type": "string", "description": "Why the pages should be merged."},
                            "target_scope": {"type": "string", "description": "Whether to inspect the codebase or the public web.", "enum": ["codebase", "web"]},
                            "evidence_hints": {"type": "array", "items": {"type": "string"}, "description": "Optional evidence supporting the merge."}
                        },
                        "required": ["canonical_slug", "duplicate_slug", "reason"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "propose_knowledge_correction",
                    "description": "Queue a correction or contradiction-resolution proposal for a knowledge page.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "slug": {"type": "string", "description": "The page that needs correction."},
                            "reason": {"type": "string", "description": "What looks wrong, stale, or contradictory."},
                            "target_scope": {"type": "string", "description": "Whether to inspect the codebase or the public web.", "enum": ["codebase", "web"]},
                            "evidence_hints": {"type": "array", "items": {"type": "string"}, "description": "Optional evidence supporting the correction."},
                            "related_slugs": {"type": "array", "items": {"type": "string"}, "description": "Optional other pages involved in the conflict."}
                        },
                        "required": ["slug", "reason"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "queue_knowledge_refresh",
                    "description": "Queue a freshness refresh for a knowledge page when its source evidence may have changed.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "slug": {"type": "string", "description": "The page that should be refreshed."},
                            "reason": {"type": "string", "description": "Why the page may be stale."},
                            "target_scope": {"type": "string", "description": "Whether to inspect the codebase or the public web.", "enum": ["codebase", "web"]},
                            "evidence_hints": {"type": "array", "items": {"type": "string"}, "description": "Optional evidence supporting the refresh."}
                        },
                        "required": ["slug", "reason"]
                    }
                }
            }
        ]

        messages = [
            {
                "role": "system",
                "content": _build_research_system_prompt(
                    target_scope=target_scope,
                    task_description=task_description,
                    repo_snapshot=repo_snapshot,
                    spec_paths=spec_paths,
                ),
            },
            {
                "role": "user",
                "content": f"RESEARCH TASK: {task_description}\\nPlease begin your research. Call tools to gather data."
            }
        ]

        # Use the centralized dynamic parameter telemetry module
        max_iterations = self.storage.parameters.get_parameter(
            key="max_research_iterations", 
            default_value=6, 
            description="The maximum number of recursive LLM queries a single background research agent is allowed to execute before timing out."
        )
        
        final_report_data = None
        knowledge_pages = KnowledgePageStore(self.storage)
        
        for iteration in range(max_iterations):
            print(f"Research Loop Iteration {iteration+1}/{max_iterations}")
            response = await self.model.chat(messages, tools=RESEARCH_TOOLS)
            
            if response.get("status") == "error":
                err_msg = response.get("message", "Unknown model adapter error.")
                raise Exception(f"Research loop aborted: Model adapter returned error: {err_msg}")

            tool_calls = response.get("tool_calls")
            if tool_calls and isinstance(tool_calls, list) and len(tool_calls) > 0:
                call = tool_calls[0]
                func_name = call.get("function", {}).get("name")
                try:
                    args = json.loads(call.get("function", {}).get("arguments", "{}"))
                except:
                    args = {}
                    
                if func_name == "finalize_research":
                    final_report_data = args
                    break
                    
                elif func_name == "list_directory":
                    rel_path = args.get("path", ".") or "."
                    print(f"  -> Research Agent listing directory: {rel_path}")
                    try:
                        directory = (Path(root) / rel_path).resolve()
                        repo_root = Path(root).resolve()
                        directory.relative_to(repo_root)
                        if not directory.exists():
                            tool_result = f"Directory does not exist: {rel_path}"
                        elif not directory.is_dir():
                            tool_result = f"Path is not a directory: {rel_path}"
                        else:
                            children = sorted(
                                child.name + ("/" if child.is_dir() else "")
                                for child in directory.iterdir()
                                if not child.name.startswith(".")
                            )
                            preview = children[:80]
                            suffix = "\n... truncated ..." if len(children) > 80 else ""
                            tool_result = "\n".join(preview) + suffix if preview else "(empty directory)"
                    except Exception as e:
                        tool_result = f"Directory listing failed: {e}"

                    messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
                    messages.append({"role": "tool", "content": tool_result, "tool_call_id": call.get("id", "call_1")})

                elif func_name == "search_web":
                    query = args.get("query", "python")
                    print(f"  -> Research Agent searching web for: {query}")
                    try:
                        async with httpx.AsyncClient() as client:
                            resp = await client.get(
                                "https://html.duckduckgo.com/html/",
                                params={"q": query},
                                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/91.0.4472.124"},
                                timeout=8.0
                            )
                            resp.raise_for_status()
                            snippets = re.findall(r'<a class="result__snippet[^>]*>(.*?)</a>', resp.text, re.IGNORECASE | re.DOTALL)
                            results = [re.sub('<[^<]+>', '', s).strip() for s in snippets[:4]]
                            tool_result = "\\n".join(f"- {r}" for r in results) if results else "No snippets found."
                    except Exception as e:
                        tool_result = f"Search failed: {e}"
                        
                    messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
                    messages.append({"role": "tool", "content": tool_result, "tool_call_id": call.get("id", "call_1")})
                    
                elif func_name == "read_file":
                    filepath = args.get("filepath", "")
                    print(f"  -> Research Agent reading file: {filepath}")
                    full_path = os.path.join(root, filepath)
                    
                    async def fetch_raw_content():
                        with open(full_path, "r", encoding="utf-8") as f:
                            return f.read()
                    
                    try:
                        # Apply Progressive Disclosure Wrapper
                        tool_result = await self.storage.get_resource_summary(
                            resource_id=filepath,
                            raw_content_callback=fetch_raw_content,
                            model_adapter=self.model
                        )
                    except Exception as e:
                        tool_result = f"File read failed: {e}"
                        
                    messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
                    messages.append({"role": "tool", "content": tool_result, "tool_call_id": call.get("id", "call_1")})

                elif func_name == "write_library_file":
                    fname = args.get("filename", "untitled.md")
                    content = args.get("content", "")
                    kb_dir = os.path.join(root, ".knowledge")
                    os.makedirs(kb_dir, exist_ok=True)
                    try:
                        with open(os.path.join(kb_dir, fname), "w", encoding="utf-8") as f:
                            f.write(content)
                        tool_result = f"Successfully wrote {fname} to .knowledge library."
                        print(f"  -> Research Agent saved atomic note: {fname}")
                    except Exception as e:
                        tool_result = f"Failed to write file: {e}"
                        
                    messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
                    messages.append({"role": "tool", "content": tool_result, "tool_call_id": call.get("id", "call_1")})
                elif func_name == "list_knowledge_pages":
                    pages = knowledge_pages.list_pages(
                        query=args.get("query"),
                        tag=args.get("tag"),
                        domain=args.get("domain"),
                        audience="operator",
                        limit=int(args.get("limit") or 8),
                    )
                    if not pages:
                        tool_result = "No synthesized knowledge pages matched that query."
                    else:
                        tool_result = "Knowledge Page Metadata:\n" + "\n".join(
                            f"- {page.get('slug')}: {page.get('title')} | summary={page.get('summary')} | "
                            f"domain={page.get('domain')} | maintenance={page.get('maintenance', {}).get('freshness_status', 'unknown')} | "
                            f"last_updated={page.get('last_updated')}"
                            for page in pages
                        )
                    messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
                    messages.append({"role": "tool", "content": tool_result, "tool_call_id": call.get("id", "call_1")})
                elif func_name == "read_knowledge_page":
                    slug = str(args.get("slug") or "")
                    heading = str(args.get("heading") or "").strip()
                    if heading:
                        section = knowledge_pages.get_page_section(slug, heading, audience="operator")
                        tool_result = section.get("content") or f"No section '{heading}' found in knowledge page '{slug}'."
                    else:
                        page = knowledge_pages.get_page(slug, audience="operator")
                        tool_result = page.get("body") or f"No synthesized knowledge page found for '{slug}'."
                    messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
                    messages.append({"role": "tool", "content": tool_result, "tool_call_id": call.get("id", "call_1")})
                elif func_name == "inspect_knowledge_maintenance":
                    report = knowledge_pages.get_maintenance_report()
                    tool_result = json.dumps(report, indent=2) if report else "No knowledge maintenance report is available yet."
                    messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
                    messages.append({"role": "tool", "content": tool_result, "tool_call_id": call.get("id", "call_1")})
                elif func_name == "submit_feedback_signal":
                    signal = register_feedback_signal(
                        self.storage,
                        source_type=str(args.get("source_type") or "system"),
                        source_id=str(args.get("source_id") or task_description[:32]),
                        signal_kind=str(args.get("signal_kind") or "highlight"),
                        signal_value=str(args.get("signal_value") or ""),
                        source_actor="researcher",
                        session_id="",
                        source_preview=str(args.get("source_preview") or task_description),
                        note=str(args.get("note") or ""),
                        expected_outcome=str(args.get("expected_outcome") or ""),
                        observed_outcome=str(args.get("observed_outcome") or ""),
                        metadata={"module": "research"},
                    )
                    self.storage.commit()
                    tool_result = json.dumps(signal, indent=2)
                    messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
                    messages.append({"role": "tool", "content": tool_result, "tool_call_id": call.get("id", "call_1")})
                elif func_name == "propose_knowledge_merge":
                    canonical_slug = str(args.get("canonical_slug") or "")
                    duplicate_slug = str(args.get("duplicate_slug") or "")
                    reason = str(args.get("reason") or "possible duplicate knowledge pages")
                    task = knowledge_pages.enqueue_update_task(
                        slug=canonical_slug,
                        reason=f"[merge] {reason}",
                        target_scope=str(args.get("target_scope") or target_scope or "codebase"),
                        evidence=[str(item) for item in (args.get("evidence_hints") or [])],
                        operation="knowledge_merge",
                        related_slugs=[duplicate_slug],
                    )
                    self.storage.commit()
                    if self.enqueue_task:
                        await self.enqueue_task(task.task_id)
                    tool_result = (
                        f"Queued knowledge merge proposal task {task.task_id} to evaluate '{canonical_slug}' "
                        f"against duplicate candidate '{duplicate_slug}'."
                    )
                    messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
                    messages.append({"role": "tool", "content": tool_result, "tool_call_id": call.get("id", "call_1")})
                elif func_name == "propose_knowledge_correction":
                    slug = str(args.get("slug") or "")
                    reason = str(args.get("reason") or "possible knowledge correction needed")
                    task = knowledge_pages.enqueue_update_task(
                        slug=slug,
                        reason=f"[correction] {reason}",
                        target_scope=str(args.get("target_scope") or target_scope or "codebase"),
                        evidence=[str(item) for item in (args.get("evidence_hints") or [])],
                        operation="knowledge_correction",
                        related_slugs=[str(item) for item in (args.get("related_slugs") or [])],
                    )
                    self.storage.commit()
                    if self.enqueue_task:
                        await self.enqueue_task(task.task_id)
                    tool_result = f"Queued knowledge correction task {task.task_id} for '{slug}'."
                    messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
                    messages.append({"role": "tool", "content": tool_result, "tool_call_id": call.get("id", "call_1")})
                elif func_name == "queue_knowledge_refresh":
                    slug = str(args.get("slug") or "")
                    reason = str(args.get("reason") or "page may be stale")
                    task = knowledge_pages.enqueue_update_task(
                        slug=slug,
                        reason=f"[refresh] {reason}",
                        target_scope=str(args.get("target_scope") or target_scope or "codebase"),
                        evidence=[str(item) for item in (args.get("evidence_hints") or [])],
                        operation="knowledge_refresh",
                    )
                    self.storage.commit()
                    if self.enqueue_task:
                        await self.enqueue_task(task.task_id)
                    tool_result = f"Queued knowledge refresh task {task.task_id} for '{slug}'."
                    messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
                    messages.append({"role": "tool", "content": tool_result, "tool_call_id": call.get("id", "call_1")})
            else:
                # LLM failed to call a tool, force it
                messages.append({"role": "assistant", "content": response.get("content", "")})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "You MUST call a tool. For codebase or alignment tasks, inspect the local repository with "
                            "`list_directory` or `read_file` before saying anything is missing. If you are done, call finalize_research."
                        ),
                    }
                )
                
        from datetime import datetime
        kb_dir = os.path.join(root, ".knowledge")
        os.makedirs(kb_dir, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        
        if not final_report_data:
            # Fallback if loop exhausted: Save the WIP trace and THROW so it triggers a failover!
            wip_file = os.path.join(kb_dir, f"wip_research_{ts}.md")
            with open(wip_file, "w", encoding="utf-8") as f:
                f.write(f"# WIP Research Dump: {task_description}\\n\\n")
                for m in messages:
                    if m.get("content"):
                         f.write(f"**{m['role'].upper()}**: {m['content']}\\n\\n")
            
            raise Exception(f"Agent iteration limit reached. Partial context saved to durable `.knowledge` library at: {wip_file}")

        # Telemetry: If it successfully finalized, log a success for the parameter!
        try:
            self.storage.parameters.record_success("max_research_iterations")
            self.storage.commit()
        except Exception:
            pass

        # Save Finalized Research to Knowledge Library
        final_file = os.path.join(kb_dir, f"final_research_{ts}.md")
        with open(final_file, "w", encoding="utf-8") as f:
            f.write(f"# 🧠 Final Research Report\\n**Target**: {task_description}\\n\\n")
            f.write(f"### Context Gathered\\n{final_report_data.get('context_gathered', 'Inconclusive')}\\n\\n")
            f.write(f"### Key Constraints\\n{final_report_data.get('key_constraints_discovered', [])}\\n\\n")
            f.write(f"### Suggested Approach\\n{final_report_data.get('suggested_approach', 'Standard best practices')}\\n")
            f.write(f"### Sources\\n{final_report_data.get('reference_urls', [])}\\n")

        return ResearchReport(
            context_gathered=final_report_data.get("context_gathered", "Analysis was inconclusive."),
            key_constraints_discovered=final_report_data.get("key_constraints_discovered", []),
            suggested_approach=final_report_data.get("suggested_approach", "Proceed with standard best practices."),
            reference_urls=final_report_data.get("reference_urls", [])
        )
