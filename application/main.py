"""Composition root: build dependencies, wire FastAPI, expose ``app``.

This is the only place that knows how the layers plug together. ``app`` is what
uvicorn serves (see Dockerfile / README). Import-time construction means the
process fails fast at startup if configuration is missing.

With a memory service configured, the brain can also *become* any agent that
service holds: a request naming ``agent_id`` is answered by an ``Agent`` built from
that agent's awakening. The pieces are wired here and nowhere else — token
provider (Authentra) -> Munnin client -> awakening domain (payload -> prompt)
-> agent registry (prompt -> warm ``Agent``) -> chat service.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from application.api_controllers.chat_controller import router
from application.api_integrations.authentra.token_provider import (
    ClientCredentials,
    ClientCredentialsTokenProvider,
    TokenError,
)
from application.api_integrations.munnin.munnin_client import MunninClient, MunninError
from application.api_integrations.openrouter.llm_client import build_agent
from application.business_domain import awakening_domain
from application.business_domain.awakening_domain import AgentNotFound
from application.business_domain.link_provider import LinkProvider
from application.business_services.agent_registry import AgentRegistry
from application.business_services.chat_service import ChatService
from application.business_services.link_service import LinkService
from application.business_services.toolsets import (
    build_toolsets,
    describe_toolsets,
    load_builders,
)
from application.common.loading import import_callable
from application.configuration.env import Config, MemoryServiceConfig, load_config
from application.data_repositories.message_repository import MessageRepository
from application.data_repositories.pending_approval_repository import (
    PendingApprovalRepository,
)
from application.data_repositories.service_link_repository import ServiceLinkRepository
from application.logger import logger_setup
from application.middleware.error_handler import (
    handle_not_found,
    handle_unexpected,
    handle_upstream,
    handle_value_error,
)

log = logging.getLogger("universal-chat-agent")


def build_bindings(config: Config) -> dict[str, list]:
    """agent_id -> instantiated toolsets, from AGENT_TOOLSETS and TOOLSET_SOURCES.

    Fails at startup — never at chat time — when a binding names a toolset whose
    source was not named, or whose own configuration is missing.

    What goes into ``deps`` is only what is identical for every caller: shared
    build-time facilities, never a credential. A credential arrives per run,
    because an agent built here is kept warm for hours and answers everybody.
    """
    if not config.agent_toolsets:
        return {}
    builders = load_builders(config.toolset_sources)
    deps: dict = {}
    bindings: dict[str, list] = {}
    for agent_id, toolset in config.agent_toolsets:
        built = build_toolsets([toolset], deps, builders)
        bindings.setdefault(agent_id, []).extend(built)
    return bindings


def _link_providers(config: Config) -> dict[str, LinkProvider]:
    """service name -> provider, from LINK_PROVIDERS.

    Each source names a zero-argument factory: a provider reads its own service's
    configuration, so the brain never has to know which services exist.
    """
    providers: dict[str, LinkProvider] = {}
    for name, target in config.link_providers:
        factory = import_callable(target, what=f"link provider {name!r}")
        providers[name] = factory()
    return providers


def _tools_block(toolsets: list) -> str:
    """The prompt's Available Tools section — derived from the toolsets actually
    bound, so the prompt can never claim a tool the runtime does not hold."""
    lines = describe_toolsets(toolsets)
    if lines:
        body = "\n".join(f"- {line}" for line in lines)
    else:
        body = (
            "none — you have no tools in this deployment. Say so plainly when asked "
            "to look something up or change something; never invent results."
        )
    return f"\n\n# Available Tools\n{body}"


def build_registry(
    config: Config, memory: MemoryServiceConfig, bindings: dict[str, list]
) -> tuple[AgentRegistry, httpx.AsyncClient]:
    """Wire the awakening pipeline for one memory service.

    Returns the registry and the HTTP client it shares with the token provider, so
    the composition root can close that client when the app shuts down.
    """
    http = httpx.AsyncClient(timeout=30.0)
    token_provider = ClientCredentialsTokenProvider(
        ClientCredentials(
            issuer=memory.issuer,
            client_id=memory.client_id,
            client_secret=memory.client_secret,
            resource=memory.resource,
            scope=memory.scope,
        ),
        http,
    )
    munnin = MunninClient(memory.url, token_provider, http)

    async def load_prompt(agent_id: str) -> str:
        payload = await munnin.awaken(agent_id)
        prompt = awakening_domain.assemble_system_prompt(
            payload, layers=memory.layers, exclude=memory.exclude
        )
        return prompt + _tools_block(bindings.get(agent_id, []))

    def make_agent(agent_id: str, prompt: str):
        return build_agent(
            config.openrouter_model,
            config.openrouter_base_url,
            config.openrouter_api_key,
            prompt,
            toolsets=bindings.get(agent_id) or None,
        )

    registry = AgentRegistry(load_prompt, make_agent, ttl_seconds=memory.cache_ttl_seconds)
    return registry, http


def create_app() -> FastAPI:
    logger_setup.configure()
    config = load_config()

    repository = MessageRepository(config.db_path)
    default_agent = build_agent(
        model=config.openrouter_model,
        base_url=config.openrouter_base_url,
        api_key=config.openrouter_api_key,
        system_prompt=config.system_prompt,
    )
    bindings = build_bindings(config)
    registry: AgentRegistry | None = None
    http: httpx.AsyncClient | None = None
    if config.memory_service:
        registry, http = build_registry(config, config.memory_service, bindings)
    elif bindings:
        raise ValueError(
            "AGENT_TOOLSETS is set but no memory service is configured (MUNNIN_URL) — "
            "toolsets bind to agents the memory service holds"
        )
    pending = PendingApprovalRepository(config.db_path) if bindings else None

    # Linking exists only where there is a service to link to. Without it the
    # brain is exactly what it was: an agent with no per-caller credentials.
    providers = _link_providers(config)
    links: LinkService | None = None
    if providers:
        links = LinkService(ServiceLinkRepository(config.db_path), providers)

    service = ChatService(
        default_agent, repository, config.memory_window, registry, pending, links
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        if http is not None:
            await http.aclose()

    app = FastAPI(title="universal-chat-agent", lifespan=lifespan)
    app.state.config = config
    app.state.chat_service = service
    app.state.agent_registry = registry
    app.include_router(router)
    app.add_exception_handler(AgentNotFound, handle_not_found)
    app.add_exception_handler(MunninError, handle_upstream)
    app.add_exception_handler(TokenError, handle_upstream)
    app.add_exception_handler(ValueError, handle_value_error)
    app.add_exception_handler(Exception, handle_unexpected)

    log.info(
        "brain ready (model=%s, memory service=%s, toolsets=%s) — serving /chat",
        config.openrouter_model,
        config.memory_service.url if config.memory_service else "none",
        ", ".join(f"{a}={t}" for a, t in config.agent_toolsets) or "none",
    )
    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=app.state.config.host, port=app.state.config.port)


if __name__ == "__main__":
    main()
