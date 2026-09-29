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
  a stale code, a code already bound to someone else, a service being down —
  comes back as a sentence for them to read. An exception here would surface as
  the bridge's generic "something went wrong", which tells them nothing about
  what to do next.

The sentences are the brain's, not a service's. A provider hands over a label and
a how-to-link line; everything a person reads is composed here. That is what lets
a service this brain has never heard of be linked without touching this file.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping

from application.business_domain import link_commands as lc
from application.business_domain.link_provider import (
    LinkError,
    LinkErrorKind,
    LinkProvider,
    UnsupportedPlatform,
)
from application.data_repositories.service_link_repository import ServiceLinkRepository

log = logging.getLogger("universal-chat-agent")


class LinkService:
    def __init__(
        self, repository: ServiceLinkRepository, providers: Mapping[str, LinkProvider]
    ) -> None:
        self._repo = repository
        self._providers = dict(providers)

    def _sole(self) -> tuple[str, LinkProvider] | None:
        """The one provider, when there is exactly one to address a code to.

        A link code names no service — only the service that issued it can redeem
        it — so a redemption is unambiguous only while a single provider is
        configured. The store is per service, so logout needs no such guarantee.
        """
        if len(self._providers) != 1:
            return None
        name = next(iter(self._providers))
        return name, self._providers[name]

    @staticmethod
    def _how_to_link(provider: LinkProvider | None) -> str:
        if provider is None:
            return "Ask the service you want to connect for a link code."
        return provider.how_to_link

    def credentials(self, end_user_id: str | None) -> dict[str, str]:
        """This caller's tokens by service — empty when nobody is linked."""
        return self._repo.credentials(end_user_id) if end_user_id else {}

    def forget(self, end_user_id: str | None, service: str) -> None:
        """Drop one credential the service itself has already rejected (a 401).

        Used when a service refuses a token we still hold: the binding was
        revoked from the web side, so keeping the row only produces more 401s.

        Scoped to the one service on purpose — being refused by one service says
        nothing about this person's link to anything else, and dropping those too
        would silently unlink them from services that never complained.
        """
        if end_user_id:
            self._repo.delete(service, end_user_id)

    async def execute(self, command: lc.LinkCommand, end_user_id: str | None) -> str:
        """Carry out a link command and return the reply the person should see."""
        sole = self._sole()
        provider = sole[1] if sole else None
        if end_user_id is None:
            # No caller identity means no one to link; only a bridge that sends
            # end_user_id can offer linking at all.
            return "This chat can't be linked to an account."
        if command.kind == lc.PROMPT:
            return self._prompt(end_user_id, provider)
        if command.kind == lc.BAD_CODE:
            return "That doesn't look like a link code. " + self._how_to_link(provider)
        if command.kind == lc.REDEEM:
            return await self._redeem(command.code or "", end_user_id, sole)
        if command.kind == lc.LOGOUT:
            return await self._logout(end_user_id)
        return self._how_to_link(provider)  # a new kind should still say something

    def _prompt(self, end_user_id: str, provider: LinkProvider | None) -> str:
        linked = self._repo.credentials(end_user_id)
        if linked:
            return (
                f"You're already linked to {', '.join(sorted(linked))}. "
                "Send /logout to disconnect."
            )
        if provider is None:
            return "Hi! I'm not linked to anything yet. " + self._how_to_link(provider)
        return (
            f"Hi! I'm not linked to your {provider.account_label} yet. "
            + self._how_to_link(provider)
        )

    async def _redeem(
        self, code: str, end_user_id: str, sole: tuple[str, LinkProvider] | None
    ) -> str:
        if sole is None:
            return "Linking isn't set up here yet."
        name, provider = sole
        try:
            account = await provider.redeem(code, end_user_id)
        except UnsupportedPlatform:
            return "Linking isn't available for this chat platform yet."
        except LinkError as exc:
            return self._redeem_error(exc, provider)
        self._repo.put(name, end_user_id, account.token)
        log.info("linked %s to %s (%s)", end_user_id, name, account.account_name)
        return (
            f"Linked as {account.display_name} — {account.account_name}. "
            f"You can ask me about your {provider.account_label} now."
        )

    @staticmethod
    def _redeem_error(exc: LinkError, provider: LinkProvider) -> str:
        if exc.kind == LinkErrorKind.STALE_CODE:
            # A stale code is the common case, but a service may reuse this refusal
            # for a shape it will not accept, so the detail is logged rather than
            # swallowed — the sentence a person reads cannot tell them apart.
            log.warning("redeem refused: %s %s", exc.kind.value, exc.detail)
            return (
                "That link code didn't work — codes are single-use and expire "
                "after about five minutes. Generate a fresh one and try again."
            )
        if exc.kind == LinkErrorKind.CONFLICT:
            return (
                "This chat account is already linked to a different "
                f"{provider.account_label} account. Send /logout here first, or "
                "disconnect it from your account settings."
            )
        if exc.kind == LinkErrorKind.UNREACHABLE:
            return (
                f"I can't reach {provider.account_label} right now. "
                "Try again in a moment."
            )
        log.warning("redeem failed: %s %s", exc.kind.value, exc.detail)
        return "Linking failed. Try generating a fresh code."

    async def _logout(self, end_user_id: str) -> str:
        linked = self._repo.credentials(end_user_id)
        if not linked:
            return "You weren't linked to anything."

        revoked, kept = [], []
        for service, token in sorted(linked.items()):
            provider = self._providers.get(service)
            if provider is None:
                # No provider for it, so the row goes but the credential upstream
                # cannot be revoked from here. Say so rather than imply otherwise.
                kept.append(service)
                continue
            try:
                await provider.revoke(token, end_user_id)
                revoked.append(service)
            except LinkError as exc:
                # The local row still goes: leaving it would keep sending a token
                # the user has asked us to stop using.
                log.warning(
                    "revoke failed for %s: %s %s", service, exc.kind.value, exc.detail
                )
                kept.append(service)

        self._repo.delete_all(end_user_id)
        if revoked and not kept:
            return f"Disconnected from {', '.join(revoked)}. Your token has been revoked."
        if revoked:
            return (
                f"Disconnected from {', '.join(revoked + kept)}. "
                f"Couldn't revoke {', '.join(kept)} upstream — disconnect it from "
                "your account settings to be sure."
            )
        return (
            f"Disconnected from {', '.join(kept)} here, but couldn't revoke upstream "
            "— disconnect it from your account settings to be sure."
        )
