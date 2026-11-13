"""Read advertised knowledge, not arbitrary tenant records, through the memory adapter."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from pydantic_ai.toolsets import FunctionToolset

from application.api_integrations.memory_service.memory_service_client import (
    MemoryServiceAuthError,
    MemoryServiceClient,
    MemoryServiceError,
)
from application.business_domain.awakening_domain import selected_layers


def _uuid(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return UUID(value).hex
    except ValueError:
        return None


def build_knowledge_tools(
    client: MemoryServiceClient, agent_id: str, payload: Mapping[str, Any],
    *, layers=None, exclude=(),
) -> FunctionToolset:
    """Capture one awakening's selected knowledge IDs with their expected owner."""
    selected = selected_layers(payload, layers=layers, exclude=exclude)
    allowed: dict[str, str | None] = {}
    for layer, owner in (("shared.knowledge_index", None), ("knowledge_index", agent_id)):
        rows = selected.get(layer, [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, Mapping) and not row.get("project"):
                address = _uuid(row.get("uuid"))
                if address:
                    allowed[address] = owner

    toolset = FunctionToolset(id="memory-knowledge")

    @toolset.tool_plain
    async def read_knowledge(uuid: str) -> dict[str, Any]:
        """Read relevant knowledge by its full UUID from your knowledge index.

        The index is only a reference. Fetch a body when the current task needs it;
        do not load the entire directory.
        """
        address = _uuid(uuid)
        if address not in allowed:
            return {"error": "knowledge_not_available"}
        try:
            record = await client.get_knowledge_record(address)
        except MemoryServiceAuthError:
            return {"error": "knowledge_auth_failed"}
        except MemoryServiceError:
            # Tool errors are data, not a model-run failure or a credential echo.
            return {"error": "knowledge_read_failed"}
        if not isinstance(record, dict):
            return {"error": "invalid_knowledge_response"}
        if (
            _uuid(record.get("uuid")) != address
            or record.get("record_type") != "knowledge"
            or record.get("agent_id") != allowed[address]
            or record.get("project") is not None
            or record.get("archived_date") is not None
            or not isinstance(record.get("content"), str)
        ):
            return {"error": "knowledge_not_available"}
        if not (
            type(record.get("blocks_returned")) is int
            and type(record.get("blocks_total")) is int
            and record["blocks_returned"] >= 1
            and record["blocks_total"] >= record["blocks_returned"]
            and (record.get("next_cursor") is None or isinstance(record["next_cursor"], str))
        ):
            return {"error": "invalid_knowledge_response"}
        return record

    return toolset
