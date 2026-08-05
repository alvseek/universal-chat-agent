"""Business orchestration: turn a (conversation_id, message) into a reply.

Coordinates the layers for one chat turn:
  validate id (domain) -> load history (repository) -> run LLM (integration)
  -> persist the exchange (repository) -> return the reply.

Holds no HTTP and no SQL — pure orchestration, so it is reusable by any
front-end (the HTTP controller today; a CLI or test harness tomorrow).
"""
from __future__ import annotations

from pydantic_ai import Agent

from application.api_integrations.openrouter import llm_client
from application.business_domain import conversation_domain as domain
from application.data_repositories.message_repository import MessageRepository


class ChatService:
    def __init__(
        self, agent: Agent, repository: MessageRepository, memory_window: int
    ) -> None:
        self._agent = agent
        self._repo = repository
        self._window = memory_window

    async def handle(self, conversation_id: str, message: str) -> str:
        cid = domain.normalize_conversation_id(conversation_id)

        stored = self._repo.recent(cid, self._window)
        history = domain.select_window(
            [(m.role, m.content) for m in stored], self._window
        )

        reply = await llm_client.generate(self._agent, history, message)

        # Persist only after a successful reply, so a failure never stores half a turn.
        self._repo.append(cid, "user", message)
        self._repo.append(cid, "assistant", reply)
        return reply
