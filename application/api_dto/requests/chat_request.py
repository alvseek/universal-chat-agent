"""Request DTO: the contract a bridge sends to POST /chat."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    # Opaque per-conversation key set by the bridge, e.g. "telegram:12345".
    conversation_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
