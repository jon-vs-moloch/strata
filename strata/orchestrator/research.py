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
Your current tools: list_directory, read_file, search_web, write_library_file.

[LIBRARY STRUCTURE]
- As you find complete atomic findings, you MUST use `write_library_file` to save them locally into the `.knowledge/` memory store.
- Enforce a clean library structure: small, atomic files. ALWAYS include YAML metadata (title, subjects, tags) at the top.
- Use [[Wikilinks]] to cross-reference other documents you create.

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
    def __init__(self, model_adapter, storage_manager):
        """
        @summary Initialize the ResearchModule.
        @inputs model_adapter instance, storage_manager instance
        @outputs none
        """
        self.model = model_adapter
        self.storage = storage_manager

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
