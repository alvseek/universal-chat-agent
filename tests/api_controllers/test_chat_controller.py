"""E2E tests for the HTTP layer — model-free (the LLM is stubbed).

Verifies the controller wiring: routing, DTO validation, and that the reply the
service returns is what the endpoint sends back.
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from application.api_controllers.chat_controller import router


class _StubService:
    """Stands in for ChatService — records the call, returns a canned reply."""

    def __init__(self):
        self.calls = []

    async def handle(self, conversation_id: str, message: str, agent_id=None) -> str:
        self.calls.append((conversation_id, message))
        return f"echo:{message}"


def _make_client(service: _StubService) -> TestClient:
    app = FastAPI()
    app.state.chat_service = service
    app.include_router(router)
    return TestClient(app)


def test_health_ok():
    client = _make_client(_StubService())
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_chat_returns_service_reply():
    service = _StubService()
    client = _make_client(service)
    resp = client.post(
        "/chat", json={"conversation_id": "telegram:1", "message": "hi"}
    )
    assert resp.status_code == 200
    assert resp.json() == {"reply": "echo:hi"}
    assert service.calls == [("telegram:1", "hi")]


def test_chat_rejects_missing_fields():
    client = _make_client(_StubService())
    resp = client.post("/chat", json={"message": "hi"})
    assert resp.status_code == 422  # pydantic validation via FastAPI


def test_chat_rejects_empty_message():
    client = _make_client(_StubService())
    resp = client.post(
        "/chat", json={"conversation_id": "telegram:1", "message": ""}
    )
    assert resp.status_code == 422
