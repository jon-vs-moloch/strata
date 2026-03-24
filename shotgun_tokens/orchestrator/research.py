"""
@module orchestrator.research
@purpose Gather and synthesize contextual information prior to task execution or decomposition.
@owns metadata retrieval, documentation search, context synthesis
@does_not_own LLM API interactions directly (uses ModelAdapter), or task state mutations
@key_exports ResearchModule
@side_effects none
"""

from typing import Dict, Any, Optional
from shotgun_tokens.schemas.core import ResearchReport

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

    async def conduct_research(self, task_description: str, repo_path: Optional[str] = None, target_scope: str = "codebase") -> ResearchReport:
        """
        @summary Autonomous agent loop for research. Decomposes the task, queries the web/codebase iteratively, and synthesizes.
        """
        import os
        import json
        import httpx
        import re
        
        print(f"Starting autonomous research loop for: {task_description[:50]}...")
        root = repo_path or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        
        RESEARCH_TOOLS = [
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
                "content": f"""You are an Expert Research Agent building a persistent knowledge library.
Your primary goal is to decompose the user's research task and iteratively gather data using your tools (search_web and read_file).
As you find complete atomic findings, you MUST use `write_library_file` to save them locally into the `.knowledge/` memory store.
Enforce a clean library structure: small, atomic files. ALWAYS include YAML metadata (title, subjects, tags) at the top of your files, and use [[Wikilinks]] to reference other documents you create.
When you have collected enough comprehensive information across all sources and saved your atomic notes, call 'finalize_research' with a high-level synthesized report to end the loop.
You are currently focused heavily on: {target_scope.upper()} scope."""
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
                    try:
                        with open(full_path, "r", encoding="utf-8") as f:
                            tool_result = f.read()[:2000] # Cap file read to 2k chars
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
                messages.append({"role": "user", "content": "You MUST call a tool. If you are done, call finalize_research."})
                
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
