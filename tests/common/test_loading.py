"""The config-named extension point: a dotted path in, an object out.

A bad path must stop the process at startup and say which config entry is wrong —
the failure this pins is a half-enabled integration failing at chat time instead.
"""
import json

import pytest
from application.common.loading import import_callable


def test_a_dotted_path_resolves_to_the_object_it_names():
    assert import_callable("json:dumps", what="x") is json.dumps


@pytest.mark.parametrize("bad", ["json", "json:", ":dumps", ""])
def test_a_path_without_both_halves_is_refused(bad):
    with pytest.raises(ValueError, match="module:attribute"):
        import_callable(bad, what="test source")


def test_an_unimportable_module_names_the_config_entry():
    with pytest.raises(ValueError, match="toolset source 'x'"):
        import_callable("no_such_package_anywhere:build", what="toolset source 'x'")


def test_a_missing_attribute_names_the_config_entry():
    with pytest.raises(ValueError, match="link provider 'y'"):
        import_callable("json:nope", what="link provider 'y'")
