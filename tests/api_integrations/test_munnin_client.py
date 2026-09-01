"""Munnin client tests — the server is doubled with httpx.MockTransport.

Verifies the call shape (path, agent_id query, bearer from the provider), that the
payload comes back untouched, and that 401 / other errors surface as typed errors.
"""
import asyncio

import httpx
import pytest

from application.api_integrations.munnin.munnin_client import (
    MunninAuthError,
    MunninClient,
    MunninError,
)


async def _token():
    return "bearer-1"


def _client(handler):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return MunninClient("https://munnin.example/", _token, http)


def test_awaken_calls_api_with_agent_id_and_bearer():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"agent_id": "x", "identity": [{"title": "t", "content": "c"}]})

    payload = asyncio.run(_client(handler).awaken("x"))

    assert seen["url"] == "https://munnin.example/api/awaken?agent_id=x"
    assert seen["auth"] == "Bearer bearer-1"
    assert payload["identity"][0]["content"] == "c"


def test_401_is_auth_error():
    def handler(request):
        return httpx.Response(401, text="invalid token")

    with pytest.raises(MunninAuthError):
        asyncio.run(_client(handler).awaken("x"))


def test_5xx_is_munnin_error():
    def handler(request):
        return httpx.Response(503, text="down")

    with pytest.raises(MunninError):
        asyncio.run(_client(handler).awaken("x"))


def test_non_object_body_is_munnin_error():
    def handler(request):
        return httpx.Response(200, json=[1, 2, 3])

    with pytest.raises(MunninError):
        asyncio.run(_client(handler).awaken("x"))
