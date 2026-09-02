"""The invintiry toolset: the operator's four tools over the Invintiry API.

Implements the operator's tool contract (authored in its identity): reads
resolve names themselves and answer in contract shapes; writes require
human-in-the-loop approval (``requires_approval=True`` — the model cannot call
them directly, the run pauses and only the application can resume it).

API failures come back to the model as data (``{"error": ...}``), never as
exceptions: the operator's product rule is a plain "inventory isn't reachable"
line, and a stack trace has no way to become one.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping

from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.toolsets.abstract import AbstractToolset

from application.api_integrations.invintiry.invintiry_client import (
    InvintiryClient,
    InvintiryError,
)
from application.business_domain import inventory_resolution as resolution

log = logging.getLogger("universal-chat-agent")


def _item_brief(dto: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": dto.get("id"),
        "name": dto.get("name"),
        "sku": dto.get("sku"),
        "quantity": dto.get("quantity"),
        "is_low_stock": dto.get("is_low_stock"),
        "locations": [
            {"location": loc.get("location_name"), "quantity": loc.get("quantity")}
            for loc in dto.get("item_locations") or []
        ],
    }


def _item_detail(dto: Mapping[str, Any]) -> dict[str, Any]:
    detail = _item_brief(dto)
    detail.update(
        description=dto.get("description"),
        bottom_stock=dto.get("bottom_stock"),
        category=dto.get("category_name"),
        tags=dto.get("tags"),
    )
    for loc, raw in zip(detail["locations"], dto.get("item_locations") or []):
        loc["expires_at"] = raw.get("expires_at")
    return detail


def _api_error(exc: InvintiryError) -> dict[str, Any]:
    log.warning("invintiry API error %s: %s", exc.status, exc.detail)
    if exc.status == 0:
        return {"error": "unreachable", "detail": "inventory service not reachable"}
    if exc.status in (401, 403):
        return {"error": "auth_failed", "detail": "inventory credential refused"}
    return {"error": "api_error", "detail": exc.detail}


def build_invintiry_toolsets(deps: Mapping[str, Any]) -> list[AbstractToolset]:
    client: InvintiryClient = deps["invintiry_client"]

    async def _resolve_item(ref: str) -> tuple[int | None, dict[str, Any] | None]:
        """(item_id, error_dict) — numeric refs skip the search round-trip."""
        ref = str(ref).strip()
        if ref.isdigit():
            return int(ref), None
        items = await client.search_items(ref)
        r = resolution.resolve_item(items, ref)
        if r.kind == "match":
            return r.id, None
        if r.kind == "ambiguous":
            return None, {"error": "ambiguous", "candidates": r.candidates}
        return None, {"error": "not_found", "detail": f"no item matches {ref!r}"}

    async def _resolve_location(ref: str) -> tuple[int | None, dict[str, Any] | None]:
        ref = str(ref).strip()
        if ref.isdigit():
            return int(ref), None
        locations = await client.search_locations(ref)
        r = resolution.resolve_location(locations, ref)
        if r.kind == "match":
            return r.id, None
        if r.kind == "ambiguous":
            return None, {"error": "location_ambiguous", "candidates": r.candidates}
        return None, {"error": "location_not_found", "detail": f"no location matches {ref!r}"}

    async def find_items(
        query: str, low_stock_only: bool = False, limit: int = 10
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """Search items by name, SKU, or description; empty list means nothing matched."""
        try:
            items = await client.search_items(query, low_stock_only=low_stock_only)
        except InvintiryError as exc:
            return _api_error(exc)
        return [_item_brief(i) for i in items[: max(1, limit)]]

    async def get_item(item: str) -> dict[str, Any]:
        """Full detail for one item; `item` is an id, a SKU, or a name."""
        try:
            item_id, err = await _resolve_item(item)
            if err:
                return err
            return _item_detail(await client.get_item(item_id))
        except InvintiryError as exc:
            return _api_error(exc)

    async def create_item(
        name: str,
        location: str,
        quantity: int,
        category: str | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        """Create an item and place `quantity` of it in `location` in one step."""
        try:
            existing = await client.search_items(name)
            dup = [i for i in existing if str(i.get("name", "")).lower() == name.lower()]
            if dup:
                return {
                    "error": "duplicate_name",
                    "detail": "an item with that exact name already exists",
                    "existing": _item_brief(dup[0]),
                }
            location_id, err = await _resolve_location(location)
            if err:
                return err
            category_id: int | None = None
            if category:
                cats = await client.search_categories(category)
                hits = [
                    c for c in cats
                    if str(c.get("name") or "").lower() == category.lower()
                    or str(c.get("code") or "").lower() == category.lower()
                ]
                if not hits:
                    return {"error": "category_not_found", "detail": f"no category matches {category!r}"}
                category_id = hits[0]["id"]
            created = await client.create_item(
                name, location_id, quantity, category_id=category_id, description=description
            )
            return _item_detail(created)
        except InvintiryError as exc:
            return _api_error(exc)

    async def move_item(
        item: str,
        from_location: str,
        to_location: str,
        quantity: int | None = None,
    ) -> dict[str, Any]:
        """Move stock of one item between locations; omit `quantity` to move everything at the source."""
        try:
            item_id, err = await _resolve_item(item)
            if err:
                return err
            from_id, err = await _resolve_location(from_location)
            if err:
                return err
            to_id, err = await _resolve_location(to_location)
            if err:
                return err
            if from_id == to_id:
                return {"error": "same_location", "detail": "source and destination are the same"}

            def _at(dto: Mapping[str, Any], location_id: int) -> int:
                for loc in dto.get("item_locations") or []:
                    if loc.get("location") == location_id:
                        return loc.get("quantity") or 0
                return 0

            before = await client.get_item(item_id)
            if quantity is None:
                quantity = _at(before, from_id)
                if not quantity:
                    return {"error": "no_stock_at_source", "detail": "nothing of that item at the source"}
            moved = await client.transfer(item_id, from_id, to_id, quantity)
            after = await client.get_item(item_id)
            return {
                "item": before.get("name"),
                "from_location": from_location,
                "to_location": to_location,
                "moved": quantity,
                "remaining_at_source": _at(after, from_id),
                "movement_id": moved.get("id"),
            }
        except InvintiryError as exc:
            return _api_error(exc)

    reads = FunctionToolset(tools=[find_items, get_item], id="invintiry-reads")
    writes = FunctionToolset(
        tools=[create_item, move_item], id="invintiry-writes", requires_approval=True
    )
    return [reads, writes]
