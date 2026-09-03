"""What counts as a linking command — and, just as importantly, what does not.

A false positive here is worse than a false negative: swallowing an ordinary
message would make the agent go silent on it, so anything uncertain must fall
through to the model rather than be claimed by the parser.
"""
import pytest

from application.business_domain import link_commands as lc


def test_start_with_a_code_is_a_redeem():
    cmd = lc.parse("/start AbC-123_xyz")
    assert cmd == lc.LinkCommand(lc.REDEEM, "AbC-123_xyz")


def test_bare_start_asks_for_the_link_prompt():
    assert lc.parse("/start") == lc.LinkCommand(lc.PROMPT)


def test_logout_is_recognised():
    assert lc.parse("/logout") == lc.LinkCommand(lc.LOGOUT)


@pytest.mark.parametrize(
    "text",
    [
        "/start@alvi_telegent_bot CODE1",
        "/logout@alvi_telegent_bot",
    ],
)
def test_group_style_bot_suffix_is_stripped(text):
    # Telegram appends @botname in groups; the command is still ours.
    assert lc.parse(text) is not None


def test_command_matching_is_case_insensitive():
    assert lc.parse("/START code1") == lc.LinkCommand(lc.REDEEM, "code1")


def test_surrounding_whitespace_is_ignored():
    assert lc.parse("  /logout  ") == lc.LinkCommand(lc.LOGOUT)


@pytest.mark.parametrize(
    "text",
    [
        "hello",
        "what is in stock?",
        "",
        "   ",
        "start CODE1",  # no slash: ordinary text that happens to say start
    ],
)
def test_ordinary_messages_are_not_commands(text):
    assert lc.parse(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "/startle",      # near-miss on /start
        "/logouts",      # near-miss on /logout
        "/help",         # a command we do not own — the agent may answer it
        "/logout now",   # /logout takes no arguments
    ],
)
def test_near_misses_fall_through_to_the_agent(text):
    assert lc.parse(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "/start not a code",     # a start payload cannot contain spaces
        "/start bad!code",       # outside Telegram's payload charset
        "/start " + "x" * 65,    # past Telegram's 64-character limit
    ],
)
def test_malformed_codes_are_named_rather_than_sent_upstream(text):
    assert lc.parse(text) == lc.LinkCommand(lc.BAD_CODE)


def test_a_code_at_the_exact_limit_is_accepted():
    code = "x" * 64
    assert lc.parse(f"/start {code}") == lc.LinkCommand(lc.REDEEM, code)
