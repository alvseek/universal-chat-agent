"""Presentation layer: HTTP endpoints. No business logic lives here.

Parses/validates the request (via the DTOs), calls the service, formats the
response. The service and the agent registry are resolved from ``app.state``
(wired in main).
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from application.api_dto.requests.chat_request import ChatRequest
from application.api_dto.responses.chat_response import ChatResponse
from application.business_services.chat_service import RegistryUnavailable

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request) -> ChatResponse:
    service = request.app.state.chat_service
    reply = await service.handle(
        req.conversation_id, req.message, req.agent_id, req.end_user_id
    )
    return ChatResponse(reply=reply)


@router.post("/agents/{agent_id}/reload")
async def reload_agent(agent_id: str, request: Request) -> dict:
    """Rebuild one agent from the memory service now, ahead of its cache TTL."""
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        raise RegistryUnavailable("this brain has no memory service configured (MUNNIN_URL)")
    await registry.reload(agent_id)
    return {"agent_id": agent_id, "reloaded": True}
