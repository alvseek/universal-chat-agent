"""Linking end to end below the LLM: redeem, logout, and which token each uses.

The property worth pinning hardest is credential direction — redeem runs on the
brain's own token because the person has none yet, and revoke runs on the token
being revoked. Getting those the wrong way round would still "work" against a
permissive server and would be wrong in a way no reply text reveals.
"""
import asyncio

import pytest

from application.api_integrations.invintiry.invintiry_client import InvintiryError
from application.api_integrations.invintiry.link_provider import (
    InvintiryLinkProvider,
    LinkedAccount,
    UnsupportedPlatform,
    split_end_user_id,
)
from application.business_domain import link_commands as lc
from application.business_services.link_service import LinkService
from application.data_repositories.service_link_repository import ServiceLinkRepository


class FakeProvider:
    service = "invintiry"

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
    return LinkService(repo, provider), repo, provider


def _run(service, kind, end_user_id="telegram:1", code=None):
    return asyncio.run(service.execute(lc.LinkCommand(kind, code), end_user_id))


# -- redeem ------------------------------------------------------------------


def test_redeem_stores_the_token_and_names_the_workspace(tmp_path):
    service, repo, provider = _service(tmp_path)

    reply = _run(service, lc.REDEEM, code="CODE1")

    assert repo.get("invintiry", "telegram:1") == "user-token"
    assert "Alvi" in reply and "Alviandi Inventory" in reply
    assert provider.redeemed == [("CODE1", "telegram:1")]


def test_relinking_replaces_the_previous_token(tmp_path):
    service, repo, _ = _service(tmp_path)
    repo.put("invintiry", "telegram:1", "stale-token")

    _run(service, lc.REDEEM, code="CODE1")

    assert repo.get("invintiry", "telegram:1") == "user-token"


@pytest.mark.parametrize(
    "status, expected",
    [
        (400, "single-use"),
        (409, "already linked"),
        (0, "can't reach"),
        (500, "Linking failed"),
    ],
)
def test_a_refused_redeem_becomes_a_sentence_not_an_exception(tmp_path, status, expected):
    service, repo, _ = _service(
        tmp_path, FakeProvider(error=InvintiryError(status, "nope"))
    )

    reply = _run(service, lc.REDEEM, code="STALE")

    assert expected in reply
    assert repo.credentials("telegram:1") == {}  # nothing stored on failure


def test_a_whatsapp_caller_links_the_same_way_a_telegram_one_does(tmp_path):
    """The regression this whole change exists for: a non-Telegram chat can link."""
    service, repo, provider = _service(tmp_path)

    reply = _run(service, lc.REDEEM, end_user_id="whatsapp:6282311020200", code="CODE1")

    assert provider.redeemed == [("CODE1", "whatsapp:6282311020200")]
    assert repo.credentials("whatsapp:6282311020200") == {"invintiry": "user-token"}
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
    repo.put("invintiry", "telegram:1", "user-token")

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
        tmp_path, FakeProvider(revoke_error=InvintiryError(500, "boom"))
    )
    repo.put("invintiry", "telegram:1", "user-token")

    reply = _run(service, lc.LOGOUT)

    assert repo.credentials("telegram:1") == {}
    assert "couldn't revoke" in reply.lower()
    assert "Settings" in reply  # tells them how to be sure


def test_logout_unlinks_every_service_not_just_the_provider(tmp_path):
    # Decision 6: a user who logs out must not keep a live link elsewhere.
    service, repo, provider = _service(tmp_path)
    repo.put("invintiry", "telegram:1", "user-token")
    repo.put("other", "telegram:1", "other-token")

    reply = _run(service, lc.LOGOUT)

    assert repo.credentials("telegram:1") == {}
    assert provider.revoked == ["user-token"]  # only the one it can speak for
    assert "other" in reply


def test_logout_leaves_other_people_alone(tmp_path):
    service, repo, _ = _service(tmp_path)
    repo.put("invintiry", "telegram:1", "mine")
    repo.put("invintiry", "telegram:2", "theirs")

    _run(service, lc.LOGOUT, end_user_id="telegram:1")

    assert repo.get("invintiry", "telegram:2") == "theirs"


