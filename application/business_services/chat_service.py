"""Business orchestration: turn a (conversation_id, message[, agent_id]) into a reply.

Coordinates the layers for one chat turn:
  validate id (domain) -> pick the agent (default, or from the registry by id)
  -> load history (repository) -> run LLM (integration)
  -> persist the exchange (repository) -> return the reply.

Holds no HTTP and no SQL — pure orchestration, so it is reusable by any
front-end (the HTTP controller today; a CLI or test harness tomorrow).

Memory is keyed per agent when one is named: the same person talking to two bots
that share this brain gets two histories, not one. Without an ``agent_id`` the key
is the bare ``conversation_id``, exactly as before, so a single-agent deployment's
existing history is untouched.
"""
from __future__ import annotations

from pydantic_ai import Agent

from application.api_integrations.openrouter import llm_client
from application.business_domain import conversation_domain as domain
from application.business_services.agent_registry import AgentRegistry
from application.data_repositories.message_repository import MessageRepository


class RegistryUnavailable(ValueError):
    """A request named an agent, but this brain has no memory service to load it from."""


def memory_key(conversation_id: str, agent_id: str | None) -> str:
    """The history key: ``agent_id:conversation_id`` when an agent is named."""
    return f"{agent_id}:{conversation_id}" if agent_id else conversation_id


class ChatService:
    def __init__(
        self,
        agent: Agent,
        repository: MessageRepository,
        memory_window: int,
        registry: AgentRegistry | None = None,
    ) -> None:
        self._agent = agent
        self._repo = repository
        self._window = memory_window
        self._registry = registry

    async def _resolve_agent(self, agent_id: str | None) -> Agent:
        if agent_id is None:
            return self._agent
        if self._registry is None:
            raise RegistryUnavailable(
                "this brain has no memory service configured (MUNNIN_URL), "
                "so it cannot serve a named agent"
            )
        return await self._registry.get(agent_id)

    async def handle(
        self, conversation_id: str, message: str, agent_id: str | None = None
    ) -> str:
        cid = domain.normalize_conversation_id(conversation_id)
        agent = await self._resolve_agent(agent_id)
        key = memory_key(cid, agent_id)

        stored = self._repo.recent(key, self._window)
        history = domain.select_window(
            [(m.role, m.content) for m in stored], self._window
        )

        reply = await llm_client.generate(agent, history, message)

        # Persist only after a successful reply, so a failure never stores half a turn.
        self._repo.append(key, "user", message)
        self._repo.append(key, "assistant", reply)
        return reply
