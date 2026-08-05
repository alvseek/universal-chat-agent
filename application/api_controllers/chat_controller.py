"""Presentation layer: HTTP endpoints. No business logic lives here.

Parses/validates the request (via the DTOs), calls the service, formats the
response. The service is resolved from ``app.state`` (wired in main).
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from application.api_dto.requests.chat_request import ChatRequest
from application.api_dto.responses.chat_response import ChatResponse

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request) -> ChatResponse:
    service = request.app.state.chat_service
    reply = await service.handle(req.conversation_id, req.message)
    return ChatResponse(reply=reply)
