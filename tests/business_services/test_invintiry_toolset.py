"""Invintiry toolset tests — fake client, no HTTP, no model.

The tools are exercised directly (they are plain async closures); the
approval property is asserted on the toolset object itself and separately
end-to-end in test_chat_approval.py.
"""
import asyncio

import pytest

from application.api_integrations.invintiry.invintiry_client import InvintiryError
from application.business_services import toolsets as registry
from application.business_services.toolsets.invintiry import build_invintiry_toolsets

ITEM = {
    "id": 1, "name": "Kabel HDMI 2m", "sku": "EL-001", "quantity": 7,
    "is_low_stock": False, "description": "", "bottom_stock": 2,
    "category_name": "Elektronik", "tags": [],
    "item_locations": [
        {"location": 10, "location_name": "Rak B", "quantity": 5, "expires_at": None},
        {"location": 11, "location_name": "Rak A", "quantity": 2, "expires_at": None},
    ],
}


def _loc(id_, name, **extra):
    """A LocationDTO-shaped row — the counts are what the real API annotates."""
    return {
        "id": id_, "name": name, "slug": name.lower().replace(" ", "-"),
        "description": "", "expiry_warning_days": None,
        "category": None, "category_name": None, "tags": [],
        "item_count": 0, "expiring_soon_count": 0, "expired_count": 0,
        **extra,
    }


class FakeClient:
    def __init__(self):
        self.items = [ITEM]
        self.locations = [
            _loc(10, "Rak B", item_count=1, category_name="Gudang", category=3),
            _loc(11, "Rak A", item_count=1),
        ]
        self.location_categories = [
            {"id": 3, "name": "Gudang", "slug": "gudang"},
            {"id": 4, "name": "", "slug": "gudang-dingin"},
        ]
        self.transfers = []
        self.patched = []

    async def get_location(self, location_id):
        for l in self.locations:
            if l["id"] == location_id:
                return l
        raise InvintiryError(404, "Not found.")

    async def list_item_locations(self, location_id):
        rows = []
        for i in self.items:
            for il in i["item_locations"]:
                if il["location"] == location_id:
                    rows.append({
                        "item": i["id"], "item_name": i["name"],
                        "location": location_id, "quantity": il["quantity"],
                        "expires_at": il.get("expires_at"),
                    })
        return rows

    async def search_location_categories(self, query):
        q = query.lower()
        return [
            c for c in self.location_categories
            if q in (c.get("name") or "").lower() or q in c["slug"].lower()
        ]

    async def create_location(self, name, category_id=None, description="",
                              expiry_warning_days=None):
        row = _loc(50, name, description=description, category=category_id,
                   expiry_warning_days=expiry_warning_days)
        self.locations.append(row)
        return row

    async def update_location(self, location_id, fields):
        self.patched.append((location_id, dict(fields)))
        for l in self.locations:
            if l["id"] == location_id:
                l.update(fields)
                return l
        raise InvintiryError(404, "Not found.")

    async def search_items(self, query, low_stock_only=False):
        q = query.lower()
        return [i for i in self.items if q in i["name"].lower() or q == (i.get("sku") or "").lower()]

    async def get_item(self, item_id):
        for i in self.items:
            if i["id"] == item_id:
                return i
        raise InvintiryError(404, "Not found.")

    async def search_locations(self, query):
        q = query.lower()
        return [l for l in self.locations if q in l["name"].lower()]

    async def search_categories(self, query):
        return []

    async def create_item(self, name, location_id, quantity, category_id=None, description=""):
        return {**ITEM, "id": 99, "name": name, "sku": None, "quantity": quantity,
                "item_locations": [{"location": location_id, "location_name": "Rak B", "quantity": quantity}]}

    async def transfer(self, item_id, from_id, to_id, quantity, notes=""):
        self.transfers.append((item_id, from_id, to_id, quantity))
        return {"id": 500}


def _tools():
    reads, writes = build_invintiry_toolsets({"invintiry_client": FakeClient()})
    return {**reads.tools, **writes.tools}, reads, writes


def _call(tools, _tool, **kwargs):
    return asyncio.run(tools[_tool].function(**kwargs))


def test_find_items_maps_to_contract_shape():
    tools, _, _ = _tools()
    out = _call(tools, "find_items", query="kabel")
    assert out == [{
        "id": 1, "name": "Kabel HDMI 2m", "sku": "EL-001", "quantity": 7,
        "is_low_stock": False,
        "locations": [{"location": "Rak B", "quantity": 5}, {"location": "Rak A", "quantity": 2}],
    }]


