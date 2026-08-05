"""Database schema + row model for a stored conversation message.

The DATA-ENTITIES layer defines *what the persisted shape is*. Repositories
(data_repositories) read/write these; nothing above the data layer builds SQL.
"""
from __future__ import annotations

from dataclasses import dataclass

# One stored turn in a conversation. ``role`` is "user" or "assistant".
@dataclass(frozen=True)
class Message:
    conversation_id: str
    role: str
    content: str


# Conversation history, keyed by an opaque ``conversation_id`` string
# (e.g. "telegram:12345") so any bridge/platform namespaces cleanly.
SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT    NOT NULL,
    role            TEXT    NOT NULL,
    content         TEXT    NOT NULL,
    ts              REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id, id);
"""
