"""Tests for the meta_labels extension."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "meta_labels_test")


from zephyrex.extensions.meta_labels.BLL_Meta_Labels import (
    LabelLinkManager,
    LabelLinkModel,
    LabelManager,
    LabelModel,
)
from zephyrex.extensions.meta_labels.EXT_Meta_Labels import (
    EXT_Meta_Labels,
)


class TestCanonicalWiring:
    def test_label_round_trip(self):
        assert LabelModel.Manager is LabelManager
        assert LabelManager._model is LabelModel

    def test_label_link_round_trip(self):
        assert LabelLinkModel.Manager is LabelLinkManager
        assert LabelLinkManager._model is LabelLinkModel

    def test_extension_metadata(self):
        assert EXT_Meta_Labels.name == "meta_labels"
        abilities = EXT_Meta_Labels.get_abilities()
        assert "labels_attach" in abilities
        assert "labels_detach" in abilities


class TestLifecycle:
    def test_on_initialize_returns_true(self):
        assert EXT_Meta_Labels.on_initialize() is True
