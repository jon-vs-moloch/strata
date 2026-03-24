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
    tags: List[str] = Field(default_factory=list, description="Metadata tags for filtering.")

class ModelPool(BaseModel):
    """
    @summary Filtered collection of model endpoints for a specific execution tier.
    """
    name: str = Field(..., description="Name of the pool (e.g., 'strong', 'weak').")
    endpoints: List[ModelEndpoint] = Field(..., description="Endpoints available in this pool.")
