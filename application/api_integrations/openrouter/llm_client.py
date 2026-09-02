"""External integration: the LLM provider (OpenRouter, OpenAI-compatible).

This is the ONLY module that knows about the model / pydantic-ai. Swapping the
brain's model or provider, or adding tools later, happens here without touching
services, controllers, or the data layer.
"""
from __future__ import annotations

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
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults, ToolDenied
from pydantic_ai.toolsets.abstract import AbstractToolset

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
) -> Agent:
    """Construct an Agent bound to an OpenAI-compatible endpoint (OpenRouter).

    With toolsets, the output type widens to include ``DeferredToolRequests`` so
    a write tool's approval requirement pauses the run instead of failing it; a
    tool-less agent keeps the plain string contract it always had.
    """
    provider = OpenAIProvider(base_url=base_url, api_key=api_key)
    llm = OpenAIChatModel(model, provider=provider)
    if toolsets:
        return Agent(
            llm,
            system_prompt=system_prompt,
            toolsets=list(toolsets),
            output_type=[str, DeferredToolRequests],
        )
    return Agent(llm, system_prompt=system_prompt)


def _summarize(requests: DeferredToolRequests) -> str:
    lines = []
    for call in requests.approvals:
        args = call.args_as_dict() if call.args is not None else {}
        rendered = ", ".join(f"{k}={v!r}" for k, v in args.items())
        lines.append(f"{call.tool_name}({rendered})")
    return "; ".join(lines)


def _outcome(result) -> str | PendingRun:
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
) -> str | PendingRun:
    """Resume a paused run with one verdict for every paused call.

    Approval executes the write(s); denial returns ``DENIED_MESSAGE`` to the
    model, with the user's actual message (``followup``) carried into the same
    run so the reply addresses what they really said.
    """
    verdict = True if approve else ToolDenied(DENIED_MESSAGE)
    results = DeferredToolResults(approvals={i: verdict for i in approval_ids})
    history = ModelMessagesTypeAdapter.validate_json(pending_messages)
    result = await agent.run(
        followup, message_history=history, deferred_tool_results=results
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


async def generate(agent: Agent, history: List[Turn], user_msg: str) -> str | PendingRun:
    """Run the agent for one user message, given prior conversation history.

    Returns the reply text — or a ``PendingRun`` when a write tool paused the
    run awaiting the user's confirmation (tool-bound agents only).
    """
    result = await agent.run(user_msg, message_history=_to_history(history))
    return _outcome(result)
