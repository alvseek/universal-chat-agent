"""Resolve a ``module:attribute`` path that configuration names.

Extension points are wired by name, not by import: the brain must be able to use a
builder or a provider it does not ship. Resolution is deliberately dumb — one
dotted path in, one object out — and every failure is a ``ValueError`` naming the
config entry, because a bad path should stop the process at startup rather than
half-enable an integration at chat time.
"""
from __future__ import annotations

import importlib
from typing import Any


def import_callable(target: str, *, what: str) -> Any:
    """``"my_pkg.toolsets:build"`` -> the object that path names.

    ``what`` names the configuration entry in every error, since one helper serves
    several extension points and the message has to say which one is wrong.
    """
    module_name, separator, attribute = target.partition(":")
    if not separator or not module_name.strip() or not attribute.strip():
        raise ValueError(f"{what}: {target!r} is not a 'module:attribute' path")
    module_name, attribute = module_name.strip(), attribute.strip()
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ValueError(f"{what}: cannot import {module_name!r} ({exc})") from exc
    try:
        return getattr(module, attribute)
    except AttributeError as exc:
        raise ValueError(
            f"{what}: module {module_name!r} has no attribute {attribute!r}"
        ) from exc
