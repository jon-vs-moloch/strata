"""
@module models.providers
@purpose Provide abstract and concrete implementations for model provider transports.
@key_exports BaseModelProvider, LocalProvider, CloudProvider
"""

import httpx
import asyncio
from typing import Any, Dict, Optional, List, Literal
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
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
    usage: Optional[Dict[str, Any]] = Field(default=None)

@dataclass
class ThrottleState:
    semaphore: asyncio.Semaphore
    lock: asyncio.Lock
    next_allowed_at: float = 0.0

@dataclass
class ProviderTelemetryState:
    request_count: int = 0
    success_count: int = 0
    error_count: int = 0
    retried_count: int = 0
    throttled_count: int = 0
    rate_limit_hits: int = 0
    total_wait_s: float = 0.0
    total_latency_s: float = 0.0
    last_status_code: Optional[int] = None
    last_error: Optional[str] = None
    last_request_at: Optional[str] = None
    last_success_at: Optional[str] = None
    last_rate_limit_headers: Optional[Dict[str, str]] = None

class BaseModelProvider(ABC):
    """
    @summary Universal interface for all model interactions.
    """
    def __init__(
        self,
        model_id: str,
        provider_id: str,
        endpoint_url: str,
        api_key: Optional[str] = None,
        requests_per_minute: Optional[int] = None,
        max_concurrency: Optional[int] = None,
        min_interval_ms: Optional[int] = None,
    ):
        self.model_id = model_id
        self.provider_id = provider_id
        self.endpoint_url = endpoint_url
        self.api_key = api_key
        self.requests_per_minute = requests_per_minute
        self.max_concurrency = max_concurrency
        self.min_interval_ms = min_interval_ms

    @abstractmethod
    async def complete(self, messages: List[Dict[str, str]], **kwargs) -> ModelResponse:
        pass

