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
from functools import partial

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
from application.business_services.agent_registry import AgentRegistry
from application.business_services.chat_service import ChatService
from application.configuration.env import Config, MemoryServiceConfig, load_config
from application.data_repositories.message_repository import MessageRepository
from application.logger import logger_setup
from application.middleware.error_handler import (
    handle_not_found,
    handle_unexpected,
    handle_upstream,
    handle_value_error,
)

log = logging.getLogger("universal-chat-agent")


def build_registry(
    config: Config, memory: MemoryServiceConfig
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
        return awakening_domain.assemble_system_prompt(
            payload, layers=memory.layers, exclude=memory.exclude
        )

    make_agent = partial(
        build_agent,
        config.openrouter_model,
        config.openrouter_base_url,
        config.openrouter_api_key,
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
    registry: AgentRegistry | None = None
    http: httpx.AsyncClient | None = None
    if config.memory_service:
        registry, http = build_registry(config, config.memory_service)
    service = ChatService(default_agent, repository, config.memory_window, registry)

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
        "brain ready (model=%s, memory service=%s) — serving /chat",
        config.openrouter_model,
        config.memory_service.url if config.memory_service else "none",
    )
    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=app.state.config.host, port=app.state.config.port)


if __name__ == "__main__":
    main()
