"""The toolset registry: config-named sources, and refusal.

The property that matters is that a name resolves to a builder at *startup* — an
unknown name or a bad path has to refuse boot rather than fail at chat time. The
brain bundles no toolset, so every name comes from configuration.
"""
import json

import pytest
from application.business_services import toolsets as registry


def test_nothing_is_bundled():
    assert registry.load_builders(()) == {}


def test_a_config_named_source_resolves_to_its_builder():
    builders = registry.load_builders((("extra", "json:dumps"),))
    assert builders["extra"] is json.dumps
    assert registry.known_toolsets(builders) == ["extra"]


def test_a_later_source_wins_over_an_earlier_one():
    builders = registry.load_builders(
        (("extra", "json:dumps"), ("extra", "json:loads"))
    )
    assert builders["extra"] is json.loads


def test_an_unknown_toolset_name_is_refused_at_build_time():
    with pytest.raises(ValueError, match="unknown toolset 'nope'"):
        registry.build_toolsets(["nope"], {}, registry.load_builders(()))


def test_a_bad_source_path_names_the_config_entry():
    with pytest.raises(ValueError, match="toolset source 'extra'"):
        registry.load_builders((("extra", "json:nope"),))
