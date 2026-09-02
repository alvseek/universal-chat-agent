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

import string

from pydantic_ai import Agent

from application.api_integrations.openrouter import llm_client
from application.business_domain import conversation_domain as domain
from application.business_services.agent_registry import AgentRegistry
from application.data_repositories.message_repository import MessageRepository
from application.data_repositories.pending_approval_repository import (
    PendingApprovalRepository,
)


class RegistryUnavailable(ValueError):
    """A request named an agent, but this brain has no memory service to load it from."""


def memory_key(conversation_id: str, agent_id: str | None) -> str:
    """The history key: ``agent_id:conversation_id`` when an agent is named."""
    return f"{agent_id}:{conversation_id}" if agent_id else conversation_id


# A *clear* yes, per the operator's rule: anything else is a change request.
_YES = frozenset({"yes", "y", "ya", "iya", "yup", "confirm", "yes please"})


def is_clear_yes(text: str) -> bool:
    return text.strip().strip(string.punctuation + " ").lower() in _YES


def confirmation_reply(summary: str) -> str:
    return (
        f"Before I do it, confirm:\n{summary}\n"
        'Reply "yes" to proceed — anything else cancels.'
    )


class ChatService:
    def __init__(
        self,
        agent: Agent,
        repository: MessageRepository,
        memory_window: int,
        registry: AgentRegistry | None = None,
        pending: PendingApprovalRepository | None = None,
    ) -> None:
        self._agent = agent
        self._repo = repository
        self._window = memory_window
        self._registry = registry
        self._pending = pending

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

        pending = self._pending.get(key) if self._pending else None
        if pending is not None:
            # Delete before resuming: a crash mid-resume loses the pending write
            # rather than replaying it (lost beats doubled, as in the bridge).
            self._pending.delete(key)
            approve = is_clear_yes(message)
            outcome = await llm_client.resume(
                agent,
                pending.messages,
                pending.approval_ids,
                approve=approve,
                followup=None if approve else message,
            )
        else:
            stored = self._repo.recent(key, self._window)
            history = domain.select_window(
                [(m.role, m.content) for m in stored], self._window
            )
            outcome = await llm_client.generate(agent, history, message)

        if isinstance(outcome, llm_client.PendingRun):
            if self._pending is None:  # tool-bound agent without a store is a wiring bug
                raise RuntimeError("write tool paused a run but no pending store is configured")
            self._pending.put(key, outcome.messages, outcome.approval_ids, outcome.summary)
            reply = confirmation_reply(outcome.summary)
        else:
            reply = outcome

        # Persist only after a successful reply, so a failure never stores half a turn.
        self._repo.append(key, "user", message)
        self._repo.append(key, "assistant", reply)
        return reply
