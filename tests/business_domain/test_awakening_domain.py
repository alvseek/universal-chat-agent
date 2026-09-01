"""Awakening domain tests — pure, no I/O.

The invariant under test: every layer the payload carries is rendered, by shape,
in a predictable order — nothing is dropped by name or by size; narrowing is only
ever done through the ``layers`` / ``exclude`` configuration.
"""
import pytest

from application.business_domain import awakening_domain as ad

# The shape Munnin's /api/awaken returns: whole records carry ``content``, index
# entries carry title/tags/dates only, single-record layers may be None.
PAYLOAD = {
    "agent_id": "op",
    "shared": {
        "reasoning": [{"uuid": "r1", "title": "Verify first", "content": "Check before claiming."}],
        "knowledge": [{"uuid": "k1", "title": "Line endings", "content": "LF for new files."}],
        "user_profile": {"uuid": "p1", "title": "User Profile", "content": "Name: Alvi"},
    },
    "identity": [
        {"uuid": "i1", "title": "Domain Agent Identity", "content": "**Name**: Agent Op"},
        {"uuid": "i2", "title": "Domain Core Knowledge", "content": "Tools are the only truth."},
        {"uuid": "i3", "title": "Domain Ras", "content": "Confirm before any write."},
    ],
    "reasoning": [],
    "emotional": [],
    "knowledge_index": [{"uuid": "ki1", "title": "invintiry-tools", "tags": ["tools"], "created_date": "2026-09-01"}],
    "episodic_index": [],
    "latest_episode": None,
}


def test_every_present_layer_is_rendered_and_nothing_is_dropped():
    prompt = ad.assemble_system_prompt(PAYLOAD)

    for heading in ("shared.user_profile", "shared.reasoning", "shared.knowledge",
                    "identity", "knowledge_index"):
        assert f"## {heading}" in prompt
    # bodies of whole records are in, verbatim
    assert "Check before claiming." in prompt
    assert "LF for new files." in prompt
    assert "Name: Alvi" in prompt
    assert "Tools are the only truth." in prompt
    # index entries render as one line each, no body to render
    assert "- invintiry-tools (2026-09-01; tools)" in prompt
    # empty layers produce no heading at all
    assert "## reasoning" not in prompt
    assert "## emotional" not in prompt
    assert "## latest_episode" not in prompt


def test_canonical_order_then_unknown_layers_by_name():
    payload = dict(PAYLOAD)
    payload["zeta_new_layer"] = [{"title": "Z", "content": "new from the server"}]
    payload["alpha_new_layer"] = "a plain value"

    prompt = ad.assemble_system_prompt(payload)

    order = [h for h in ("## shared.user_profile", "## shared.reasoning", "## shared.knowledge",
                         "## identity", "## knowledge_index", "## alpha_new_layer", "## zeta_new_layer")]
    positions = [prompt.index(h) for h in order]
    assert positions == sorted(positions)
    assert "new from the server" in prompt
    assert "a plain value" in prompt


def test_configured_layers_are_the_exact_set_and_order():
    prompt = ad.assemble_system_prompt(PAYLOAD, layers=("identity", "shared.reasoning"))

    assert prompt.index("## identity") < prompt.index("## shared.reasoning")
    assert "## shared.knowledge" not in prompt
    assert "## knowledge_index" not in prompt


def test_exclude_removes_named_layers_only():
    prompt = ad.assemble_system_prompt(PAYLOAD, exclude=("shared.knowledge",))

    assert "## shared.knowledge" not in prompt
    assert "## shared.reasoning" in prompt
    assert "## identity" in prompt


def test_records_keep_title_and_body_in_payload_order():
    prompt = ad.assemble_system_prompt(PAYLOAD, layers=("identity",))

    assert prompt.index("### Domain Agent Identity") < prompt.index("### Domain Core Knowledge") \
        < prompt.index("### Domain Ras")
    assert "**Name**: Agent Op" in prompt


def test_empty_identity_raises_agent_not_found():
    absent = {**PAYLOAD, "identity": []}
    with pytest.raises(ad.AgentNotFound) as exc:
        ad.assemble_system_prompt(absent)
    assert exc.value.agent_id == "op"


def test_flatten_unwraps_groups_one_level_and_skips_metadata():
    flat = ad.flatten_layers(PAYLOAD)

    assert "agent_id" not in flat
    assert set(flat) >= {"shared.reasoning", "shared.user_profile", "identity", "latest_episode"}
    assert "shared" not in flat


def test_render_layer_by_shape():
    assert ad.render_layer("x", None) == ""
    assert ad.render_layer("x", []) == ""
    assert ad.render_layer("x", {"title": "T", "content": "B"}) == "## x\n### T\nB"
    assert ad.render_layer("x", [{"title": "T"}]) == "## x\n- T"
    assert ad.render_layer("x", ["a", "b"]) == "## x\n- a\n- b"
    assert ad.render_layer("x", [{"title": "A"}, {"title": "B"}]) == "## x\n- A\n- B"
    mixed = ad.render_layer("x", [{"title": "R", "content": "body"}, {"title": "I"}])
    assert mixed == "## x\n### R\nbody\n\n- I"
    assert ad.render_layer("x", 42) == "## x\n42"
