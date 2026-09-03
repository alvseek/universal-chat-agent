"""What one chat turn carries into the tools that run inside it.

The credential a tool uses cannot be captured when the agent is built: an agent
is constructed once and kept warm for hours, and it answers *everyone* who talks
to that bot. Anything baked into it at build time is therefore shared by every
caller — which is exactly how one person's message ends up acting as another
person's account.

So the caller's credentials ride the run instead. pydantic-ai carries this object
to every tool as ``ctx.deps``, on the first run and on a resumed one alike, which
matters because an approved write executes during the *resume* — the turn after
the one that asked for it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping


@dataclass(frozen=True)
class ChatDeps:
    """One caller, for the length of one turn."""

    # service name -> that caller's token for it. Absent service = not linked,
    # which every tool must answer as data rather than assume away.
    credentials: Mapping[str, str] = field(default_factory=dict)
    # Namespaced (``telegram:8932435376``); None when the bridge sends no caller
    # identity, in which case nothing can be linked and no tool has a credential.
    end_user_id: str | None = None
    # Called with the *service name* when that service refuses this caller's
    # stored credential. The binding was revoked elsewhere — typically
    # disconnected from the web UI — so keeping the row produces nothing but more
    # refusals. A callback rather than a repository keeps the toolset ignorant of
    # where credentials are stored; the service name is required because being
    # refused by one service says nothing about a caller's links to any other.
    on_auth_failed: Callable[[str], None] | None = None
