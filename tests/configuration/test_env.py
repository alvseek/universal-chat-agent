"""Config tests — the memory-service block is optional as a whole, required as a set.

Without MEMORY_SERVICE_URL the brain is the single default agent (memory_service None).
With it, the credential and issuer become required, the resource defaults to
``<url>/mcp``, and the layer lists parse from CSV.
"""
import pytest

from application.configuration import env

BASE = {"LLM_API_KEY": "k", "LLM_MODEL": "m"}


def _set(monkeypatch, values):
    for name in ("MEMORY_SERVICE_URL", "MEMORY_SERVICE_RESOURCE", "MEMORY_SERVICE_CLIENT_ID", "MEMORY_SERVICE_CLIENT_SECRET",
                 "MEMORY_SERVICE_SCOPE", "OIDC_ISSUER", "AGENT_CACHE_TTL_SECONDS",
                 "AWAKENING_LAYERS", "AWAKENING_EXCLUDE",
                 "AGENT_TOOLSETS", "TOOLSET_SOURCES", "LINK_PROVIDERS", *BASE):
        monkeypatch.delenv(name, raising=False)
    for k, v in {**BASE, **values}.items():
        monkeypatch.setenv(k, v)


def test_no_memory_service_without_a_url(monkeypatch):
    _set(monkeypatch, {})
    assert env.load_config().memory_service is None


def test_memory_service_requires_credential_and_issuer(monkeypatch):
    _set(monkeypatch, {"MEMORY_SERVICE_URL": "https://memory.example"})
    with pytest.raises(ValueError, match="MEMORY_SERVICE_CLIENT_ID"):
        env.load_config()


def test_memory_service_defaults(monkeypatch):
    _set(monkeypatch, {
        "MEMORY_SERVICE_URL": "https://memory.example/",
        "MEMORY_SERVICE_CLIENT_ID": "app",
        "MEMORY_SERVICE_CLIENT_SECRET": "s",
        "OIDC_ISSUER": "https://auth.example/oidc/",
    })
    ms = env.load_config().memory_service

    assert ms.url == "https://memory.example"
    assert ms.resource == "https://memory.example/mcp"
    assert ms.issuer == "https://auth.example/oidc"
    assert ms.scope == ""
    assert ms.cache_ttl_seconds == 8 * 60 * 60
    assert ms.layers is None
    assert ms.exclude == ()


def test_memory_service_layers_parse_from_csv(monkeypatch):
    _set(monkeypatch, {
        "MEMORY_SERVICE_URL": "https://memory.example",
        "MEMORY_SERVICE_CLIENT_ID": "app",
        "MEMORY_SERVICE_CLIENT_SECRET": "s",
        "OIDC_ISSUER": "https://auth.example/oidc",
        "AGENT_CACHE_TTL_SECONDS": "60",
        "AWAKENING_LAYERS": " identity, shared.reasoning ,",
        "AWAKENING_EXCLUDE": "emotional",
    })
    ms = env.load_config().memory_service

    assert ms.cache_ttl_seconds == 60
    assert ms.layers == ("identity", "shared.reasoning")
    assert ms.exclude == ("emotional",)


def test_no_toolsets_by_default(monkeypatch):
    _set(monkeypatch, {})
    config = env.load_config()
    assert config.agent_toolsets == ()
    assert config.toolset_sources == () and config.link_providers == ()


def test_agent_toolsets_parse_pairs(monkeypatch):
    _set(monkeypatch, {"AGENT_TOOLSETS": " demo-agent=demo , other=x "})
    assert env.load_config().agent_toolsets == (
        ("demo-agent", "demo"), ("other", "x"),
    )


def test_agent_toolsets_malformed_pair_fails_at_startup(monkeypatch):
    _set(monkeypatch, {"AGENT_TOOLSETS": "demo-agent"})
    with pytest.raises(ValueError, match="AGENT_TOOLSETS"):
        env.load_config()


# -- extension points --------------------------------------------------------


def test_sources_parse_into_alias_and_target(monkeypatch):
    _set(monkeypatch, {
        "TOOLSET_SOURCES": "demo=my_pkg.toolsets:build, other=other.mod:make",
    })
    assert env.load_config().toolset_sources == (
        ("demo", "my_pkg.toolsets:build"),
        ("other", "other.mod:make"),
    )


def test_an_extension_point_is_empty_when_unset(monkeypatch):
    _set(monkeypatch, {})
    config = env.load_config()
    assert config.toolset_sources == () and config.link_providers == ()


@pytest.mark.parametrize("bad", ["demo", "=mypkg:build", "demo=mypkg"])
def test_a_malformed_extension_point_is_refused(monkeypatch, bad):
    _set(monkeypatch, {"LINK_PROVIDERS": bad})
    with pytest.raises(ValueError, match="alias=module:attribute"):
        env.load_config()
