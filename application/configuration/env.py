"""Configuration: load and validate environment for universal-chat-agent.

Single source of settings. Fails fast (with a clear message) when a required
variable is missing, so misconfiguration is obvious at startup rather than at
the first request.

Two groups of settings:

* the brain itself — model, memory window, the default ``SYSTEM_PROMPT`` used
  when a request names no agent;
* the memory service — present only when ``MUNNIN_URL`` is set. With it, requests
  may name an ``agent_id`` and the brain awakens that agent from Munnin using a
  machine credential from Authentra. Without it, the brain is the single default
  agent it always was.
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
    """How to reach Munnin and the credential that identifies this brain to it."""

    url: str  # e.g. https://munnin.lok.quest
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
class InvintiryConfig:
    """How to reach the Invintiry inventory API, as the workspace's agent principal."""

    api_url: str  # e.g. https://api.invintiry.example
    token: str    # RS256 agent token minted by the workspace OWNER


@dataclass(frozen=True)
class Config:
    openrouter_api_key: str
    openrouter_model: str
    openrouter_base_url: str
    memory_window: int
    db_path: str
    system_prompt: str
    host: str
    port: int
    memory_service: MemoryServiceConfig | None
    # Which named toolsets each agent is bound to (AGENT_TOOLSETS). Code owns
    # what a toolset does; this mapping owns who gets it. Empty = no agent has tools.
    agent_toolsets: tuple[tuple[str, str], ...]
    invintiry: InvintiryConfig | None


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
    url = os.getenv("MUNNIN_URL", "").strip().rstrip("/")
    if not url:
        return None
    layers = _csv("AWAKENING_LAYERS")
    return MemoryServiceConfig(
        url=url,
        resource=os.getenv("MUNNIN_RESOURCE", "").strip() or f"{url}/mcp",
        client_id=_require("MUNNIN_M2M_CLIENT_ID"),
        client_secret=_require("MUNNIN_M2M_CLIENT_SECRET"),
        scope=os.getenv("MUNNIN_M2M_SCOPE", "").strip(),
        issuer=_require("AUTHENTRA_ISSUER").rstrip("/"),
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


def _invintiry() -> InvintiryConfig | None:
    url = os.getenv("INVINTIRY_API_URL", "").strip().rstrip("/")
    if not url:
        return None
    return InvintiryConfig(api_url=url, token=_require("INVINTIRY_AGENT_TOKEN"))


def load_config() -> Config:
    """Load settings from the environment (.env already applied)."""
    return Config(
        openrouter_api_key=_require("OPENROUTER_API_KEY"),
        openrouter_model=_require("OPENROUTER_MODEL"),
        openrouter_base_url=os.getenv("OPENROUTER_BASE_URL", DEFAULT_BASE_URL).strip()
        or DEFAULT_BASE_URL,
        memory_window=_int("MEMORY_WINDOW", 15),
        db_path=os.getenv("DB_PATH", "agent.db").strip() or "agent.db",
        system_prompt=os.getenv("SYSTEM_PROMPT", "").strip() or DEFAULT_SYSTEM_PROMPT,
        host=os.getenv("HOST", "0.0.0.0").strip() or "0.0.0.0",
        port=_int("PORT", 8000, minimum=1),
        memory_service=_memory_service(),
        agent_toolsets=_agent_toolsets(),
        invintiry=_invintiry(),
    )
