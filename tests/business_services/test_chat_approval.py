"""End-to-end write-approval flow — scripted model, fake invintiry, real wiring.

Proves the property decision 2 bought: a write tool call cannot execute in the
turn the model makes it. The run pauses, the user gets a confirmation line, and
only a clear "yes" in the *next* turn executes the write; anything else denies
it. The model here is a FunctionModel that always tries to call move_item first
— the most adversarial script for this property.
"""
import asyncio

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.tools import DeferredToolRequests

from application.business_services.chat_service import ChatService
from application.business_services.toolsets.invintiry import build_invintiry_toolsets
from application.data_repositories.message_repository import MessageRepository
from application.data_repositories.pending_approval_repository import (
    PendingApprovalRepository,
)

from .test_invintiry_toolset import FakeClient


MOVE_CALL = (
    "move_item",
    {"item": "EL-001", "from_location": "Rak B", "to_location": "Rak A", "quantity": 3},
)
CREATE_LOCATION_CALL = ("create_location", {"name": "Rak C", "category": "Gudang"})


def _scripted_model(call=MOVE_CALL):
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


def _service(tmp_path, call=MOVE_CALL):
    client = FakeClient()
    toolsets = build_invintiry_toolsets({"invintiry_client": client})
    agent = Agent(
        _scripted_model(call),
        system_prompt="operator",
        toolsets=toolsets,
        output_type=[str, DeferredToolRequests],
    )
    repo = MessageRepository(str(tmp_path / "chat.db"))
    pending = PendingApprovalRepository(str(tmp_path / "chat.db"))
    return ChatService(agent, repo, 15, None, pending), client, pending


def test_write_pauses_then_yes_executes(tmp_path):
    service, client, pending = _service(tmp_path)

    reply1 = asyncio.run(service.handle("c1", "move 3 kabel to rak a"))
    assert "confirm" in reply1.lower() and "move_item" in reply1
    assert client.transfers == []  # nothing executed yet
    assert pending.get("c1") is not None

    reply2 = asyncio.run(service.handle("c1", "yes"))
    assert client.transfers == [(1, 10, 11, 3)]
    assert pending.get("c1") is None
    assert "result:" in reply2


def test_anything_but_yes_denies(tmp_path):
    service, client, pending = _service(tmp_path)

    asyncio.run(service.handle("c2", "move 3 kabel to rak a"))
    reply = asyncio.run(service.handle("c2", "hmm actually wait"))
    assert client.transfers == []
    assert pending.get("c2") is None
    assert "did not confirm" in reply.lower()


def test_creating_a_location_pauses_the_same_way(tmp_path):
    """The gate is a property of the writes toolset, not of move_item — a newly
    added write inherits it, and the confirmation line names the real call."""
    service, client, pending = _service(tmp_path, call=CREATE_LOCATION_CALL)
    before = len(client.locations)

    reply1 = asyncio.run(service.handle("c5", "add a shelf called Rak C"))
    assert "confirm" in reply1.lower() and "create_location" in reply1
    assert "Rak C" in reply1
    assert len(client.locations) == before  # nothing created yet

    reply2 = asyncio.run(service.handle("c5", "yes"))
    assert [l["name"] for l in client.locations][-1] == "Rak C"
    assert pending.get("c5") is None
    assert "result:" in reply2


def test_creating_a_location_denied_leaves_nothing_behind(tmp_path):
    service, client, pending = _service(tmp_path, call=CREATE_LOCATION_CALL)
    before = len(client.locations)

    asyncio.run(service.handle("c6", "add a shelf called Rak C"))
    reply = asyncio.run(service.handle("c6", "no wait"))
    assert len(client.locations) == before
    assert pending.get("c6") is None
    assert "did not confirm" in reply.lower()


def test_pending_is_per_conversation(tmp_path):
    service, client, pending = _service(tmp_path)

    asyncio.run(service.handle("c3", "move 3 kabel"))
    # A different conversation is untouched by c3's pending write.
    assert pending.get("c4") is None
    reply = asyncio.run(service.handle("c3", "ya"))
    assert client.transfers == [(1, 10, 11, 3)]
    assert "result:" in reply
