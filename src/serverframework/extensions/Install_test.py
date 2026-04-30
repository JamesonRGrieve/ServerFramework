"""Tests for ``install_from_manifest`` (Item 20)."""

from __future__ import annotations

import tarfile
import textwrap
import zipfile
from pathlib import Path
from typing import Tuple

import pytest

from serverframework.extensions.AbstractExtensionProvider import ExtensionRegistry
from serverframework.extensions.Install import (
    InstallResult,
    install_from_manifest,
)
from serverframework.lib.Paths import set_extensions_root


def _write_extension(
    root: Path,
    name: str,
    *,
    version: str = "1.0.0",
    extension_dependencies=None,
) -> Path:
    """Create a minimal extension dir with manifest + EXT_*.py."""
    ext_dir = root / name
    ext_dir.mkdir(parents=True, exist_ok=True)
    deps_block = ""
    for dep in extension_dependencies or []:
        deps_block += textwrap.dedent(
            f"""
            [[extension_dependencies]]
            name = "{dep['name']}"
            optional = {str(dep.get('optional', False)).lower()}
            """
        )
    (ext_dir / "manifest.toml").write_text(
        textwrap.dedent(
            f"""
            name = "{name}"
            version = "{version}"
            entry_module = "EXT_{name.title()}"
            description = "fixture extension"
            """
        ).strip()
        + "\n"
        + deps_block,
        encoding="utf-8",
    )
    # Minimal stub Python module so the extension dir is structurally valid.
    (ext_dir / f"EXT_{name.title()}.py").write_text(
        textwrap.dedent(
            f"""
            # Test fixture extension {name}
            class EXT_{name.title()}:
                name = "{name}"
                version = "{version}"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return ext_dir


@pytest.fixture
def isolated_extensions_root(tmp_path: Path):
    """Point ``lib.Paths`` at a tmp dir for the duration of the test."""
    root = tmp_path / "extensions_root"
    root.mkdir()
    set_extensions_root(str(root))
    try:
        yield root
    finally:
        set_extensions_root(None)


@pytest.fixture
def empty_registry(isolated_extensions_root: Path) -> ExtensionRegistry:
    return ExtensionRegistry(extensions_csv="", extensions_path=str(isolated_extensions_root))


def test_install_from_local_dir(
    tmp_path: Path,
    isolated_extensions_root: Path,
    empty_registry: ExtensionRegistry,
) -> None:
    """Installing from a local directory copies into extensions root and registers."""
    src = tmp_path / "incoming"
    src.mkdir()
    _write_extension(src, "demo", version="1.2.3")

    result: InstallResult = install_from_manifest(
        src / "demo", registry=empty_registry, run_migrations=False
    )
    assert result.success, result.error_detail
    assert result.name == "demo"
    assert result.version == "1.2.3"
    assert (isolated_extensions_root / "demo" / "manifest.toml").exists()
    assert (isolated_extensions_root / "demo" / "EXT_Demo.py").exists()
    assert empty_registry.loaded_extensions["demo"] == "1.2.3"


def test_install_rejects_existing_directory_without_overwrite(
    tmp_path: Path,
    isolated_extensions_root: Path,
    empty_registry: ExtensionRegistry,
) -> None:
    """Re-installing without ``overwrite=True`` fails with a clear error."""
    src = tmp_path / "incoming"
    src.mkdir()
    _write_extension(src, "demo")
    (isolated_extensions_root / "demo").mkdir()

    result = install_from_manifest(
        src / "demo", registry=empty_registry, run_migrations=False
    )
    assert not result.success
    assert "already exists" in (result.error_detail or "")


def test_install_overwrite_replaces(
    tmp_path: Path,
    isolated_extensions_root: Path,
    empty_registry: ExtensionRegistry,
) -> None:
    """``overwrite=True`` replaces the existing directory."""
    src = tmp_path / "incoming"
    src.mkdir()
    _write_extension(src, "demo", version="2.0.0")
    (isolated_extensions_root / "demo").mkdir()
    (isolated_extensions_root / "demo" / "stale.txt").write_text("old", encoding="utf-8")

    result = install_from_manifest(
        src / "demo",
        registry=empty_registry,
        run_migrations=False,
        overwrite=True,
    )
    assert result.success, result.error_detail
    assert not (isolated_extensions_root / "demo" / "stale.txt").exists()
    assert (isolated_extensions_root / "demo" / "manifest.toml").exists()


def test_install_from_targz(
    tmp_path: Path,
    isolated_extensions_root: Path,
    empty_registry: ExtensionRegistry,
) -> None:
    """Installing from a .tar.gz archive succeeds."""
    staging = tmp_path / "staging"
    staging.mkdir()
    _write_extension(staging, "tarred")

    archive_path = tmp_path / "tarred.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tf:
        tf.add(staging / "tarred", arcname="tarred")

    result = install_from_manifest(
        archive_path, registry=empty_registry, run_migrations=False
    )
    assert result.success, result.error_detail
    assert (isolated_extensions_root / "tarred" / "manifest.toml").exists()


def test_install_from_zip(
    tmp_path: Path,
    isolated_extensions_root: Path,
    empty_registry: ExtensionRegistry,
) -> None:
    """Installing from a .zip archive succeeds."""
    staging = tmp_path / "staging"
    staging.mkdir()
    _write_extension(staging, "zipped")

    archive_path = tmp_path / "zipped.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        for f in (staging / "zipped").rglob("*"):
            zf.write(f, f.relative_to(staging))

    result = install_from_manifest(
        archive_path, registry=empty_registry, run_migrations=False
    )
    assert result.success, result.error_detail
    assert (isolated_extensions_root / "zipped" / "manifest.toml").exists()


def test_install_from_file_url(
    tmp_path: Path,
    isolated_extensions_root: Path,
    empty_registry: ExtensionRegistry,
) -> None:
    """Installing via a ``file://`` URL exercises the URL fetch path."""
    staging = tmp_path / "staging"
    staging.mkdir()
    _write_extension(staging, "urlfetched")

    archive_path = tmp_path / "urlfetched.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tf:
        tf.add(staging / "urlfetched", arcname="urlfetched")

    url = archive_path.resolve().as_uri()  # file:///...
    result = install_from_manifest(
        url, registry=empty_registry, run_migrations=False
    )
    assert result.success, result.error_detail
    assert (isolated_extensions_root / "urlfetched" / "manifest.toml").exists()


def test_install_fails_without_manifest(
    tmp_path: Path,
    isolated_extensions_root: Path,
    empty_registry: ExtensionRegistry,
) -> None:
    """Source directory without manifest.toml is rejected."""
    src = tmp_path / "no_manifest"
    src.mkdir()
    (src / "EXT_X.py").write_text("# stub\n", encoding="utf-8")

    result = install_from_manifest(
        src, registry=empty_registry, run_migrations=False
    )
    assert not result.success
    assert "manifest.toml" in (result.error_detail or "")


def test_install_fails_on_missing_required_dep(
    tmp_path: Path,
    isolated_extensions_root: Path,
    empty_registry: ExtensionRegistry,
) -> None:
    """A required extension_dependency that is not in the registry fails fast."""
    src = tmp_path / "incoming"
    src.mkdir()
    _write_extension(
        src,
        "needsdep",
        extension_dependencies=[{"name": "missing_other", "optional": False}],
    )
    result = install_from_manifest(
        src / "needsdep", registry=empty_registry, run_migrations=False
    )
    assert not result.success
    assert "missing_other" in (result.error_detail or "")


def test_install_optional_dep_missing_does_not_block(
    tmp_path: Path,
    isolated_extensions_root: Path,
    empty_registry: ExtensionRegistry,
) -> None:
    """Optional extension_dependency missing from registry does NOT fail install."""
    src = tmp_path / "incoming"
    src.mkdir()
    _write_extension(
        src,
        "optdep",
        extension_dependencies=[{"name": "absent_opt", "optional": True}],
    )
    result = install_from_manifest(
        src / "optdep", registry=empty_registry, run_migrations=False
    )
    assert result.success, result.error_detail


def test_install_blocks_oversized_download(
    tmp_path: Path,
    isolated_extensions_root: Path,
    empty_registry: ExtensionRegistry,
) -> None:
    """An archive larger than ``max_download_bytes`` is refused."""
    staging = tmp_path / "staging"
    staging.mkdir()
    _write_extension(staging, "biggie")

    archive_path = tmp_path / "biggie.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tf:
        tf.add(staging / "biggie", arcname="biggie")

    url = archive_path.resolve().as_uri()
    # Set an absurdly low cap so the download aborts immediately.
    result = install_from_manifest(
        url,
        registry=empty_registry,
        run_migrations=False,
        max_download_bytes=10,
    )
    assert not result.success
    assert "exceeded max size" in (result.error_detail or "")


def test_uninstall_via_directory_removal_drops_from_registry(
    tmp_path: Path,
    isolated_extensions_root: Path,
    empty_registry: ExtensionRegistry,
) -> None:
    """Removing the extension dir from disk and rebuilding the registry drops it.

    Documented behavior: uninstall is performed by removing the extension
    directory and triggering SIGHUP / ``rebuild_registry``. Migrations are
    intentionally LEFT as-is -- we never drop tables -- so previously-
    written user data is preserved across uninstall + reinstall cycles.
    """
    from serverframework.extensions.HotReload import rebuild_registry

    src = tmp_path / "incoming"
    src.mkdir()
    _write_extension(src, "removable")
    install_from_manifest(
        src / "removable", registry=empty_registry, run_migrations=False
    )
    assert "removable" in empty_registry.loaded_extensions

    # Operator removes the directory.
    import shutil
    shutil.rmtree(isolated_extensions_root / "removable")

    new_registry, diff = rebuild_registry(empty_registry, run_migrations=False)
    assert "removable" in diff.removed
    assert "removable" not in new_registry.loaded_extensions
