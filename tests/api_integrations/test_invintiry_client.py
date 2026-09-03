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


def test_location_reads_hit_their_own_endpoints():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json=[])

    c = _client(handler)
    asyncio.run(c.get_location(12))
    asyncio.run(c.list_item_locations(12))
    asyncio.run(c.search_location_categories("gudang"))

    assert seen[0] == "https://inv.example/api/locations/12/"
    assert "api/item-locations/" in seen[1] and "location=12" in seen[1]
    # The whole point of the separate method: location categories are NOT
    # /api/categories/, which serves items over an identical parameter shape.
    assert "api/location-categories/" in seen[2] and "search=gudang" in seen[2]
    assert "api/categories/" not in seen[2].replace("api/location-categories/", "")


def test_create_location_omits_optional_fields_when_absent():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": 5})

    asyncio.run(_client(handler).create_location("Rak C"))
    assert seen["body"] == {"name": "Rak C", "description": ""}


def test_create_location_includes_optional_fields_when_given():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": 5})

    asyncio.run(
        _client(handler).create_location(
            "Rak C", category_id=3, description="d", expiry_warning_days=14
        )
    )
    assert seen["body"] == {
        "name": "Rak C", "description": "d", "category": 3, "expiry_warning_days": 14,
    }


def test_update_location_patches_only_supplied_fields():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": 12})

    asyncio.run(_client(handler).update_location(12, {"description": "cold room"}))
    assert seen["method"] == "PATCH"
    assert seen["url"] == "https://inv.example/api/locations/12/"
    assert seen["body"] == {"description": "cold room"}


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

# -- account linking ---------------------------------------------------------
#
# Redeem and unlink are the two calls that do not run on the caller's own
# inventory token, so what each one *sends* is the thing worth pinning.


def test_redeem_link_posts_the_contract_body_with_the_brains_token():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "token": "user-token-xyz",
                "user_display_name": "Alvi",
                "workspace_slug": "alviandi-inventory",
                "workspace_name": "Alviandi Inventory",
            },
        )

    result = asyncio.run(_client(handler).redeem_link("CODE1", 8932435376))

    assert result["token"] == "user-token-xyz"
    assert result["workspace_slug"] == "alviandi-inventory"
    assert seen["url"].endswith("/api/telegram-links/redeem/")
    assert seen["auth"] == "Bearer tok-123"  # whatever this instance was built with
    assert seen["body"] == {"code": "CODE1", "telegram_user_id": 8932435376}


def test_redeem_link_surfaces_a_spent_code_as_400():
    def handler(request):
        return httpx.Response(400, json={"detail": "code expired or already used"})

    with pytest.raises(InvintiryError) as exc:
        asyncio.run(_client(handler).redeem_link("STALE", 1))
    assert exc.value.status == 400
    assert "expired" in exc.value.detail


def test_redeem_link_surfaces_a_taken_telegram_id_as_409():
    # The contract's own case: that id already belongs to a different live user.
    def handler(request):
        return httpx.Response(409, json={"detail": "already linked to another user"})

    with pytest.raises(InvintiryError) as exc:
        asyncio.run(_client(handler).redeem_link("CODE1", 1))
    assert exc.value.status == 409


def test_unlink_sends_delete_and_accepts_a_bodyless_204():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        return httpx.Response(204)  # no body at all

    asyncio.run(_client(handler).unlink())  # must not raise on empty content

    assert seen["method"] == "DELETE"
    assert seen["url"].endswith("/api/telegram-links/")


def test_unlink_treats_404_as_already_unlinked():
    def handler(request):
        return httpx.Response(404, json={"detail": "no link"})

    asyncio.run(_client(handler).unlink())  # success: the wanted state holds


def test_unlink_still_raises_on_a_real_failure():
    # Negative control: 404 is special-cased, everything else must still surface.
    def handler(request):
        return httpx.Response(500, text="boom")

    with pytest.raises(InvintiryError) as exc:
        asyncio.run(_client(handler).unlink())
    assert exc.value.status == 500


def test_a_shared_pool_is_not_closed_by_a_per_caller_instance():
    # Per-caller clients share one AsyncClient; if aclose() closed it, the first
    # user to finish a turn would break every other user's connection.
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    borrower = InvintiryClient("https://inv.example", "tok", client=http)

    asyncio.run(borrower.aclose())

    assert not http.is_closed


def test_an_owned_pool_is_closed():
    owner = InvintiryClient("https://inv.example", "tok")
    asyncio.run(owner.aclose())
    assert owner._client.is_closed
