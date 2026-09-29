"""Domain contract: what the brain needs in order to link a person to a service.

Linking is the one place the brain speaks to a service *as a person*: a one-time
code becomes a credential that then rides every chat turn. The brain owns the flow
and the sentences; a provider owns the service's wire shapes and its words. So the
brain can link a service it has never heard of, and a service can change its API
without the brain knowing.

Nothing here enumerates services. ``service`` — the name a credential is stored
under — is the *configuration key* a provider is registered by, not something a
provider declares about itself; it has to match the name the bound toolset reads
its token by, which is why the two are configured together.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class LinkErrorKind(str, Enum):
    """Why a link operation failed, in the brain's vocabulary rather than HTTP's.

    A provider translates its own statuses into these, so the sentences a person
    reads are composed in one place and never drift per service.
    """

    STALE_CODE = "stale_code"  # unknown, already spent, or past its expiry
    CONFLICT = "conflict"  # this chat account already belongs to someone else
    UNREACHABLE = "unreachable"  # the service could not be reached at all
    OTHER = "other"  # refused for a reason of its own


class LinkError(Exception):
    """A link operation the service refused. ``kind`` is what the caller renders."""

    def __init__(self, kind: LinkErrorKind, detail: str = "") -> None:
        super().__init__(f"{kind.value}: {detail}" if detail else kind.value)
        self.kind = kind
        self.detail = detail


class UnsupportedPlatform(ValueError):
    """The caller id was not a ``platform:account`` pair the provider could split."""


@dataclass(frozen=True)
class LinkedAccount:
    """What a successful redeem hands back."""

    token: str
    display_name: str
    account_name: str


class LinkProvider(Protocol):
    """One service a person can link a chat identity to.

    ``account_label`` and ``how_to_link`` are the only words the brain borrows: it
    renders its own sentences around them and says nothing of its own about the
    service. ``redeem`` runs on whatever credential the provider holds (the
    person has none yet — that is the point); ``revoke`` must run on the
    credential being revoked, because revoking is something only its holder may
    ask for.
    """

    account_label: str  # e.g. "inventory" — a noun for the brain's sentences
    how_to_link: str  # one or two sentences telling the person what to do

    async def redeem(self, code: str, end_user_id: str) -> LinkedAccount: ...

    async def revoke(self, token: str, end_user_id: str) -> None: ...
