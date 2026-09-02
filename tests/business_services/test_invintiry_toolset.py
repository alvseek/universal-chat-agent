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


class FakeClient:
    def __init__(self):
        self.items = [ITEM]
        self.locations = [{"id": 10, "name": "Rak B"}, {"id": 11, "name": "Rak A"}]
        self.transfers = []

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
    assert sorted(names) == ["create_item", "find_items", "get_item", "move_item"]
    assert all(" — " in line for line in lines)
