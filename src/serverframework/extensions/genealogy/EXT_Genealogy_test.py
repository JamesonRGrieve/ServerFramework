"""Extension-level tests for genealogy."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "genealogy_ext_test")

from serverframework.extensions.genealogy.BLL_Genealogy import ALL_MODELS
from serverframework.extensions.genealogy.EXT_Genealogy import GenealogyExtension


class TestExtensionMetadata:
    def test_name_and_description(self):
        assert GenealogyExtension.name == "genealogy"
        assert "person" in GenealogyExtension.description.lower()

    def test_no_extension_dependencies(self):
        assert GenealogyExtension.extension_dependencies == []

    def test_models_returns_full_roster(self):
        models = GenealogyExtension.models()
        assert set(models) == set(ALL_MODELS)
        assert len(models) == 2  # PersonModel, RelationshipModel
