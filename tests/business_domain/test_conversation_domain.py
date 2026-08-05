"""Unit tests for pure conversation rules (no I/O, no mocks)."""
import pytest

from application.business_domain import conversation_domain as domain


def test_normalize_strips_whitespace():
    assert domain.normalize_conversation_id("  telegram:12345 ") == "telegram:12345"


def test_normalize_rejects_empty():
    with pytest.raises(ValueError):
        domain.normalize_conversation_id("   ")


def test_normalize_rejects_non_string():
    with pytest.raises(ValueError):
        domain.normalize_conversation_id(123)  # type: ignore[arg-type]


def test_window_returns_last_n_oldest_first():
    turns = [("user", "a"), ("assistant", "b"), ("user", "c")]
    assert domain.select_window(turns, 2) == [("assistant", "b"), ("user", "c")]


def test_window_shorter_than_limit_returns_all():
    turns = [("user", "a")]
    assert domain.select_window(turns, 5) == turns


def test_window_non_positive_limit_is_empty():
    assert domain.select_window([("user", "a")], 0) == []
