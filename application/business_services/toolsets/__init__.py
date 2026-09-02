"""The named toolsets this brain can bind to an agent.

Code owns the mechanism (what a toolset named ``invintiry`` does); configuration
owns the selection (``AGENT_TOOLSETS=invintiry-operator=invintiry``). An agent
with no binding gets no tools — and its prompt says so.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from pydantic_ai.toolsets.abstract import AbstractToolset

from .invintiry import build_invintiry_toolsets

# name -> builder(deps) -> toolsets. Builders receive the shared deps mapping and
# pick what they need, so adding a toolset never changes this module's shape.
_BUILDERS: dict[str, Callable[[Mapping[str, Any]], list[AbstractToolset]]] = {
    "invintiry": build_invintiry_toolsets,
}


def known_toolsets() -> list[str]:
    return sorted(_BUILDERS)


def build_toolsets(
    names: Sequence[str], deps: Mapping[str, Any]
) -> list[AbstractToolset]:
    """Instantiate the named toolsets. Unknown names raise at startup, not at chat time."""
    toolsets: list[AbstractToolset] = []
    for name in names:
        builder = _BUILDERS.get(name)
        if builder is None:
            raise ValueError(
                f"unknown toolset {name!r} in AGENT_TOOLSETS (known: {', '.join(known_toolsets())})"
            )
        toolsets.extend(builder(deps))
    return toolsets


def describe_toolsets(toolsets: Sequence[AbstractToolset]) -> list[str]:
    """One line per tool — name and the first sentence of its description —
    taken from the toolset objects themselves so the prompt can never claim a
    tool the runtime does not hold."""
    lines: list[str] = []
    for toolset in toolsets:
        tools = getattr(toolset, "tools", None)
        if not isinstance(tools, dict):
            continue
        for name, tool in sorted(tools.items()):
            first = (tool.description or "").strip().split("\n")[0]
            lines.append(f"{name} — {first}" if first else name)
    return lines
