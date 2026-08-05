"""Response DTO: the contract POST /chat returns to a bridge."""
from __future__ import annotations

from pydantic import BaseModel


class ChatResponse(BaseModel):
    reply: str
