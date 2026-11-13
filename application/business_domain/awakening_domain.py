"""Pure domain: turn an agent's awakening payload into a system prompt.

An awakening is what the memory server assembles for an agent: a set of named
*layers*, each holding memory records. This module renders those layers into one
text the model can be given as its system prompt.

The one rule that shapes everything here: **render by shape, never by name.** A
layer is rendered according to what it *is* — a list of whole records, a single
record, a list of index entries, a plain value — not according to what it is
called. Consequences that follow from that rule, and are the point of it:

* every layer the server sends is rendered; nothing is dropped because of how it
  looks or how big it is. What an awakening contains is decided where the memory
  lives, not here;
* a layer the server adds tomorrow renders today, appended after the known ones;
* which layers to include, and in what order, is **configuration** (``layers`` /
  ``exclude``), so a deployment can narrow a prompt without a code change — and
  the default is everything, in the canonical order below.

A second rule follows from the first: a record that describes itself as a
**partial page** keeps that description in the prompt. The memory server bounds a
rolling episode to a few timestamp groups and says so in the JSON; a renderer that
dropped those fields would turn a page into something that looks like a whole
record, which is the one failure the bound exists to prevent. See
``_pagination_notice`` — it fires on record shape, never on layer name, and it
never fetches anything.

No I/O, no framework types: a dict in, a string out.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

# The order the fleet reads its own memory in: who the user is, how the fleet
# reasons, what it knows, then this agent's identity, its own reasoning and
# moments, and finally what it can browse. Layers absent from a payload are simply
# not rendered; layers not listed here render after these, in name order.
CANONICAL_ORDER: tuple[str, ...] = (
    "shared.user_profile",
    "shared.reasoning",
    "shared.knowledge_index",
    "shared.knowledge",
    "identity",
    "reasoning",
    "emotional",
    "knowledge_index",
    "episodic_index",
    "latest_episode",
)

# The layer whose absence means "no such agent". A memory server answers a query
# for an unknown agent with the same shape as a known one, every layer empty; the
# identity layer is the one every real agent has, so it is the discriminator.
IDENTITY_LAYER = "identity"

# Payload keys that are metadata about the payload, not memory.
_META_KEYS = frozenset({"agent_id"})


class AgentNotFound(LookupError):
    """The awakening names an agent the memory server does not hold."""

    def __init__(self, agent_id: str | None) -> None:
        super().__init__(f"no agent {agent_id!r} in the memory service")
        self.agent_id = agent_id


# --- shapes ---------------------------------------------------------------


def is_record(value: Any) -> bool:
    """A memory record: a mapping with a body (``content``) and/or a ``title``."""
    return isinstance(value, Mapping) and ("content" in value or "title" in value)


def is_group(value: Any) -> bool:
    """A group of layers: a mapping that is not itself a record."""
    return isinstance(value, Mapping) and not is_record(value)


def flatten_layers(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Every layer in the payload, keyed by a dotted path, groups unwrapped one level.

    ``{"shared": {"reasoning": [...]}, "identity": [...]}`` becomes
    ``{"shared.reasoning": [...], "identity": [...]}``. Metadata keys are skipped.
    """
    flat: dict[str, Any] = {}
    for name, value in payload.items():
        if name in _META_KEYS:
            continue
        if is_group(value):
            for inner, inner_value in value.items():
                flat[f"{name}.{inner}"] = inner_value
        else:
            flat[name] = value
    return flat


def is_absent(payload: Mapping[str, Any]) -> bool:
    """True when the payload describes no agent (identity layer missing or empty)."""
    return not payload.get(IDENTITY_LAYER)


# --- rendering ------------------------------------------------------------


def _heading(name: str) -> str:
    return f"## {name}"


def _pagination_notice(record: Mapping[str, Any]) -> str | None:
    """The notice a *partial* record owes its reader, or ``None`` for a whole one.

    A bounded page is only honest if the reader can see that it is bounded and knows
    how to finish it, and the JSON carries that in three fields: how many timestamp
    groups arrived, how many the record holds, and — the only one that answers "is
    there more?" — the cursor. The counts alone cannot: a final page returns fewer
    than the total and is still complete.

    So the trigger is ``next_cursor``, never ``returned < total``. Applied by record
    **shape** (valid page metadata on the record), never by layer name, because which
    layer a paged record sits in is the memory server's business and can change.

    Returns "" for a whole record — the caller then renders exactly what it always did.
    """
    cursor = record.get("next_cursor")
    returned = record.get("blocks_returned")
    total = record.get("blocks_total")
    if not isinstance(cursor, str) or not cursor.strip():
        return None
    if not isinstance(returned, int) or not isinstance(total, int):
        return None
    uuid = record.get("uuid")
    address = f" `{uuid}`" if isinstance(uuid, str) and uuid.strip() else ""
    return (
        f"> **Partial record** — {returned} of {total} timestamp groups returned;"
        f" record{address}; next cursor `{cursor}`."
        " More remains: read it with `get` using that uuid and cursor."
    )


