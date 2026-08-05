"""Data access for conversation messages (SQLite).

Abstracts persistence away from the business layer: services ask for "recent
turns" and "append a turn" without knowing it's SQLite. Swappable (Postgres,
Redis) without touching business_services.
"""
from __future__ import annotations

import sqlite3
import time
from typing import List

from application.data_entities.message_entity import SCHEMA, Message


class MessageRepository:
    """A tiny SQLite-backed conversation store, keyed by conversation_id."""

    def __init__(self, db_path: str) -> None:
        # check_same_thread=False: the async server may touch the connection
        # from different worker threads; a single long-lived connection is safe
        # for one process with SQLite's default serialized threading.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def append(self, conversation_id: str, role: str, content: str) -> None:
        self._conn.execute(
            "INSERT INTO messages (conversation_id, role, content, ts) "
            "VALUES (?, ?, ?, ?)",
            (conversation_id, role, content, time.time()),
        )
        self._conn.commit()

    def recent(self, conversation_id: str, limit: int) -> List[Message]:
        """Return the last ``limit`` turns for a conversation, oldest-first."""
        rows = self._conn.execute(
            "SELECT conversation_id, role, content FROM messages "
            "WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
        return [Message(cid, role, content) for cid, role, content in reversed(rows)]

    def close(self) -> None:
        self._conn.close()
