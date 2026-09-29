"""The property this whole design exists for: one warm agent, many callers.

An agent is built once and cached for hours, and it answers everyone who talks to
that bot. So the dangerous failure is not a crash — it is a *quiet* one: user A's
message reaching the service on user B's credential, which looks like a normal
successful reply and is wrong in a way no error surfaces.

These tests go through a real toolset and a real ``Agent``, not the closures
directly, because what is being proven is that pydantic-ai carries the caller
into the tool on both paths — the first run, and the resume where an approved
write actually executes.
"""
import asyncio

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.tools import DeferredToolRequests

from application.business_services.chat_deps import ChatDeps
from application.business_services.chat_service import ChatService
from application.business_services.link_service import LinkService
from application.data_repositories.message_repository import MessageRepository
from application.data_repositories.pending_approval_repository import (
    PendingApprovalRepository,
)
from application.data_repositories.service_link_repository import ServiceLinkRepository
from tests.support import demo_toolset as demo

ALICE = "telegram:111"
BOB = "telegram:222"


def _read_model():
    """Calls look_up once, then reports what came back."""

    def model_fn(messages, info):
        returns = [
            p for m in messages for p in getattr(m, "parts", [])
            if isinstance(p, ToolReturnPart)
        ]
        if returns:
            return ModelResponse(parts=[TextPart(f"result: {returns[-1].content}")])
        return ModelResponse(parts=[ToolCallPart(tool_name="look_up", args={"query": "kabel"})])

    return FunctionModel(model_fn)


def _write_model():
    """Calls store once, then reports."""

    def model_fn(messages, info):
        returns = [
            p for m in messages for p in getattr(m, "parts", [])
            if isinstance(p, ToolReturnPart)
        ]
        if returns:
            return ModelResponse(parts=[TextPart(f"result: {returns[-1].content}")])
        return ModelResponse(
            parts=[ToolCallPart(tool_name="store", args={"name": "widget"})]
        )

    return FunctionModel(model_fn)


def _build(tmp_path, model, links_seed, refuse_for=()):
    """One agent, built ONCE — exactly as the registry would cache it."""
    demo.reset(refuse_tokens=set(refuse_for))
    agent = Agent(
        model,
        system_prompt="operator",
        toolsets=demo.build_demo_toolsets({}),
        deps_type=ChatDeps,
        output_type=[str, DeferredToolRequests],
    )
    db = str(tmp_path / "agent.db")
    link_repo = ServiceLinkRepository(db)
    for end_user_id, token in links_seed.items():
        link_repo.put(demo.SERVICE, end_user_id, token)
    pending = PendingApprovalRepository(db)
    service = ChatService(
        agent, MessageRepository(db), 15, None, pending,
        LinkService(link_repo, {demo.SERVICE: _Provider()}),
    )
    return service, pending, link_repo


class _Provider:
    account_label = "account"
    how_to_link = "Go to Settings → Chat Apps and tap the link."

    async def redeem(self, code, end_user_id):  # pragma: no cover - not used here
        raise AssertionError("not exercised")

    async def revoke(self, token, end_user_id):
        return None


# -- the isolation property ---------------------------------------------------


def test_two_callers_through_one_agent_use_their_own_tokens(tmp_path):
    service, _, _ = _build(
        tmp_path, _read_model(), {ALICE: "alice-token", BOB: "bob-token"}
    )

    asyncio.run(service.handle("chat:1", "what do we have", end_user_id=ALICE))
    asyncio.run(service.handle("chat:2", "what do we have", end_user_id=BOB))

    tokens = [token for token, _ in demo.calls]
    assert tokens == ["alice-token", "bob-token"]
    # Negative control: if deps were ignored and a credential were shared, both
    # entries would be identical. Two distinct tokens is the whole proof.
    assert len(set(tokens)) == 2


def test_an_approved_write_executes_on_the_approvers_token(tmp_path):
    """The resume path is where a write really runs — a turn after it was asked."""
    service, pending, _ = _build(
        tmp_path, _write_model(), {ALICE: "alice-token", BOB: "bob-token"}
    )

    paused = asyncio.run(service.handle("chat:1", "store widget", end_user_id=ALICE))
    assert "confirm" in paused.lower()
    assert demo.stored == []  # nothing ran while parked

    asyncio.run(service.handle("chat:1", "yes", end_user_id=ALICE))

    assert demo.calls == [("alice-token", "store")]
    assert demo.stored == ["widget"]


