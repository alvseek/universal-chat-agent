"""build_bindings startup-guard tests — the boot must fail, clearly, not the chat."""
import pytest

from application.configuration.env import Config
from application.main import build_bindings

DEMO = ("demo", "tests.support.demo_toolset:build_demo_toolsets")


def _config(agent_toolsets, toolset_sources=()):
    return Config(
        llm_api_key="k", llm_model="m", llm_base_url="b",
        memory_window=15, db_path=":memory:", system_prompt="p",
        host="127.0.0.1", port=8000, memory_service=None,
        agent_toolsets=agent_toolsets, toolset_sources=toolset_sources,
    )


def test_no_bindings_builds_nothing():
    assert build_bindings(_config(())) == {}


def test_a_named_source_builds_its_toolsets():
    bindings = build_bindings(
        _config((("demo-agent", "demo"),), toolset_sources=(DEMO,))
    )
    assert len(bindings["demo-agent"]) == 2


def test_binding_a_name_with_no_source_fails_at_startup():
    with pytest.raises(ValueError, match="unknown toolset 'nope'"):
        build_bindings(_config((("someone", "nope"),)))


def test_a_source_that_cannot_be_imported_names_itself():
    with pytest.raises(ValueError, match="toolset source 'ghost'"):
        build_bindings(
            _config((("a", "ghost"),), toolset_sources=(("ghost", "no_such_pkg:x"),))
        )


def test_registry_binds_knowledge_and_external_tools_to_the_actual_agent(monkeypatch):
    import asyncio

    from application import main
    from application.configuration.env import MemoryServiceConfig
    from tests.business_services.test_knowledge_tools import PAYLOAD

    captured = []

    async def awaken(self, agent_id):
        return PAYLOAD

    def build(*args, **kwargs):
        captured.append((args, kwargs))
        return object()

    monkeypatch.setattr(main.MemoryServiceClient, "awaken", awaken)
    monkeypatch.setattr(main, "build_agent", build)
    external = build_bindings(
        _config((("op", "demo"),), toolset_sources=(DEMO,))
    )
    memory = MemoryServiceConfig(
        url="https://memory.example", resource="r", client_id="id", client_secret="secret",
        scope="", issuer="https://issuer.example", cache_ttl_seconds=60, layers=None, exclude=(),
    )

    async def run():
        registry, http = main.build_registry(_config(()), memory, external)
        try:
            await registry.get("op")
            await registry.reload("op")
        finally:
            await http.aclose()

    asyncio.run(run())
    assert len(captured) == 2
    for args, kwargs in captured:
        prompt = args[3]
        assert "read_knowledge" in prompt and "look_up" in prompt
        tools = kwargs["toolsets"]
        assert len(tools) == 3
        assert "read_knowledge" in tools[-1].tools
    assert len(external["op"]) == 2  # no mutation or repeated binding on reload
