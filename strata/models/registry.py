"""
@module models.registry
@purpose Central registry for model endpoints and pools.
@key_exports ModelRegistry
"""

import os
from typing import Dict, List, Optional
from strata.schemas.models import ModelPool, ModelEndpoint
from strata.schemas.execution import ExecutionContext
from strata.models.providers import LocalProvider, CloudProvider, BaseModelProvider

class ModelRegistry:
    """
    @summary Coordinates endpoint discovery and provider instantiation.
    """
    def __init__(self, config: Dict[str, List[Dict]] = None):
        self.pools: Dict[str, ModelPool] = {}
        self._config = config or {}
        if config:
            self._load_config(config)

    def _load_config(self, config: Dict[str, List[Dict]]):
        if not config: return
        self._config = config
        for pool_name, endpoints_data in config.items():
            endpoints = [ModelEndpoint(**e) for e in endpoints_data]
            self.pools[pool_name] = ModelPool(name=pool_name, endpoints=endpoints)

    def to_dict(self) -> Dict[str, List[Dict]]:
        """
        @summary Returns the current registry configuration as a serializable dictionary.
        """
        return self._config

    def get_provider_for_context(self, context: ExecutionContext) -> BaseModelProvider:
        """
        @summary Resolves a provider that complies with the given execution context.
        """
        pool_name = context.mode
        if pool_name not in self.pools:
            raise ValueError(f"No model pool found for context mode '{pool_name}'")
        
        pool = self.pools[pool_name]
        
        # Simple selection for now: first available endpoint that matches transport constraints
        for endpoint in pool.endpoints:
            if endpoint.transport == "cloud" and not context.allow_cloud:
                continue
            if endpoint.transport == "local" and not context.allow_local:
                continue
            
            # Resolve API key if needed
            api_key = os.environ.get(endpoint.api_key_env) if endpoint.api_key_env else None
            
            if endpoint.transport == "local":
                # Default local endpoint if not specified
                url = endpoint.endpoint_url or "http://127.0.0.1:1234/v1/chat/completions"
                return LocalProvider(
                    model_id=endpoint.model, 
                    provider_id=endpoint.provider, 
                    endpoint_url=url,
                    api_key=api_key
                )
            else:
                # Cloud transport
                if not api_key and endpoint.api_key_env:
                     # Warn or fail if no key? The requirements say: missing cloud credentials must fail clearly.
                     raise ValueError(f"Missing API key env '{endpoint.api_key_env}' for cloud provider '{endpoint.provider}'")
                
                url = endpoint.endpoint_url or "https://openrouter.ai/api/v1/chat/completions"
                return CloudProvider(
                    model_id=endpoint.model, 
                    provider_id=endpoint.provider, 
                    endpoint_url=url,
                    api_key=api_key
                )
        
        raise ValueError(f"No suitable model found in pool '{pool_name}' for the current context.")

# Example default registry (can be overridden)
DEFAULT_CONFIG = {
    "strong": [
        {
            "provider": "openrouter",
            "model": "anthropic/claude-3.5-sonnet",
            "transport": "cloud",
            "api_key_env": "OPENROUTER_API_KEY",
            "endpoint_url": "https://openrouter.ai/api/v1/chat/completions"
        }
    ],
    "weak": [
        {
            "provider": "lmstudio",
            "model": "mlx-qwen3.5-9b-claude-4.6-opus-reasoning-distilled-v2",
            "transport": "local",
            "endpoint_url": "http://127.0.0.1:1234/v1/chat/completions"
        }
    ]
}

registry = ModelRegistry(DEFAULT_CONFIG)
