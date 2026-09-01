"""Config tests — the memory-service block is optional as a whole, required as a set.

Without MUNNIN_URL the brain is the single default agent (memory_service None).
With it, the credential and issuer become required, the resource defaults to
``<url>/mcp``, and the layer lists parse from CSV.
"""
import pytest

from application.configuration import env

BASE = {"OPENROUTER_API_KEY": "k", "OPENROUTER_MODEL": "m"}


def _set(monkeypatch, values):
    for name in ("MUNNIN_URL", "MUNNIN_RESOURCE", "MUNNIN_M2M_CLIENT_ID", "MUNNIN_M2M_CLIENT_SECRET",
                 "MUNNIN_M2M_SCOPE", "AUTHENTRA_ISSUER", "AGENT_CACHE_TTL_SECONDS",
                 "AWAKENING_LAYERS", "AWAKENING_EXCLUDE", *BASE):
        monkeypatch.delenv(name, raising=False)
    for k, v in {**BASE, **values}.items():
        monkeypatch.setenv(k, v)


def test_no_memory_service_without_munnin_url(monkeypatch):
    _set(monkeypatch, {})
    assert env.load_config().memory_service is None


def test_memory_service_requires_credential_and_issuer(monkeypatch):
    _set(monkeypatch, {"MUNNIN_URL": "https://munnin.example"})
    with pytest.raises(ValueError, match="MUNNIN_M2M_CLIENT_ID"):
        env.load_config()


def test_memory_service_defaults(monkeypatch):
    _set(monkeypatch, {
        "MUNNIN_URL": "https://munnin.example/",
        "MUNNIN_M2M_CLIENT_ID": "app",
        "MUNNIN_M2M_CLIENT_SECRET": "s",
        "AUTHENTRA_ISSUER": "https://auth.example/oidc/",
    })
    ms = env.load_config().memory_service

    assert ms.url == "https://munnin.example"
    assert ms.resource == "https://munnin.example/mcp"
    assert ms.issuer == "https://auth.example/oidc"
    assert ms.scope == ""
    assert ms.cache_ttl_seconds == 8 * 60 * 60
    assert ms.layers is None
    assert ms.exclude == ()


def test_memory_service_layers_parse_from_csv(monkeypatch):
    _set(monkeypatch, {
        "MUNNIN_URL": "https://munnin.example",
        "MUNNIN_M2M_CLIENT_ID": "app",
        "MUNNIN_M2M_CLIENT_SECRET": "s",
        "AUTHENTRA_ISSUER": "https://auth.example/oidc",
        "AGENT_CACHE_TTL_SECONDS": "60",
        "AWAKENING_LAYERS": " identity, shared.reasoning ,",
        "AWAKENING_EXCLUDE": "emotional",
    })
    ms = env.load_config().memory_service

    assert ms.cache_ttl_seconds == 60
    assert ms.layers == ("identity", "shared.reasoning")
    assert ms.exclude == ("emotional",)
