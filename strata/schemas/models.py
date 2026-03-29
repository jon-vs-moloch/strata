"""
@module schemas.models
@purpose Define Pydantic schemas for ModelEndpoint and ModelPool abstractions.
@key_exports ModelEndpoint, ModelPool
"""

from pydantic import BaseModel, Field
from typing import List, Literal, Optional

class ModelEndpoint(BaseModel):
    """
    @summary Concrete transport-aware model definition.
    """
    provider: str = Field(..., description="The model provider (e.g., 'ollama', 'openrouter', 'openai').")
    model: str = Field(..., description="The name of the model on this provider.")
    transport: Literal["local", "cloud"] = Field(..., description="The transport layer used for this model.")
    endpoint_url: Optional[str] = Field(None, description="The custom URL for this endpoint if applicable.")
    api_key_env: Optional[str] = Field(None, description="The environment variable containing the API key.")
    requests_per_minute: Optional[int] = Field(None, description="Soft request budget for this endpoint.")
    max_concurrency: Optional[int] = Field(None, description="Maximum in-flight requests allowed for this endpoint.")
    min_interval_ms: Optional[int] = Field(None, description="Minimum delay between requests to this endpoint.")
    tags: List[str] = Field(default_factory=list, description="Metadata tags for filtering.")

class ModelPool(BaseModel):
    """
    @summary Filtered collection of model endpoints for a specific execution tier.
    """
    name: str = Field(..., description="Name of the pool (e.g., 'strong', 'weak').")
    allow_cloud: bool = Field(True, description="Whether cloud-backed endpoints are allowed by default for this pool.")
    allow_local: bool = Field(True, description="Whether local endpoints are allowed by default for this pool.")
    preferred_transport: Optional[Literal["local", "cloud"]] = Field(
        None,
        description="Preferred transport for this pool when multiple endpoints are eligible.",
    )
    endpoints: List[ModelEndpoint] = Field(..., description="Endpoints available in this pool.")
