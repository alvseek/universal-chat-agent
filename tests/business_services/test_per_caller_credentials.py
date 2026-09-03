"""The property this whole change exists for: one warm agent, many callers.

An agent is built once and cached for hours, and it answers everyone who talks to
that bot. So the dangerous failure is not a crash — it is a *quiet* one: user A's
message reaching inventory on user B's credential, which looks like a normal
successful reply and is wrong in a way no error surfaces.

These tests go through the real toolset and a real ``Agent``, not the closures
directly, because what is being proven is that pydantic-ai carries the caller
into the tool on both paths — the first run, and the resume where an approved
write actually executes.
"""
import asyncio

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.tools import DeferredToolRequests

from application.api_integrations.invintiry.invintiry_client import InvintiryError
from application.business_services.chat_deps import ChatDeps
from application.business_services.chat_service import ChatService
from application.business_services.link_service import LinkService
from application.business_services.toolsets.invintiry import build_invintiry_toolsets
from application.data_repositories.message_repository import MessageRepository
from application.data_repositories.pending_approval_repository import (
    PendingApprovalRepository,
)
from application.data_repositories.service_link_repository import ServiceLinkRepository

ALICE = "telegram:111"
BOB = "telegram:222"

ITEM = {
    "id": 1, "name": "Kabel HDMI 2m", "sku": "EL-001", "quantity": 7,
    "is_low_stock": False, "description": "", "bottom_stock": 2,
    "category_name": "Elektronik", "tags": [],
    "item_locations": [
        {"location": 10, "location_name": "Rak B", "quantity": 5, "expires_at": None},
        {"location": 11, "location_name": "Rak A", "quantity": 2, "expires_at": None},
    ],
}


class RecordingClient:
    """One per caller, built by the factory — remembers which token built it."""

    calls: list[tuple[str, str]] = []  # (token, operation), shared across instances

    def __init__(self, token: str, fail_auth: bool = False):
        self.token = token
        self._fail_auth = fail_auth

    def _record(self, op: str) -> None:
        if self._fail_auth:
            raise InvintiryError(401, "credential refused")
        RecordingClient.calls.append((self.token, op))

    async def search_items(self, query, low_stock_only=False):
        self._record("search_items")
        return [ITEM]

    async def get_item(self, item_id):
        self._record("get_item")
        return ITEM

    async def transfer(self, item_id, from_id, to_id, quantity, notes=""):
        self._record(f"transfer:{quantity}")
        return {"id": 99}


def _factory(fail_auth_for=()):
    def make(token: str) -> RecordingClient:
        return RecordingClient(token, fail_auth=token in fail_auth_for)

    return make


def _read_model():
    """Calls find_items once, then reports what came back."""

    def model_fn(messages, info):
        returns = [
            p for m in messages for p in getattr(m, "parts", [])
            if isinstance(p, ToolReturnPart)
        ]
        if returns:
            return ModelResponse(parts=[TextPart(f"result: {returns[-1].content}")])
        return ModelResponse(parts=[ToolCallPart(tool_name="find_items", args={"query": "kabel"})])

    return FunctionModel(model_fn)


def _write_model():
    """Calls move_item once (numeric refs, so no lookup round-trips), then reports."""

    def model_fn(messages, info):
        returns = [
            p for m in messages for p in getattr(m, "parts", [])
            if isinstance(p, ToolReturnPart)
        ]
        if returns:
            return ModelResponse(parts=[TextPart(f"result: {returns[-1].content}")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="move_item",
                    args={"item": "1", "from_location": "10", "to_location": "11", "quantity": 3},
                )
            ]
        )

    return FunctionModel(model_fn)


def _build(tmp_path, model, links_seed, fail_auth_for=()):
    """One agent, built ONCE — exactly as the registry would cache it."""
    RecordingClient.calls = []
    toolsets = build_invintiry_toolsets({"invintiry_make_client": _factory(fail_auth_for)})
    agent = Agent(
        model,
        system_prompt="operator",
        toolsets=toolsets,
        deps_type=ChatDeps,
        output_type=[str, DeferredToolRequests],
    )
    db = str(tmp_path / "agent.db")
    link_repo = ServiceLinkRepository(db)
    for end_user_id, token in links_seed.items():
        link_repo.put("invintiry", end_user_id, token)
    pending = PendingApprovalRepository(db)
    service = ChatService(
        agent, MessageRepository(db), 15, None, pending, LinkService(link_repo, _Provider())
    )
    return service, pending, link_repo


class _Provider:
    service = "invintiry"

    async def redeem(self, code, end_user_id):  # pragma: no cover - not used here
        raise AssertionError("not exercised")

    async def revoke(self, token):
        return None


# -- the isolation property ---------------------------------------------------


