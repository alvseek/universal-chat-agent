"""Token provider tests — the issuer is doubled with httpx.MockTransport.

Verifies the client_credentials exchange (grant, resource, scope, Basic auth), the
expiry-aware cache (one exchange until the token nears expiry), and that an issuer
refusal surfaces as TokenError rather than a token.
"""
import asyncio
import base64
import json

import httpx
import pytest

from application.api_integrations.authentra.token_provider import (
    ClientCredentials,
    ClientCredentialsTokenProvider,
    TokenError,
)

CREDS = ClientCredentials(
    issuer="https://auth.example/oidc",
    client_id="app-1",
    client_secret="s3cret",
    resource="https://munnin.example/mcp",
    scope="memory:read",
)


class _Issuer:
    """Records every token request; answers with a fresh token each time."""

    def __init__(self, status=200, expires_in=3600):
        self.requests = []
        self.status = status
        self.expires_in = expires_in

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "invalid_client"})
        return httpx.Response(
            200,
            json={
                "access_token": f"tok-{len(self.requests)}",
                "token_type": "Bearer",
                "expires_in": self.expires_in,
            },
        )


def _provider(issuer, clock):
    client = httpx.AsyncClient(transport=httpx.MockTransport(issuer.handler))
    return ClientCredentialsTokenProvider(CREDS, client, leeway=60, clock=clock)


def test_exchange_sends_grant_resource_scope_and_basic_auth():
    issuer = _Issuer()
    provider = _provider(issuer, clock=lambda: 1000.0)

    token = asyncio.run(provider())

    assert token == "tok-1"
    req = issuer.requests[0]
    assert req.url == "https://auth.example/oidc/token"
    form = dict(pair.split("=") for pair in req.content.decode().split("&"))
    assert form["grant_type"] == "client_credentials"
    assert form["resource"] == "https%3A%2F%2Fmunnin.example%2Fmcp"
    assert form["scope"] == "memory%3Aread"
    expected = base64.b64encode(b"app-1:s3cret").decode()
    assert req.headers["authorization"] == f"Basic {expected}"


def test_token_is_reused_until_near_expiry_then_refreshed():
    issuer = _Issuer(expires_in=600)
    now = {"t": 0.0}
    provider = _provider(issuer, clock=lambda: now["t"])

    first = asyncio.run(provider())
    now["t"] = 500.0  # 100 s left, leeway is 60 -> still fresh
    second = asyncio.run(provider())
    now["t"] = 545.0  # 55 s left -> inside leeway -> refresh
    third = asyncio.run(provider())

    assert first == second == "tok-1"
    assert third == "tok-2"
    assert len(issuer.requests) == 2


def test_issuer_refusal_raises_token_error():
    issuer = _Issuer(status=401)
    provider = _provider(issuer, clock=lambda: 0.0)

    with pytest.raises(TokenError):
        asyncio.run(provider())


def test_missing_access_token_raises():
    def handler(request):
        return httpx.Response(200, json={"token_type": "Bearer"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = ClientCredentialsTokenProvider(CREDS, client, clock=lambda: 0.0)

    with pytest.raises(TokenError):
        asyncio.run(provider())


def test_json_body_shape_is_tolerated_for_expires_in():
    def handler(request):
        return httpx.Response(200, content=json.dumps({"access_token": "x", "expires_in": "oops"}))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = ClientCredentialsTokenProvider(CREDS, client, clock=lambda: 0.0)

    assert asyncio.run(provider()) == "x"
