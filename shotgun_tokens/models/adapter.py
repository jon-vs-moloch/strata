"""
@module models.adapter
@purpose Provide a unified interface for local model interaction (Ollama/LM Studio).
@owns LLM prompting, response normalization, JSON/YAML extraction
@does_not_own specific task business logic, DB persistence
@key_exports ModelAdapter
"""

import httpx
import json
import yaml
from typing import Dict, Any, Optional, List

class ModelAdapter:
    """
    @summary Universal bridge between the orchestrator and local inference engines.
    @inputs endpoint: local model URL
    @outputs side-effect driven (network calls) or completion strings
    @side_effects initiates HTTP POST requests to local APIs
    @depends httpx, json, yaml
    @invariants does not expose API keys if built locally (defaults to local-no-auth)
    """
    def __init__(self, endpoint: str = "http://127.0.0.1:1234/v1/chat/completions", model: str = "qwen3.5-4b-claude-4.6-opus-reasoning-distilled-v2"):
        """
        @summary Initialize the ModelAdapter.
        @inputs endpoint: local LLM completion URL, model: default model ID
        @outputs none
        """
        self.endpoint = endpoint
        self.active_model = model

    async def chat(self, messages: List[Dict[str, str]], format: str = "json", tools: Optional[List[Dict]] = None) -> Dict[str, Any]:

        """
        @summary Send a chat message list to the model and return a structured response.
        @inputs messages: context list, format: desired output ('json' or 'yaml'), tools: optional OpenAI tools array
        @outputs dictionary representing the model's structured decision
        @side_effects makes network calls to the model provider
        """
        print(f"Calling local model at {self.endpoint}...")
        
        async with httpx.AsyncClient() as client:
            try:
                payload = {
                    "model": self.active_model, 
                    "messages": messages, 
                    "stream": False,
                    "temperature": 0.7
                }
                if tools:
                    payload["tools"] = tools

                # Use a long read-timeout for reasoning models (they can think for many seconds)
                # but keep the connect-timeout short so a dead server fails fast.
                timeout = httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=5.0)
                response = await client.post(self.endpoint, json=payload, timeout=timeout)
                response.raise_for_status()
                result = response.json()
                
                message_obj = result.get("choices", [{}])[0].get("message", {})
                content = message_obj.get("content", "")
                tool_calls = message_obj.get("tool_calls", None)
                
                # Fallback: Some models (like M37) inject tool calls into the content as XML blocks 
                # instead of returning a proper tool_calls array. Let's parse them out.
                if not tool_calls and "<tool_call>" in content and "</tool_call>" in content:
                    import re
                    # Extract the JSON block between the tags
                    match = re.search(r"<tool_call>\s*(.*?)\s*</tool_call>", content, re.DOTALL | re.IGNORECASE)
                    if match:
                        try:
                            # It should be a JSON string like {"name": "search_web", "arguments": {...}}
                            parsed_tool = json.loads(match.group(1).strip())
                            
                            # Normalize it into the standard OpenAI tool_calls structure
                            tool_calls = [{
                                "id": f"call_xml_{len(content)}",
                                "type": "function",
                                "function": {
                                    "name": parsed_tool.get("name"),
                                    "arguments": json.dumps(parsed_tool.get("arguments", {}))
                                }
                            }]
                            
                            # Optionally strip the tool call from the visible content to avoid UI artifacts
                            content = content.replace(match.group(0), "").strip()
                            
                        except:
                            pass # If it's malformed JSON, we ignore the fallback attempt

                return {"status": "success", "content": content, "tool_calls": tool_calls}
            except Exception as e:
                print(f"Model call failed: {e}")
                return {"status": "error", "message": str(e)}
        return {"status": "error", "message": "Failed to complete model call"}

    def extract_yaml(self, raw_content: str) -> Dict[str, Any]:
        """
        @summary Helper to extract and parse YAML blocks from model output.
        @inputs raw string from model
        @outputs parsed dictionary
        """
        try:
            # Simple markdown block parser
            if "```yaml" in raw_content:
                raw_content = raw_content.split("```yaml")[1].split("```")[0]
            return yaml.safe_load(raw_content)
        except Exception:
            return {"error": "Failed to parse YAML"}

    async def discover_available_models(self) -> List[str]:
        """
        @summary Dynamic discovery of available models from the provider's /v1/models endpoint.
        @inputs none (uses internal endpoint)
        @outputs list of model IDs matching core criteria
        """
        async with httpx.AsyncClient() as client:
            try:
                # Assuming standard OpenAI-compatible discovery route
                base = self.endpoint.rsplit("/v1/", 1)[0]
                resp = await client.get(f"{base}/v1/models", timeout=5.0)
                resp.raise_for_status()
                data = resp.json()
                models = [m.get("id", m.get("name", "unknown")) for m in data.get("data", [])]
                
                # Filter criteria: identifying strings for common OSS models
                valid = ["qwen", "llama", "phi", "mistral", "claude", "gpt", "deepseek"]
                discovered = [m for m in models if any(v in m.lower() for v in valid)]
                return discovered if discovered else models # Return all if none match our labels
            except Exception as e:
                print(f"Dynamic model discovery failed at {self.endpoint}: {e}")
                return [self.active_model]
