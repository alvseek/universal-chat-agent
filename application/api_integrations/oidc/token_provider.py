"""External integration: a machine credential from the identity provider.

The brain is a server, not a person, so it authenticates to other services with the
OAuth ``client_credentials`` grant: its own id and secret, exchanged at the issuer's
token endpoint for a bearer bound to one resource (RFC 8707 ``resource``). The token
is cached and reused until shortly before it expires.

This module knows nothing about *which* service the token is for beyond the resource
string it is asked to name — any client that needs a bearer takes a ``TokenProvider``
and calls it. Swapping the issuer, or handing a client a static token in a test, is a
different callable, not a different client.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx

# Anything awaitable that yields a bearer string. The client does not care how.
TokenProvider = Callable[[], Awaitable[str]]


class TokenError(RuntimeError):
    """The issuer refused or failed to issue a token."""


@dataclass(frozen=True)
class ClientCredentials:
    issuer: str  # e.g. https://auth.lok.quest/oidc — token endpoint is issuer + "/token"
    client_id: str
    client_secret: str
    resource: str  # the API resource indicator the token must be bound to
    scope: str = ""


class ClientCredentialsTokenProvider:
    """``client_credentials`` with an expiry-aware cache.

    ``leeway`` seconds before the issuer's ``expires_in`` runs out the token is treated
    as expired, so a request never leaves carrying a bearer about to lapse in flight.
    Concurrent first calls share one exchange (the lock), rather than each minting.
    """

    def __init__(
        self,
        credentials: ClientCredentials,
        client: httpx.AsyncClient,
        *,
        leeway: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._creds = credentials
        self._client = client
        self._leeway = leeway
        self._clock = clock
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    def _fresh(self) -> bool:
        return self._token is not None and self._clock() < self._expires_at - self._leeway

    async def __call__(self) -> str:
        if self._fresh():
            return self._token  # type: ignore[return-value]
        async with self._lock:
            if self._fresh():
                return self._token  # type: ignore[return-value]
            return await self._mint()

    async def _mint(self) -> str:
        form = {
            "grant_type": "client_credentials",
            "resource": self._creds.resource,
        }
        if self._creds.scope:
            form["scope"] = self._creds.scope
        resp = await self._client.post(
            self._creds.issuer.rstrip("/") + "/token",
            data=form,
            auth=(self._creds.client_id, self._creds.client_secret),
        )
        if resp.status_code != 200:
            raise TokenError(
                f"token endpoint answered {resp.status_code}: {resp.text[:200]}"
            )
        body = resp.json()
        token = body.get("access_token")
        if not token:
            raise TokenError("token endpoint answered 200 without an access_token")
        try:
            ttl = float(body.get("expires_in", 0))
        except (TypeError, ValueError):
            ttl = 0.0
        self._token = token
        self._expires_at = self._clock() + ttl
        return token
