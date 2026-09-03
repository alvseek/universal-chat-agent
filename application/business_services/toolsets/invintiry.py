"""The invintiry toolset: the operator's tools over the Invintiry API.

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
from typing import Any, Callable, Mapping, Sequence

from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.toolsets.abstract import AbstractToolset

from application.api_integrations.invintiry.invintiry_client import (
    InvintiryClient,
    InvintiryError,
)
from application.business_domain import inventory_resolution as resolution
from application.business_services.chat_deps import ChatDeps

SERVICE = "invintiry"

log = logging.getLogger("universal-chat-agent")

# How many stored items one ``get_location`` answer carries. Fixed here rather
# than exposed as a tool argument: the cap exists to keep a crowded shelf from
# flooding the reply, and an argument would let the model ask for all of it.
# The answer always carries ``total``, so a clipped list is never miscounted.
LOCATION_CONTENTS_CAP = 20


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


def _location_brief(dto: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": dto.get("id"),
        "name": dto.get("name"),
        "category": dto.get("category_name"),
        "item_count": dto.get("item_count"),
    }


def _location_detail(
    dto: Mapping[str, Any], rows: Sequence[Mapping[str, Any]] | None
) -> dict[str, Any]:
    """A location plus what is stored in it.

    ``rows`` are the item-location records for this location. Only rows holding
    stock are contents — which is also how the API counts ``item_count``, so the
    two numbers cannot disagree. ``total`` counts everything stored here and
    ``truncated`` says whether the list below it was clipped.

    ``rows=None`` means the contents were never fetched, which is not the same
    as a location holding nothing: the contents keys are then left out entirely
    and ``contents_unavailable`` is set, because a ``total`` of 0 would be read
    as an empty shelf.
    """
    if rows is None:
        detail = _location_brief(dto)
        detail.update(
            description=dto.get("description"),
            expiry_warning_days=dto.get("expiry_warning_days"),
            contents_unavailable=True,
        )
        return detail
    stored = sorted(
        (r for r in rows if (r.get("quantity") or 0) > 0),
        key=lambda r: str(r.get("item_name") or "").lower(),
    )
    detail = _location_brief(dto)
    detail.update(
        description=dto.get("description"),
        expiry_warning_days=dto.get("expiry_warning_days"),
        expiring_soon=dto.get("expiring_soon_count"),
        expired=dto.get("expired_count"),
        total=len(stored),
        truncated=len(stored) > LOCATION_CONTENTS_CAP,
        contents=[
            {
                "item": r.get("item_name"),
                "quantity": r.get("quantity"),
                "expires_at": r.get("expires_at"),
            }
            for r in stored[:LOCATION_CONTENTS_CAP]
        ],
    )
    return detail


def _api_error(
    exc: InvintiryError, ctx: RunContext[ChatDeps] | None = None
) -> dict[str, Any]:
    log.warning("invintiry API error %s: %s", exc.status, exc.detail)
    if exc.status == 0:
        return {"error": "unreachable", "detail": "inventory service not reachable"}
    if exc.status in (401, 403):
        # The credential we hold has been revoked at the source — almost always a
        # disconnect from the web UI. Drop it here, so the next message asks the
        # person to link again instead of refusing forever with a stale token.
        if ctx is not None and ctx.deps.on_auth_failed is not None:
            ctx.deps.on_auth_failed(SERVICE)
        return {
            "error": "auth_failed",
            "detail": (
                "this person's inventory credential was refused and has been "
                "forgotten; ask them to connect again from Settings -> Telegram"
            ),
        }
    return {"error": "api_error", "detail": exc.detail}


def _not_linked() -> dict[str, Any]:
    """What every tool answers when this caller has no credential.

    Data, not an exception, and the same contract the API errors already use:
    the model relays it, so the person is told how to connect rather than being
    met with silence. It names the service because a caller may be linked to one
    and not another.
    """
    return {
        "error": "not_linked",
        "service": SERVICE,
        "detail": (
            "this person has not connected their inventory account; ask them to "
            "open invintiry, go to Settings -> Telegram, and tap the link shown there"
        ),
    }


def build_invintiry_toolsets(deps: Mapping[str, Any]) -> list[AbstractToolset]:
    """Tools bound once, credentials resolved per run.

    ``deps`` supplies only what is the same for everyone (how to build a client);
    who is calling arrives on each run as ``ctx.deps``.
    """
    make_client: Callable[[str], InvintiryClient] = deps["invintiry_make_client"]

    def _client_for(
        ctx: RunContext[ChatDeps],
    ) -> tuple[InvintiryClient | None, dict[str, Any] | None]:
        """(client, unlinked_error) — exactly one of the two is set."""
        token = (ctx.deps.credentials or {}).get(SERVICE)
        if not token:
            return None, _not_linked()
        return make_client(token), None

    async def _resolve_item(
        client: InvintiryClient, ref: str
    ) -> tuple[int | None, dict[str, Any] | None]:
        """(item_id, error_dict) — numeric refs skip the search round-trip."""
        ref = str(ref).strip()
        if ref.lstrip("-").isdigit():  # same numeric rule as inventory_resolution
            return int(ref), None
        items = await client.search_items(ref)
        r = resolution.resolve_item(items, ref)
        if r.kind == "match":
            return r.id, None
        if r.kind == "ambiguous":
            return None, {"error": "ambiguous", "candidates": r.candidates}
        return None, {"error": "not_found", "detail": f"no item matches {ref!r}"}

    async def _resolve_location(
        client: InvintiryClient, ref: str
    ) -> tuple[int | None, dict[str, Any] | None]:
        ref = str(ref).strip()
        if ref.lstrip("-").isdigit():  # same numeric rule as inventory_resolution
            return int(ref), None
        locations = await client.search_locations(ref)
        r = resolution.resolve_location(locations, ref)
        if r.kind == "match":
            return r.id, None
        if r.kind == "ambiguous":
            return None, {"error": "location_ambiguous", "candidates": r.candidates}
        return None, {"error": "location_not_found", "detail": f"no location matches {ref!r}"}

    async def _resolve_location_category(
        client: InvintiryClient, ref: str
    ) -> tuple[int | None, dict[str, Any] | None]:
        """Location categories only — never ``search_categories``, which serves
        items over an identically shaped endpoint, where a borrowed id would be
        accepted as a valid row from the wrong tree instead of refused."""
        ref = str(ref).strip()
        if ref.lstrip("-").isdigit():  # same numeric rule as inventory_resolution
            return int(ref), None
        cats = await client.search_location_categories(ref)
        r = resolution.resolve_location_category(cats, ref)
        if r.kind == "match":
            return r.id, None
        if r.kind == "ambiguous":
            return None, {"error": "category_ambiguous", "candidates": r.candidates}
        return None, {
            "error": "category_not_found",
            "detail": f"no location category matches {ref!r}",
        }

    async def find_items(
        ctx: RunContext[ChatDeps],
        query: str,
        low_stock_only: bool = False,
        limit: int = 10,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """Search items by name, SKU, or description; empty list means nothing matched."""
        client, unlinked = _client_for(ctx)
        if unlinked:
            return unlinked
        try:
            items = await client.search_items(query, low_stock_only=low_stock_only)
        except InvintiryError as exc:
            return _api_error(exc, ctx)
        return [_item_brief(i) for i in items[: max(1, limit)]]

    async def get_item(ctx: RunContext[ChatDeps], item: str) -> dict[str, Any]:
        """Full detail for one item; `item` is an id, a SKU, or a name."""
        client, unlinked = _client_for(ctx)
        if unlinked:
            return unlinked
        try:
            item_id, err = await _resolve_item(client, item)
            if err:
                return err
            return _item_detail(await client.get_item(item_id))
        except InvintiryError as exc:
            return _api_error(exc, ctx)

    async def find_locations(
        ctx: RunContext[ChatDeps], query: str = "", limit: int = 10
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """Search storage locations by name; empty query lists them all."""
        client, unlinked = _client_for(ctx)
        if unlinked:
            return unlinked
        try:
            locations = await client.search_locations(query)
        except InvintiryError as exc:
            return _api_error(exc, ctx)
        return [_location_brief(loc) for loc in locations[: max(1, limit)]]

    async def get_location(
        ctx: RunContext[ChatDeps], location: str
    ) -> dict[str, Any]:
        """One location and what is stored in it; `location` is an id or a name."""
        client, unlinked = _client_for(ctx)
        if unlinked:
            return unlinked
        try:
            location_id, err = await _resolve_location(client, location)
            if err:
                return err
            dto = await client.get_location(location_id)
            rows = await client.list_item_locations(location_id)
            return _location_detail(dto, rows)
        except InvintiryError as exc:
            return _api_error(exc, ctx)

    async def create_item(
        ctx: RunContext[ChatDeps],
        name: str,
        location: str,
        quantity: int,
        category: str | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        """Create an item and place `quantity` of it in `location` in one step."""
        client, unlinked = _client_for(ctx)
        if unlinked:
            return unlinked
        try:
            existing = await client.search_items(name)
            dup = [i for i in existing if str(i.get("name", "")).lower() == name.lower()]
            if dup:
                return {
                    "error": "duplicate_name",
                    "detail": "an item with that exact name already exists",
                    "existing": _item_brief(dup[0]),
                }
            location_id, err = await _resolve_location(client, location)
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
            return _api_error(exc, ctx)

    async def move_item(
        ctx: RunContext[ChatDeps],
        item: str,
        from_location: str,
        to_location: str,
        quantity: int | None = None,
    ) -> dict[str, Any]:
        """Move stock of one item between locations; omit `quantity` to move everything at the source."""
        client, unlinked = _client_for(ctx)
        if unlinked:
            return unlinked
        try:
            item_id, err = await _resolve_item(client, item)
            if err:
                return err
            from_id, err = await _resolve_location(client, from_location)
            if err:
                return err
            to_id, err = await _resolve_location(client, to_location)
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
            return _api_error(exc, ctx)

    async def create_location(
        ctx: RunContext[ChatDeps],
        name: str,
        category: str | None = None,
        description: str = "",
        expiry_warning_days: int | None = None,
    ) -> dict[str, Any]:
        """Create a new storage location (a shelf, room, or box)."""
        client, unlinked = _client_for(ctx)
        if unlinked:
            return unlinked
        try:
            category_id: int | None = None
            if category:
                category_id, err = await _resolve_location_category(client, category)
                if err:
                    return err
            created = await client.create_location(
                name,
                category_id=category_id,
                description=description,
                expiry_warning_days=expiry_warning_days,
            )
            return _location_detail(created, [])
        except InvintiryError as exc:
            return _api_error(exc, ctx)

    async def edit_location(
        ctx: RunContext[ChatDeps],
        location: str,
        description: str | None = None,
        expiry_warning_days: int | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        """Change a location's description, expiry warning days, or category.

        Renaming is not available; only the fields named here can change, and
        anything left out is untouched.
        """
        client, unlinked = _client_for(ctx)
        if unlinked:
            return unlinked
        try:
            location_id, err = await _resolve_location(client, location)
            if err:
                return err
            fields: dict[str, Any] = {}
            if description is not None:
                fields["description"] = description
            if expiry_warning_days is not None:
                fields["expiry_warning_days"] = expiry_warning_days
            if category is not None:
                category_id, err = await _resolve_location_category(client, category)
                if err:
                    return err
                fields["category"] = category_id
            if not fields:
                return {"error": "nothing_to_change", "detail": "no fields given to edit"}
            updated = await client.update_location(location_id, fields)
            # The change has already landed. A failure reading the contents back
            # must not be reported as a failed edit — the user would be told a
            # write did not happen when it did, and would confirm it a second
            # time. Degrade to the record without its contents instead.
            try:
                rows = await client.list_item_locations(location_id)
            except InvintiryError as exc:
                log.warning("edit_location applied but contents unread: %s", exc.detail)
                rows = None
            return _location_detail(updated, rows)
        except InvintiryError as exc:
            return _api_error(exc, ctx)

    reads = FunctionToolset(
        tools=[find_items, get_item, find_locations, get_location],
        id="invintiry-reads",
    )
    writes = FunctionToolset(
        tools=[create_item, move_item, create_location, edit_location],
        id="invintiry-writes",
        requires_approval=True,
    )
    return [reads, writes]
