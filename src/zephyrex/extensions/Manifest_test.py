"""Tests for the extension manifest format and loader (Item 20)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from zephyrex.extensions.Manifest import (
    ExtensionDependencyDecl,
    ExtensionManifest,
    find_manifest,
    load_manifest,
)


def _write(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def test_load_manifest_round_trip(tmp_path: Path) -> None:
    """A well-formed manifest deserializes into the expected schema."""
    manifest_path = tmp_path / "manifest.toml"
    _write(
        manifest_path,
        """
        name = "demo"
        version = "1.2.3"
        description = "demo extension"
        author = "qa"
        entry_module = "EXT_Demo"
        pip_dependencies = ["requests>=2"]
        system_dependencies = ["libxml2"]
        minimum_framework_version = "0.0.0"

        [[extension_dependencies]]
        name = "email"
        optional = false

        [[extension_dependencies]]
        name = "auth_mfa"
        version = ">=1.0,<2"
        optional = true
        """,
    )
    manifest = load_manifest(manifest_path)
    assert manifest.name == "demo"
    assert manifest.version == "1.2.3"
    assert manifest.entry_module == "EXT_Demo"
    assert manifest.pip_dependencies == ["requests>=2"]
    assert manifest.system_dependencies == ["libxml2"]
    assert len(manifest.extension_dependencies) == 2
    assert manifest.extension_dependencies[0].name == "email"
    assert manifest.extension_dependencies[0].optional is False
    assert manifest.extension_dependencies[1].name == "auth_mfa"
    assert manifest.extension_dependencies[1].optional is True


def test_load_manifest_accepts_directory(tmp_path: Path) -> None:
    """Passing the extension directory resolves manifest.toml inside it."""
    _write(
        tmp_path / "manifest.toml",
        """
        name = "dir_demo"
        version = "0.1.0"
        entry_module = "EXT_DirDemo"
        """,
    )
    manifest = load_manifest(tmp_path)
    assert manifest.name == "dir_demo"


def test_load_manifest_missing_file(tmp_path: Path) -> None:
    """Absent manifest.toml raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_manifest(tmp_path / "manifest.toml")


def test_load_manifest_rejects_malformed_toml(tmp_path: Path) -> None:
    """Malformed TOML raises ValueError."""
    p = tmp_path / "manifest.toml"
    p.write_text("name = 'x\nversion = '0.1.0'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed manifest TOML"):
        load_manifest(p)


def test_load_manifest_rejects_invalid_name(tmp_path: Path) -> None:
    """Names containing path traversal or shell metacharacters are rejected."""
    p = tmp_path / "manifest.toml"
    _write(
        p,
        """
        name = "../escape"
        version = "0.1.0"
        entry_module = "EXT_X"
        """,
    )
    with pytest.raises(ValueError, match="schema validation"):
        load_manifest(p)


def test_load_manifest_rejects_invalid_version(tmp_path: Path) -> None:
    """Non-PEP-440 version strings are rejected."""
    p = tmp_path / "manifest.toml"
    _write(
        p,
        """
        name = "demo"
        version = "not-a-version"
        entry_module = "EXT_Demo"
        """,
    )
    with pytest.raises(ValueError, match="schema validation"):
        load_manifest(p)


def test_load_manifest_rejects_entry_module_with_path(tmp_path: Path) -> None:
    """entry_module containing path separators or '.py' is rejected."""
    p = tmp_path / "manifest.toml"
    _write(
        p,
        """
        name = "demo"
        version = "1.0.0"
        entry_module = "EXT_Demo.py"
        """,
    )
    with pytest.raises(ValueError, match="schema validation"):
        load_manifest(p)


def test_extension_dependency_invalid_name() -> None:
    """ExtensionDependencyDecl validates dependency names."""
    with pytest.raises(Exception):
        ExtensionDependencyDecl(name="bad/name")


def test_find_manifest_returns_path_or_none(tmp_path: Path) -> None:
    """find_manifest helper returns the path when present and None when absent."""
    assert find_manifest(tmp_path) is None
    _write(
        tmp_path / "manifest.toml",
        """
        name = "demo"
        version = "0.1.0"
        entry_module = "EXT_Demo"
        """,
    )
    found = find_manifest(tmp_path)
    assert found is not None and found.name == "manifest.toml"


def test_bundled_auth_mfa_manifest_loads() -> None:
    """The example manifest shipped with auth_mfa is valid."""
    here = Path(__file__).resolve().parent
    manifest = load_manifest(here / "auth_mfa")
    assert manifest.name == "auth_mfa"
    assert manifest.entry_module == "EXT_Auth_MFA"
    assert manifest.version == "1.0.0"
