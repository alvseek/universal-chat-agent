"""Business service: connecting a person to the services the agent acts on.

Owns the whole of linking — redeem, unlink, and reading back a caller's
credentials for a chat turn — so the chat path only has to ask "was this a link
command?" and hand it over.

Two shapes are deliberate:

* **Revoke before forget.** Logout asks each service to revoke the credential
  *before* the local row is deleted. The other order would leave a live token at
  the issuer with nothing left here to revoke it with — a credential nobody can
  reach is worse than one you can still see.
* **Refusals are replies, not exceptions.** Every failure a person can cause —
  a stale code, a code already bound to someone else, inventory being down —
  comes back as a sentence for them to read. An exception here would surface as
  the bridge's generic "something went wrong", which tells them nothing about
  what to do next.
"""
from __future__ import annotations

import logging

from application.api_integrations.invintiry.invintiry_client import InvintiryError
from application.api_integrations.invintiry.link_provider import UnsupportedPlatform
from application.business_domain import link_commands as lc
from application.data_repositories.service_link_repository import ServiceLinkRepository

log = logging.getLogger("universal-chat-agent")

HOW_TO_LINK = (
    "Open invintiry in your browser, go to Settings → Chat Apps, and tap the "
    "link it shows you. That connects this chat to your own inventory."
)


class LinkService:
    def __init__(self, repository: ServiceLinkRepository, provider) -> None:
        self._repo = repository
        self._provider = provider

    def credentials(self, end_user_id: str | None) -> dict[str, str]:
        """This caller's tokens by service — empty when nobody is linked."""
        return self._repo.credentials(end_user_id) if end_user_id else {}

    def forget(self, end_user_id: str | None, service: str) -> None:
        """Drop one credential the service itself has already rejected (a 401).

        Used when a service refuses a token we still hold: the binding was
        revoked from the web side, so keeping the row only produces more 401s.

        Scoped to the one service on purpose — being refused by inventory says
        nothing about this person's link to anything else, and dropping those too
        would silently unlink them from services that never complained.
        """
        if end_user_id:
            self._repo.delete(service, end_user_id)

    async def execute(self, command: lc.LinkCommand, end_user_id: str | None) -> str:
        """Carry out a link command and return the reply the person should see."""
        if end_user_id is None:
            # No caller identity means no one to link; only a bridge that sends
            # end_user_id can offer linking at all.
            return "This chat can't be linked to an inventory account."
        if command.kind == lc.PROMPT:
            return self._prompt(end_user_id)
        if command.kind == lc.BAD_CODE:
            return "That doesn't look like a link code. " + HOW_TO_LINK
        if command.kind == lc.REDEEM:
            return await self._redeem(command.code or "", end_user_id)
        if command.kind == lc.LOGOUT:
            return await self._logout(end_user_id)
        return HOW_TO_LINK  # unreachable today; a new kind should still say something

    def _prompt(self, end_user_id: str) -> str:
        linked = self._repo.credentials(end_user_id)
        if linked:
            return (
                f"You're already linked to {', '.join(sorted(linked))}. "
                "Send /logout to disconnect."
            )
        return "Hi! I'm not linked to your inventory yet. " + HOW_TO_LINK

    async def _redeem(self, code: str, end_user_id: str) -> str:
        try:
            account = await self._provider.redeem(code, end_user_id)
        except UnsupportedPlatform:
            return "Linking isn't available for this chat platform yet."
        except InvintiryError as exc:
            return self._redeem_error(exc)
        self._repo.put(self._provider.service, end_user_id, account.token)
        log.info(
            "linked %s to %s (%s)",
            end_user_id, self._provider.service, account.workspace_name,
        )
        return (
            f"Linked as {account.display_name} — {account.workspace_name}. "
            "You can ask me about your inventory now."
        )

    @staticmethod
    def _redeem_error(exc: InvintiryError) -> str:
        if exc.status == 400:
            # A 400 is nearly always a stale code, but it is also how inventory
            # refuses a chat service it does not offer or an account id
            # malformed for one. Those two would send a person round the
            # generate-a-fresh-code loop forever, and only this log says which
            # of the three actually happened.
            log.warning("redeem refused: 400 %s", exc.detail)
            return (
                "That link code didn't work — codes are single-use and expire "
                "after about five minutes. Generate a fresh one and try again."
            )
        if exc.status == 409:
            return (
                "This chat account is already linked to a different inventory "
                "user. Send /logout there first, or disconnect it from Settings "
                "→ Chat Apps."
            )
        if exc.status == 0:
            return "I can't reach inventory right now. Try again in a moment."
        log.warning("redeem failed: %s %s", exc.status, exc.detail)
        return "Linking failed. Try generating a fresh code."

    async def _logout(self, end_user_id: str) -> str:
        linked = self._repo.credentials(end_user_id)
        if not linked:
            return "You weren't linked to anything."

        revoked, kept = [], []
        for service, token in sorted(linked.items()):
            if service != self._provider.service:
                # No provider for it, so the row goes but the credential upstream
                # cannot be revoked from here. Say so rather than imply otherwise.
                kept.append(service)
                continue
            try:
                await self._provider.revoke(token, end_user_id)
                revoked.append(service)
            except InvintiryError as exc:
                # The local row still goes: leaving it would keep sending a token
                # the user has asked us to stop using.
                log.warning("revoke failed for %s: %s %s", service, exc.status, exc.detail)
                kept.append(service)

        self._repo.delete_all(end_user_id)
        if revoked and not kept:
            return f"Disconnected from {', '.join(revoked)}. Your token has been revoked."
        if revoked:
            return (
                f"Disconnected from {', '.join(revoked + kept)}. "
                f"Couldn't revoke {', '.join(kept)} upstream — disconnect it from "
                "Settings → Chat Apps to be sure."
            )
        return (
            f"Disconnected from {', '.join(kept)} here, but couldn't revoke upstream "
            "— disconnect it from Settings → Chat Apps to be sure."
        )
