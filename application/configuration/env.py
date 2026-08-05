"""Configuration: load and validate environment for universal-chat-agent.

Single source of settings. Fails fast (with a clear message) when a required
variable is missing, so misconfiguration is obvious at startup rather than at
the first request.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

DEFAULT_SYSTEM_PROMPT = "You are a helpful, friendly assistant. Be concise and clear."
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


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
    )
