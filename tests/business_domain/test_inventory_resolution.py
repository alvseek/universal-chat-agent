"""Resolution tests — pure, no HTTP."""
from application.business_domain.inventory_resolution import (
    resolve_item,
    resolve_location,
)

ITEMS = [
    {"id": 1, "name": "Kabel HDMI 2m", "sku": "EL-001"},
    {"id": 2, "name": "kabel hdmi 2m", "sku": "EL-002"},
    {"id": 3, "name": "Charger Aki", "sku": None},
]


def test_numeric_ref_is_an_id():
    assert resolve_item(ITEMS, "3").id == 3
    assert resolve_item(ITEMS, "99").kind == "not_found"


def test_sku_wins_before_name():
    r = resolve_item(ITEMS, "el-001")
    assert (r.kind, r.id) == ("match", 1)


def test_duplicate_names_are_ambiguous_with_candidates():
    r = resolve_item(ITEMS, "Kabel HDMI 2m")
    assert r.kind == "ambiguous"
    assert [c["id"] for c in r.candidates] == [1, 2]


def test_unique_name_matches_case_insensitively():
    assert resolve_item(ITEMS, "charger aki").id == 3


def test_location_by_name_only():
    locs = [{"id": 10, "name": "Rak B"}]
    assert resolve_location(locs, "rak b").id == 10
    assert resolve_location(locs, "Rak Z").kind == "not_found"


def test_partial_name_narrowing_to_one_resolves():
    locs = [{"id": 10, "name": "Rak B"}, {"id": 12, "name": "Gudang Utara"}]
    assert resolve_location(locs, "gudang").id == 12


def test_partial_name_matching_several_is_ambiguous():
    locs = [{"id": 10, "name": "Rak B"}, {"id": 11, "name": "Rak A"}]
    r = resolve_location(locs, "Rak")
    assert r.kind == "ambiguous"
    assert sorted(c["id"] for c in r.candidates) == [10, 11]


def test_exact_match_beats_partial_superset():
    # "Rak B" is exact for id 10 even though it is also a substring of "Rak B2".
    locs = [{"id": 10, "name": "Rak B"}, {"id": 13, "name": "Rak B2"}]
    assert resolve_location(locs, "rak b").id == 10
