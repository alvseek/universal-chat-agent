"""External integration: the Invintiry inventory API.

The ONLY module that knows Invintiry's HTTP surface. It speaks the wire shapes
(DTOs, endpoints, Bearer auth) and returns raw API payloads; mapping those to
the operator's tool contract lives in the toolset, and name resolution lives in
the domain layer. The credential is a long-lived RS256 agent token minted by the
workspace OWNER; the token itself selects the workspace, so no workspace id ever
appears here.
"""
from __future__ import annotations

from typing import Any

import httpx


class InvintiryError(Exception):
    """The API refused or failed a call.

    ``status`` is the HTTP status (0 for transport failures); ``detail`` is the
    API's own message where it gave one.
    """

    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"invintiry API error {status}: {detail}")


class InvintiryClient:
    def __init__(
        self, base_url: str, token: str, client: httpx.AsyncClient | None = None
    ) -> None:
        self._base = base_url.rstrip("/")
        # Injectable so tests can hand in a client with a mock transport.
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._headers = {"Authorization": f"Bearer {token}"}

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            resp = await self._client.request(
                method, f"{self._base}{path}", headers=self._headers, **kwargs
            )
        except httpx.HTTPError as exc:
            raise InvintiryError(0, f"invintiry unreachable: {exc}") from exc
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            raise InvintiryError(resp.status_code, str(detail)[:300])
        return resp.json()

    # -- reads ---------------------------------------------------------------

    async def search_items(
        self, query: str, low_stock_only: bool = False
    ) -> list[dict[str, Any]]:
        """GET /api/items/?search= — unpaginated list of ItemDTO."""
        params: dict[str, Any] = {"search": query}
        if low_stock_only:
            params["low_stock"] = "true"
        return await self._request("GET", "/api/items/", params=params)

    async def get_item(self, item_id: int) -> dict[str, Any]:
        """GET /api/items/{id}/ — one ItemDTO."""
        return await self._request("GET", f"/api/items/{item_id}/")

    async def search_locations(self, query: str) -> list[dict[str, Any]]:
        """GET /api/locations/?search= — unpaginated list of LocationDTO."""
        return await self._request("GET", "/api/locations/", params={"search": query})

    async def search_categories(self, query: str) -> list[dict[str, Any]]:
        """GET /api/categories/?search= — unpaginated list of CategoryDTO."""
        return await self._request("GET", "/api/categories/", params={"search": query})

    # -- writes --------------------------------------------------------------

    async def create_item(
        self,
        name: str,
        location_id: int,
        quantity: int,
        category_id: int | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        """POST /api/items/ — create and place in one step (ItemDTO back)."""
        body: dict[str, Any] = {
            "name": name,
            "description": description,
            "initial_location": location_id,
            "initial_quantity": quantity,
        }
        if category_id is not None:
            body["category"] = category_id
        return await self._request("POST", "/api/items/", json=body)

    async def transfer(
        self,
        item_id: int,
        from_location_id: int,
        to_location_id: int,
        quantity: int,
        notes: str = "",
    ) -> dict[str, Any]:
        """POST /api/stock-movements/transfer/ — move stock between locations."""
        return await self._request(
            "POST",
            "/api/stock-movements/transfer/",
            json={
                "item": item_id,
                "from_location": from_location_id,
                "to_location": to_location_id,
                "quantity": quantity,
                "notes": notes,
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()
