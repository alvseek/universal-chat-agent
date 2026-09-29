"""Configuration: load and validate environment for universal-chat-agent.

Single source of settings. Fails fast (with a clear message) when a required
variable is missing, so misconfiguration is obvious at startup rather than at
the first request.

Two groups of settings:

* the brain itself — model, memory window, the default ``SYSTEM_PROMPT`` used
  when a request names no agent;
* the memory service — present only when ``MEMORY_SERVICE_URL`` is set. With it,
  requests may name an ``agent_id`` and the brain awakens that agent from the
  service using a machine credential from the OIDC issuer. Without it, the brain is
  the single default agent it always was.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

DEFAULT_SYSTEM_PROMPT = "You are a helpful, friendly assistant. Be concise and clear."
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_AGENT_CACHE_TTL_SECONDS = 8 * 60 * 60


@dataclass(frozen=True)
class MemoryServiceConfig:
    """How to reach the memory service and the credential that identifies this brain to it."""

    url: str  # e.g. https://memory.example
    resource: str  # the API resource indicator tokens are bound to
    client_id: str
    client_secret: str
    scope: str
    issuer: str  # e.g. https://auth.lok.quest/oidc
    cache_ttl_seconds: int
    # Which awakening layers to render, in order; None = every layer, canonical order.
    layers: tuple[str, ...] | None
    exclude: tuple[str, ...]


@dataclass(frozen=True)
class Config:
    llm_api_key: str
    llm_model: str
    llm_base_url: str
    memory_window: int
    db_path: str
    system_prompt: str
    host: str
    port: int
    memory_service: MemoryServiceConfig | None
    # Which named toolsets each agent is bound to (AGENT_TOOLSETS). Code owns
    # what a toolset does; this mapping owns who gets it. Empty = no agent has tools.
    agent_toolsets: tuple[tuple[str, str], ...]
    # Where the builder for each named toolset comes from (TOOLSET_SOURCES) and
    # where each link provider comes from (LINK_PROVIDERS), both written as
    # alias=module:attribute. Empty = only whatever the brain itself bundles.
    toolset_sources: tuple[tuple[str, str], ...] = ()
    link_providers: tuple[tuple[str, str], ...] = ()


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(
            f"Missing required environment variable: {name}. "
            f"Copy .env.example to .env and fill it in (see README)."
        )
    return value


def _int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except ValueError:
        return default
    return value if value >= minimum else default


def _csv(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _memory_service() -> MemoryServiceConfig | None:
    url = os.getenv("MEMORY_SERVICE_URL", "").strip().rstrip("/")
    if not url:
        return None
    layers = _csv("AWAKENING_LAYERS")
    return MemoryServiceConfig(
        url=url,
        resource=os.getenv("MEMORY_SERVICE_RESOURCE", "").strip() or f"{url}/mcp",
        client_id=_require("MEMORY_SERVICE_CLIENT_ID"),
        client_secret=_require("MEMORY_SERVICE_CLIENT_SECRET"),
        scope=os.getenv("MEMORY_SERVICE_SCOPE", "").strip(),
        issuer=_require("OIDC_ISSUER").rstrip("/"),
        cache_ttl_seconds=_int("AGENT_CACHE_TTL_SECONDS", DEFAULT_AGENT_CACHE_TTL_SECONDS),
        layers=layers or None,
        exclude=_csv("AWAKENING_EXCLUDE"),
    )


def _agent_toolsets() -> tuple[tuple[str, str], ...]:
    """Parse ``AGENT_TOOLSETS=agent=toolset,agent2=toolset2`` (repeats allowed)."""
    pairs: list[tuple[str, str]] = []
    for part in _csv("AGENT_TOOLSETS"):
        agent, sep, toolset = part.partition("=")
        if not sep or not agent.strip() or not toolset.strip():
            raise ValueError(
                f"AGENT_TOOLSETS: {part!r} is not an agent=toolset pair"
            )
        pairs.append((agent.strip(), toolset.strip()))
    return tuple(pairs)


def _sources(name: str) -> tuple[tuple[str, str], ...]:
    """Parse ``NAME=alias=module:attribute`` into (alias, target) pairs.

    The alias is what ``AGENT_TOOLSETS`` (or a stored service name) refers to; the
    target is where the object lives. A colon is required in the target so a bare
    module name cannot be mistaken for one.
    """
    pairs: list[tuple[str, str]] = []
    for part in _csv(name):
        alias, separator, target = part.partition("=")
        if not separator or not alias.strip() or ":" not in target:
            raise ValueError(
                f"{name}: {part!r} is not an alias=module:attribute pair"
            )
        pairs.append((alias.strip(), target.strip()))
    return tuple(pairs)


def load_config() -> Config:
    """Load settings from the environment (.env already applied)."""
    return Config(
        llm_api_key=_require("LLM_API_KEY"),
        llm_model=_require("LLM_MODEL"),
        llm_base_url=os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL).strip()
        or DEFAULT_BASE_URL,
        memory_window=_int("MEMORY_WINDOW", 15),
        db_path=os.getenv("DB_PATH", "agent.db").strip() or "agent.db",
        system_prompt=os.getenv("SYSTEM_PROMPT", "").strip() or DEFAULT_SYSTEM_PROMPT,
        host=os.getenv("HOST", "0.0.0.0").strip() or "0.0.0.0",
        port=_int("PORT", 8000, minimum=1),
        memory_service=_memory_service(),
        agent_toolsets=_agent_toolsets(),
        toolset_sources=_sources("TOOLSET_SOURCES"),
        link_providers=_sources("LINK_PROVIDERS"),
    )