class GenericOpenAICompatibleProvider(BaseModelProvider):
    """
    @summary Base implementation for any provider using the OpenAI-compatible v1/chat/completions schema.
    """
    _throttle_states: Dict[str, ThrottleState] = {}
    _telemetry_states: Dict[str, ProviderTelemetryState] = {}

    def _throttle_key(self) -> str:
        return ":".join([
            self.provider_id,
            self.model_id,
            self.endpoint_url,
            str(self.requests_per_minute or ""),
            str(self.max_concurrency or ""),
            str(self.min_interval_ms or ""),
        ])

    def _get_throttle_state(self) -> ThrottleState:
        key = self._throttle_key()
        if key not in self._throttle_states:
            concurrency = max(1, int(self.max_concurrency or 16))
            self._throttle_states[key] = ThrottleState(
                semaphore=asyncio.Semaphore(concurrency),
                lock=asyncio.Lock(),
            )
        return self._throttle_states[key]

    def _get_telemetry_state(self) -> ProviderTelemetryState:
        key = self._throttle_key()
        if key not in self._telemetry_states:
            self._telemetry_states[key] = ProviderTelemetryState()
        return self._telemetry_states[key]

    def _effective_min_interval_ms(self) -> float:
        min_interval = float(self.min_interval_ms or 0)
        if self.requests_per_minute and self.requests_per_minute > 0:
            rpm_interval = 60000.0 / float(self.requests_per_minute)
            min_interval = max(min_interval, rpm_interval)
        return min_interval

    async def _wait_for_turn(self, state: ThrottleState, telemetry: ProviderTelemetryState):
        delay_s = 0.0
        async with state.lock:
            now = asyncio.get_running_loop().time()
            if state.next_allowed_at > now:
                delay_s = state.next_allowed_at - now
            reservation_start = max(now, state.next_allowed_at)
            state.next_allowed_at = reservation_start + (self._effective_min_interval_ms() / 1000.0)
        if delay_s > 0:
            telemetry.throttled_count += 1
            telemetry.total_wait_s += delay_s
            await asyncio.sleep(delay_s)

    async def _apply_retry_after(self, state: ThrottleState, retry_after_s: float):
        if retry_after_s <= 0:
            return
        async with state.lock:
            now = asyncio.get_running_loop().time()
            state.next_allowed_at = max(state.next_allowed_at, now + retry_after_s)

    def _normalize_usage(self, usage: Any) -> Dict[str, Any]:
        if not isinstance(usage, dict):
            return {}
        normalized: Dict[str, Any] = {}
        for key, value in usage.items():
            if isinstance(value, (int, float, str)) or value is None:
                normalized[key] = value
            elif isinstance(value, dict):
                # Preserve nested usage details without forcing them through scalar validation.
                normalized[key] = {
                    str(nested_key): nested_value
                    for nested_key, nested_value in value.items()
                    if isinstance(nested_value, (int, float, str, bool)) or nested_value is None
                }
            elif isinstance(value, list):
                normalized[key] = [
                    item
                    for item in value
                    if isinstance(item, (int, float, str, bool)) or item is None
                ]
        return normalized

    async def complete(self, messages: List[Dict[str, str]], **kwargs) -> ModelResponse:
        max_retries = kwargs.get("max_retries", 3)
        backoff_time = 2.0

        headers = {
            "Content-Type": "application/json"
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        timeout_arg = kwargs.get("timeout")
        if timeout_arg:
            timeout = httpx.Timeout(timeout_arg)
        else:
            timeout = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)

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

        state = self._get_throttle_state()
        telemetry = self._get_telemetry_state()

        async with state.semaphore:
            for retry in range(max_retries):
                try:
                    telemetry.request_count += 1
                    telemetry.last_request_at = datetime.utcnow().isoformat()
                    started_at = asyncio.get_running_loop().time()
                    await self._wait_for_turn(state, telemetry)
                    async with httpx.AsyncClient() as client:
                        response = await client.post(self.endpoint_url, json=payload, headers=headers, timeout=timeout)
                        response.raise_for_status()
                        telemetry.success_count += 1
                        telemetry.last_status_code = response.status_code
                        telemetry.last_success_at = datetime.utcnow().isoformat()
                        telemetry.last_error = None
                        telemetry.total_latency_s += asyncio.get_running_loop().time() - started_at
                        result = response.json()

                        message_obj = result.get("choices", [{}])[0].get("message", {})
                        content = message_obj.get("content", "")
                        tool_calls = message_obj.get("tool_calls", None)

                        return ModelResponse(
                            status="success",
                            content=content,
                            tool_calls=tool_calls,
                            model=self.model_id,
                            provider=self.provider_id,
                            usage=self._normalize_usage(result.get("usage") or {}),
                        )
                except httpx.HTTPStatusError as e:
                    status_code = e.response.status_code
                    telemetry.error_count += 1
                    telemetry.last_status_code = status_code
                    telemetry.last_error = str(e)
                    telemetry.total_latency_s += asyncio.get_running_loop().time() - started_at
                    retry_after = e.response.headers.get("retry-after")
                    retry_after_s = 0.0
                    if retry_after:
                        try:
                            retry_after_s = float(retry_after)
                        except ValueError:
                            retry_after_s = 0.0
                    if status_code in {429, 503}:
                        telemetry.rate_limit_hits += 1
                        telemetry.last_rate_limit_headers = {
                            key: value for key, value in e.response.headers.items()
                            if key.lower().startswith("x-ratelimit") or key.lower() == "retry-after"
                        }
                        await self._apply_retry_after(state, retry_after_s or backoff_time)

                    if retry < max_retries - 1 and status_code in {408, 409, 425, 429, 500, 502, 503, 504}:
                        telemetry.retried_count += 1
                        await asyncio.sleep(float(max(retry_after_s, backoff_time)))
                        backoff_time *= 2.0
                        continue
                    return ModelResponse(
                        status="error",
                        content=f"HTTP {status_code}: {e.response.text}",
                        model=self.model_id,
                        provider=self.provider_id,
                        usage={},
                    )
                except Exception as e:
                    telemetry.error_count += 1
                    telemetry.last_error = str(e)
                    telemetry.last_status_code = None
                    telemetry.total_latency_s += asyncio.get_running_loop().time() - started_at
                    if retry < max_retries - 1:
                        telemetry.retried_count += 1
                        await asyncio.sleep(float(backoff_time))
                        backoff_time *= 2.0
                        continue
                    return ModelResponse(
                        status="error",
                        content=str(e),
                        model=self.model_id,
                        provider=self.provider_id,
                        usage={},
                    )
        
        return ModelResponse(
            status="error", 
            content="Max retries reached", 
            model=self.model_id, 
            provider=self.provider_id,
            usage={},
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


def get_provider_telemetry_snapshot() -> Dict[str, Dict[str, object]]:
    snapshot: Dict[str, Dict[str, object]] = {}
    for key, state in GenericOpenAICompatibleProvider._telemetry_states.items():
        avg_wait_ms = (state.total_wait_s / state.throttled_count * 1000.0) if state.throttled_count else 0.0
        avg_latency_ms = (state.total_latency_s / state.request_count * 1000.0) if state.request_count else 0.0
        snapshot[key] = {
            "request_count": state.request_count,
            "success_count": state.success_count,
            "error_count": state.error_count,
            "retried_count": state.retried_count,
            "throttled_count": state.throttled_count,
            "rate_limit_hits": state.rate_limit_hits,
            "avg_wait_ms": round(avg_wait_ms, 2),
            "avg_latency_ms": round(avg_latency_ms, 2),
            "last_status_code": state.last_status_code,
            "last_error": state.last_error,
            "last_request_at": state.last_request_at,
            "last_success_at": state.last_success_at,
            "last_rate_limit_headers": state.last_rate_limit_headers or {},
        }
    return snapshot
