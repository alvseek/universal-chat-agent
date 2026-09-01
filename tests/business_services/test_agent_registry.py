"""Agent registry tests — fake clock, fake loader, fake builder; no model, no HTTP.

Verifies: build on first use and cache; rebuild after TTL; reload bypasses TTL;
a transient refresh failure serves the cached copy and backs off; an absent agent
is never served from cache; concurrent first calls share one load.
"""
import asyncio

import pytest

from application.business_domain.awakening_domain import AgentNotFound
from application.business_services.agent_registry import AgentRegistry


class _Loader:
    def __init__(self):
        self.calls = []
        self.fail_with = None
        self.delay = 0.0

    async def __call__(self, agent_id):
        self.calls.append(agent_id)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail_with is not None:
            raise self.fail_with
        return f"prompt-for-{agent_id}-v{len(self.calls)}"


def _registry(loader, clock, ttl=100.0, retry=30.0):
    return AgentRegistry(
        loader, build_agent=lambda prompt: {"prompt": prompt},
        ttl_seconds=ttl, error_retry_seconds=retry, clock=lambda: clock["t"],
    )


def test_builds_on_first_use_and_caches_within_ttl():
    loader, clock = _Loader(), {"t": 0.0}
    reg = _registry(loader, clock)

    a = asyncio.run(reg.get("op"))
    clock["t"] = 99.0
    b = asyncio.run(reg.get("op"))

    assert a is b
    assert loader.calls == ["op"]
    assert reg.cached() == ["op"]


def test_rebuilds_after_ttl():
    loader, clock = _Loader(), {"t": 0.0}
    reg = _registry(loader, clock)

    a = asyncio.run(reg.get("op"))
    clock["t"] = 101.0
    b = asyncio.run(reg.get("op"))

    assert a is not b
    assert b["prompt"] == "prompt-for-op-v2"


def test_reload_rebuilds_now_regardless_of_ttl():
    loader, clock = _Loader(), {"t": 0.0}
    reg = _registry(loader, clock)

    asyncio.run(reg.get("op"))
    fresh = asyncio.run(reg.reload("op"))

    assert fresh["prompt"] == "prompt-for-op-v2"
    assert loader.calls == ["op", "op"]


def test_transient_refresh_failure_serves_stale_and_backs_off():
    loader, clock = _Loader(), {"t": 0.0}
    reg = _registry(loader, clock, ttl=100.0, retry=30.0)

    stale = asyncio.run(reg.get("op"))
    clock["t"] = 150.0
    loader.fail_with = RuntimeError("munnin down")
    served = asyncio.run(reg.get("op"))
    clock["t"] = 160.0  # inside the retry window: no new attempt
    served_again = asyncio.run(reg.get("op"))
    clock["t"] = 181.0  # past the window: tries again, still failing
    served_third = asyncio.run(reg.get("op"))

    assert served is stale and served_again is stale and served_third is stale
    assert loader.calls == ["op", "op", "op"]  # first load + two refresh attempts


def test_first_load_failure_propagates():
    loader, clock = _Loader(), {"t": 0.0}
    loader.fail_with = RuntimeError("munnin down")
    reg = _registry(loader, clock)

    with pytest.raises(RuntimeError):
        asyncio.run(reg.get("op"))
    assert reg.cached() == []


def test_absent_agent_is_dropped_from_cache_and_raised():
    loader, clock = _Loader(), {"t": 0.0}
    reg = _registry(loader, clock, ttl=100.0)

    asyncio.run(reg.get("op"))
    clock["t"] = 101.0
    loader.fail_with = AgentNotFound("op")

    with pytest.raises(AgentNotFound):
        asyncio.run(reg.get("op"))
    assert reg.cached() == []


def test_reload_of_absent_agent_raises_and_drops():
    loader, clock = _Loader(), {"t": 0.0}
    reg = _registry(loader, clock)
    asyncio.run(reg.get("op"))
    loader.fail_with = AgentNotFound("op")

    with pytest.raises(AgentNotFound):
        asyncio.run(reg.reload("op"))
    assert reg.cached() == []


def test_concurrent_first_calls_share_one_load():
    loader, clock = _Loader(), {"t": 0.0}
    loader.delay = 0.01
    reg = _registry(loader, clock)

    async def both():
        return await asyncio.gather(reg.get("op"), reg.get("op"))

    a, b = asyncio.run(both())

    assert a is b
    assert loader.calls == ["op"]
