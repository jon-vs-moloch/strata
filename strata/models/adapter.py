from typing import Dict, Any, Optional, List
import json
import re
from strata.schemas.execution import ExecutionContext, TrainerExecutionContext
from strata.models.registry import registry
from strata.models.providers import ModelResponse, persist_provider_telemetry_snapshot

class ModelAdapter:
    """
    @summary Universal bridge between the orchestrator and any inference engine.
    @inputs execution_context: current runtime context
    @outputs side-effect driven (network calls) or completion strings
    """
    def __init__(self, context: Optional[ExecutionContext] = None):
        """
        @summary Initialize the ModelAdapter.
        @inputs optional execution_context
        """
        self.context = context or TrainerExecutionContext(run_id="default")
        self.registry = registry
        self.last_response: Optional[ModelResponse] = None
        self._selected_models: Dict[str, str] = {}

    def bind_execution_context(self, context: ExecutionContext):
        """
        @summary Switch the execution context, re-resolving provider constraints.
        """
        self.context = context

    def _validate_lane_transport(self, provider) -> None:
        provider_name = provider.__class__.__name__
        is_local = provider_name == "LocalProvider"
        is_cloud = provider_name == "CloudProvider"
        pool = self.registry.pools.get(self.context.mode)
        allow_local = bool(getattr(pool, "allow_local", True)) if pool is not None else True
        allow_cloud = bool(getattr(pool, "allow_cloud", True)) if pool is not None else True
        if is_local and not allow_local:
            raise RuntimeError(
                f"{self.context.mode.title()} lane transport violation: local transport is disabled for this pool."
            )
        if is_cloud and not allow_cloud:
            raise RuntimeError(
                f"{self.context.mode.title()} lane transport violation: cloud transport is disabled for this pool."
            )

    def _resolve_endpoint(self):
        preferred_model = self._selected_models.get(self.context.mode)
        return self.registry.resolve_endpoint_for_context(
            self.context,
            preferred_model=preferred_model,
        )

    @property
    def endpoint(self) -> str:
        endpoint = self._resolve_endpoint()
        if endpoint.transport == "local":
            return endpoint.endpoint_url or "http://127.0.0.1:1234/v1/chat/completions"
        return endpoint.endpoint_url or "https://openrouter.ai/api/v1/chat/completions"

    @property
    def active_model(self) -> str:
        endpoint = self._resolve_endpoint()
        return self._selected_models.get(self.context.mode, endpoint.model)

    @active_model.setter
    def active_model(self, model_id: str):
        self._selected_models[self.context.mode] = model_id

    async def chat(self, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        """
        @summary Sends a chat completion request to the provider allowed by the current ExecutionContext.
        @inputs messages: list of message objects, kwargs: additional inference parameters
        @outputs dictionary with status, content, and any tool calls
        """
        try:
            # Enforce Context Restrictions
            if self.context.evaluation_run and self.context.mode == "agent":
                # Strict local-only enforcement for evaluation
                if self.context.allow_cloud:
                    return {"status": "error", "message": "CRITICAL: Cloud usage attempted during weak-eval. Aborting."}

            provider = self.registry.get_provider_for_context(
                self.context,
                preferred_model=self._selected_models.get(self.context.mode),
            )
            self._validate_lane_transport(provider)
            
            # Log for auditability
            print(f"DEBUG [Context: {self.context.mode}] Routing to {provider.provider_id}/{provider.model_id} (Transport: {'local' if provider.__class__.__name__ == 'LocalProvider' else 'cloud'})")

            response: ModelResponse = await provider.complete(messages, **kwargs)
            self.last_response = response
            persist_provider_telemetry_snapshot()

            # Post-call validation: if weak-eval but used cloud provider, mark as invalid
            if self.context.evaluation_run and self.context.mode == "agent" and provider.__class__.__name__ == "CloudProvider":
                 return {"status": "error", "message": "CRITICAL: Cloud provider violation detected in weak-eval context."}

            return {
                "status": response.status,
                "content": response.content,
                "message": response.content if response.status == "error" else "",
                "tool_calls": response.tool_calls,
                "model": response.model,
                "provider": response.provider,
                "usage": response.usage or {},
                "error": response.error or {},
            }
        except Exception as e:
            persist_provider_telemetry_snapshot()
            return {"status": "error", "message": str(e), "content": str(e), "error": {"kind": e.__class__.__name__}}

    def extract_yaml(self, raw_content: str) -> Dict[str, Any]:
        """
        @summary Helper to extract and parse YAML blocks from model output.
        """
        import yaml
        try:
            if "```yaml" in raw_content:
                raw_content = raw_content.split("```yaml")[1].split("```")[0]
            return yaml.safe_load(raw_content)
        except Exception:
            return {"error": "Failed to parse YAML"}

    def extract_structured_object(self, raw_content: str) -> Dict[str, Any]:
        """
        @summary Best-effort parser for structured model output that may ignore strict JSON-only instructions.
        """
        import yaml

        normalized = str(raw_content or "").strip()
        if not normalized:
            return {"error": "Empty structured response"}

        fenced = re.search(r"```(?:json|yaml)?\s*(.*?)```", normalized, re.DOTALL | re.IGNORECASE)
        if fenced:
            normalized = fenced.group(1).strip()

        try:
            parsed = json.loads(normalized)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except Exception:
            pass

        json_match = re.search(r"\{.*\}", normalized, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group(0))
                return parsed if isinstance(parsed, dict) else {"value": parsed}
            except Exception:
                pass

        try:
            parsed = yaml.safe_load(normalized)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        yaml_match = re.search(r"([A-Za-z0-9_\"'\-]+\s*:\s*.+)", normalized, re.DOTALL)
        if yaml_match:
            try:
                parsed = yaml.safe_load(yaml_match.group(0))
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass

        return {"error": "Failed to parse structured object"}
