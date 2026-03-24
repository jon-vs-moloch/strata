"""
@module models.providers
@purpose Provide abstract and concrete implementations for model provider transports.
@key_exports BaseModelProvider, LocalProvider, CloudProvider
"""

import httpx
import json
import asyncio
from typing import Dict, Any, Optional, List, Protocol, Literal
from abc import ABC, abstractmethod
from pydantic import BaseModel, Field

class ModelResponse(BaseModel):
    """
    @summary Normalized response from an LLM adapter.
    """
    status: Literal["success", "error"] = Field(...)
    content: str = Field(...)
    tool_calls: Optional[List[Dict]] = Field(None)
    model: str = Field(...)
    provider: str = Field(...)

class BaseModelProvider(ABC):
    """
    @summary Universal interface for all model interactions.
    """
    def __init__(self, model_id: str, provider_id: str, endpoint_url: str, api_key: Optional[str] = None):
        self.model_id = model_id
        self.provider_id = provider_id
        self.endpoint_url = endpoint_url
        self.api_key = api_key

    @abstractmethod
    async def complete(self, messages: List[Dict[str, str]], **kwargs) -> ModelResponse:
        pass

class GenericOpenAICompatibleProvider(BaseModelProvider):
    """
    @summary Base implementation for any provider using the OpenAI-compatible v1/chat/completions schema.
    """
    async def complete(self, messages: List[Dict[str, str]], **kwargs) -> ModelResponse:
        max_retries = kwargs.get("max_retries", 3)
        backoff_time = 2.0
        
        headers = {
            "Content-Type": "application/json"
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        for retry in range(max_retries):
            try:
                async with httpx.AsyncClient() as client:
                    payload = {
                        "model": self.model_id, 
                        "messages": messages, 
                        "stream": False,
                        "temperature": kwargs.get("temperature", 0.7)
                    }
                    if "tools" in kwargs:
                        payload["tools"] = kwargs["tools"]
                    if "response_format" in kwargs:
                        payload["response_format"] = kwargs["response_format"]

                    timeout = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)
                    response = await client.post(self.endpoint_url, json=payload, headers=headers, timeout=timeout)
                    response.raise_for_status()
                    result = response.json()
                    
                    message_obj = result.get("choices", [{}])[0].get("message", {})
                    content = message_obj.get("content", "")
                    tool_calls = message_obj.get("tool_calls", None)
                    
                    return ModelResponse(
                        status="success", 
                        content=content, 
                        tool_calls=tool_calls,
                        model=self.model_id,
                        provider=self.provider_id
                    )
            except Exception as e:
                if retry < max_retries - 1:
                    await asyncio.sleep(float(backoff_time))
                    backoff_time *= 2.0
                    continue
                else:
                    return ModelResponse(
                        status="error", 
                        content=str(e),
                        model=self.model_id, 
                        provider=self.provider_id
                    )
        
        return ModelResponse(
            status="error", 
            content="Max retries reached", 
            model=self.model_id, 
            provider=self.provider_id
        )

class LocalProvider(GenericOpenAICompatibleProvider):
    """
    @summary Wrapper for local inference endpoints (Ollama, LM Studio).
    """
    pass

class CloudProvider(GenericOpenAICompatibleProvider):
    """
    @summary Wrapper for cloud inference endpoints (OpenRouter, OpenAI).
    """
    pass
