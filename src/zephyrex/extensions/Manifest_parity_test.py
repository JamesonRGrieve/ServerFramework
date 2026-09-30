# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every in-framework extension ships a manifest.toml that validates and
mirrors its class: an installer reads the manifest before the class can be
imported, so a manifest that disagrees with the class installs the wrong
dependencies (or none)."""

import importlib
import inspect
from pathlib import Path
from typing import Tuple, Type

import pytest

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.extensions.Manifest import load_manifest
from zephyrex.lib.Dependencies import EXT_Dependency, SYS_Dependency

_EXTENSIONS = Path(__file__).resolve().parent
_FOLDERS = sorted(
    entry
    for entry in _EXTENSIONS.iterdir()
    if entry.is_dir() and not entry.name.startswith("__")
)


def _extension_class(folder: Path) -> Tuple[str, Type[AbstractStaticExtension]]:
    for module_file in sorted(folder.glob("EXT_*.py")):
        if module_file.stem.endswith("_test"):
            continue
        module = importlib.import_module(
            f"zephyrex.extensions.{folder.name}.{module_file.stem}"
        )
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(cls, AbstractStaticExtension)
                and cls.__module__ == module.__name__
                and getattr(cls, "name", None) == folder.name
            ):
                return module_file.stem, cls
    raise AssertionError(f"{folder.name}: no EXT_*.py class named {folder.name!r}")


@pytest.mark.parametrize("folder", _FOLDERS, ids=[f.name for f in _FOLDERS])
def test_manifest_mirrors_the_extension_class(folder: Path) -> None:
    entry_module, cls = _extension_class(folder)
    manifest = load_manifest(folder / "manifest.toml")

    assert (manifest.name, manifest.version, manifest.description) == (
        cls.name,
        cls.version,
        cls.description,
    )
    assert manifest.entry_module == entry_module
    # The extension's and its providers' requirements: what its extra
    # installs (sync_dependencies writes both).
    assert sorted(manifest.pip_dependencies) == cls.pip_requirements()
    assert sorted(manifest.system_dependencies) == sorted(
        dep.name for dep in cls.dependencies if isinstance(dep, SYS_Dependency)
    )
    assert sorted((d.name, d.optional) for d in manifest.extension_dependencies) == (
        sorted(
            (dep.name, dep.optional)
            for dep in cls.dependencies
            if isinstance(dep, EXT_Dependency)
        )
    )
