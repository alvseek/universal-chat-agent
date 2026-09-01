"""HTTP middleware: consistent JSON error responses.

Registered on the app in main. Keeps error shape uniform ({"error": ...}) and
maps domain validation errors (ValueError) to 400, everything unexpected to 500
(without leaking internals to the caller).
"""
from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

log = logging.getLogger("universal-chat-agent")


async def handle_value_error(request: Request, exc: Exception) -> JSONResponse:
    # Domain/validation problem the caller can fix (e.g. empty conversation_id).
    return JSONResponse(status_code=400, content={"error": str(exc)})


async def handle_not_found(request: Request, exc: Exception) -> JSONResponse:
    # The caller named something that does not exist (e.g. an unknown agent_id).
    return JSONResponse(status_code=404, content={"error": str(exc)})


async def handle_upstream(request: Request, exc: Exception) -> JSONResponse:
    # A dependency the brain needs (memory service, identity provider) is failing.
    # Not the caller's fault and not a bug here: say which, and say it is temporary.
    log.warning("upstream failure on %s: %s: %s", request.url.path, type(exc).__name__, exc)
    return JSONResponse(
        status_code=503,
        content={"error": f"memory service unavailable: {exc}"},
    )


async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"error": "internal error"})
