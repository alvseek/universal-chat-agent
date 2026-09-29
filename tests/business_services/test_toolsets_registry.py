"""The toolset registry: bundled names, config-named sources, and refusal.

The property that matters is that a name resolves to a builder at *startup* — an
unknown name or a bad path has to refuse boot rather than fail at chat time.
"""
import json

import pytest
from application.business_services import toolsets as registry


def test_the_bundled_name_is_known():
    builders = registry.load_builders(())
    assert "invintiry" in builders
    assert registry.known_toolsets(builders) == sorted(builders)


def test_a_config_named_source_is_added_beside_the_bundled_ones():
    builders = registry.load_builders((("extra", "json:dumps"),))
    assert builders["extra"] is json.dumps
    assert "invintiry" in builders  # the bundled set survives


def test_a_config_named_source_can_replace_a_bundled_name():
    builders = registry.load_builders((("invintiry", "json:dumps"),))
    assert builders["invintiry"] is json.dumps


def test_an_unknown_toolset_name_is_refused_at_build_time():
    with pytest.raises(ValueError, match="unknown toolset 'nope'"):
        registry.build_toolsets(["nope"], {}, registry.load_builders(()))
