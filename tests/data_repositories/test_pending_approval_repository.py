"""Pending-approval store — the image column, and the in-place upgrade to it."""
import sqlite3

from application.data_repositories.pending_approval_repository import (
    PendingApprovalRepository,
)


def test_image_round_trips(tmp_path):
    repo = PendingApprovalRepository(str(tmp_path / "chat.db"))
    repo.put("k", b"msgs", ["a1"], "store something", image=b"jpeg-bytes")

    row = repo.get("k")
    assert row is not None
    assert row.image == b"jpeg-bytes"

    repo.put("k", b"msgs", ["a1"], "store something")
    assert repo.get("k").image is None


def test_a_pre_change_table_gains_the_column(tmp_path):
    """A DB created before `image` existed must be upgraded in place, not crash.

    `CREATE TABLE IF NOT EXISTS` is a no-op on an existing table, so the column
    only appears via the explicit ALTER — and the legacy row then reads NULL.
    """
    db = str(tmp_path / "chat.db")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE pending_approvals ("
        "key TEXT PRIMARY KEY, messages BLOB NOT NULL, approval_ids TEXT NOT NULL, "
        "summary TEXT NOT NULL, created REAL NOT NULL)"
    )
    conn.execute(
        "INSERT INTO pending_approvals VALUES (?, ?, ?, ?, ?)",
        ("old", b"m", "[]", "legacy", 1.0),
    )
    conn.commit()
    conn.close()

    repo = PendingApprovalRepository(db)  # must ALTER, not fail

    row = repo.get("old")
    assert row is not None and row.image is None
