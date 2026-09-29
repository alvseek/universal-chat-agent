"""Linking end to end below the LLM: redeem, logout, and which token each uses.

The property worth pinning hardest is credential direction — redeem runs on the
brain's own token because the person has none yet, and revoke runs on the token
being revoked. Getting those the wrong way round would still "work" against a
permissive server and would be wrong in a way no reply text reveals.
"""
import asyncio

import pytest

from application.business_domain import link_commands as lc
from application.business_domain.link_provider import (
    LinkError,
    LinkErrorKind,
    LinkedAccount,
    UnsupportedPlatform,
)
from application.business_services.link_service import LinkService
from application.data_repositories.service_link_repository import ServiceLinkRepository


class FakeProvider:
    account_label = "inventory"
    # The words the brain borrows for its own sentences — and nothing more.
    how_to_link = "Go to Settings → Chat Apps and tap the link."

    def __init__(self, account=None, error=None, revoke_error=None):
        self._account = account or LinkedAccount("user-token", "Alvi", "Alviandi Inventory")
        self._error = error
        self._revoke_error = revoke_error
        self.redeemed: list[tuple[str, str]] = []
        self.revoked: list[str] = []
        self.revoked_for: list[tuple[str, str]] = []

    async def redeem(self, code, end_user_id):
        self.redeemed.append((code, end_user_id))
        if self._error:
            raise self._error
        return self._account

    async def revoke(self, token, end_user_id):
        self.revoked.append(token)
        self.revoked_for.append((token, end_user_id))
        if self._revoke_error:
            raise self._revoke_error


def _service(tmp_path, provider=None):
    repo = ServiceLinkRepository(str(tmp_path / "agent.db"))
    provider = provider or FakeProvider()
    return LinkService(repo, {"demo": provider}), repo, provider


def _run(service, kind, end_user_id="telegram:1", code=None):
    return asyncio.run(service.execute(lc.LinkCommand(kind, code), end_user_id))


# -- redeem ------------------------------------------------------------------


def test_redeem_stores_the_token_and_names_the_workspace(tmp_path):
    service, repo, provider = _service(tmp_path)

    reply = _run(service, lc.REDEEM, code="CODE1")

    assert repo.get("demo", "telegram:1") == "user-token"
    assert "Alvi" in reply and "Alviandi Inventory" in reply
    assert provider.redeemed == [("CODE1", "telegram:1")]


def test_relinking_replaces_the_previous_token(tmp_path):
    service, repo, _ = _service(tmp_path)
    repo.put("demo", "telegram:1", "stale-token")

    _run(service, lc.REDEEM, code="CODE1")

    assert repo.get("demo", "telegram:1") == "user-token"


@pytest.mark.parametrize(
    "kind, expected",
    [
        (LinkErrorKind.STALE_CODE, "single-use"),
        (LinkErrorKind.CONFLICT, "already linked"),
        (LinkErrorKind.UNREACHABLE, "can't reach"),
        (LinkErrorKind.OTHER, "Linking failed"),
    ],
)
def test_a_refused_redeem_becomes_a_sentence_not_an_exception(tmp_path, kind, expected):
    service, repo, _ = _service(
        tmp_path, FakeProvider(error=LinkError(kind, "nope"))
    )

    reply = _run(service, lc.REDEEM, code="STALE")

    assert expected in reply
    assert repo.credentials("telegram:1") == {}  # nothing stored on failure


def test_a_whatsapp_caller_links_the_same_way_a_telegram_one_does(tmp_path):
    """The regression this whole change exists for: a non-Telegram chat can link."""
    service, repo, provider = _service(tmp_path)

    reply = _run(service, lc.REDEEM, end_user_id="whatsapp:6282311020200", code="CODE1")

    assert provider.redeemed == [("CODE1", "whatsapp:6282311020200")]
    assert repo.credentials("whatsapp:6282311020200") == {"demo": "user-token"}
    assert "Linked as Alvi" in reply


