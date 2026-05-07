"""Extension-level tests for rpg_state."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "rpg_state_ext_test")

from serverframework.extensions.rpg_state.BLL_RPGState import ALL_MODELS
from serverframework.extensions.rpg_state.EXT_RPGState import RPGStateExtension


class TestExtensionMetadata:
    def test_name_and_description(self):
        assert RPGStateExtension.name == "rpg_state"
        assert "RPG" in RPGStateExtension.description

    def test_depends_on_genealogy(self):
        # Hard dependency: rpg_state widens genealogy.PersonModel and
        # genealogy.RelationshipModel via @extension_model.
        assert "genealogy" in RPGStateExtension.extension_dependencies

    def test_models_returns_full_roster(self):
        models = RPGStateExtension.models()
        assert set(models) == set(ALL_MODELS)
        assert len(models) == 13  # bump when adding owned tables.
