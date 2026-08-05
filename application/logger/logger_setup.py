"""Observability: logging setup for the brain.

One place to configure log level/format. Also silences the HTTP client's
INFO-level URL logging (defensive — outbound provider URLs can carry query
params/keys we don't want in logs).
"""
from __future__ import annotations

import logging


def configure(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
