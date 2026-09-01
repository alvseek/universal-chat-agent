"""Business service: the agents this brain can currently *be*, kept warm.

``get(agent_id)`` returns a ready pydantic-ai ``Agent`` for that id, building it on
first use from a system prompt the ``load_prompt`` callable produces (in production:
awaken from the memory server, render through the awakening domain). Built agents
are cached and rebuilt after ``ttl_seconds``; ``reload(agent_id)`` rebuilds one now.

Two behaviours are deliberate rather than incidental:

* **Stale beats dark.** When a cached agent's TTL lapses and the refresh fails for a
  transient reason, the cached copy keeps serving, a WARNING is logged, and the next
  refresh is attempted after ``error_retry_seconds`` — an outage of the memory
  server never takes the bot down over a TTL boundary nobody chose.
* **Absent is not transient.** If the memory server says the agent no longer exists
  (``AgentNotFound``), the cached copy is dropped and the error propagates: serving an
  agent that has been removed is not resilience.

The registry knows nothing about HTTP or about where prompts come from; both are
injected, which is also what makes it testable with a fake clock and a fake loader.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from pydantic_ai import Agent

from application.business_domain.awakening_domain import AgentNotFound

log = logging.getLogger("universal-chat-agent")

PromptLoader = Callable[[str], Awaitable[str]]
AgentBuilder = Callable[[str], Agent]


@dataclass
class _Entry:
    agent: Agent
    loaded_at: float
    retry_at: float = 0.0  # after a failed refresh: do not retry before this


class AgentRegistry:
    def __init__(
        self,
        load_prompt: PromptLoader,
        build_agent: AgentBuilder,
        *,
        ttl_seconds: float,
        error_retry_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._load_prompt = load_prompt
        self._build = build_agent
        self._ttl = ttl_seconds
        self._retry = error_retry_seconds
        self._clock = clock
        self._entries: dict[str, _Entry] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, agent_id: str) -> asyncio.Lock:
        lock = self._locks.get(agent_id)
        if lock is None:
            lock = self._locks[agent_id] = asyncio.Lock()
        return lock

    def _fresh(self, entry: _Entry | None, now: float) -> bool:
        return entry is not None and now < entry.loaded_at + self._ttl

    def cached(self) -> list[str]:
        """Agent ids currently warm, for diagnostics."""
        return sorted(self._entries)

    async def get(self, agent_id: str) -> Agent:
        now = self._clock()
        entry = self._entries.get(agent_id)
        if self._fresh(entry, now):
            return entry.agent  # type: ignore[union-attr]
        if entry is not None and now < entry.retry_at:
            return entry.agent  # a refresh failed recently; serve stale, do not hammer
        async with self._lock(agent_id):
            now = self._clock()
            entry = self._entries.get(agent_id)
            if self._fresh(entry, now) or (entry is not None and now < entry.retry_at):
                return entry.agent  # type: ignore[union-attr]
            try:
                return await self._load(agent_id)
            except AgentNotFound:
                self._entries.pop(agent_id, None)
                raise
            except Exception as exc:  # transient: network, issuer, 5xx
                if entry is None:
                    raise
                entry.retry_at = now + self._retry
                log.warning(
                    "agent %r: refresh failed (%s: %s) — serving the cached copy, "
                    "retrying after %.0fs",
                    agent_id, type(exc).__name__, exc, self._retry,
                )
                return entry.agent

    async def reload(self, agent_id: str) -> Agent:
        """Rebuild one agent now. Failures propagate — a reload is an explicit ask."""
        async with self._lock(agent_id):
            try:
                return await self._load(agent_id)
            except AgentNotFound:
                self._entries.pop(agent_id, None)
                raise

    async def _load(self, agent_id: str) -> Agent:
        prompt = await self._load_prompt(agent_id)
        agent = self._build(prompt)
        self._entries[agent_id] = _Entry(agent=agent, loaded_at=self._clock())
        log.info("agent %r: loaded (%d chars of system prompt)", agent_id, len(prompt))
        return agent
