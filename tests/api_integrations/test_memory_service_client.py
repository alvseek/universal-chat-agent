"""Memory-service client tests — the server is doubled with httpx.MockTransport.

Verifies the call shape (path, agent_id query, bearer from the provider), that the
payload comes back untouched, and that 401 / other errors surface as typed errors.
"""
import asyncio

import httpx
import pytest

from application.api_integrations.memory_service.memory_service_client import (
    MemoryServiceAuthError,
    MemoryServiceClient,
    MemoryServiceError,
)


async def _token():
    return "bearer-1"


def _client(handler):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return MemoryServiceClient("https://memory.example/", _token, http)


def test_awaken_calls_api_with_agent_id_and_bearer():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"agent_id": "x", "identity": [{"title": "t", "content": "c"}]})

    payload = asyncio.run(_client(handler).awaken("x"))

    assert seen["url"] == "https://memory.example/api/awaken?agent_id=x"
    assert seen["auth"] == "Bearer bearer-1"
    assert payload["identity"][0]["content"] == "c"


def test_401_is_auth_error():
    def handler(request):
        return httpx.Response(401, text="invalid token")

    with pytest.raises(MemoryServiceAuthError):
        asyncio.run(_client(handler).awaken("x"))


def test_5xx_is_a_memory_service_error():
    def handler(request):
        return httpx.Response(503, text="down")

    with pytest.raises(MemoryServiceError):
        asyncio.run(_client(handler).awaken("x"))


def test_non_object_body_is_a_memory_service_error():
    def handler(request):
        return httpx.Response(200, json=[1, 2, 3])

    with pytest.raises(MemoryServiceError):
        asyncio.run(_client(handler).awaken("x"))


def test_rendering_a_partial_awakening_makes_no_extra_request():
    """The page notice is a rendering of facts already in the payload.

    A paged reply is finished by calling the memory server again, but *this* client
    never does it: the notice tells the model what remains and what to ask for, and
    the request count stays at the one ``awaken``. A second request here would be a
    silent, unrequested fetch.
    """
    from application.business_domain import awakening_domain as ad

    calls = []
    payload = {
        "agent_id": "x",
        "identity": [{"uuid": "i1", "title": "Identity", "content": "**Name**: Op"}],
        "latest_episode": {
            "uuid": "abcdef0123456789abcdef0123456789",
            "title": "Rolling episode",
            "content": "### 2026-11-10 10.05 - newest\n\nbody\n",
            "blocks_returned": 3,
            "blocks_total": 5,
            "next_cursor": "2026-11-10 10.02",
        },
    }

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=payload)

    async def run() -> str:
        return ad.assemble_system_prompt(await _client(handler).awaken("x"))

    prompt = asyncio.run(run())

    assert calls == ["https://memory.example/api/awaken?agent_id=x"]
    assert "Partial record" in prompt
    assert "3 of 5 timestamp groups" in prompt
    assert payload["latest_episode"]["uuid"] in prompt
    assert payload["latest_episode"]["next_cursor"] in prompt

