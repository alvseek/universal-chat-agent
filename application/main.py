"""Composition root: build dependencies, wire FastAPI, expose ``app``.

This is the only place that knows how the layers plug together. ``app`` is what
uvicorn serves (see Dockerfile / README). Import-time construction means the
process fails fast at startup if configuration is missing.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI

from application.api_controllers.chat_controller import router
from application.api_integrations.openrouter.llm_client import build_agent
from application.business_services.chat_service import ChatService
from application.configuration.env import load_config
from application.data_repositories.message_repository import MessageRepository
from application.logger import logger_setup
from application.middleware.error_handler import handle_unexpected, handle_value_error


def create_app() -> FastAPI:
    logger_setup.configure()
    config = load_config()

    repository = MessageRepository(config.db_path)
    agent = build_agent(
        model=config.openrouter_model,
        base_url=config.openrouter_base_url,
        api_key=config.openrouter_api_key,
        system_prompt=config.system_prompt,
    )
    service = ChatService(agent, repository, config.memory_window)

    app = FastAPI(title="universal-chat-agent")
    app.state.config = config
    app.state.chat_service = service
    app.include_router(router)
    app.add_exception_handler(ValueError, handle_value_error)
    app.add_exception_handler(Exception, handle_unexpected)

    logging.getLogger("universal-chat-agent").info(
        "brain ready (model=%s) — serving /chat", config.openrouter_model
    )
    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=app.state.config.host, port=app.state.config.port)


if __name__ == "__main__":
    main()