def test_bobs_yes_cannot_execute_alices_staged_write(tmp_path):
    """Pending state is keyed by conversation, and credentials by person.

    Bob answering "yes" in his own chat must not resume anything of Alice's, and
    whatever he does run must be on his own token.
    """
    service, pending, _ = _build(
        tmp_path, _write_model(), {ALICE: "alice-token", BOB: "bob-token"}
    )

    asyncio.run(service.handle("chat:alice", "store widget", end_user_id=ALICE))
    demo.calls.clear()

    asyncio.run(service.handle("chat:bob", "yes", end_user_id=BOB))

    assert pending.get("chat:alice") is not None  # Alice's stays parked
    assert all(token == "bob-token" for token, _ in demo.calls)


# -- unlinked ----------------------------------------------------------------


def test_an_unlinked_caller_gets_not_linked_as_data(tmp_path):
    service, _, _ = _build(tmp_path, _read_model(), {})

    reply = asyncio.run(service.handle("chat:1", "what do we have", end_user_id=ALICE))

    assert demo.calls == []  # no credential was ever used
    assert "not_linked" in reply  # the model relayed the tool's answer
    assert "link" in reply


def test_a_bridge_sending_no_caller_identity_is_treated_as_unlinked(tmp_path):
    service, _, _ = _build(tmp_path, _read_model(), {ALICE: "alice-token"})

    reply = asyncio.run(service.handle("chat:1", "what do we have"))

    assert demo.calls == []
    assert "not_linked" in reply


# -- link changes and revoked credentials -------------------------------------


def test_a_link_change_drops_a_staged_write(tmp_path):
    """Resuming after an identity change would run one person's approved write
    against another's account — so the staged write goes when the link does."""
    service, pending, _ = _build(tmp_path, _write_model(), {ALICE: "alice-token"})

    asyncio.run(service.handle("chat:1", "store widget", end_user_id=ALICE))
    assert pending.get("chat:1") is not None

    reply = asyncio.run(service.handle("chat:1", "/logout", end_user_id=ALICE))

    assert pending.get("chat:1") is None
    assert "disconnected" in reply.lower()

    # And the "yes" that would have approved it now runs nothing.
    demo.calls.clear()
    asyncio.run(service.handle("chat:1", "yes", end_user_id=ALICE))
    assert demo.calls == []


def test_a_refused_credential_is_forgotten(tmp_path):
    service, _, link_repo = _build(
        tmp_path, _read_model(), {ALICE: "stale-token"}, refuse_for={"stale-token"}
    )

    reply = asyncio.run(service.handle("chat:1", "what do we have", end_user_id=ALICE))

    assert "auth_failed" in reply
    assert link_repo.credentials(ALICE) == {}  # dropped, so the next turn asks to link


def test_one_service_refusing_does_not_unlink_the_others(tmp_path):
    """Being refused by one service says nothing about a link to anything else.

    The store is keyed by (service, person) precisely so a 401 from one service
    cannot take the rest with it — a service-blind cleanup would undo that at the
    one moment it matters.
    """
    service, _, link_repo = _build(
        tmp_path, _read_model(), {ALICE: "stale-token"}, refuse_for={"stale-token"}
    )
    link_repo.put("other", ALICE, "other-token")

    asyncio.run(service.handle("chat:1", "what do we have", end_user_id=ALICE))

    assert link_repo.credentials(ALICE) == {"other": "other-token"}


def test_logout_when_linked_revokes_and_reports(tmp_path):
    service, _, link_repo = _build(tmp_path, _read_model(), {ALICE: "alice-token"})

    reply = asyncio.run(service.handle("chat:1", "/logout", end_user_id=ALICE))

    assert link_repo.credentials(ALICE) == {}
    assert "revoked" in reply.lower()
    assert link_repo.credentials(BOB) == {}  # nobody else was touched


def test_start_without_a_code_prompts_and_never_reaches_the_model(tmp_path):
    service, _, _ = _build(tmp_path, _read_model(), {})

    reply = asyncio.run(service.handle("chat:1", "/start", end_user_id=ALICE))

    assert "Settings" in reply
    assert demo.calls == []
