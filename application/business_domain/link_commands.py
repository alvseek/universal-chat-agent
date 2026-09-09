"""Pure domain: is this message a linking command, and what does it ask for?

Linking is driven from the chat because that is where the deep link lands, and it
lands as an ordinary message on every transport: ``t.me/<bot>?start=<code>`` makes
Telegram send ``/start <code>`` on one tap, while ``wa.me/<number>?text=`` can only
pre-fill the same text for the person to send themselves. Either way the chat path
has to recognise three things before the model ever sees the text — redeem this
code, tell me how to link, forget me — and everything else must fall through
untouched to the agent.

That difference is why the accepted spelling matters more than it looks: a service
that can only *suggest* a message is relying on this parser to accept exactly what
it suggested, so the two commands here are a contract with whoever builds the link,
not merely an input format.

No I/O, no transport knowledge and no knowledge of any service: this decides only
what was *asked*, never what to do about it. That is ``LinkService``'s job.

The payload charset is Telegram's rule for start parameters (``A-Za-z0-9_-``, 64
max) and is kept as the common denominator for every transport, since a code that
survives the strictest carrier survives all of them. Anything arriving through a
real deep link already conforms, so rejecting the rest here turns a hand-typed
mistake into a plain sentence instead of a 400 from someone else's API.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Telegram's documented start-parameter charset and limit.
_CODE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

REDEEM = "redeem"
PROMPT = "prompt"
LOGOUT = "logout"
BAD_CODE = "bad_code"


@dataclass(frozen=True)
class LinkCommand:
    """``kind`` is one of REDEEM / PROMPT / LOGOUT / BAD_CODE; ``code`` only on REDEEM."""

    kind: str
    code: str | None = None


def parse(text: str) -> LinkCommand | None:
    """The command this message is, or None when it is ordinary conversation.

    None is the common case and must stay cheap: anything not starting with a
    slash is not a command, and a slash command we do not own (``/help``) is the
    agent's to answer, not ours to swallow.
    """
    stripped = (text or "").strip()
    if not stripped.startswith("/"):
        return None

    head, _, rest = stripped.partition(" ")
    # In groups Telegram appends the bot's username: "/logout@alvi_telegent_bot".
    command = head.split("@", 1)[0].lower()
    args = rest.split()

    if command == "/start":
        if not args:
            return LinkCommand(PROMPT)
        if len(args) > 1:
            # A start payload cannot contain a space, so this was typed by hand.
            return LinkCommand(BAD_CODE)
        return LinkCommand(REDEEM, args[0]) if _CODE.match(args[0]) else LinkCommand(BAD_CODE)

    if command == "/logout" and not args:
        return LinkCommand(LOGOUT)

    return None
