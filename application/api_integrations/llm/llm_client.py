"""External integration: the LLM provider (an OpenAI-compatible endpoint; OpenRouter by default).

This is the ONLY module that knows about the model / pydantic-ai. Swapping the
brain's model or provider, or adding tools later, happens here without touching
services, controllers, or the data layer.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Sequence, Tuple

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults, ToolDenied
from pydantic_ai.toolsets.abstract import AbstractToolset

from application.business_services.chat_deps import ChatDeps

log = logging.getLogger("universal-chat-agent")

Turn = Tuple[str, str]  # (role, content)

DENIED_MESSAGE = "The user did not confirm. Do not perform the operation."


@dataclass(frozen=True)
class PendingRun:
    """A run paused on write-tool approval: everything needed to resume it."""

    approval_ids: list[str]
    summary: str        # human-readable restatement of the paused call(s)
    messages: bytes     # the run's full message state, serialized


def build_agent(
    model: str,
    base_url: str,
    api_key: str,
    system_prompt: str,
    toolsets: Sequence[AbstractToolset] | None = None,
    session_id: str | None = None,
) -> Agent:
    """Construct an Agent bound to an OpenAI-compatible endpoint.

    With toolsets, the output type widens to include ``DeferredToolRequests`` so
    a write tool's approval requirement pauses the run instead of failing it; a
    tool-less agent keeps the plain string contract it always had.

    ``session_id`` rides as OpenRouter's sticky-routing key, pinning this agent's
    requests to one upstream provider so a cached prompt prefix stays reachable.
    Left unset, OpenRouter derives the key from the first system message and the
    first non-system message — and this brain's memory window slides, so that
    derived key moves and the cache is left behind. Providers that ignore the
    field are unaffected by it.
    """
    provider = OpenAIProvider(base_url=base_url, api_key=api_key)
    llm = OpenAIChatModel(model, provider=provider)
    pinned: dict = (
        {"model_settings": ModelSettings(extra_body={"session_id": session_id})}
        if session_id
        else {}
    )
    if toolsets:
        return Agent(
            llm,
            system_prompt=system_prompt,
            toolsets=list(toolsets),
            # Tools read the caller's credentials from ctx.deps, so the agent
            # itself holds none: one warm agent serves everyone who talks to
            # this bot, and each run supplies who is talking.
            deps_type=ChatDeps,
            output_type=[str, DeferredToolRequests],
            **pinned,
        )
    return Agent(llm, system_prompt=system_prompt, **pinned)


def _summarize(requests: DeferredToolRequests) -> str:
    lines = []
    for call in requests.approvals:
        args = call.args_as_dict() if call.args is not None else {}
        rendered = ", ".join(f"{k}={v!r}" for k, v in args.items())
        lines.append(f"{call.tool_name}({rendered})")
    return "; ".join(lines)


def _log_usage(result) -> None:
    """One line per model run: tokens spent, and how much came from the cache.

    Cache reads decide whether this model choice is affordable, and nothing else
    records them — the transport logs an HTTP status, which says nothing about it.
    """
    usage = getattr(result, "usage", None)
    if usage is None:
        return
    details = getattr(usage, "details", None)
    reasoning = details.get("reasoning_tokens", 0) if isinstance(details, dict) else 0
    log.info(
        "llm turn: in=%d out=%d cache_read=%d cache_write=%d reasoning=%d",
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_read_tokens,
        usage.cache_write_tokens,
        reasoning,
    )


def _outcome(result) -> str | PendingRun:
    _log_usage(result)
    output = result.output
    if isinstance(output, DeferredToolRequests):
        return PendingRun(
            approval_ids=[c.tool_call_id for c in output.approvals],
            summary=_summarize(output),
            messages=ModelMessagesTypeAdapter.dump_json(result.all_messages()),
        )
    return output


async def resume(
    agent: Agent,
    pending_messages: bytes,
    approval_ids: Sequence[str],
    *,
    approve: bool,
    followup: str | None = None,
    deps: ChatDeps | None = None,
) -> str | PendingRun:
    """Resume a paused run with one verdict for every paused call.

    Approval executes the write(s); denial returns ``DENIED_MESSAGE`` to the
    model, with the user's actual message (``followup``) carried into the same
    run so the reply addresses what they really said.

    ``deps`` matters more here than on a first run: this is the call in which an
    approved write actually executes, so the credential it runs under is the one
    passed now — not whatever was in scope when the write was proposed.
    """
    verdict = True if approve else ToolDenied(DENIED_MESSAGE)
    results = DeferredToolResults(approvals={i: verdict for i in approval_ids})
    history = ModelMessagesTypeAdapter.validate_json(pending_messages)
    result = await agent.run(
        followup,
        message_history=history,
        deferred_tool_results=results,
        deps=deps,
    )
    return _outcome(result)


def _to_history(history: List[Turn]) -> List[ModelMessage]:
    """Map stored (role, content) turns into pydantic-ai message history."""
    messages: List[ModelMessage] = []
    for role, content in history:
        if role == "user":
            messages.append(ModelRequest(parts=[UserPromptPart(content=content)]))
        else:
            messages.append(ModelResponse(parts=[TextPart(content=content)]))
    return messages


async def generate(
    agent: Agent,
    history: List[Turn],
    user_msg: str,
    deps: ChatDeps | None = None,
) -> str | PendingRun:
    """Run the agent for one user message, given prior conversation history.

    Returns the reply text — or a ``PendingRun`` when a write tool paused the
    run awaiting the user's confirmation (tool-bound agents only).
    """
    result = await agent.run(
        user_msg, message_history=_to_history(history), deps=deps
    )
    return _outcome(result)
