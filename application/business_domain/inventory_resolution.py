"""Domain rule: turn a user's reference to an item or location into an id.

Pure matching over lists the tools have already fetched — no HTTP here. The
operator's contract says ``item`` accepts an id, a SKU, or a name and
``location`` an id or a name; a reference matching several things must come
back as *ambiguous with candidates*, never a silent pick.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence

Kind = Literal["match", "ambiguous", "not_found"]


@dataclass(frozen=True)
class Resolution:
    kind: Kind
    id: int | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)


def _brief(record: Mapping[str, Any]) -> dict[str, Any]:
    return {k: record.get(k) for k in ("id", "name", "sku") if record.get(k) is not None}


def _resolve(records: Sequence[Mapping[str, Any]], ref: str, keys: Sequence[str]) -> Resolution:
    """Shared resolution: numeric ref = id; otherwise case-insensitive match on
    ``keys`` in priority order — exact first, then substring, so a partial name
    that narrows to one thing resolves and one that matches several comes back
    as ambiguous-with-candidates (the contract's rule), never a silent pick."""
    ref = str(ref).strip()
    if ref.lstrip("-").isdigit():
        wanted = int(ref)
        for r in records:
            if r.get("id") == wanted:
                return Resolution("match", wanted)
        return Resolution("not_found")
    lowered = ref.lower()
    for match in (
        lambda value: value == lowered,          # exact pass
        lambda value: lowered in value,          # substring pass
    ):
        for key in keys:
            hits = [r for r in records if match(str(r.get(key) or "").lower())]
            if len(hits) == 1:
                return Resolution("match", hits[0]["id"])
            if len(hits) > 1:
                return Resolution("ambiguous", candidates=[_brief(r) for r in hits])
    return Resolution("not_found")


def resolve_item(items: Sequence[Mapping[str, Any]], ref: str) -> Resolution:
    """id → SKU → name, exact and case-insensitive."""
    return _resolve(items, ref, ("sku", "name"))


def resolve_location(locations: Sequence[Mapping[str, Any]], ref: str) -> Resolution:
    """id → name (locations have no SKU; names are unique per workspace)."""
    return _resolve(locations, ref, ("name",))
