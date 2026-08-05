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


async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"error": "internal error"})
