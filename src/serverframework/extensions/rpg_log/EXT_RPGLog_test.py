"""Extension-level tests for rpg_log."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "rpg_log_ext_test")

from serverframework.extensions.rpg_log.BLL_RPGLog import ALL_MODELS
from serverframework.extensions.rpg_log.EXT_RPGLog import RPGLogExtension


class TestExtensionMetadata:
    def test_name_and_description(self):
        assert RPGLogExtension.name == "rpg_log"
        assert "log" in RPGLogExtension.description.lower()

    def test_depends_on_rpg_state(self):
        assert RPGLogExtension.extension_dependencies == ["rpg_state"]

    def test_models_returns_full_roster(self):
        models = RPGLogExtension.models()
        assert set(models) == set(ALL_MODELS)
        assert len(models) == 7  # bump when adding log tables.
