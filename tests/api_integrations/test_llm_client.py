"""Two wiring properties of the LLM client: the sticky-routing key, and usage logging.

Neither shows up in a reply, and neither shows up in the transport log. A missing
session_id silently costs cache hits; unlogged usage silently hides whether the
model choice is affordable at all. Both are cheap to pin here.
"""
import logging
from types import SimpleNamespace

from application.api_integrations.llm import llm_client


def _agent(**kwargs):
    return llm_client.build_agent(
        model="m", base_url="http://example/v1", api_key="k", system_prompt="p", **kwargs
    )


def test_a_session_id_becomes_the_extra_body_field_openrouter_pins_routing_on():
    agent = _agent(session_id="invintiry-operator")
    assert agent.model_settings == {"extra_body": {"session_id": "invintiry-operator"}}


def test_without_a_session_id_nothing_extra_is_sent():
    assert _agent().model_settings is None


def test_usage_is_logged_including_the_cache_reads(caplog):
    usage = SimpleNamespace(
        input_tokens=2143,
        output_tokens=19,
        cache_read_tokens=2048,
        cache_write_tokens=0,
        details={"reasoning_tokens": 5},
    )

    with caplog.at_level(logging.INFO, logger="universal-chat-agent"):
        llm_client._log_usage(SimpleNamespace(usage=usage))

    assert "in=2143" in caplog.text
    assert "cache_read=2048" in caplog.text
    assert "reasoning=5" in caplog.text


def test_a_result_carrying_no_usage_logs_nothing(caplog):
    with caplog.at_level(logging.INFO, logger="universal-chat-agent"):
        llm_client._log_usage(SimpleNamespace())

    assert caplog.text == ""
