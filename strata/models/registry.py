"""
@module models.registry
@purpose Central registry for model endpoints and pools.
@key_exports ModelRegistry
"""

import os
from typing import Any, Dict, List, Optional
from strata.schemas.models import ModelPool, ModelEndpoint
from strata.schemas.execution import ExecutionContext
from strata.models.providers import LocalProvider, CloudProvider, BaseModelProvider

REGISTRY_PRESETS = {
    "trainer": {
        "cerebras_glm_4_7": {
            "provider": "cerebras",
            "model": "zai-glm-4.7",
            "transport": "cloud",
            "api_key_env": "CEREBRAS_API_KEY",
            "endpoint_url": "https://api.cerebras.ai/v1/chat/completions",
            "requests_per_minute": 20,
            "max_concurrency": 1,
            "min_interval_ms": 3000,
            "tags": ["free-tier", "bootstrap", "openai-compatible", "strongest-known"],
        },
        "google_gemma_3_27b": {
            "provider": "google",
            "model": "gemma-3-27b-it",
            "transport": "cloud",
            "api_key_env": "GEMINI_API_KEY",
            "endpoint_url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            "requests_per_minute": 20,
            "max_concurrency": 1,
            "min_interval_ms": 3000,
            "tags": ["free-tier", "gemma", "openai-compatible"],
        },
        "openrouter_free": {
            "provider": "openrouter",
            "model": "openrouter/free",
            "transport": "cloud",
            "api_key_env": "OPENROUTER_API_KEY",
            "endpoint_url": "https://openrouter.ai/api/v1/chat/completions",
            "requests_per_minute": 10,
            "max_concurrency": 1,
            "min_interval_ms": 6000,
            "tags": ["free-tier", "fallback", "openai-compatible"],
        },
    },
    "agent": {
        "lmstudio_local": {
            "provider": "lmstudio",
            "model": "mlx-qwen3.5-4b-claude-4.6-opus-reasoning-distilled-v2",
            "transport": "local",
            "endpoint_url": "http://127.0.0.1:1234/v1/chat/completions",
            "requests_per_minute": 6,
            "max_concurrency": 1,
            "min_interval_ms": 4000,
            "tags": ["local", "default"],
        },
        "ollama_local": {
            "provider": "ollama",
            "model": "gemma3:27b",
            "transport": "local",
            "endpoint_url": "http://127.0.0.1:11434/v1/chat/completions",
            "max_concurrency": 1,
            "min_interval_ms": 500,
            "tags": ["local", "ollama"],
        },
    },
}

