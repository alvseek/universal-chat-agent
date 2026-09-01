"""Request DTO: the contract a bridge sends to POST /chat."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    # Opaque per-conversation key set by the bridge, e.g. "telegram:12345".
    conversation_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    # Which agent answers. Absent = the brain's default agent (SYSTEM_PROMPT);
    # present = that agent is awakened from the memory service and answers as itself.
    agent_id: str | None = Field(default=None, min_length=1)
