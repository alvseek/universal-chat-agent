"""Data access for end-user credentials, per external service (SQLite).

One row per ``(service, end_user_id)``: the token this brain uses when it acts on
that person's behalf against that service. The key is composite because linkage
is a fact about a *pair* — a person may be linked to inventory and not to
whatever is added next — and because ``end_user_id`` is namespaced by platform
(``telegram:8932435376``), so two bridges can never collide on a bare id.

Storing a credential is what this table is for, so it is worth being plain about
what that means: these tokens are as exposed as the process's own environment
already is, on the same box, in the same file as the conversation history. What
makes long-lived per-user tokens acceptable is not secrecy here but revocation
at the issuer — ``LinkService.logout`` calls the service's own revoke before it
deletes a row, and a token revoked from the web side stops working regardless of
what this table still holds.
"""
from __future__ import annotations

import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS service_links (
    service     TEXT NOT NULL,
    end_user_id TEXT NOT NULL,
    token       TEXT NOT NULL,
    linked_at   REAL NOT NULL,
    PRIMARY KEY (service, end_user_id)
);
"""


class ServiceLinkRepository:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def put(self, service: str, end_user_id: str, token: str) -> None:
        """Link (or re-link) one person to one service. Re-linking replaces."""
        self._conn.execute(
            "INSERT OR REPLACE INTO service_links "
            "(service, end_user_id, token, linked_at) VALUES (?, ?, ?, ?)",
            (service, end_user_id, token, time.time()),
        )
        self._conn.commit()

    def get(self, service: str, end_user_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT token FROM service_links WHERE service = ? AND end_user_id = ?",
            (service, end_user_id),
        ).fetchone()
        return row[0] if row else None

    def credentials(self, end_user_id: str) -> dict[str, str]:
        """Every service this person is linked to -> their token for it.

        This is what a chat turn needs: one read, whatever the agent's toolsets
        turn out to want.
        """
        rows = self._conn.execute(
            "SELECT service, token FROM service_links WHERE end_user_id = ?",
            (end_user_id,),
        ).fetchall()
        return {service: token for service, token in rows}

    def delete(self, service: str, end_user_id: str) -> bool:
        """True when a row was actually removed."""
        cur = self._conn.execute(
            "DELETE FROM service_links WHERE service = ? AND end_user_id = ?",
            (service, end_user_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def delete_all(self, end_user_id: str) -> list[str]:
        """Unlink every service for one person; returns the services dropped.

        The caller revokes at each service *before* calling this, so the returned
        list is what it already acted on rather than a promise about the future.
        """
        services = sorted(self.credentials(end_user_id))
        self._conn.execute(
            "DELETE FROM service_links WHERE end_user_id = ?", (end_user_id,)
        )
        self._conn.commit()
        return services

    def close(self) -> None:
        self._conn.close()