def test_two_callers_through_one_agent_use_their_own_tokens(tmp_path):
    service, _, _ = _build(
        tmp_path, _read_model(), {ALICE: "alice-token", BOB: "bob-token"}
    )

    asyncio.run(service.handle("chat:1", "what kabel do we have", end_user_id=ALICE))
    asyncio.run(service.handle("chat:2", "what kabel do we have", end_user_id=BOB))

    tokens = [token for token, _ in RecordingClient.calls]
    assert tokens == ["alice-token", "bob-token"]
    # Negative control: if deps were ignored and a credential were shared, both
    # entries would be identical. Two distinct tokens is the whole proof.
    assert len(set(tokens)) == 2


def test_an_approved_write_executes_on_the_approvers_token(tmp_path):
    """The resume path is where a write really runs — a turn after it was asked."""
    service, pending, _ = _build(
        tmp_path, _write_model(), {ALICE: "alice-token", BOB: "bob-token"}
    )

    paused = asyncio.run(service.handle("chat:1", "move 3 to rak a", end_user_id=ALICE))
    assert "confirm" in paused.lower()
    assert not any("transfer" in op for _, op in RecordingClient.calls)

    asyncio.run(service.handle("chat:1", "yes", end_user_id=ALICE))

    transfers = [(token, op) for token, op in RecordingClient.calls if op.startswith("transfer")]
    assert transfers == [("alice-token", "transfer:3")]


def test_bobs_yes_cannot_execute_alices_staged_write(tmp_path):
    """Pending state is keyed by conversation, and credentials by person.

    Bob answering "yes" in his own chat must not resume anything of Alice's, and
    whatever he does run must be on his own token.
    """
    service, pending, _ = _build(
        tmp_path, _write_model(), {ALICE: "alice-token", BOB: "bob-token"}
    )

    asyncio.run(service.handle("chat:alice", "move 3 to rak a", end_user_id=ALICE))
    RecordingClient.calls = []

    asyncio.run(service.handle("chat:bob", "yes", end_user_id=BOB))

    assert pending.get("chat:alice") is not None  # Alice's stays parked
    assert all(token == "bob-token" for token, _ in RecordingClient.calls)


# -- unlinked ----------------------------------------------------------------


def test_an_unlinked_caller_gets_not_linked_as_data(tmp_path):
    service, _, _ = _build(tmp_path, _read_model(), {})

    reply = asyncio.run(service.handle("chat:1", "what do we have", end_user_id=ALICE))

    assert RecordingClient.calls == []  # no client was ever built
    assert "not_linked" in reply  # the model relayed the tool's answer
    assert "Settings" in reply


def test_a_bridge_sending_no_caller_identity_is_treated_as_unlinked(tmp_path):
    service, _, _ = _build(tmp_path, _read_model(), {ALICE: "alice-token"})

    reply = asyncio.run(service.handle("chat:1", "what do we have"))

    assert RecordingClient.calls == []
    assert "not_linked" in reply


# -- link changes and revoked credentials -------------------------------------


def test_a_link_change_drops_a_staged_write(tmp_path):
    """Resuming after an identity change would run one person's approved write
    against another's account — so the staged write goes when the link does."""
    service, pending, _ = _build(tmp_path, _write_model(), {ALICE: "alice-token"})

    asyncio.run(service.handle("chat:1", "move 3 to rak a", end_user_id=ALICE))
    assert pending.get("chat:1") is not None

    reply = asyncio.run(service.handle("chat:1", "/logout", end_user_id=ALICE))

    assert pending.get("chat:1") is None
    assert "disconnected" in reply.lower()

    # And the "yes" that would have approved it now runs nothing.
    RecordingClient.calls = []
    asyncio.run(service.handle("chat:1", "yes", end_user_id=ALICE))
    assert not any(op.startswith("transfer") for _, op in RecordingClient.calls)


def test_a_refused_credential_is_forgotten(tmp_path):
    service, _, link_repo = _build(
        tmp_path, _read_model(), {ALICE: "stale-token"}, fail_auth_for={"stale-token"}
    )

    reply = asyncio.run(service.handle("chat:1", "what do we have", end_user_id=ALICE))

    assert "auth_failed" in reply
    assert link_repo.credentials(ALICE) == {}  # dropped, so the next turn asks to link


def test_one_service_refusing_does_not_unlink_the_others(tmp_path):
    """Being refused by inventory says nothing about a link to anything else.

    The store is keyed by (service, person) precisely so a 401 from one service
    cannot take the rest with it — a service-blind cleanup would undo that at the
    one moment it matters.
    """
    service, _, link_repo = _build(
        tmp_path, _read_model(), {ALICE: "stale-token"}, fail_auth_for={"stale-token"}
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
    assert RecordingClient.calls == []