# -- prompt, credentials, forget ---------------------------------------------


def test_prompt_tells_an_unlinked_person_where_to_go(tmp_path):
    service, _, _ = _service(tmp_path)
    reply = _run(service, lc.PROMPT)
    assert "Settings" in reply and "not linked" in reply.lower()


def test_prompt_tells_a_linked_person_they_are_already_connected(tmp_path):
    service, repo, _ = _service(tmp_path)
    repo.put("invintiry", "telegram:1", "user-token")
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
    repo.put("invintiry", "telegram:1", "user-token")
    assert service.credentials(None) == {}
    assert service.credentials("telegram:1") == {"invintiry": "user-token"}


def test_forget_drops_a_credential_the_service_rejected(tmp_path):
    service, repo, _ = _service(tmp_path)
    repo.put("invintiry", "telegram:1", "revoked-elsewhere")

    service.forget("telegram:1", "invintiry")

    assert repo.credentials("telegram:1") == {}


# -- the id mapping ----------------------------------------------------------


@pytest.mark.parametrize(
    "end_user_id, expected",
    [
        ("telegram:8932435376", ("telegram", "8932435376")),
        ("whatsapp:6282311020200", ("whatsapp", "6282311020200")),
        # A service this module has never heard of still splits: inventory owns
        # the registry, so refusing here would be guessing on its behalf.
        ("signal:abc", ("signal", "abc")),
        # First colon only — an account id may contain one, a service never does.
        ("matrix:@alvi:example.org", ("matrix", "@alvi:example.org")),
    ],
)
def test_an_end_user_id_splits_into_service_and_account(end_user_id, expected):
    assert split_end_user_id(end_user_id) == expected


@pytest.mark.parametrize("bad", ["8932435376", "telegram:", ":123", "", ":"])
def test_ids_that_name_no_service_or_no_account_are_refused(bad):
    with pytest.raises(UnsupportedPlatform):
        split_end_user_id(bad)


def test_provider_redeem_uses_the_brain_token_and_stores_nothing_itself():
    used: list[str] = []

    class Client:
        def __init__(self, token):
            used.append(token)

        async def redeem_link(self, code, platform, external_id):
            assert (code, platform, external_id) == ("CODE1", "telegram", "42")
            return {
                "token": "user-token",
                "user_display_name": "Alvi",
                "workspace_name": "Alviandi Inventory",
            }

    provider = InvintiryLinkProvider(Client, "BRAIN-TOKEN")
    account = asyncio.run(provider.redeem("CODE1", "telegram:42"))

    assert used == ["BRAIN-TOKEN"]  # not the user's — they have none yet
    assert account == LinkedAccount("user-token", "Alvi", "Alviandi Inventory")


def test_a_success_carrying_no_token_becomes_a_link_failure():
    """A 201 that omits the credential must not escape as an unhandled error —
    the person is mid-flow and needs a sentence, not a generic apology."""

    class Client:
        def __init__(self, token):
            pass

        async def redeem_link(self, code, platform, external_id):
            return {"user_display_name": "Alvi"}  # no token

    provider = InvintiryLinkProvider(Client, "BRAIN-TOKEN")
    with pytest.raises(InvintiryError):
        asyncio.run(provider.redeem("CODE1", "telegram:42"))


def test_provider_revoke_uses_the_token_being_revoked_and_names_its_service():
    used: list[str] = []
    unlinked: list[str] = []

    class Client:
        def __init__(self, token):
            used.append(token)

        async def unlink(self, platform):
            unlinked.append(platform)

    provider = InvintiryLinkProvider(Client, "BRAIN-TOKEN")
    asyncio.run(provider.revoke("USER-TOKEN", "whatsapp:6282311020200"))

    assert used == ["USER-TOKEN"]  # not the brain's
    assert unlinked == ["whatsapp"]  # a logout must not reach past its own service
