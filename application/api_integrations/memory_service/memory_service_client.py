"""External integration: read an agent's awakening from a memory service over HTTP.

Reference adapter, written against the Munnin server: server-to-server HTTP rather
than MCP, ``GET /api/awaken?agent_id=…`` behind a bearer a ``TokenProvider``
supplies, returning the payload as the plain JSON it arrived in. What the payload
*means* — which parts become a prompt, in what order — is the domain's business
(``business_domain/awakening_domain.py``), not the transport's, so a change on the
service's side never has to be mirrored here.
"""
from __future__ import annotations

from typing import Any

import httpx

from application.api_integrations.oidc.token_provider import TokenProvider


class MemoryServiceError(RuntimeError):
    """The memory service could not be reached or answered with an error."""


class MemoryServiceAuthError(MemoryServiceError):
    """The memory service refused the bearer — wrong audience, expired, or unmapped identity."""


class MemoryServiceClient:
    def __init__(
        self, base_url: str, token_provider: TokenProvider, client: httpx.AsyncClient
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = token_provider
        self._client = client

    async def awaken(self, agent_id: str) -> dict[str, Any]:
        """The agent's full awakening payload, exactly as the service assembled it."""
        bearer = await self._token()
        resp = await self._client.get(
            self._base + "/api/awaken",
            params={"agent_id": agent_id},
            headers={"Authorization": f"Bearer {bearer}"},
        )
        if resp.status_code == 401:
            raise MemoryServiceAuthError(f"memory service refused the token: {resp.text[:200]}")
        if resp.status_code != 200:
            raise MemoryServiceError(f"memory service answered {resp.status_code}: {resp.text[:200]}")
        payload = resp.json()
        if not isinstance(payload, dict):
            raise MemoryServiceError("memory service answered 200 with a non-object body")
        return payload
