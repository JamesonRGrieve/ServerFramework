# SPDX-License-Identifier: AGPL-3.0-or-later
"""Several extensions roots, for a product composed with another (its own extensions first, a
pinned checkout of the other's after): searched in order, then the bundled ones; a name in two
roots refused; every root importable as ``zephyrex.extensions.*``."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

import zephyrex.extensions
from zephyrex.lib import Paths
from zephyrex.lib.Paths import (
    DuplicateExtensionError,
    extension_roots,
    extensions_dir,
    find_extension_dir,
    set_extensions_root,
)


@pytest.fixture
def roots(tmp_path: Path) -> Iterator[tuple[Path, Path]]:
    """Two roots: a product's own (``game``) and another product's (``world``, with a plain
    package ``world_shared`` beside its extensions)."""
    own, other = tmp_path / "own", tmp_path / "other"
    for folder in (own / "game", other / "world", other / "world_shared"):
        folder.mkdir(parents=True)
    (other / "world_shared" / "__init__.py").write_text("")
    (other / "world_shared" / "Contract.py").write_text("NAME = 'world contract'\n")
    saved = (Paths._EXTENSIONS_ROOT_OVERRIDE, list(Paths._FURTHER_EXTENSIONS_ROOTS))
    saved_path = list(zephyrex.extensions.__path__)
    yield own.resolve(), other.resolve()
    Paths._EXTENSIONS_ROOT_OVERRIDE, Paths._FURTHER_EXTENSIONS_ROOTS = saved
    zephyrex.extensions.__path__ = saved_path


def test_roots_are_searched_in_order_then_the_bundled_ones(
    roots: tuple[Path, Path],
) -> None:
    own, other = roots
    set_extensions_root(os.pathsep.join([str(own), str(other)]))
    assert extension_roots() == [own, other]
    # New extensions go in the first root.
    assert extensions_dir() == str(own)
    assert find_extension_dir("game") == own / "game"
    assert find_extension_dir("world") == other / "world"
    # A bundled extension is still found.
    assert find_extension_dir("conversations").name == "conversations"
    assert find_extension_dir("conversations").is_dir()
    # A list works as the joined string does.
    set_extensions_root([own, other])
    assert extension_roots() == [own, other]


def test_every_root_is_importable_as_zephyrex_extensions(
    roots: tuple[Path, Path],
) -> None:
    own, other = roots
    set_extensions_root([own, other])
    from zephyrex.extensions.world_shared.Contract import NAME

    assert NAME == "world contract"


def test_a_name_in_two_roots_is_refused(roots: tuple[Path, Path]) -> None:
    own, other = roots
    (other / "game").mkdir()
    set_extensions_root([own, other])
    with pytest.raises(DuplicateExtensionError, match="'game'"):
        find_extension_dir("game")


def test_a_root_that_is_missing_is_refused(roots: tuple[Path, Path]) -> None:
    own, _ = roots
    with pytest.raises(FileNotFoundError):
        set_extensions_root([own, own.parent / "nowhere"])
    with pytest.raises(ValueError):
        set_extensions_root(os.pathsep)


def test_the_registry_and_migrations_find_an_extension_in_the_second_root(
    roots: tuple[Path, Path],
) -> None:
    from zephyrex.database.migrations.Migration import MigrationManager
    from zephyrex.extensions.AbstractExtensionProvider import ExtensionRegistry

    own, other = roots
    (other / "world" / "BLL_World.py").write_text("")
    (other / "world" / "migrations" / "versions").mkdir(parents=True)
    set_extensions_root([own, other])
    registry = ExtensionRegistry.__new__(ExtensionRegistry)
    registry.extensions_path = os.pathsep.join([str(own), str(other)])
    assert registry._extension_dir("world") == str(other / "world")
    manager = MigrationManager.__new__(MigrationManager)
    manager.paths = {"extensions_dir": own}
    manager._extensions_path_override = None
    assert manager._extension_source_dir("world") == other / "world"
    assert (
        "world",
        other / "world" / "migrations",
    ) in manager.discover_extension_migration_dirs()