def test_a_malformed_caller_id_is_refused_locally(tmp_path):
    # Not "a service we don't support" — inventory decides that. This is an id
    # the bridge could not have built correctly, so it never leaves the process.
    service, repo, _ = _service(
        tmp_path, FakeProvider(error=UnsupportedPlatform("nope"))
    )

    reply = _run(service, lc.REDEEM, end_user_id="nonsense", code="CODE1")

    assert "isn't available for this chat platform" in reply
    assert repo.credentials("nonsense") == {}


# -- logout ------------------------------------------------------------------


def test_logout_revokes_with_the_users_own_token_then_forgets(tmp_path):
    service, repo, provider = _service(tmp_path)
    repo.put("demo", "telegram:1", "user-token")

    reply = _run(service, lc.LOGOUT)

    assert provider.revoked == ["user-token"]  # the credential being revoked
    assert repo.credentials("telegram:1") == {}
    assert "revoked" in reply.lower()


def test_logout_when_nothing_is_linked_says_so(tmp_path):
    service, _, provider = _service(tmp_path)

    reply = _run(service, lc.LOGOUT)

    assert "weren't linked" in reply
    assert provider.revoked == []


def test_logout_drops_the_row_even_when_revoke_fails(tmp_path):
    # Keeping it would go on sending a token the user asked us to stop using.
    service, repo, provider = _service(
        tmp_path, FakeProvider(revoke_error=LinkError(LinkErrorKind.OTHER, "boom"))
    )
    repo.put("demo", "telegram:1", "user-token")

    reply = _run(service, lc.LOGOUT)

    assert repo.credentials("telegram:1") == {}
    assert "couldn't revoke" in reply.lower()
    assert "settings" in reply  # tells them how to be sure


def test_logout_unlinks_every_service_not_just_the_provider(tmp_path):
    # Decision 6: a user who logs out must not keep a live link elsewhere.
    service, repo, provider = _service(tmp_path)
    repo.put("demo", "telegram:1", "user-token")
    repo.put("other", "telegram:1", "other-token")

    reply = _run(service, lc.LOGOUT)

    assert repo.credentials("telegram:1") == {}
    assert provider.revoked == ["user-token"]  # only the one it can speak for
    assert "other" in reply


def test_logout_leaves_other_people_alone(tmp_path):
    service, repo, _ = _service(tmp_path)
    repo.put("demo", "telegram:1", "mine")
    repo.put("demo", "telegram:2", "theirs")

    _run(service, lc.LOGOUT, end_user_id="telegram:1")

    assert repo.get("demo", "telegram:2") == "theirs"


# -- prompt, credentials, forget ---------------------------------------------


def test_prompt_tells_an_unlinked_person_where_to_go(tmp_path):
    service, _, _ = _service(tmp_path)
    reply = _run(service, lc.PROMPT)
    assert "Settings" in reply and "not linked" in reply.lower()


def test_prompt_tells_a_linked_person_they_are_already_connected(tmp_path):
    service, repo, _ = _service(tmp_path)
    repo.put("demo", "telegram:1", "user-token")
    reply = _run(service, lc.PROMPT)
    assert "already linked" in reply and "/logout" in reply


def test_bad_code_explains_rather_than_calling_upstream(tmp_path):
    service, _, provider = _service(tmp_path)
    reply = _run(service, lc.BAD_CODE)
    assert "doesn't look like a link code" in reply
    assert provider.redeemed == []  # never reached the API


def test_a_bridge_that_sends_no_caller_identity_cannot_link(tmp_path):
    service, _, provider = _service(tmp_path)
    reply = asyncio.run(service.execute(lc.LinkCommand(lc.REDEEM, "CODE1"), None))
    assert "can't be linked" in reply
    assert provider.redeemed == []


def test_credentials_are_empty_without_a_caller(tmp_path):
    service, repo, _ = _service(tmp_path)
    repo.put("demo", "telegram:1", "user-token")
    assert service.credentials(None) == {}
    assert service.credentials("telegram:1") == {"demo": "user-token"}


def test_forget_drops_a_credential_the_service_rejected(tmp_path):
    service, repo, _ = _service(tmp_path)
    repo.put("demo", "telegram:1", "revoked-elsewhere")

    service.forget("telegram:1", "demo")

    assert repo.credentials("telegram:1") == {}
