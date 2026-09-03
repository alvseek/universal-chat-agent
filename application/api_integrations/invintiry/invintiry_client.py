"""External integration: the Invintiry inventory API.

The ONLY module that knows Invintiry's HTTP surface. It speaks the wire shapes
(DTOs, endpoints, Bearer auth) and returns raw API payloads; mapping those to
the operator's tool contract lives in the toolset, and name resolution lives in
the domain layer. The credential is a long-lived RS256 agent token minted by the
workspace OWNER; the token itself selects the workspace, so no workspace id ever
appears here.
"""
from __future__ import annotations

from typing import Any, Mapping

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
        # Injectable so tests can hand in a client with a mock transport, and so
        # the per-caller instances built each chat turn can share one connection
        # pool instead of opening their own.
        self._client = client or httpx.AsyncClient(timeout=30.0)
        # Only the instance that created the pool may close it: a per-caller
        # instance closing a shared client would break every other caller.
        self._owns_client = client is None
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
        if resp.status_code == 204 or not resp.content:
            return None  # a bodyless success (DELETE) is not a JSON document
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
        """GET /api/categories/?search= — unpaginated list of CategoryDTO.

        Item categories. Locations hang off a *separate* tree — see
        ``search_location_categories``; the two share a shape and nothing else.
        """
        return await self._request("GET", "/api/categories/", params={"search": query})

    async def get_location(self, location_id: int) -> dict[str, Any]:
        """GET /api/locations/{id}/ — one LocationDTO.

        Integer ids only: the deployed schema documents this path parameter as
        an integer, so callers resolve a name to an id first.
        """
        return await self._request("GET", f"/api/locations/{location_id}/")

    async def list_item_locations(self, location_id: int) -> list[dict[str, Any]]:
        """GET /api/item-locations/?location= — what is stored in one location.

        Each row carries ``item_name`` and ``quantity``, so naming the contents
        needs no follow-up call per item.
        """
        return await self._request(
            "GET", "/api/item-locations/", params={"location": location_id}
        )

    async def search_location_categories(self, query: str) -> list[dict[str, Any]]:
        """GET /api/location-categories/?search= — unpaginated LocationCategoryDTO.

        Deliberately not ``search_categories``: that one serves items, and the
        two endpoints take identical parameters over different tables, so an id
        borrowed from the wrong tree can be valid and wrong rather than refused.
        """
        return await self._request(
            "GET", "/api/location-categories/", params={"search": query}
        )

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

    async def create_location(
        self,
        name: str,
        category_id: int | None = None,
        description: str = "",
        expiry_warning_days: int | None = None,
    ) -> dict[str, Any]:
        """POST /api/locations/ — create a storage location (LocationDTO back).

        Only ``name`` is required by the API, and it is unique per workspace, so
        a clashing name comes back as the API's own 400 rather than needing a
        pre-check here.
        """
        body: dict[str, Any] = {"name": name, "description": description}
        if category_id is not None:
            body["category"] = category_id
        if expiry_warning_days is not None:
            body["expiry_warning_days"] = expiry_warning_days
        return await self._request("POST", "/api/locations/", json=body)

    async def update_location(
        self, location_id: int, fields: Mapping[str, Any]
    ) -> dict[str, Any]:
        """PATCH /api/locations/{id}/ — partial update (LocationDTO back).

        ``fields`` carries only what changes; anything absent is left alone by
        the server. The caller is responsible for never putting a read-only
        field (``slug``, the counts) in there.
        """
        return await self._request(
            "PATCH", f"/api/locations/{location_id}/", json=dict(fields)
        )

    async def aclose(self) -> None:
        await self._client.aclose()
