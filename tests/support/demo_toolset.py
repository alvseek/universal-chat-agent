"""A minimal toolset that stands in for whatever integration a deployment binds.

The brain's own properties — a caller's credential riding the run, a write pausing
behind the approval gate — are properties of the *runtime*, not of any one
integration. This is a two-write stand-in so those tests exercise the real wiring
without depending on an integration the brain does not ship.

It is loaded through the same configuration mechanism a real toolset uses:
``TOOLSET_SOURCES=demo=tests.support.demo_toolset:build_demo_toolsets``.
"""
from __future__ import annotations

from typing import Any, Mapping

from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.toolsets.abstract import AbstractToolset

from application.business_services.chat_deps import ChatDeps

SERVICE = "demo"

# What the stand-in did, in order: (token, operation). Read by the tests.
calls: list[tuple[str, str]] = []
# Where a store landed — a stand-in for a side effect at the far end.
stored: list[str] = []
# Tokens whose calls should be refused, as a service refuses a revoked one.
refuse: set[str] = set()


class DemoError(Exception):
    """What a client raises when its credential is refused."""

    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"demo error {status}: {detail}")


def reset(refuse_tokens: set[str] | None = None) -> None:
    calls.clear()
    stored.clear()
    refuse.clear()
    refuse.update(refuse_tokens or ())


def _token(ctx: RunContext[ChatDeps]) -> str | None:
    return (ctx.deps.credentials or {}).get(SERVICE)


def _not_linked() -> dict[str, Any]:
    return {
        "error": "not_linked",
        "service": SERVICE,
        "detail": "this caller has not connected an account; ask them to link",
    }


def _credential(token: str) -> str:
    if token in refuse:
        raise DemoError(401, "credential refused")
    return token


def build_demo_toolsets(deps: Mapping[str, Any]) -> list[AbstractToolset]:
    """A read and two approval-gated writes, all keyed on the caller's token."""

    async def look_up(ctx: RunContext[ChatDeps], query: str) -> dict[str, Any]:
        """Look something up."""
        token = _token(ctx)
        if not token:
            return _not_linked()
        try:
            token = _credential(token)
        except DemoError as exc:
            if ctx.deps.on_auth_failed is not None:
                ctx.deps.on_auth_failed(SERVICE)
            return {"error": "auth_failed", "status": exc.status}
        calls.append((token, "look_up"))
        return {"answer": f"{query} = 1"}

    async def store(ctx: RunContext[ChatDeps], name: str) -> dict[str, Any]:
        """Store something."""
        token = _token(ctx)
        if not token:
            return _not_linked()
        token = _credential(token)
        calls.append((token, "store"))
        stored.append(name)
        return {"stored": name}

    async def discard(ctx: RunContext[ChatDeps], name: str) -> dict[str, Any]:
        """Discard something."""
        token = _token(ctx)
        if not token:
            return _not_linked()
        token = _credential(token)
        calls.append((token, "discard"))
        return {"discarded": name}

    reads = FunctionToolset(tools=[look_up], id="demo-reads")
    writes = FunctionToolset(
        tools=[store, discard], id="demo-writes", requires_approval=True
    )
    return [reads, writes]
