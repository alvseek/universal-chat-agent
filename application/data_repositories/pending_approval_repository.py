"""Data access for paused runs awaiting a human's yes (SQLite).

When a write tool pauses a run, the run's full message state is parked here,
keyed by the same memory key as the conversation. One pending per key: a newer
request replaces an older one, and reading is destructive-by-caller (the
service deletes before resuming, so a crash mid-resume loses the pending write
rather than replaying it — the same lost-not-doubled bias as the bridge).
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass

SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_approvals (
    key         TEXT PRIMARY KEY,
    messages    BLOB NOT NULL,
    approval_ids TEXT NOT NULL,
    summary     TEXT NOT NULL,
    image       BLOB,
    created     REAL NOT NULL
);
"""


def _ensure_image_column(conn: sqlite3.Connection) -> None:
    """Add ``image`` to a table created before that column existed.

    ``CREATE TABLE IF NOT EXISTS`` is a no-op on an existing database, so a brain
    upgraded in place would never gain the column without this. Additive and
    nullable, so pre-change rows read ``NULL`` and resume exactly as before.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(pending_approvals)")}
    if "image" not in columns:
        conn.execute("ALTER TABLE pending_approvals ADD COLUMN image BLOB")


@dataclass(frozen=True)
class PendingApproval:
    key: str
    messages: bytes
    approval_ids: list[str]
    summary: str
    # The photo that arrived with the request this write belongs to, so the
    # resumed run — whose own turn carries none — can hand it to the tool.
    image: bytes | None = None


class PendingApprovalRepository:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.executescript(SCHEMA)
        _ensure_image_column(self._conn)
        self._conn.commit()

    def put(
        self,
        key: str,
        messages: bytes,
        approval_ids: list[str],
        summary: str,
        image: bytes | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO pending_approvals "
            "(key, messages, approval_ids, summary, image, created) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (key, messages, json.dumps(approval_ids), summary, image, time.time()),
        )
        self._conn.commit()

    def get(self, key: str) -> PendingApproval | None:
        row = self._conn.execute(
            "SELECT messages, approval_ids, summary, image FROM pending_approvals WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        return PendingApproval(key, row[0], json.loads(row[1]), row[2], row[3])

    def delete(self, key: str) -> None:
        self._conn.execute("DELETE FROM pending_approvals WHERE key = ?", (key,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
