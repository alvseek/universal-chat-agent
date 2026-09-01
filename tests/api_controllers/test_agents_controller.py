"""HTTP tests for the agent-aware surface — model-free.

Verifies: /chat forwards agent_id (and omits it when absent); /agents/{id}/reload
drives the registry; a brain without a registry answers 400 on reload; an unknown
agent surfaces as 404 through the error handler.
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from application.api_controllers.chat_controller import router
from application.api_integrations.munnin.munnin_client import MunninError
from application.business_domain.awakening_domain import AgentNotFound
from application.middleware.error_handler import (
    handle_not_found,
    handle_upstream,
    handle_value_error,
)


class _StubService:
    def __init__(self, raise_not_found=False, raise_upstream=False):
        self.calls = []
        self.raise_not_found = raise_not_found
        self.raise_upstream = raise_upstream

    async def handle(self, conversation_id, message, agent_id=None):
        self.calls.append((conversation_id, message, agent_id))
        if self.raise_not_found:
            raise AgentNotFound(agent_id)
        if self.raise_upstream:
            raise MunninError("munnin answered 503: down")
        return f"echo:{message}:{agent_id}"


class _StubRegistry:
    def __init__(self):
        self.reloaded = []

    async def reload(self, agent_id):
        self.reloaded.append(agent_id)


def _client(service, registry):
    app = FastAPI()
    app.state.chat_service = service
    app.state.agent_registry = registry
    app.include_router(router)
    app.add_exception_handler(AgentNotFound, handle_not_found)
    app.add_exception_handler(MunninError, handle_upstream)
    app.add_exception_handler(ValueError, handle_value_error)
    return TestClient(app, raise_server_exceptions=False)


def test_memory_service_failure_is_503_with_message():
    resp = _client(_StubService(raise_upstream=True), _StubRegistry()).post(
        "/chat", json={"conversation_id": "telegram:1", "message": "hi", "agent_id": "op"}
    )
    assert resp.status_code == 503
    assert "memory service unavailable" in resp.json()["error"]


def test_chat_forwards_agent_id():
    service = _StubService()
    resp = _client(service, _StubRegistry()).post(
        "/chat", json={"conversation_id": "telegram:1", "message": "hi", "agent_id": "op"}
    )
    assert resp.status_code == 200
    assert resp.json() == {"reply": "echo:hi:op"}
    assert service.calls == [("telegram:1", "hi", "op")]


def test_chat_without_agent_id_passes_none():
    service = _StubService()
    _client(service, _StubRegistry()).post(
        "/chat", json={"conversation_id": "telegram:1", "message": "hi"}
    )
    assert service.calls == [("telegram:1", "hi", None)]


def test_chat_rejects_empty_agent_id():
    resp = _client(_StubService(), _StubRegistry()).post(
        "/chat", json={"conversation_id": "telegram:1", "message": "hi", "agent_id": ""}
    )
    assert resp.status_code == 422


def test_unknown_agent_is_404():
    resp = _client(_StubService(raise_not_found=True), _StubRegistry()).post(
        "/chat", json={"conversation_id": "telegram:1", "message": "hi", "agent_id": "ghost"}
    )
    assert resp.status_code == 404
    assert "ghost" in resp.json()["error"]


def test_reload_drives_registry():
    registry = _StubRegistry()
    resp = _client(_StubService(), registry).post("/agents/op/reload")
    assert resp.status_code == 200
    assert resp.json() == {"agent_id": "op", "reloaded": True}
    assert registry.reloaded == ["op"]


def test_reload_without_registry_is_400():
    resp = _client(_StubService(), None).post("/agents/op/reload")
    assert resp.status_code == 400
    assert "MUNNIN_URL" in resp.json()["error"]
