"""build_bindings startup-guard tests — the boot must fail, clearly, not the chat."""
import pytest

from application.configuration.env import Config, InvintiryConfig
from application.main import build_bindings


def _config(agent_toolsets, invintiry=None):
    return Config(
        openrouter_api_key="k", openrouter_model="m", openrouter_base_url="b",
        memory_window=15, db_path=":memory:", system_prompt="p",
        host="127.0.0.1", port=8000, memory_service=None,
        agent_toolsets=agent_toolsets, invintiry=invintiry,
    )


def test_no_bindings_builds_nothing():
    bindings, client, _factory = build_bindings(_config(()))
    assert bindings == {} and client is None


def test_binding_with_configured_service_builds_toolsets():
    config = _config(
        (("invintiry-operator", "invintiry"),),
        invintiry=InvintiryConfig(api_url="https://api.inv.example", brain_token="t"),
    )
    bindings, client, _factory = build_bindings(config)
    assert len(bindings["invintiry-operator"]) == 2
    assert client is not None


def test_binding_without_backing_service_names_the_gap():
    with pytest.raises(ValueError, match="INVINTIRY_API_URL"):
        build_bindings(_config((("invintiry-operator", "invintiry"),)))


def test_unknown_toolset_fails_at_startup():
    with pytest.raises(ValueError, match="unknown toolset 'nope'"):
        build_bindings(_config((("someone", "nope"),)))