def _render_record(record: Mapping[str, Any], *, level: int = 3) -> str:
    title = record.get("title")
    body = record.get("content")
    lines: list[str] = []
    if title:
        lines.append(f"{'#' * level} {title}")
    if body:
        lines.append(str(body).strip())
    rendered = "\n".join(lines)
    notice = _pagination_notice(record)
    if notice:
        # A blank line, so the notice reads as a statement about the record rather than
        # as the last line of its body. Ordinary records never take this branch.
        rendered = f"{rendered}\n\n{notice}" if rendered else notice
    return rendered


def _render_index_entry(entry: Mapping[str, Any]) -> str:
    title = entry.get("title") or entry.get("uuid") or "(untitled)"
    extras: list[str] = []
    date = entry.get("created_date")
    if date:
        extras.append(str(date))
    tags = entry.get("tags")
    if tags:
        extras.append(", ".join(str(t) for t in tags))
    line = f"- {title}" + (f" ({'; '.join(extras)})" if extras else "")
    if entry.get("uuid"):
        line += f" [uuid: {entry['uuid']}]"
    if entry.get("content_summary"):
        line += f" — {entry['content_summary']}"
    return line


def render_layer(name: str, value: Any) -> str:
    """One layer as text, chosen by the value's shape. Empty input renders empty."""
    if value is None or value == [] or value == "" or value == {}:
        return ""
    if is_record(value):
        return f"{_heading(name)}\n{_render_record(value)}"
    if isinstance(value, (list, tuple)):
        # Whole records are sections (blank line between); one-liners — index
        # entries and plain values — are bullets on consecutive lines.
        blocks: list[str] = []
        bullets: list[str] = []
        for item in value:
            if is_record(item) and item.get("content"):
                if bullets:
                    blocks.append("\n".join(bullets))
                    bullets = []
                blocks.append(_render_record(item))
            elif is_record(item):
                bullets.append(_render_index_entry(item))
            elif item is not None and item != "":
                bullets.append(f"- {item}")
        if bullets:
            blocks.append("\n".join(bullets))
        body = "\n\n".join(b for b in blocks if b)
        return f"{_heading(name)}\n{body}" if body else ""
    return f"{_heading(name)}\n{value}"


def layer_order(present: Iterable[str], configured: Sequence[str] | None = None) -> list[str]:
    """The layers to render, in order.

    With ``configured`` given, that list is the exact order and the exact set (names
    not present are skipped). Without it: the canonical order for known layers, then
    every other layer present, sorted by name — so nothing the server sent is lost.
    """
    names = list(present)
    if configured:
        return [n for n in configured if n in names]
    known = [n for n in CANONICAL_ORDER if n in names]
    unknown = sorted(n for n in names if n not in CANONICAL_ORDER)
    return known + unknown


def assemble_system_prompt(
    payload: Mapping[str, Any],
    *,
    layers: Sequence[str] | None = None,
    exclude: Iterable[str] = (),
) -> str:
    """The whole awakening as one system prompt.

    Raises ``AgentNotFound`` when the payload describes no agent — that answer must
    never become a prompt, since a model given an empty identity would answer as
    nobody in particular while looking healthy.
    """
    if is_absent(payload):
        raise AgentNotFound(payload.get("agent_id"))
    flat = selected_layers(payload, layers=layers, exclude=exclude)
    parts: list[str] = []
    for name in flat:
        text = render_layer(name, flat[name])
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def selected_layers(
    payload: Mapping[str, Any], *, layers: Sequence[str] | None = None,
    exclude: Iterable[str] = (),
) -> dict[str, Any]:
    """The exact rendered selection, also used to constrain knowledge reads.

    Old configuration names follow the new field only when that field exists.
    If both fields arrive during a rollout, the index wins; bodies are not duplicated.
    """
    flat = flatten_layers(payload)
    has_index = "shared.knowledge_index" in flat
    if has_index:
        flat.pop("shared.knowledge", None)

    def current(name: str) -> str:
        return "shared.knowledge_index" if has_index and name == "shared.knowledge" else name

    configured = tuple(dict.fromkeys(current(n) for n in layers)) if layers else None
    skip = {current(n) for n in exclude}
    return {n: flat[n] for n in layer_order(flat, configured) if n not in skip}
