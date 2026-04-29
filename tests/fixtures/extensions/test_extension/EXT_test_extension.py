"""Fixture extension for AbstractExtensionProvider_test.py (Item 72).

A real, minimal extension that the test suite loads through the registry
to verify dependency-installation behavior without patching
``importlib.import_module``. The extension declares an empty Dependencies
list so ``install_extension_dependencies`` returns ``{}`` against it
deterministically.
"""

from __future__ import annotations

from typing import ClassVar

from extensions.AbstractExtensionProvider import AbstractStaticExtension
from lib.Dependencies import Dependencies


class EXT_test_extension(AbstractStaticExtension):
    """Minimal real extension class."""

    name: ClassVar[str] = "test_extension"
    description: ClassVar[str] = "Fixture extension for Item 72 tests"
    version: ClassVar[str] = "0.0.1"
    dependencies: ClassVar[Dependencies] = Dependencies([])
