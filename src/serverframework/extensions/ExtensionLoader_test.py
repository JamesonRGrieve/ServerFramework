"""Tests for ExtensionLoader (Item 61)."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from serverframework.extensions.ExtensionLoader import (
    get_loaded_extension_module,
    is_extension_module_loaded,
    load_extension_module,
)


@pytest.fixture
def out_of_tree_extension(tmp_path: Path) -> Path:
    """Create a fake extensions tree at an out-of-package location."""
    ext_root = tmp_path / "my_extensions"
    ext_dir = ext_root / "fakeext"
    ext_dir.mkdir(parents=True)
    (ext_dir / "__init__.py").write_text("")
    (ext_dir / "BLL_Fake.py").write_text(
        textwrap.dedent(
            """
            FAKE_EXPORT = "loaded"

            class FakeManager:
                kind = "fake"
            """
        )
    )
    return ext_root


def _cleanup(extension_name: str, file_stem: str) -> None:
    sys.modules.pop(f"serverframework_ext_{extension_name}_{file_stem}", None)
    sys.modules.pop(f"serverframework.extensions.{extension_name}.{file_stem}", None)


def test_load_extension_module_registers_under_both_names(out_of_tree_extension):
    _cleanup("fakeext", "BLL_Fake")
    try:
        mod = load_extension_module(out_of_tree_extension, "fakeext", "BLL_Fake")
        assert mod.FAKE_EXPORT == "loaded"
        assert "serverframework_ext_fakeext_BLL_Fake" in sys.modules
        assert "serverframework.extensions.fakeext.BLL_Fake" in sys.modules
        assert (
            sys.modules["serverframework_ext_fakeext_BLL_Fake"]
            is sys.modules["serverframework.extensions.fakeext.BLL_Fake"]
        )
    finally:
        _cleanup("fakeext", "BLL_Fake")


def test_load_extension_module_idempotent(out_of_tree_extension):
    _cleanup("fakeext", "BLL_Fake")
    try:
        a = load_extension_module(out_of_tree_extension, "fakeext", "BLL_Fake")
        b = load_extension_module(out_of_tree_extension, "fakeext", "BLL_Fake")
        assert a is b
    finally:
        _cleanup("fakeext", "BLL_Fake")


def test_load_extension_module_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_extension_module(tmp_path, "nope", "AlsoNope")


def test_is_loaded_helpers(out_of_tree_extension):
    _cleanup("fakeext", "BLL_Fake")
    try:
        assert not is_extension_module_loaded("fakeext", "BLL_Fake")
        assert get_loaded_extension_module("fakeext", "BLL_Fake") is None
        load_extension_module(out_of_tree_extension, "fakeext", "BLL_Fake")
        assert is_extension_module_loaded("fakeext", "BLL_Fake")
        mod = get_loaded_extension_module("fakeext", "BLL_Fake")
        assert mod is not None
        assert mod.FAKE_EXPORT == "loaded"
    finally:
        _cleanup("fakeext", "BLL_Fake")
