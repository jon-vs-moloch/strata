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
    def __init__(self, endpoint: str = "http://127.0.0.1:1234/v1/chat/completions"):
        """
        @summary Initialize the ModelAdapter.
        @inputs endpoint: local LLM completion URL
        @outputs none
        """
        self.endpoint = endpoint

    async def chat(self, messages: List[Dict[str, str]], format: str = "json") -> Dict[str, Any]:

        """
        @summary Send a chat message list to the model and return a structured response.
        @inputs messages: context list, format: desired output ('json' or 'yaml')
        @outputs dictionary representing the model's structured decision
        @side_effects makes network calls to the model provider
        """
        print(f"Calling local model at {self.endpoint}...")
        
        # This is a sample scaffold for LM Studio (OpenAI Compatible)
        async with httpx.AsyncClient() as client:
            try:
                # Actual LMS endpoint for chat
                payload = {
                    "model": "qwen3.5-4b-claude-4.6-opus-reasoning-distilled-v2", 
                    "messages": messages, 
                    "stream": False,
                    "temperature": 0.7
                }
                response = await client.post(self.endpoint, json=payload, timeout=60.0)
                response.raise_for_status()
                result = response.json()
                
                # Extract text from OpenAI-compatible structure
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                return {"status": "success", "content": content}
            except Exception as e:
                print(f"Model call failed: {e}")
                return {"status": "error", "message": str(e)}

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
