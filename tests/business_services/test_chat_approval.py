"""End-to-end write-approval flow — scripted model, real wiring.

Proves the property the gate bought: a write tool call cannot execute in the turn
the model makes it. The run pauses, the user gets a confirmation line, and only a
clear "yes" in the *next* turn executes the write; anything else denies it. The
model here is a FunctionModel that always tries to call a write tool first — the
most adversarial script for this property.
"""
import asyncio
import base64

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
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

STORE_CALL = ("store", {"name": "Rak C"})
DISCARD_CALL = ("discard", {"name": "Rak D"})


def _scripted_model(call=STORE_CALL):
    """Calls the given write tool on first contact; answers with its result after."""
    tool_name, args = call

    def model_fn(messages, info):
        returns = [
            p
            for m in messages
            for p in getattr(m, "parts", [])
            if isinstance(p, ToolReturnPart)
        ]
        if returns:
            return ModelResponse(parts=[TextPart(f"result: {returns[-1].content}")])
        return ModelResponse(parts=[ToolCallPart(tool_name=tool_name, args=args)])

    return FunctionModel(model_fn)


CALLER = "telegram:1"


class _LinkedCaller:
    """Every message in these tests comes from one linked person.

    A thin wrapper rather than an argument on each call: what these tests are
    about is the approval gate, and threading an identity through every line
    would bury that behind plumbing.
    """

    def __init__(self, service):
        self._service = service

    async def handle(self, conversation_id, message, image=None):
        return await self._service.handle(
            conversation_id, message, end_user_id=CALLER, image=image
        )


class _StubProvider:
    """Enough of a link provider for tests that never redeem or revoke."""

    account_label = "account"
    how_to_link = "Tap the link in your account settings."

    async def redeem(self, code, end_user_id):  # pragma: no cover - unused here
        raise AssertionError("these tests do not redeem")

    async def revoke(self, token, end_user_id):
        return None


def _service(tmp_path, call=STORE_CALL, linked=True):
    demo.reset()
    agent = Agent(
        _scripted_model(call),
        system_prompt="operator",
        toolsets=demo.build_demo_toolsets({}),
        deps_type=ChatDeps,
        output_type=[str, DeferredToolRequests],
    )
    repo = MessageRepository(str(tmp_path / "chat.db"))
    pending = PendingApprovalRepository(str(tmp_path / "chat.db"))
    links = ServiceLinkRepository(str(tmp_path / "chat.db"))
    if linked:
        links.put(demo.SERVICE, CALLER, "user-token")
    service = ChatService(
        agent, repo, 15, None, pending,
        LinkService(links, {demo.SERVICE: _StubProvider()}),
    )
    return _LinkedCaller(service), pending


def test_write_pauses_then_yes_executes(tmp_path):
    service, pending = _service(tmp_path)

    reply1 = asyncio.run(service.handle("c1", "store Rak C"))
    assert "confirm" in reply1.lower() and "store" in reply1
    assert demo.stored == []  # nothing executed yet
    assert pending.get("c1") is not None

    reply2 = asyncio.run(service.handle("c1", "yes"))
    assert demo.calls == [("user-token", "store")]
    assert demo.stored == ["Rak C"]
    assert pending.get("c1") is None
    assert "result:" in reply2


def test_anything_but_yes_denies(tmp_path):
    service, pending = _service(tmp_path)

    asyncio.run(service.handle("c2", "store Rak C"))
    reply = asyncio.run(service.handle("c2", "hmm actually wait"))
    assert demo.stored == []
    assert pending.get("c2") is None
    assert "did not confirm" in reply.lower()


def test_a_second_write_pauses_the_same_way(tmp_path):
    """The gate is a property of the writes toolset, not of one tool — a newly
    added write inherits it, and the confirmation line names the real call."""
    service, pending = _service(tmp_path, call=DISCARD_CALL)

    reply1 = asyncio.run(service.handle("c5", "discard Rak D"))
    assert "confirm" in reply1.lower() and "discard" in reply1
    assert demo.calls == []  # nothing ran yet

    reply2 = asyncio.run(service.handle("c5", "yes"))
    assert demo.calls == [("user-token", "discard")]
    assert pending.get("c5") is None
    assert "result:" in reply2


def test_a_denied_second_write_leaves_nothing_behind(tmp_path):
    service, pending = _service(tmp_path, call=DISCARD_CALL)

    asyncio.run(service.handle("c6", "discard Rak D"))
    reply = asyncio.run(service.handle("c6", "no wait"))
    assert demo.calls == []
    assert pending.get("c6") is None
    assert "did not confirm" in reply.lower()


def test_pending_is_per_conversation(tmp_path):
    service, pending = _service(tmp_path)

    asyncio.run(service.handle("c3", "store Rak C"))
    # A different conversation is untouched by c3's pending write.
    assert pending.get("c4") is None
    reply = asyncio.run(service.handle("c3", "ya"))
    assert demo.stored == ["Rak C"]
    assert "result:" in reply


def test_a_photo_survives_the_approval_pause(tmp_path):
    """The write executes two turns later, so the photo from the request turn is
    parked with it and restored on the resume — the "yes" turn carries none."""
    service, pending = _service(tmp_path)
    raw = b"\x89PNG\r\n\x1a\nphoto-bytes"

    asyncio.run(
        service.handle("c7", "store Rak C", image=base64.b64encode(raw).decode())
    )

    parked = pending.get("c7")
    assert parked is not None and parked.image == raw
    assert demo.stored == []  # nothing ran yet

    asyncio.run(service.handle("c7", "yes"))

    assert demo.stored == ["Rak C"]
    assert demo.images == [raw]  # the tool saw the photo from the parked turn


def test_a_declined_photo_is_discarded(tmp_path):
    service, pending = _service(tmp_path)
    raw = b"\x89PNG\r\n\x1a\nphoto-bytes"

    asyncio.run(
        service.handle("c8", "store Rak C", image=base64.b64encode(raw).decode())
    )
    asyncio.run(service.handle("c8", "no thanks"))

    assert pending.get("c8") is None
    assert demo.images == []
    assert demo.stored == []
