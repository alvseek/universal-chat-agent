"""The named toolsets this brain can bind to an agent.

Code owns the mechanism (what a toolset does); configuration owns both the
selection (``AGENT_TOOLSETS=some-agent=some-toolset``) and where each name's
builder comes from (``TOOLSET_SOURCES=some-toolset=my_pkg.toolsets:build``).

The brain ships no toolset of its own. An agent with no binding gets no tools —
and its prompt says so.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from pydantic_ai.toolsets.abstract import AbstractToolset

from application.common.loading import import_callable

Builder = Callable[[Mapping[str, Any]], list[AbstractToolset]]


def load_builders(specs: Sequence[tuple[str, str]]) -> dict[str, Builder]:
    """Resolve ``alias=module:attribute`` specs into builders.

    Nothing is bundled: every name an agent is bound to has to be named here, so
    the brain holds no integration knowledge of its own.
    """
    return {
        name: import_callable(target, what=f"toolset source {name!r}")
        for name, target in specs
    }


def known_toolsets(builders: Mapping[str, Builder]) -> list[str]:
    return sorted(builders)


def build_toolsets(
    names: Sequence[str], deps: Mapping[str, Any], builders: Mapping[str, Builder]
) -> list[AbstractToolset]:
    """Instantiate the named toolsets. Unknown names raise at startup, not at chat time."""
    toolsets: list[AbstractToolset] = []
    for name in names:
        builder = builders.get(name)
        if builder is None:
            raise ValueError(
                f"unknown toolset {name!r} "
                f"(known: {', '.join(known_toolsets(builders))})"
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
