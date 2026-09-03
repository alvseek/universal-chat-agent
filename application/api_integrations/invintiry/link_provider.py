"""External integration: invintiry's side of account linking.

Two calls, and the only place that knows how a namespaced ``end_user_id`` maps
onto what invintiry's endpoint expects. That mapping lives here on purpose: the
store keys people as ``telegram:8932435376`` so two platforms can never collide,
while invintiry's redeem endpoint wants a bare Telegram integer — and the module
that already speaks invintiry's wire shapes is the right one to know it.

A consequence worth stating plainly: this provider can only link *Telegram*
users, because that is what invintiry's endpoint accepts. An id from any other
platform is refused here rather than sent upstream to be rejected there.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from application.api_integrations.invintiry.invintiry_client import (
    InvintiryClient,
    InvintiryError,
)

TELEGRAM_PREFIX = "telegram:"


class UnsupportedPlatform(ValueError):
    """This provider was handed an end user it cannot link (not a Telegram id)."""


@dataclass(frozen=True)
class LinkedAccount:
    """What a successful redeem hands back."""

    token: str
    display_name: str
    workspace_name: str


def telegram_id(end_user_id: str) -> int:
    """``telegram:8932435376`` -> ``8932435376``, or refuse."""
    if not end_user_id.startswith(TELEGRAM_PREFIX):
        raise UnsupportedPlatform(
            f"invintiry linking is Telegram-only; got {end_user_id!r}"
        )
    raw = end_user_id[len(TELEGRAM_PREFIX):]
    try:
        return int(raw)
    except ValueError as exc:
        raise UnsupportedPlatform(f"not a Telegram user id: {raw!r}") from exc


class InvintiryLinkProvider:
    """Redeems codes on the brain's own credential; revokes on the user's."""

    service = "invintiry"

    def __init__(
        self, make_client: Callable[[str], InvintiryClient], brain_token: str
    ) -> None:
        self._make_client = make_client
        self._brain_token = brain_token

    async def redeem(self, code: str, end_user_id: str) -> LinkedAccount:
        # The brain's token, not the user's: the whole point is that they have
        # none yet. It is deliberately not workspace-scoped, so one credential
        # serves whichever workspace the code belongs to.
        client = self._make_client(self._brain_token)
        payload = await client.redeem_link(code, telegram_id(end_user_id))
        token = (payload or {}).get("token")
        if not token:
            # A 201 with no credential in it. Rare, but the person is mid-flow,
            # so this has to become a sentence about linking rather than escape
            # as an unhandled error and reach them as a generic apology.
            raise InvintiryError(502, "link succeeded but returned no token")
        return LinkedAccount(
            token=token,
            display_name=payload.get("user_display_name") or "you",
            workspace_name=payload.get("workspace_name")
            or payload.get("workspace_slug")
            or "your workspace",
        )

    async def revoke(self, token: str) -> None:
        """Revoke server-side using the credential being revoked."""
        await self._make_client(token).unlink()
