"""Invintiry client tests — mock transport, no network."""
import asyncio
import json

import httpx
import pytest

from application.api_integrations.invintiry.invintiry_client import (
    InvintiryClient,
    InvintiryError,
)


def _client(handler):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return InvintiryClient("https://inv.example", "tok-123", client=http)


def test_search_items_sends_bearer_and_params():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        seen["url"] = str(request.url)
        return httpx.Response(200, json=[{"id": 1, "name": "Kabel"}])

    items = asyncio.run(_client(handler).search_items("kabel", low_stock_only=True))
    assert items == [{"id": 1, "name": "Kabel"}]
    assert seen["auth"] == "Bearer tok-123"
    assert "search=kabel" in seen["url"] and "low_stock=true" in seen["url"]


def test_transfer_posts_contract_body():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": 9})

    asyncio.run(_client(handler).transfer(1, 2, 3, 4, notes="n"))
    assert seen["body"] == {
        "item": 1, "from_location": 2, "to_location": 3, "quantity": 4, "notes": "n",
    }


def test_create_item_omits_category_when_absent():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": 5})

    asyncio.run(_client(handler).create_item("X", location_id=7, quantity=2))
    assert seen["body"] == {
        "name": "X", "description": "", "initial_location": 7, "initial_quantity": 2,
    }


def test_api_error_carries_status_and_detail():
    def handler(request):
        return httpx.Response(400, json={"detail": "Insufficient stock: available 2"})

    with pytest.raises(InvintiryError) as err:
        asyncio.run(_client(handler).transfer(1, 2, 3, 99))
    assert err.value.status == 400
    assert "available 2" in err.value.detail


def test_transport_failure_is_status_zero():
    def handler(request):
        raise httpx.ConnectError("boom")

    with pytest.raises(InvintiryError) as err:
        asyncio.run(_client(handler).search_items("x"))
    assert err.value.status == 0
