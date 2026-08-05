"""Pure conversation rules — no I/O, no DB, no network, no framework.

Everything here is a pure function: same input -> same output, testable without
mocks. This is the only layer with unit tests (see tests/business_domain).
"""
from __future__ import annotations

from typing import List, Tuple

Turn = Tuple[str, str]  # (role, content)


def normalize_conversation_id(raw: str) -> str:
    """Validate + normalize a conversation id.

    A bridge tags every message with an id like "telegram:12345". It must be a
    non-empty string; we strip surrounding whitespace. Empty -> ValueError so a
    misbehaving bridge can never collapse everyone into one shared history.
    """
    if not isinstance(raw, str):
        raise ValueError("conversation_id must be a string")
    cid = raw.strip()
    if not cid:
        raise ValueError("conversation_id must not be empty")
    return cid


def select_window(turns: List[Turn], limit: int) -> List[Turn]:
    """Return at most the last ``limit`` turns (oldest-first order preserved).

    A defensive guard so history sent to the model stays bounded even if a
    repository returns more than expected. ``limit`` < 1 yields an empty window.
    """
    if limit < 1:
        return []
    return turns[-limit:]
