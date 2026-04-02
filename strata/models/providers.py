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
from threading import Lock
import time
from pydantic import BaseModel, Field

from strata.observability.writer import enqueue_provider_observability_snapshot, flush_observability_writes
from strata.observability.host import get_host_telemetry_snapshot
from strata.storage.models import ProviderTelemetrySnapshotModel


DEFAULT_RUNTIME_POLICY: Dict[str, Any] = {
    "throttle_mode": "hard",
    "operator_comfort": {
        "profile": "quiet",
        "ambiguity_bias": "prefer_quiet",
        "allow_annoying_if_explicit": False,
        "context": {
            "machine_in_use": True,
            "room_occupied": True,
            "ambient_noise_masking": False,
        },
    },
}

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
    message: Optional[str] = Field(default=None)
    error: Optional[Dict[str, Any]] = Field(default=None)
    error: Optional[Dict[str, Any]] = Field(default=None)

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
    learned_cloud_min_interval_ms: float = 0.0
    cloud_probe_min_interval_ms: float = 0.0
    cloud_next_probe_at: float = 0.0
    cloud_last_rate_limit_at: Optional[str] = None

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
    _telemetry_persist_lock = Lock()
    _telemetry_dirty: bool = False
    _telemetry_revision: int = 0
    _last_persisted_revision: int = 0
    _last_persisted_at: float = 0.0
    _runtime_policy: Dict[str, Any] = dict(DEFAULT_RUNTIME_POLICY)

    def _throttle_partition(self, kwargs: Optional[Dict[str, Any]] = None) -> str:
        if not self._is_local_transport():
            return "shared"
        explicit = str((kwargs or {}).get("throttle_partition") or "").strip().lower()
        if explicit:
            return explicit
        return "chat" if self._request_origin(kwargs) == "foreground" else "worker"

    def _throttle_key(self, kwargs: Optional[Dict[str, Any]] = None) -> str:
        return ":".join([
            self.provider_id,
            self.model_id,
            self.endpoint_url,
            str(self.requests_per_minute or ""),
            str(self.max_concurrency or ""),
            str(self.min_interval_ms or ""),
            self._throttle_partition(kwargs),
        ])

    def _get_throttle_state(self, kwargs: Optional[Dict[str, Any]] = None) -> ThrottleState:
        key = self._throttle_key(kwargs)
        if key not in self._throttle_states:
            concurrency = max(1, int(self.max_concurrency or 16))
            self._throttle_states[key] = ThrottleState(
                semaphore=asyncio.Semaphore(concurrency),
                lock=asyncio.Lock(),
            )
        return self._throttle_states[key]

    def _get_telemetry_state(self, kwargs: Optional[Dict[str, Any]] = None) -> ProviderTelemetryState:
        key = self._throttle_key(kwargs)
        if key not in self._telemetry_states:
            self._telemetry_states[key] = ProviderTelemetryState()
        return self._telemetry_states[key]

    @classmethod
    def _mark_telemetry_dirty(cls):
        base = GenericOpenAICompatibleProvider
        with base._telemetry_persist_lock:
            base._telemetry_dirty = True
            base._telemetry_revision += 1

    @classmethod
    def set_runtime_policy(cls, policy: Optional[Dict[str, Any]] = None):
        base = GenericOpenAICompatibleProvider
        merged = dict(DEFAULT_RUNTIME_POLICY)
        if isinstance(policy, dict):
            merged.update({k: v for k, v in policy.items() if k != "operator_comfort"})
            comfort = dict(DEFAULT_RUNTIME_POLICY.get("operator_comfort") or {})
            comfort.update(dict(policy.get("operator_comfort") or {}))
            comfort_context = dict((DEFAULT_RUNTIME_POLICY.get("operator_comfort") or {}).get("context") or {})
            comfort_context.update(dict((policy.get("operator_comfort") or {}).get("context") or {}))
            comfort["context"] = comfort_context
            merged["operator_comfort"] = comfort
        base._runtime_policy = merged

    @classmethod
    def get_runtime_policy(cls) -> Dict[str, Any]:
        base = GenericOpenAICompatibleProvider
        return dict(base._runtime_policy or DEFAULT_RUNTIME_POLICY)

    def _is_local_transport(self) -> bool:
        return self.__class__.__name__ == "LocalProvider"

    def _request_origin(self, kwargs: Optional[Dict[str, Any]] = None) -> str:
        origin = str((kwargs or {}).get("request_origin") or "background").strip().lower()
        return origin if origin in {"foreground", "background"} else "background"

    def _adaptive_min_interval_ms(self, telemetry: ProviderTelemetryState) -> float:
        if not self._is_local_transport():
            return 0.0
        observed_requests = max(telemetry.success_count, telemetry.request_count)
        if observed_requests < 3:
            return 0.0
        avg_latency_ms = (telemetry.total_latency_s / max(1, telemetry.success_count)) * 1000.0 if telemetry.success_count else 0.0
        error_rate = telemetry.error_count / max(1, telemetry.request_count)
        adaptive_ms = 0.0
        if avg_latency_ms >= 2500.0:
            adaptive_ms = max(adaptive_ms, min(15000.0, avg_latency_ms * 0.25))
        if avg_latency_ms >= 15000.0:
            adaptive_ms = max(adaptive_ms, min(45000.0, avg_latency_ms * 0.45))
        if error_rate >= 0.15:
            adaptive_ms = max(adaptive_ms, min(20000.0, 1500.0 + (avg_latency_ms * 0.2)))
        return adaptive_ms

    def _comfort_multiplier(self, *, request_origin: str = "background") -> float:
        if request_origin == "foreground":
            return 1.0
        policy = self.get_runtime_policy()
        comfort = dict(policy.get("operator_comfort") or {})
        profile = str(comfort.get("profile") or "quiet").strip().lower()
        context = dict(comfort.get("context") or {})
        machine_in_use = bool(context.get("machine_in_use", True))
        room_occupied = bool(context.get("room_occupied", True))
        ambient_noise_masking = bool(context.get("ambient_noise_masking", False))

        multiplier = {
            "quiet": 2.0,
            "balanced": 1.0,
            "aggressive": 0.75,
        }.get(profile, 1.0)
        if not machine_in_use:
            multiplier *= 0.8
        if not room_occupied:
            multiplier *= 0.75
        if ambient_noise_masking:
            multiplier *= 0.85
        host = get_host_telemetry_snapshot()
        thermal = dict(host.get("thermal") or {})
        memory = dict(host.get("memory") or {})
        cpu = dict(host.get("cpu") or {})
        fan = dict(host.get("fan") or {})
        temperature = dict(host.get("temperature") or {})
        if str(thermal.get("warning_level") or "nominal").strip().lower() not in {"nominal", "unknown"}:
            multiplier *= 1.35
        if bool(thermal.get("performance_limited")) or bool(thermal.get("cpu_power_limited")):
            multiplier *= 1.25
        fan_rpm = float(fan.get("rpm") or 0.0) if bool(fan.get("available")) else 0.0
        if fan_rpm >= 4200.0:
            multiplier *= 1.45
        elif fan_rpm >= 3500.0:
            multiplier *= 1.3
        elif fan_rpm >= 2500.0:
            multiplier *= 1.15
        temperature_celsius = float(temperature.get("celsius") or 0.0) if bool(temperature.get("available")) else 0.0
        if temperature_celsius >= 85.0:
            multiplier *= 1.3
        elif temperature_celsius >= 75.0:
            multiplier *= 1.18
        elif temperature_celsius >= 68.0:
            multiplier *= 1.08
        if str(memory.get("pressure") or "nominal").strip().lower() == "high":
            multiplier *= 1.4
        elif str(memory.get("pressure") or "nominal").strip().lower() == "moderate":
            multiplier *= 1.15
        load_avg = list(cpu.get("load_avg") or [])
        cpu_count = max(1.0, float(cpu.get("cpu_count") or 1.0))
        if load_avg:
            normalized_load = float(load_avg[0] or 0.0) / cpu_count
            if normalized_load >= 3.0:
                multiplier *= 1.35
            elif normalized_load >= 2.0:
                multiplier *= 1.18
            elif normalized_load >= 1.25:
                multiplier *= 1.08
        if machine_in_use and profile == "quiet":
            multiplier *= 1.2
        if float(cpu.get("normalized_percent") or 0.0) >= 85.0:
            multiplier *= 1.2
        elif float(cpu.get("normalized_percent") or 0.0) >= 50.0 and profile == "quiet":
            multiplier *= 1.1
        return max(0.5, min(multiplier, 2.5))

    def _cloud_greedy_probe_multiplier(self, telemetry: ProviderTelemetryState) -> float:
        policy = self.get_runtime_policy()
        if str(policy.get("throttle_mode") or "hard").strip().lower() != "greedy":
            return 1.0
        now = time.monotonic()
        if telemetry.cloud_next_probe_at and now < telemetry.cloud_next_probe_at:
            return 1.0
        request_count = max(1, telemetry.request_count)
        if telemetry.rate_limit_hits > 0 or telemetry.error_count / request_count >= 0.1:
            return 1.0
        if request_count < 5:
            return 1.0
        if request_count < 20:
            return 0.9
        return 0.8

    def _payload_summary(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        tools = [str(((item or {}).get("function") or {}).get("name") or "") for item in list(payload.get("tools") or [])]
        messages = list(payload.get("messages") or [])
        return {
            "model": str(payload.get("model") or ""),
            "message_count": len(messages),
            "message_roles": [str((item or {}).get("role") or "") for item in messages[-6:]],
            "tool_names": [name for name in tools if name],
            "has_response_format": "response_format" in payload,
            "reasoning_effort": payload.get("reasoning_effort"),
            "has_reasoning_config": "reasoning" in payload,
            "max_tokens": payload.get("max_tokens"),
            "temperature": payload.get("temperature"),
            "top_p": payload.get("top_p"),
        }

    def _parse_error_body(self, response: httpx.Response) -> Dict[str, Any]:
        try:
            body = response.json()
            if isinstance(body, dict):
                return body
            return {"raw": body}
        except Exception:
            text = str(response.text or "").strip()
            return {"raw_text": text[:4000]}

    def _local_greedy_probe_multiplier(self, telemetry: ProviderTelemetryState) -> float:
        policy = self.get_runtime_policy()
        if str(policy.get("throttle_mode") or "hard").strip().lower() != "greedy":
            return 1.0
        comfort = dict(policy.get("operator_comfort") or {})
        profile = str(comfort.get("profile") or "quiet").strip().lower()
        if profile == "quiet":
            return 1.0
        request_count = max(1, telemetry.request_count)
        if telemetry.error_count / request_count >= 0.1:
            return 1.0
        if request_count < 5:
            return 1.0
        if telemetry.success_count >= 10:
            return 0.9
        return 0.95

    def _effective_min_interval_ms(
        self,
        telemetry: Optional[ProviderTelemetryState] = None,
        *,
        request_origin: str = "background",
    ) -> float:
        min_interval = float(self.min_interval_ms or 0)
        if self.requests_per_minute and self.requests_per_minute > 0:
            rpm_interval = 60000.0 / float(self.requests_per_minute)
            min_interval = max(min_interval, rpm_interval)
        if telemetry is not None:
            if self._is_local_transport():
                if request_origin == "foreground":
                    return min_interval
                min_interval = max(min_interval, self._adaptive_min_interval_ms(telemetry))
                min_interval = min_interval * self._comfort_multiplier(request_origin=request_origin) * self._local_greedy_probe_multiplier(telemetry)
            else:
                min_interval = max(min_interval, float(telemetry.learned_cloud_min_interval_ms or 0.0))
                min_interval = min_interval * self._cloud_greedy_probe_multiplier(telemetry)
        return min_interval

    def _estimate_cloud_retry_after_s(self, telemetry: ProviderTelemetryState, retry_after_s: float) -> float:
        if retry_after_s > 0:
            return retry_after_s
        learned_ms = float(telemetry.learned_cloud_min_interval_ms or 0.0)
        probe_ms = float(telemetry.cloud_probe_min_interval_ms or 0.0)
        baseline_ms = max(float(self.min_interval_ms or 0.0), learned_ms, probe_ms, 1000.0)
        return max(2.0, min(120.0, (baseline_ms / 1000.0) * 2.0))

    def _record_cloud_rate_limit(self, telemetry: ProviderTelemetryState, retry_after_s: float) -> float:
        effective_retry_after_s = self._estimate_cloud_retry_after_s(telemetry, retry_after_s)
        learned_ms = max(
            float(telemetry.learned_cloud_min_interval_ms or 0.0),
            effective_retry_after_s * 1000.0,
            float(self.min_interval_ms or 0.0),
        )
        telemetry.learned_cloud_min_interval_ms = min(300000.0, learned_ms)
        telemetry.cloud_probe_min_interval_ms = telemetry.learned_cloud_min_interval_ms
        now = time.monotonic()
        telemetry.cloud_next_probe_at = now + max(60.0, effective_retry_after_s * 4.0)
        telemetry.cloud_last_rate_limit_at = datetime.utcnow().isoformat()
        return effective_retry_after_s

    def _record_cloud_success(self, telemetry: ProviderTelemetryState) -> None:
        if self._is_local_transport():
            return
        now = time.monotonic()
        if telemetry.cloud_next_probe_at and now < telemetry.cloud_next_probe_at:
            return
        current_probe = float(telemetry.cloud_probe_min_interval_ms or telemetry.learned_cloud_min_interval_ms or 0.0)
        if current_probe <= 0:
            return
        reduced_probe = max(float(self.min_interval_ms or 0.0), current_probe * 0.9)
        telemetry.cloud_probe_min_interval_ms = reduced_probe
        telemetry.learned_cloud_min_interval_ms = reduced_probe
        telemetry.cloud_next_probe_at = now + 120.0

    async def _wait_for_turn(
        self,
        state: ThrottleState,
        telemetry: ProviderTelemetryState,
        *,
        request_origin: str = "background",
    ):
        delay_s = 0.0
        async with state.lock:
            now = asyncio.get_running_loop().time()
            if state.next_allowed_at > now:
                delay_s = state.next_allowed_at - now
            reservation_start = max(now, state.next_allowed_at)
            state.next_allowed_at = reservation_start + (self._effective_min_interval_ms(telemetry, request_origin=request_origin) / 1000.0)
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

    def _request_summary(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        messages = list(payload.get("messages") or [])
        return {
            "model": str(payload.get("model") or self.model_id),
            "message_count": len(messages),
            "roles": [str((item or {}).get("role") or "") for item in messages[-6:]],
            "tool_names": [
                str(((item or {}).get("function") or {}).get("name") or "")
                for item in list(payload.get("tools") or [])
            ],
            "has_response_format": "response_format" in payload,
            "temperature": payload.get("temperature"),
            "top_p": payload.get("top_p"),
            "max_tokens": payload.get("max_tokens"),
        }

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
        if "top_p" in kwargs:
            payload["top_p"] = kwargs["top_p"]
        if "max_tokens" in kwargs:
            payload["max_tokens"] = kwargs["max_tokens"]
        if "reasoning_effort" in kwargs:
            payload["reasoning_effort"] = kwargs["reasoning_effort"]
        if "reasoning" in kwargs:
            payload["reasoning"] = kwargs["reasoning"]
        if "tools" in kwargs:
            payload["tools"] = kwargs["tools"]
        if "response_format" in kwargs:
            payload["response_format"] = kwargs["response_format"]

        state = self._get_throttle_state(kwargs)
        telemetry = self._get_telemetry_state(kwargs)

        async with state.semaphore:
            for retry in range(max_retries):
                try:
                    request_origin = self._request_origin(kwargs)
                    telemetry.request_count += 1
                    telemetry.last_request_at = datetime.utcnow().isoformat()
                    started_at = asyncio.get_running_loop().time()
                    await self._wait_for_turn(state, telemetry, request_origin=request_origin)
                    async with httpx.AsyncClient() as client:
                        response = await client.post(self.endpoint_url, json=payload, headers=headers, timeout=timeout)
                        response.raise_for_status()
                        telemetry.success_count += 1
                        telemetry.last_status_code = response.status_code
                        telemetry.last_success_at = datetime.utcnow().isoformat()
                        telemetry.last_error = None
                        telemetry.total_latency_s += asyncio.get_running_loop().time() - started_at
                        self._record_cloud_success(telemetry)
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
                    response_text = str(e.response.text or "").strip()
                    clipped_body = response_text[:4000]
                    response_json: Optional[Dict[str, Any]] = None
                    try:
                        parsed = e.response.json()
                        if isinstance(parsed, dict):
                            response_json = parsed
                    except Exception:
                        response_json = None
                    telemetry.error_count += 1
                    telemetry.last_status_code = status_code
                    telemetry.last_error = f"HTTP {status_code}: {clipped_body or str(e)}"
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
                        effective_retry_after_s = self._record_cloud_rate_limit(telemetry, retry_after_s)
                        await self._apply_retry_after(state, effective_retry_after_s)
                    else:
                        effective_retry_after_s = retry_after_s

                    if retry < max_retries - 1 and status_code in {408, 409, 425, 429, 500, 502, 503, 504}:
                        self._mark_telemetry_dirty()
                        telemetry.retried_count += 1
                        await asyncio.sleep(float(max(effective_retry_after_s, backoff_time)))
                        backoff_time *= 2.0
                        continue
                    self._mark_telemetry_dirty()
                    return ModelResponse(
                        status="error",
                        content=f"HTTP {status_code}: {clipped_body or str(e)}",
                        model=self.model_id,
                        provider=self.provider_id,
                        usage={},
                        error={
                            "kind": "http_status_error",
                            "http_status": status_code,
                            "response_body": clipped_body,
                            "response_json": response_json,
                            "request_summary": self._request_summary(payload),
                        },
                    )
                except Exception as e:
                    telemetry.error_count += 1
                    telemetry.last_error = str(e)
                    telemetry.last_status_code = None
                    telemetry.total_latency_s += asyncio.get_running_loop().time() - started_at
                    if retry < max_retries - 1:
                        self._mark_telemetry_dirty()
                        telemetry.retried_count += 1
                        await asyncio.sleep(float(backoff_time))
                        backoff_time *= 2.0
                        continue
                    self._mark_telemetry_dirty()
                    return ModelResponse(
                        status="error",
                        content=str(e),
                        model=self.model_id,
                        provider=self.provider_id,
                        usage={},
                        error={
                            "kind": e.__class__.__name__,
                            "request_summary": self._request_summary(payload),
                        },
                    )
                finally:
                    self._mark_telemetry_dirty()
        
        self._mark_telemetry_dirty()
        return ModelResponse(
            status="error", 
            content="Max retries reached", 
            model=self.model_id, 
            provider=self.provider_id,
            usage={},
            error={
                "kind": "max_retries_reached",
                "request_summary": self._request_summary(payload),
            },
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
    runtime_policy = GenericOpenAICompatibleProvider.get_runtime_policy()
    host_snapshot = get_host_telemetry_snapshot()
    comfort = dict(runtime_policy.get("operator_comfort") or {})
    comfort_profile = str(comfort.get("profile") or "quiet").strip().lower()
    comfort_context = dict(comfort.get("context") or {})
    comfort_multiplier = {
        "quiet": 1.5,
        "balanced": 1.0,
        "aggressive": 0.75,
    }.get(comfort_profile, 1.0)
    if not bool(comfort_context.get("machine_in_use", True)):
        comfort_multiplier *= 0.8
    if not bool(comfort_context.get("room_occupied", True)):
        comfort_multiplier *= 0.75
    if bool(comfort_context.get("ambient_noise_masking", False)):
        comfort_multiplier *= 0.85
    fan = dict(host_snapshot.get("fan") or {})
    temperature = dict(host_snapshot.get("temperature") or {})
    fan_rpm = float(fan.get("rpm") or 0.0) if bool(fan.get("available")) else 0.0
    if fan_rpm >= 4200.0:
        comfort_multiplier *= 1.45
    elif fan_rpm >= 3500.0:
        comfort_multiplier *= 1.3
    elif fan_rpm >= 2500.0:
        comfort_multiplier *= 1.15
    temperature_celsius = float(temperature.get("celsius") or 0.0) if bool(temperature.get("available")) else 0.0
    if temperature_celsius >= 85.0:
        comfort_multiplier *= 1.3
    elif temperature_celsius >= 75.0:
        comfort_multiplier *= 1.18
    elif temperature_celsius >= 68.0:
        comfort_multiplier *= 1.08
    comfort_multiplier = max(0.5, min(comfort_multiplier, 2.5))
    for key, state in GenericOpenAICompatibleProvider._telemetry_states.items():
        avg_wait_ms = (state.total_wait_s / state.throttled_count * 1000.0) if state.throttled_count else 0.0
        avg_latency_ms = (state.total_latency_s / state.request_count * 1000.0) if state.request_count else 0.0
        request_count = max(1, state.request_count)
        adaptive_min_interval_ms = 0.0
        is_local = "127.0.0.1" in key
        if request_count >= 3 and is_local:
            if avg_latency_ms >= 2500.0:
                adaptive_min_interval_ms = max(adaptive_min_interval_ms, min(15000.0, avg_latency_ms * 0.25))
            error_rate = state.error_count / request_count
            if error_rate >= 0.15:
                adaptive_min_interval_ms = max(adaptive_min_interval_ms, min(20000.0, 1500.0 + (avg_latency_ms * 0.2)))
        effective_min_interval_ms = adaptive_min_interval_ms
        throttle_mode = str(runtime_policy.get("throttle_mode") or "hard").strip().lower()
        if is_local:
            effective_min_interval_ms = effective_min_interval_ms * comfort_multiplier
            if throttle_mode == "greedy":
                if comfort_profile != "quiet" and state.error_count / request_count < 0.1:
                    effective_min_interval_ms *= 0.95 if request_count < 10 else 0.9
        else:
            if throttle_mode == "greedy" and state.rate_limit_hits == 0 and state.error_count / request_count < 0.1:
                effective_min_interval_ms *= 0.9 if request_count < 20 else 0.8
        snapshot[key] = {
            "request_count": state.request_count,
            "success_count": state.success_count,
            "error_count": state.error_count,
            "error_rate": round(state.error_count / request_count, 4),
            "retried_count": state.retried_count,
            "throttled_count": state.throttled_count,
            "rate_limit_hits": state.rate_limit_hits,
            "avg_wait_ms": round(avg_wait_ms, 2),
            "avg_latency_ms": round(avg_latency_ms, 2),
            "adaptive_min_interval_ms": round(adaptive_min_interval_ms, 2),
            "effective_min_interval_ms": round(effective_min_interval_ms, 2),
            "learned_cloud_min_interval_ms": round(float(state.learned_cloud_min_interval_ms or 0.0), 2),
            "cloud_probe_min_interval_ms": round(float(state.cloud_probe_min_interval_ms or 0.0), 2),
            "cloud_next_probe_in_s": round(max(0.0, float(state.cloud_next_probe_at or 0.0) - time.monotonic()), 2),
            "throttle_mode": runtime_policy.get("throttle_mode"),
            "operator_comfort": runtime_policy.get("operator_comfort") or {},
            "host_telemetry": host_snapshot if is_local else {},
            "last_status_code": state.last_status_code,
            "last_error": state.last_error,
            "last_request_at": state.last_request_at,
            "last_success_at": state.last_success_at,
            "last_rate_limit_at": state.cloud_last_rate_limit_at,
            "last_rate_limit_headers": state.last_rate_limit_headers or {},
        }
    return snapshot


def persist_provider_telemetry_snapshot(
    storage=None,
    *,
    force: bool = False,
    min_interval_s: float = 30.0,
    commit: bool = True,
) -> bool:
    now = time.monotonic()
    snapshot = get_provider_telemetry_snapshot()
    if not snapshot:
        return False

    with GenericOpenAICompatibleProvider._telemetry_persist_lock:
        dirty = GenericOpenAICompatibleProvider._telemetry_dirty
        revision = GenericOpenAICompatibleProvider._telemetry_revision
        last_persisted_revision = GenericOpenAICompatibleProvider._last_persisted_revision
        last_persisted_at = GenericOpenAICompatibleProvider._last_persisted_at
        if not force:
            if not dirty or revision == last_persisted_revision:
                return False
            if (now - last_persisted_at) < max(0.0, float(min_interval_s)):
                return False

    owns_storage = storage is None
    if owns_storage and not force:
        should_flush = enqueue_provider_observability_snapshot(snapshot)
        if should_flush:
            return flush_observability_writes()
        return False
    if owns_storage:
        from strata.storage.services.main import StorageManager

        storage = StorageManager()
    try:
        bind = None
        try:
            bind = storage.session.get_bind()
        except Exception:
            bind = getattr(storage, "engine", None)
        if bind is not None:
            ProviderTelemetrySnapshotModel.__table__.create(bind=bind, checkfirst=True)
        storage.session.add(ProviderTelemetrySnapshotModel(snapshot=snapshot))
        if commit and hasattr(storage, "commit"):
            storage.commit()
        with GenericOpenAICompatibleProvider._telemetry_persist_lock:
            if GenericOpenAICompatibleProvider._telemetry_revision == revision:
                GenericOpenAICompatibleProvider._telemetry_dirty = False
            GenericOpenAICompatibleProvider._last_persisted_revision = max(
                GenericOpenAICompatibleProvider._last_persisted_revision,
                revision,
            )
            GenericOpenAICompatibleProvider._last_persisted_at = now
        return True
    except Exception:
        if commit and hasattr(storage, "rollback"):
            storage.rollback()
        return False
    finally:
        if owns_storage and hasattr(storage, "close"):
            storage.close()


def get_latest_persisted_provider_telemetry(storage) -> Dict[str, Dict[str, object]]:
    try:
        bind = None
        try:
            bind = storage.session.get_bind()
        except Exception:
            bind = getattr(storage, "engine", None)
        if bind is not None:
            ProviderTelemetrySnapshotModel.__table__.create(bind=bind, checkfirst=True)
        row = (
            storage.session.query(ProviderTelemetrySnapshotModel)
            .order_by(ProviderTelemetrySnapshotModel.recorded_at.desc(), ProviderTelemetrySnapshotModel.id.desc())
            .first()
        )
    except Exception:
        return {}
    if not row or not isinstance(row.snapshot, dict):
        return {}
    return row.snapshot
