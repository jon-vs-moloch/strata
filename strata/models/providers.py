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
    _telemetry_persist_lock = Lock()
    _telemetry_dirty: bool = False
    _telemetry_revision: int = 0
    _last_persisted_revision: int = 0
    _last_persisted_at: float = 0.0
    _runtime_policy: Dict[str, Any] = dict(DEFAULT_RUNTIME_POLICY)

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
        if error_rate >= 0.15:
            adaptive_ms = max(adaptive_ms, min(20000.0, 1500.0 + (avg_latency_ms * 0.2)))
        return adaptive_ms

    def _comfort_multiplier(self) -> float:
        policy = self.get_runtime_policy()
        comfort = dict(policy.get("operator_comfort") or {})
        profile = str(comfort.get("profile") or "quiet").strip().lower()
        context = dict(comfort.get("context") or {})
        machine_in_use = bool(context.get("machine_in_use", True))
        room_occupied = bool(context.get("room_occupied", True))
        ambient_noise_masking = bool(context.get("ambient_noise_masking", False))

        multiplier = {
            "quiet": 1.5,
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
        if str(thermal.get("warning_level") or "nominal").strip().lower() not in {"nominal", "unknown"}:
            multiplier *= 1.35
        if bool(thermal.get("performance_limited")) or bool(thermal.get("cpu_power_limited")):
            multiplier *= 1.25
        if str(memory.get("pressure") or "nominal").strip().lower() == "high":
            multiplier *= 1.4
        elif str(memory.get("pressure") or "nominal").strip().lower() == "moderate":
            multiplier *= 1.15
        if float(cpu.get("normalized_percent") or 0.0) >= 85.0:
            multiplier *= 1.2
        return max(0.5, min(multiplier, 2.5))

    def _cloud_greedy_probe_multiplier(self, telemetry: ProviderTelemetryState) -> float:
        policy = self.get_runtime_policy()
        if str(policy.get("throttle_mode") or "hard").strip().lower() != "greedy":
            return 1.0
        request_count = max(1, telemetry.request_count)
        if telemetry.rate_limit_hits > 0 or telemetry.error_count / request_count >= 0.1:
            return 1.0
        if request_count < 5:
            return 1.0
        if request_count < 20:
            return 0.9
        return 0.8

    def _local_greedy_probe_multiplier(self, telemetry: ProviderTelemetryState) -> float:
        policy = self.get_runtime_policy()
        if str(policy.get("throttle_mode") or "hard").strip().lower() != "greedy":
            return 1.0
        request_count = max(1, telemetry.request_count)
        if telemetry.error_count / request_count >= 0.1:
            return 1.0
        if request_count < 5:
            return 1.0
        if telemetry.success_count >= 10:
            return 0.9
        return 0.95

    def _effective_min_interval_ms(self, telemetry: Optional[ProviderTelemetryState] = None) -> float:
        min_interval = float(self.min_interval_ms or 0)
        if self.requests_per_minute and self.requests_per_minute > 0:
            rpm_interval = 60000.0 / float(self.requests_per_minute)
            min_interval = max(min_interval, rpm_interval)
        if telemetry is not None:
            if self._is_local_transport():
                min_interval = max(min_interval, self._adaptive_min_interval_ms(telemetry))
                min_interval = min_interval * self._comfort_multiplier() * self._local_greedy_probe_multiplier(telemetry)
            else:
                min_interval = min_interval * self._cloud_greedy_probe_multiplier(telemetry)
        return min_interval

    async def _wait_for_turn(self, state: ThrottleState, telemetry: ProviderTelemetryState):
        delay_s = 0.0
        async with state.lock:
            now = asyncio.get_running_loop().time()
            if state.next_allowed_at > now:
                delay_s = state.next_allowed_at - now
            reservation_start = max(now, state.next_allowed_at)
            state.next_allowed_at = reservation_start + (self._effective_min_interval_ms(telemetry) / 1000.0)
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
                        self._mark_telemetry_dirty()
                        telemetry.retried_count += 1
                        await asyncio.sleep(float(max(retry_after_s, backoff_time)))
                        backoff_time *= 2.0
                        continue
                    self._mark_telemetry_dirty()
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
                if state.error_count / request_count < 0.1:
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
            "throttle_mode": runtime_policy.get("throttle_mode"),
            "operator_comfort": runtime_policy.get("operator_comfort") or {},
            "host_telemetry": host_snapshot if is_local else {},
            "last_status_code": state.last_status_code,
            "last_error": state.last_error,
            "last_request_at": state.last_request_at,
            "last_success_at": state.last_success_at,
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
