import asyncio
from copy import deepcopy

import httpx
import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from application.api_integrations.memory_service.memory_service_client import (
    MemoryServiceClient,
)
from application.business_domain.awakening_domain import assemble_system_prompt
from application.business_services.knowledge_tools import build_knowledge_tools

SHARED = "11111111111111111111111111111111"
PRIVATE = "22222222222222222222222222222222"
OTHER = "33333333333333333333333333333333"
PAYLOAD = {
    "identity": [{"title": "Identity", "content": "Agent"}],
    "shared": {"knowledge_index": [
        {"uuid": SHARED, "title": "Windows", "content_summary": "File sharing behavior"}
    ]},
    "knowledge_index": [{"uuid": PRIVATE, "title": "Own knowledge"}],
}


def record(uuid=SHARED, **changes):
    return dict(
        uuid=uuid, record_type="knowledge", content="private body marker",
        project=None, archived_date=None, blocks_returned=1, blocks_total=1,
        next_cursor=None, **changes,
    )


def execute_tool(address=SHARED, response=None, *, status=200, exclude=(), handler=None):
    seen = []
    returns = []

    def handle(request):
        seen.append(request)
        return handler(request) if handler else httpx.Response(
            status, json=response if response is not None else record()
        )

    async def token():
        return "do-not-disclose"

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = MemoryServiceClient("https://memory.example", token, http)
            tools = build_knowledge_tools(client, "op", PAYLOAD, exclude=exclude)

            def model(messages, info):
                tool_results = [
                    part for message in messages for part in message.parts
                    if isinstance(part, ToolReturnPart)
                ]
                if tool_results:
                    returns.extend(p.content for p in tool_results)
                    return ModelResponse(parts=[TextPart("done")])
                return ModelResponse(parts=[
                    ToolCallPart("read_knowledge", {"uuid": address}, tool_call_id="read")
                ])

            await Agent(FunctionModel(model), toolsets=[tools]).run("Read relevant knowledge")

    asyncio.run(run())
    return seen, returns[-1]


@pytest.mark.parametrize("uuid,owner", [(SHARED, None), (PRIVATE, "op")])
def test_real_tool_invocation_reads_only_an_advertised_record(uuid, owner):
    body = record(uuid, agent_id=owner)
    seen, result = execute_tool(uuid, body)
    assert result == body
    assert seen[0].url.path == f"/api/record/{uuid}"
    assert seen[0].headers["authorization"] == "Bearer do-not-disclose"
    assert len(seen) == 1


@pytest.mark.parametrize("address", [OTHER, SHARED[:6], "../secret", ""])
def test_unadvertised_ids_are_refused_without_http(address):
    seen, result = execute_tool(address)
    assert not seen
    assert result == {"error": "knowledge_not_available"}


def test_excluded_shared_index_cannot_be_read_through_the_tool():
    seen, result = execute_tool(exclude=("shared.knowledge",))
    assert not seen
    assert "error" in result


@pytest.mark.parametrize("changes", [
    {"agent_id": "foreign"}, {"record_type": "episode"}, {"project": "other"},
    {"archived_date": "2026-11-13"}, {"uuid": OTHER},
])
def test_changed_record_boundary_is_checked_before_disclosure(changes):
    body = record()
    body.update(changes)
    _, result = execute_tool(response=body)
    assert "error" in result
    assert "private body marker" not in str(result)


@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_upstream_errors_do_not_echo_credentials_or_body(status):
    _, result = execute_tool(status=status, response={"secret": "do-not-disclose"})
    expected = "knowledge_auth_failed" if status in (401, 403) else "knowledge_read_failed"
    assert result == {"error": expected}


def test_network_failure_is_bounded_error_data():
    def fail(request):
        raise httpx.ConnectError("do-not-disclose", request=request)
    _, result = execute_tool(handler=fail)
    assert result == {"error": "knowledge_read_failed"}


def test_non_json_response_is_bounded_error_data():
    _, result = execute_tool(
        handler=lambda request: httpx.Response(200, text="not JSON: do-not-disclose")
    )
    assert result == {"error": "knowledge_read_failed"}


def test_partial_record_metadata_is_preserved_without_extra_fetch():
    body = record()
    body.update(blocks_total=2, next_cursor="remaining")
    seen, result = execute_tool(response=body)
    assert result == body
    assert len(seen) == 1


@pytest.mark.parametrize("response", [[], {"content": "body"}, {"uuid": SHARED}])
def test_malformed_records_never_become_knowledge(response):
    _, result = execute_tool(response=response)
    assert "error" in result


def test_index_rendering_preserves_summary_uuid_and_legacy_filter_intent():
    prompt = assemble_system_prompt(PAYLOAD, layers=("shared.knowledge", "identity"))
    assert "## shared.knowledge_index" in prompt
    assert SHARED in prompt
    assert "File sharing behavior" in prompt
    assert prompt.index("shared.knowledge_index") < prompt.index("## identity")
    assert "knowledge_index" not in assemble_system_prompt(
        PAYLOAD, exclude=("shared.knowledge", "knowledge_index")
    )
    both = deepcopy(PAYLOAD)
    both["shared"]["knowledge"] = [{"title": "old", "content": "do not duplicate"}]
    assert "do not duplicate" not in assemble_system_prompt(both)