def test_get_item_by_sku_and_not_found():
    tools, _, _ = _tools()
    assert _call(tools, "get_item", item="el-001")["category"] == "Elektronik"
    assert _call(tools, "get_item", item="Widget")["error"] == "not_found"


def test_create_item_refuses_duplicate_name():
    tools, _, _ = _tools()
    out = _call(tools, "create_item", name="Kabel HDMI 2m", location="Rak B", quantity=1)
    assert out["error"] == "duplicate_name"
    assert out["existing"]["id"] == 1


def test_move_item_defaults_to_everything_at_source():
    tools, reads, writes = _tools()
    out = _call(tools, "move_item", item="EL-001", from_location="Rak B", to_location="Rak A")
    assert (out["moved"], out["item"]) == (5, "Kabel HDMI 2m")


def test_move_item_same_location_and_no_stock():
    tools, _, _ = _tools()
    assert _call(tools, "move_item", item="1", from_location="Rak B",
                 to_location="rak b")["error"] == "same_location"


def test_ambiguous_location_carries_candidates():
    tools, _, _ = _tools()
    out = _call(tools, "move_item", item="1", from_location="Rak", to_location="Rak A")
    assert out["error"] == "location_ambiguous"
    assert len(out["candidates"]) == 2


def test_api_failure_becomes_data_not_exception():
    class DownClient(FakeClient):
        async def search_items(self, query, low_stock_only=False):
            raise InvintiryError(0, "unreachable")

    reads, _ = build_invintiry_toolsets({"invintiry_client": DownClient()})
    out = asyncio.run(reads.tools["find_items"].function(query="x"))
    assert out["error"] == "unreachable"


def test_writes_require_approval_reads_do_not():
    _, reads, writes = _tools()
    assert {t.requires_approval for t in writes.tools.values()} == {True}
    assert {t.requires_approval for t in reads.tools.values()} == {False}


def test_registry_builds_and_rejects_unknown():
    ts = registry.build_toolsets(["invintiry"], {"invintiry_client": FakeClient()})
    assert len(ts) == 2
    with pytest.raises(ValueError, match="unknown toolset 'nope'"):
        registry.build_toolsets(["nope"], {})


def test_describe_toolsets_lists_every_tool_once():
    ts = registry.build_toolsets(["invintiry"], {"invintiry_client": FakeClient()})
    lines = registry.describe_toolsets(ts)
    names = [line.split(" — ")[0] for line in lines]
    assert sorted(names) == [
        "create_item", "create_location", "edit_location", "find_items",
        "find_locations", "get_item", "get_location", "move_item",
    ]
    assert all(" — " in line for line in lines)


# -- locations ---------------------------------------------------------------


def test_find_locations_lists_all_on_empty_query():
    tools, _, _ = _tools()
    out = _call(tools, "find_locations")
    assert out == [
        {"id": 10, "name": "Rak B", "category": "Gudang", "item_count": 1},
        {"id": 11, "name": "Rak A", "category": None, "item_count": 1},
    ]


def test_get_location_carries_its_contents():
    tools, _, _ = _tools()
    out = _call(tools, "get_location", location="Rak B")
    assert out["id"] == 10 and out["category"] == "Gudang"
    assert out["contents"] == [{"item": "Kabel HDMI 2m", "quantity": 5, "expires_at": None}]
    assert (out["total"], out["truncated"]) == (1, False)


def test_get_location_not_found_and_ambiguous():
    tools, _, _ = _tools()
    assert _call(tools, "get_location", location="Nowhere")["error"] == "location_not_found"
    assert _call(tools, "get_location", location="Rak")["error"] == "location_ambiguous"


def test_get_location_caps_contents_but_totals_them_all():
    """A clipped list must still report how many are really there — otherwise
    the operator says '20 items' about a shelf holding 25."""
    from application.business_services.toolsets import invintiry as mod

    over = mod.LOCATION_CONTENTS_CAP + 5

    class Crowded(FakeClient):
        async def list_item_locations(self, location_id):
            return [
                {"item": n, "item_name": f"Item {n:02d}", "location": location_id,
                 "quantity": 1, "expires_at": None}
                for n in range(over)
            ]

    reads, _ = build_invintiry_toolsets({"invintiry_client": Crowded()})
    out = asyncio.run(reads.tools["get_location"].function(location="Rak B"))
    assert out["total"] == over
    assert out["truncated"] is True
    assert len(out["contents"]) == mod.LOCATION_CONTENTS_CAP
    assert out["contents"][0]["item"] == "Item 00"  # sorted by name, not arrival


