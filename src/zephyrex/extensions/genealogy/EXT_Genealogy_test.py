# SPDX-License-Identifier: AGPL-3.0-or-later
"""Extension-level tests for genealogy."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "genealogy_ext_test")

from zephyrex.extensions.genealogy.BLL_Genealogy import ALL_MODELS
from zephyrex.extensions.genealogy.EXT_Genealogy import EXT_Genealogy


class TestExtensionMetadata:
    def test_name_and_description(self):
        assert EXT_Genealogy.name == "genealogy"
        assert "person" in EXT_Genealogy.description.lower()

    def test_no_extension_dependencies(self):
        assert EXT_Genealogy.dependencies.ext == []

    def test_discovered_models_are_the_roster(self):
        """The framework finds exactly the declared roster in BLL_Genealogy."""
        assert EXT_Genealogy.models == set(ALL_MODELS)
        assert len(ALL_MODELS) == 2  # PersonModel, RelationshipModel

    def test_initializes(self):
        assert EXT_Genealogy.on_initialize() is True
