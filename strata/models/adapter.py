from typing import Dict, Any, Optional, List
from strata.schemas.execution import ExecutionContext, StrongExecutionContext
from strata.models.registry import registry
from strata.models.providers import ModelResponse

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
        self.context = context or StrongExecutionContext(run_id="default")
        self.registry = registry
        self.last_response: Optional[ModelResponse] = None
        self._selected_models: Dict[str, str] = {}

    def bind_execution_context(self, context: ExecutionContext):
        """
        @summary Switch the execution context, re-resolving provider constraints.
        """
        self.context = context

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
            if self.context.evaluation_run and self.context.mode == "weak":
                # Strict local-only enforcement for evaluation
                if self.context.allow_cloud:
                    return {"status": "error", "message": "CRITICAL: Cloud usage attempted during weak-eval. Aborting."}

            provider = self.registry.get_provider_for_context(
                self.context,
                preferred_model=self._selected_models.get(self.context.mode),
            )
            
            # Log for auditability
            print(f"DEBUG [Context: {self.context.mode}] Routing to {provider.provider_id}/{provider.model_id} (Transport: {'local' if provider.__class__.__name__ == 'LocalProvider' else 'cloud'})")

            response: ModelResponse = await provider.complete(messages, **kwargs)
            self.last_response = response

            # Post-call validation: if weak-eval but used cloud provider, mark as invalid
            if self.context.evaluation_run and self.context.mode == "weak" and provider.__class__.__name__ == "CloudProvider":
                 return {"status": "error", "message": "CRITICAL: Cloud provider violation detected in weak-eval context."}

            return {
                "status": response.status,
                "content": response.content,
                "tool_calls": response.tool_calls,
                "model": response.model,
                "provider": response.provider,
                "usage": response.usage or {},
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

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
