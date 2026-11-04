# SPDX-License-Identifier: AGPL-3.0-or-later
"""The scoped importer's cache: a repeated import is answered from it, and a
change to the enabled extensions is not, against a real extensions tree on
disk."""

import sys
import uuid
from pathlib import Path
from typing import Callable, Iterator, List

import pytest

import zephyrex.extensions
from zephyrex.lib.Environment import settings
from zephyrex.lib.Paths import set_extensions_root
from zephyrex.pydantic2.scoped_importer import ScopedModuleImporter

# A prefix no framework module uses, so only the tree below is discovered.
FILE_TYPE = "ZZCACHE"
MODULE_STEM = f"{FILE_TYPE}_probe"


@pytest.fixture
def extension_tree(tmp_path: Path) -> Iterator[Callable[[], str]]:
    """Point the extensions root at ``tmp_path``; the yielded factory adds
    a uniquely named extension holding one discoverable module. The
    extensions' modules and packages are unregistered afterwards."""
    created: List[str] = []

    def add_extension() -> str:
        name = f"zz_cache_{uuid.uuid4().hex[:8]}"
        (tmp_path / name).mkdir()
        (tmp_path / name / f"{MODULE_STEM}.py").write_text("VALUE = 1\n")
        created.append(name)
        return name

    set_extensions_root(tmp_path)
    try:
        yield add_extension
    finally:
        set_extensions_root(None)
        for name in created:
            for module_name in [m for m in sys.modules if name in m]:
                del sys.modules[module_name]
            if hasattr(zephyrex.extensions, name):
                delattr(zephyrex.extensions, name)


def _module(extension: str) -> str:
    return f"zephyrex.extensions.{extension}.{MODULE_STEM}"


def test_a_repeated_import_is_served_from_the_cache(
    extension_tree: Callable[[], str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard checked an attribute the importer never had, so every call
    rescanned and re-imported."""
    extension = extension_tree()
    monkeypatch.setattr(settings, "APP_EXTENSIONS", extension)
    importer = ScopedModuleImporter({})

    first = importer._scoped_import(file_type=FILE_TYPE, scopes=["extensions"])
    second = importer._scoped_import(file_type=FILE_TYPE, scopes=["extensions"])

    assert first == ([_module(extension)], [])
    assert second is first


def test_a_changed_extension_set_is_not_answered_from_the_cache(
    extension_tree: Callable[[], str], monkeypatch: pytest.MonkeyPatch
) -> None:
    before, after = extension_tree(), extension_tree()
    importer = ScopedModuleImporter({})

    monkeypatch.setattr(settings, "APP_EXTENSIONS", before)
    first = importer._scoped_import(file_type=FILE_TYPE, scopes=["extensions"])
    monkeypatch.setattr(settings, "APP_EXTENSIONS", after)
    second = importer._scoped_import(file_type=FILE_TYPE, scopes=["extensions"])

    assert first == ([_module(before)], [])
    assert second == ([_module(after)], [])


def test_clean_bypasses_the_cache(
    extension_tree: Callable[[], str], monkeypatch: pytest.MonkeyPatch
) -> None:
    extension = extension_tree()
    monkeypatch.setattr(settings, "APP_EXTENSIONS", extension)
    importer = ScopedModuleImporter({})

    first = importer._scoped_import(file_type=FILE_TYPE, scopes=["extensions"])
    fresh = importer._scoped_import(
        file_type=FILE_TYPE, scopes=["extensions"], clean=True
    )

    assert fresh == first
    assert fresh is not first
