# SPDX-License-Identifier: AGPL-3.0-or-later
"""An extension finds its own BLL_ and PRV_ modules wherever it lives.

A consumer app points the extensions root at its own directory
(``run(extensions_path=...)``) and also loads bundled extensions from the
installed framework, so discovery cannot search only the active root:
bundled genealogy found no models under a consumer's root, and a consumer's
rpg_state found none under the framework's.
"""

import pytest

from zephyrex.extensions.email.EXT_EMail import EXT_EMail
from zephyrex.extensions.genealogy.BLL_Genealogy import ALL_MODELS
from zephyrex.extensions.genealogy.EXT_Genealogy import EXT_Genealogy
from zephyrex.extensions.AbstractExtensionProvider import ExtensionType
from zephyrex.lib import Paths


@pytest.fixture
def consumer_root(tmp_path, monkeypatch):
    """The active extensions root is a consumer's directory with none of the
    bundled extensions in it."""
    monkeypatch.setattr(Paths, "_EXTENSIONS_ROOT_OVERRIDE", tmp_path)
    return tmp_path


def test_a_bundled_extension_finds_its_models_under_a_consumer_root(
    consumer_root, monkeypatch
):
    monkeypatch.setattr(EXT_Genealogy, "_models_cache", None)
    assert EXT_Genealogy.models == set(ALL_MODELS)


def test_a_bundled_extension_finds_its_providers_under_a_consumer_root(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(EXT_EMail, "_providers", [])
    bundled = {p.__name__ for p in EXT_EMail.providers}
    assert bundled

    monkeypatch.setattr(Paths, "_EXTENSIONS_ROOT_OVERRIDE", tmp_path)
    monkeypatch.setattr(EXT_EMail, "_providers", [])
    assert {p.__name__ for p in EXT_EMail.providers} == bundled


def test_the_extension_root_is_where_the_class_lives(consumer_root):
    assert EXT_Genealogy._extensions_root() == str(Paths.src_path() / "extensions")


def test_an_extension_of_modelmeta_models_is_a_database_extension(monkeypatch):
    """genealogy declares its tables with ``metaclass=ModelMeta`` and never names
    DatabaseMixin; it was typed not DATABASE, so model discovery skipped it and
    any @extension_model in such an extension was never applied."""
    monkeypatch.setattr(EXT_Genealogy, "_types_cache", None)
    assert ExtensionType.DATABASE in EXT_Genealogy.types