def test_get_location_ignores_rows_holding_no_stock():
    class Emptied(FakeClient):
        async def list_item_locations(self, location_id):
            return [
                {"item": 1, "item_name": "Gone", "location": location_id, "quantity": 0},
                {"item": 2, "item_name": "Here", "location": location_id, "quantity": 3},
            ]

    reads, _ = build_invintiry_toolsets({"invintiry_client": Emptied()})
    out = asyncio.run(reads.tools["get_location"].function(location="Rak B"))
    assert [c["item"] for c in out["contents"]] == ["Here"]
    assert out["total"] == 1


def test_create_location_resolves_category_from_the_location_tree():
    client = FakeClient()
    _, writes = build_invintiry_toolsets({"invintiry_client": client})
    out = asyncio.run(
        writes.tools["create_location"].function(name="Rak C", category="gudang-dingin")
    )
    assert out["name"] == "Rak C"
    assert out["contents"] == [] and out["total"] == 0
    # Matched on slug alone: category 4 has no name upstream, which is exactly
    # why resolution looks at slug as well as name.
    assert client.locations[-1]["category"] == 4


def test_create_location_refuses_an_unknown_category_rather_than_making_one():
    tools, _, _ = _tools()
    out = _call(tools, "create_location", name="Rak D", category="basement")
    assert out["error"] == "category_not_found"


def test_create_location_never_reads_the_item_category_tree():
    """The two endpoints take identical parameters over different tables, so a
    borrowed id would be accepted as a valid row from the wrong tree."""
    class Watched(FakeClient):
        def __init__(self):
            super().__init__()
            self.item_category_calls = 0

        async def search_categories(self, query):
            self.item_category_calls += 1
            return [{"id": 999, "name": "Gudang"}]

    client = Watched()
    _, writes = build_invintiry_toolsets({"invintiry_client": client})
    out = asyncio.run(writes.tools["create_location"].function(name="Rak E", category="Gudang"))
    assert client.item_category_calls == 0
    assert out["id"] == 50


def test_edit_location_patches_only_what_was_given():
    client = FakeClient()
    _, writes = build_invintiry_toolsets({"invintiry_client": client})
    out = asyncio.run(
        writes.tools["edit_location"].function(location="Rak A", description="cold room")
    )
    assert client.patched == [(11, {"description": "cold room"})]
    assert out["description"] == "cold room"


def test_edit_location_maps_a_category_name_to_its_id():
    client = FakeClient()
    _, writes = build_invintiry_toolsets({"invintiry_client": client})
    asyncio.run(writes.tools["edit_location"].function(location="Rak A", category="Gudang"))
    assert client.patched == [(11, {"category": 3})]


def test_edit_location_that_landed_is_never_reported_as_failed():
    """The PATCH succeeds, reading the contents back does not. The edit must not
    come back as an error, or the user re-confirms a write that already ran —
    and it must not claim the location is empty either."""
    class ContentsDown(FakeClient):
        async def list_item_locations(self, location_id):
            raise InvintiryError(0, "unreachable")

    client = ContentsDown()
    _, writes = build_invintiry_toolsets({"invintiry_client": client})
    out = asyncio.run(
        writes.tools["edit_location"].function(location="Rak A", description="cold room")
    )
    assert client.patched == [(11, {"description": "cold room"})]
    assert "error" not in out
    assert out["contents_unavailable"] is True
    assert "total" not in out and "contents" not in out  # never "the shelf is empty"
    assert out["description"] == "cold room"


def test_edit_location_with_nothing_to_change_says_so():
    tools, _, _ = _tools()
    out = _call(tools, "edit_location", location="Rak A")
    assert out["error"] == "nothing_to_change"


def test_edit_location_cannot_rename():
    """Rename is deliberately out of v1 — the tool must not accept a name."""
    import inspect

    _, writes = build_invintiry_toolsets({"invintiry_client": FakeClient()})
    params = inspect.signature(writes.tools["edit_location"].function).parameters
    assert "name" not in params
    assert sorted(params) == ["category", "description", "expiry_warning_days", "location"]
