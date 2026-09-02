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
from application.api_integrations.invintiry.invintiry_client import InvintiryClient
from application.api_integrations.munnin.munnin_client import MunninClient, MunninError
from application.api_integrations.openrouter.llm_client import build_agent
from application.business_domain import awakening_domain
from application.business_domain.awakening_domain import AgentNotFound
from application.business_services.agent_registry import AgentRegistry
from application.business_services.chat_service import ChatService
from application.business_services.toolsets import build_toolsets, describe_toolsets
from application.configuration.env import Config, MemoryServiceConfig, load_config
from application.data_repositories.message_repository import MessageRepository
from application.data_repositories.pending_approval_repository import (
    PendingApprovalRepository,
)
from application.logger import logger_setup
from application.middleware.error_handler import (
    handle_not_found,
    handle_unexpected,
    handle_upstream,
    handle_value_error,
)

log = logging.getLogger("universal-chat-agent")


def build_bindings(config: Config) -> tuple[dict[str, list], InvintiryClient | None]:
    """agent_id -> instantiated toolsets, from AGENT_TOOLSETS.

    Fails at startup — never at chat time — when a binding names an unknown
    toolset or one whose backing service is not configured. Also returns the
    invintiry client (if built) so the composition root can close it on shutdown.
    """
    if not config.agent_toolsets:
        return {}, None
    deps: dict = {}
    invintiry_client: InvintiryClient | None = None
    if config.invintiry:
        invintiry_client = InvintiryClient(
            config.invintiry.api_url, config.invintiry.token
        )
        deps["invintiry_client"] = invintiry_client
    bindings: dict[str, list] = {}
    for agent_id, toolset in config.agent_toolsets:
        try:
            built = build_toolsets([toolset], deps)
        except KeyError as exc:
            raise ValueError(
                f"AGENT_TOOLSETS binds {agent_id!r} to {toolset!r}, which needs "
                f"{exc.args[0]!r} — is its service configured (e.g. INVINTIRY_API_URL)?"
            ) from exc
        bindings.setdefault(agent_id, []).extend(built)
    return bindings, invintiry_client


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
    bindings, invintiry_client = build_bindings(config)
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
    service = ChatService(
        default_agent, repository, config.memory_window, registry, pending
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        if http is not None:
            await http.aclose()
        if invintiry_client is not None:
            await invintiry_client.aclose()

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
