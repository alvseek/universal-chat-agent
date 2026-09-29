"""External integration: invintiry's side of account linking.

Two calls, and the only place that knows how a namespaced ``end_user_id`` maps
onto what invintiry's endpoints expect. That mapping lives here on purpose: the
store keys people as ``telegram:8932435376`` or ``whatsapp:6282311020200`` so
two services can never collide, while invintiry wants the service and the
account id as two separate fields — and the module that already speaks
invintiry's wire shapes is the right one to know it.

It also translates invintiry's HTTP refusals into the brain's ``LinkErrorKind``
vocabulary, so the sentences a person reads are written in one place and do not
drift per service.

Nothing here enumerates the services. Invintiry owns that registry and validates
both the service name and the shape of an account id, so a transport added to
the bridge reaches inventory with no change on this side. An id refused *here*
is one that is malformed — no service, or no account — never one belonging to a
service this module has not heard of, because it has heard of none of them.
"""
from __future__ import annotations

from collections.abc import Callable

from application.api_integrations.invintiry.invintiry_client import (
    InvintiryClient,
    InvintiryError,
)
from application.business_domain.link_provider import (
    LinkedAccount,
    LinkError,
    LinkErrorKind,
    UnsupportedPlatform,
)

# How a status from invintiry maps onto the brain's vocabulary. 400 covers both a
# stale code and a service or account shape invintiry will not accept, which is why
# the detail is carried through for the log rather than dropped.
_KINDS = {
    0: LinkErrorKind.UNREACHABLE,
    400: LinkErrorKind.STALE_CODE,
    409: LinkErrorKind.CONFLICT,
}


def _translate(exc: InvintiryError) -> LinkError:
    return LinkError(_KINDS.get(exc.status, LinkErrorKind.OTHER), exc.detail)


def split_end_user_id(end_user_id: str) -> tuple[str, str]:
    """``whatsapp:6282311020200`` -> ``("whatsapp", "6282311020200")``, or refuse.

    Splits on the *first* colon only: an account id is free to contain one and a
    service name never does, so anything after the first separator belongs to
    the account.
    """
    platform, separator, external_id = (end_user_id or "").partition(":")
    if not separator or not platform or not external_id:
        raise UnsupportedPlatform(
            f"not a platform-namespaced end user id: {end_user_id!r}"
        )
    return platform, external_id


class InvintiryLinkProvider:
    """Redeems codes on the brain's own credential; revokes on the user's."""

    account_label = "inventory"
    how_to_link = (
        "Open invintiry in your browser, go to Settings → Chat Apps, and tap the "
        "link it shows you. That connects this chat to your own inventory."
    )

    def __init__(
        self, make_client: Callable[[str], InvintiryClient], brain_token: str
    ) -> None:
        self._make_client = make_client
        self._brain_token = brain_token

    async def redeem(self, code: str, end_user_id: str) -> LinkedAccount:
        platform, external_id = split_end_user_id(end_user_id)
        # The brain's token, not the user's: the whole point is that they have
        # none yet. It is deliberately not workspace-scoped, so one credential
        # serves whichever workspace the code belongs to.
        client = self._make_client(self._brain_token)
        try:
            payload = await client.redeem_link(code, platform, external_id)
        except InvintiryError as exc:
            raise _translate(exc) from exc
        token = (payload or {}).get("token")
        if not token:
            # A 201 with no credential in it. Rare, but the person is mid-flow,
            # so this has to become a sentence about linking rather than escape
            # as an unhandled error and reach them as a generic apology.
            raise LinkError(
                LinkErrorKind.OTHER, "link succeeded but returned no token"
            )
        return LinkedAccount(
            token=token,
            display_name=payload.get("user_display_name") or "you",
            account_name=payload.get("workspace_name")
            or payload.get("workspace_slug")
            or "your workspace",
        )

    async def revoke(self, token: str, end_user_id: str) -> None:
        """Revoke server-side using the credential being revoked.

        The end user id comes back in because chat-side logout is addressed per
        service — a token may only disconnect its own — so the service has to be
        named even though the credential already identifies the person.
        """
        platform, _ = split_end_user_id(end_user_id)
        try:
            await self._make_client(token).unlink(platform)
        except InvintiryError as exc:
            raise _translate(exc) from exc
