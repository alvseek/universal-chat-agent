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
    # WHO is asking, namespaced by platform ("telegram:8932435376") — as opposed to
    # conversation_id, which is WHERE. Credentials hang off the person, so this is
    # what a tool call runs as. Optional: a bridge that sends none simply cannot
    # offer linking, and the default agent (which has no tools) never needs it.
    end_user_id: str | None = Field(default=None, min_length=1)