class ModelRegistry:
    """
    @summary Coordinates endpoint discovery and provider instantiation.
    """
    def __init__(self, config: Dict[str, List[Dict]] = None):
        self.pools: Dict[str, ModelPool] = {}
        self._config = config or {}
        if config:
            self._load_config(config)

    def _normalize_pool_config(self, pool_name: str, pool_value: Any) -> Dict[str, Any]:
        if isinstance(pool_value, list):
            return {
                "name": pool_name,
                "allow_cloud": True,
                "allow_local": True,
                "preferred_transport": None,
                "endpoints": pool_value,
            }
        if isinstance(pool_value, dict):
            normalized = dict(pool_value)
            normalized.setdefault("name", pool_name)
            normalized.setdefault("allow_cloud", True)
            normalized.setdefault("allow_local", True)
            normalized.setdefault("preferred_transport", None)
            normalized["endpoints"] = list(normalized.get("endpoints") or [])
            return normalized
        raise ValueError(f"Invalid pool config for '{pool_name}': expected list or dict.")

    def _load_config(self, config: Dict[str, Any]):
        if not config:
            return
        normalized_config: Dict[str, Dict[str, Any]] = {}
        pools: Dict[str, ModelPool] = {}
        for pool_name, pool_value in config.items():
            normalized_pool = self._normalize_pool_config(pool_name, pool_value)
            endpoints = [ModelEndpoint(**endpoint) for endpoint in normalized_pool.get("endpoints") or []]
            pools[pool_name] = ModelPool(
                name=pool_name,
                allow_cloud=bool(normalized_pool.get("allow_cloud", True)),
                allow_local=bool(normalized_pool.get("allow_local", True)),
                preferred_transport=normalized_pool.get("preferred_transport"),
                endpoints=endpoints,
            )
            normalized_config[pool_name] = {
                "allow_cloud": pools[pool_name].allow_cloud,
                "allow_local": pools[pool_name].allow_local,
                "preferred_transport": pools[pool_name].preferred_transport,
                "endpoints": [endpoint.model_dump() for endpoint in endpoints],
            }
        self._config = normalized_config
        self.pools = pools

    def to_dict(self) -> Dict[str, List[Dict]]:
        """
        @summary Returns the current registry configuration as a serializable dictionary.
        """
        return self._config

    def presets(self) -> Dict[str, Dict[str, Dict]]:
        return REGISTRY_PRESETS

    def resolve_endpoint_for_context(
        self,
        context: ExecutionContext,
        preferred_model: Optional[str] = None,
    ) -> ModelEndpoint:
        """
        @summary Resolves the concrete endpoint for the given execution context.
        """
        pool_name = context.mode
        if pool_name not in self.pools:
            raise ValueError(f"No model pool found for context mode '{pool_name}'")

        pool = self.pools[pool_name]
        allow_cloud = pool.allow_cloud if context.allow_cloud is None else bool(context.allow_cloud)
        allow_local = pool.allow_local if context.allow_local is None else bool(context.allow_local)
        fallback_endpoint: Optional[ModelEndpoint] = None
        preferred_transport = pool.preferred_transport

        candidate_endpoints: List[ModelEndpoint] = []
        for endpoint in pool.endpoints:
            if endpoint.transport == "cloud" and not allow_cloud:
                continue
            if endpoint.transport == "local" and not allow_local:
                continue
            candidate_endpoints.append(endpoint)

        if not candidate_endpoints:
            raise ValueError(f"No suitable model found in pool '{pool_name}' for the current context.")

        for endpoint in candidate_endpoints:
            if preferred_model and endpoint.model == preferred_model:
                return endpoint

        if preferred_model:
            override_base = None
            if preferred_transport:
                override_base = next(
                    (endpoint for endpoint in candidate_endpoints if endpoint.transport == preferred_transport),
                    None,
                )
            if override_base is None:
                override_base = candidate_endpoints[0]
            return override_base.model_copy(update={"model": preferred_model})

        if preferred_transport:
            for endpoint in candidate_endpoints:
                if endpoint.transport == preferred_transport:
                    return endpoint

        fallback_endpoint = candidate_endpoints[0]

        if fallback_endpoint is not None:
            return fallback_endpoint

        raise ValueError(f"No suitable model found in pool '{pool_name}' for the current context.")

    def get_provider_for_context(
        self,
        context: ExecutionContext,
        preferred_model: Optional[str] = None,
    ) -> BaseModelProvider:
        """
        @summary Resolves a provider that complies with the given execution context.
        """
        endpoint = self.resolve_endpoint_for_context(context, preferred_model=preferred_model)

        # Resolve API key if needed
        api_key = os.environ.get(endpoint.api_key_env) if endpoint.api_key_env else None

        if endpoint.transport == "local":
            url = endpoint.endpoint_url or "http://127.0.0.1:1234/v1/chat/completions"
            return LocalProvider(
                model_id=endpoint.model,
                provider_id=endpoint.provider,
                endpoint_url=url,
                api_key=api_key,
                requests_per_minute=endpoint.requests_per_minute,
                max_concurrency=endpoint.max_concurrency,
                min_interval_ms=endpoint.min_interval_ms,
            )

        if not api_key and endpoint.api_key_env:
            raise ValueError(
                f"Missing API key env '{endpoint.api_key_env}' for cloud provider '{endpoint.provider}'"
            )

        url = endpoint.endpoint_url or "https://openrouter.ai/api/v1/chat/completions"
        return CloudProvider(
            model_id=endpoint.model,
            provider_id=endpoint.provider,
            endpoint_url=url,
            api_key=api_key,
            requests_per_minute=endpoint.requests_per_minute,
            max_concurrency=endpoint.max_concurrency,
            min_interval_ms=endpoint.min_interval_ms,
        )

# Example default registry (can be overridden)
DEFAULT_CONFIG = {
    "trainer": {
        "allow_cloud": True,
        "allow_local": False,
        "preferred_transport": "cloud",
        "endpoints": [
            {
                "provider": "google",
                "model": "gemma-3-27b-it",
                "transport": "cloud",
                "api_key_env": "GEMINI_API_KEY",
                "endpoint_url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                "requests_per_minute": 10,
                "max_concurrency": 1,
                "min_interval_ms": 6000
            }
        ],
    },
    "agent": {
        "allow_cloud": False,
        "allow_local": True,
        "preferred_transport": "local",
        "endpoints": [
            {
                "provider": "lmstudio",
                "model": "mlx-qwen3.5-4b-claude-4.6-opus-reasoning-distilled-v2",
                "transport": "local",
                "endpoint_url": "http://127.0.0.1:1234/v1/chat/completions",
                "requests_per_minute": 6,
                "max_concurrency": 1,
                "min_interval_ms": 4000
            }
        ],
    },
}

registry = ModelRegistry(DEFAULT_CONFIG)
