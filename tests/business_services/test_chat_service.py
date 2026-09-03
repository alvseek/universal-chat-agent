"""Chat service tests — LLM call stubbed; verifies agent selection and memory keying.

Without an agent_id: the default agent answers and history is keyed by the bare
conversation_id (unchanged behaviour). With one: the registry's agent answers and
history is keyed per agent. Naming an agent on a brain without a registry is refused.
"""
import asyncio

import pytest

from application.business_services import chat_service as cs
from application.business_services.chat_service import ChatService, RegistryUnavailable


class _Repo:
    def __init__(self):
        self.rows = []

    def recent(self, key, limit):
        return [r for r in self.rows if r.key == key][-limit:]

    def append(self, key, role, content):
        self.rows.append(_Row(key, role, content))


class _Row:
    def __init__(self, key, role, content):
        self.key, self.role, self.content = key, role, content


class _Registry:
    def __init__(self):
        self.asked = []

    async def get(self, agent_id):
        self.asked.append(agent_id)
        return {"agent": agent_id}


def _stub_generate(monkeypatch, seen):
    async def generate(agent, history, user_msg, deps=None):
        seen.append((agent, list(history), user_msg))
        return f"reply-from-{agent['agent'] if isinstance(agent, dict) else 'default'}"

    monkeypatch.setattr(cs.llm_client, "generate", generate)


def test_memory_key_is_bare_without_agent_and_prefixed_with():
    assert cs.memory_key("telegram:1", None) == "telegram:1"
    assert cs.memory_key("telegram:1", "op") == "op:telegram:1"


def test_default_agent_and_bare_key_without_agent_id(monkeypatch):
    seen, repo = [], _Repo()
    _stub_generate(monkeypatch, seen)
    svc = ChatService("DEFAULT", repo, memory_window=5, registry=_Registry())

    reply = asyncio.run(svc.handle("telegram:1", "hi"))

    assert reply == "reply-from-default"
    assert seen[0][0] == "DEFAULT"
    assert [(r.key, r.role) for r in repo.rows] == [("telegram:1", "user"), ("telegram:1", "assistant")]


def test_named_agent_comes_from_registry_and_history_is_per_agent(monkeypatch):
    seen, repo, registry = [], _Repo(), _Registry()
    _stub_generate(monkeypatch, seen)
    svc = ChatService("DEFAULT", repo, memory_window=5, registry=registry)

    asyncio.run(svc.handle("telegram:1", "hello op", agent_id="op"))
    asyncio.run(svc.handle("telegram:1", "hello default"))

    assert registry.asked == ["op"]
    keys = {r.key for r in repo.rows}
    assert keys == {"op:telegram:1", "telegram:1"}
    # the second call (default agent) saw none of the operator's history
    assert seen[1][1] == []


def test_named_agent_without_registry_is_refused(monkeypatch):
    seen, repo = [], _Repo()
    _stub_generate(monkeypatch, seen)
    svc = ChatService("DEFAULT", repo, memory_window=5, registry=None)

    with pytest.raises(RegistryUnavailable):
        asyncio.run(svc.handle("telegram:1", "hi", agent_id="op"))
    assert repo.rows == []
