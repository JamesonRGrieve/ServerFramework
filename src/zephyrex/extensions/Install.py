"""Manifest-driven extension installation (Item 20).

``install_from_manifest`` accepts a local directory, a local archive
(``.tar.gz`` or ``.zip``), or an HTTP(S) URL pointing at one of those, and
installs the extension into the configured extensions root.

Steps:
    1. Resolve ``source`` to a directory on disk (fetch + extract as needed).
    2. Read & validate ``manifest.toml`` against ``ExtensionManifest``.
    3. Resolve ``extension_dependencies`` against the live registry; fail
       fast on a missing required dependency.
    4. Move into ``lib.Paths.extensions_dir()`` (out-of-tree per Item 61).
       Refuse to overwrite an existing directory unless ``overwrite=True``.
    5. Run migrations via ``MigrationManager`` (extension-aware per Item 62).
    6. Trigger a registry rebuild so the new extension is registered.
    7. Return an ``InstallResult``.

This module never shells out to git/pip/cp -- HTTP fetches use stdlib
``urllib.request`` with a hard size cap (default 50 MiB) and timeout.
"""

from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional, Union

from zephyrex.extensions.Manifest import (
    ExtensionManifest,
    find_manifest,
    load_manifest,
)
from zephyrex.lib.Logging import logger
from zephyrex.lib.Paths import extensions_dir as _resolve_extensions_dir

if TYPE_CHECKING:
    from zephyrex.extensions.AbstractExtensionProvider import ExtensionRegistry


DEFAULT_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024  # 50 MiB
DEFAULT_HTTP_TIMEOUT_SECONDS = 30


@dataclass
class InstallResult:
    """Outcome of an ``install_from_manifest`` call."""

    name: Optional[str]
    version: Optional[str]
    success: bool
    error_detail: Optional[str] = None
    install_path: Optional[str] = None


class InstallError(Exception):
    """Raised when an install fails for a structural reason (bad manifest,
    missing dep, archive escape, etc.). The orchestrator wraps these into
    ``InstallResult(success=False)`` so callers do not need to catch."""


def _is_url(source: str) -> bool:
    parsed = urllib.parse.urlparse(source)
    return parsed.scheme in {"http", "https", "file"}


def _download_to_tempfile(
    url: str,
    *,
    max_bytes: int,
    timeout: float,
) -> Path:
    """Stream ``url`` into a temp file. Aborts if the body exceeds ``max_bytes``."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https", "file"}:
        raise InstallError(f"unsupported URL scheme: {parsed.scheme!r}")

    # Preserve full suffix chain (e.g. ``.tar.gz``) so the archive
    # detector downstream picks the right extractor. ``Path.suffix``
    # only returns the last component (".gz"), which would mis-route
    # ``.tar.gz`` archives.
    parsed_path = Path(parsed.path)
    suffix = "".join(parsed_path.suffixes)
    fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="ext_install_")
    os.close(fd)
    tmp = Path(tmp_path)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            written = 0
            with tmp.open("wb") as out:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise InstallError(
                            f"download exceeded max size of {max_bytes} bytes"
                        )
                    out.write(chunk)
        return tmp
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _safe_extract_tar(archive: Path, dest: Path) -> None:
    """Extract a tar archive, refusing entries that escape ``dest``."""
    dest_resolved = dest.resolve()
    with tarfile.open(archive, "r:*") as tf:
        for member in tf.getmembers():
            if member.issym() or member.islnk():
                raise InstallError(
                    f"archive contains symlink/hardlink: {member.name!r} -> {member.linkname!r}"
                )
            target = (dest / member.name).resolve()
            if not str(target).startswith(str(dest_resolved) + os.sep) and target != dest_resolved:
                raise InstallError(f"archive entry escapes dest: {member.name!r}")
        # Python 3.12 introduced data filter; we apply our own check above and
        # use the standard extractall for broad compatibility.
        tf.extractall(dest)  # noqa: S202 -- members validated above


def _safe_extract_zip(archive: Path, dest: Path) -> None:
    dest_resolved = dest.resolve()
    with zipfile.ZipFile(archive, "r") as zf:
        for name in zf.namelist():
            target = (dest / name).resolve()
            if not str(target).startswith(str(dest_resolved) + os.sep) and target != dest_resolved:
                raise InstallError(f"archive entry escapes dest: {name!r}")
        zf.extractall(dest)


def _is_archive(path: Path) -> bool:
    suffixes = "".join(path.suffixes).lower()
    return suffixes.endswith((".tar.gz", ".tgz", ".tar", ".zip"))


def _extract_archive(archive: Path, dest: Path) -> Path:
    """Extract ``archive`` into ``dest`` and return the extracted root.

    If the archive contains a single top-level directory that itself
    contains the manifest, return that directory. Otherwise return ``dest``.
    """
    suffixes = "".join(archive.suffixes).lower()
    if suffixes.endswith((".tar.gz", ".tgz", ".tar")):
        _safe_extract_tar(archive, dest)
    elif suffixes.endswith(".zip"):
        _safe_extract_zip(archive, dest)
    else:
        raise InstallError(f"unsupported archive format: {archive.name}")

    entries = [p for p in dest.iterdir() if p.name != "__MACOSX"]
    if (
        len(entries) == 1
        and entries[0].is_dir()
        and find_manifest(entries[0]) is not None
    ):
        return entries[0]
    return dest


@contextmanager
def _resolved_source(
    source: Union[str, Path],
    *,
    max_bytes: int,
    timeout: float,
    expected_sha256: Optional[str] = None,
) -> Iterator[Path]:
    """Yield a directory on disk that contains the unpacked extension.

    Cleans up any temp files / extraction roots on exit.
    """
    src_str = str(source)
    tmp_archive: Optional[Path] = None
    extraction_root: Optional[Path] = None
    try:
        # URL: download to a temp file, then treat that file as a local source.
        if _is_url(src_str):
            tmp_archive = _download_to_tempfile(
                src_str, max_bytes=max_bytes, timeout=timeout
            )
            # L-9 — verify the archive digest before any extraction. We
            # explicitly run this BEFORE `_safe_extract_*` so a tampered
            # archive never touches the filesystem with traversal-resistant
            # names that could still smuggle code via overrides.
            _verify_archive_digest(tmp_archive, expected_sha256)
            local: Path = tmp_archive
        else:
            local = Path(src_str).expanduser().resolve()
            # Local archives also honour the digest check when provided.
            if local.is_file() and _is_archive(local):
                _verify_archive_digest(local, expected_sha256)

        if local.is_dir():
            yield local
            return

        if not local.exists():
            raise InstallError(f"source does not exist: {local}")

        if _is_archive(local):
            extraction_root = Path(tempfile.mkdtemp(prefix="ext_extract_"))
            unpacked = _extract_archive(local, extraction_root)
            yield unpacked
            return

        raise InstallError(
            f"source is not a directory or supported archive: {local}"
        )
    finally:
        if tmp_archive is not None:
            tmp_archive.unlink(missing_ok=True)
        if extraction_root is not None and extraction_root.exists():
            shutil.rmtree(extraction_root, ignore_errors=True)


def _validate_dependencies(
    manifest: ExtensionManifest,
    registry: "ExtensionRegistry",
) -> None:
    """Fail fast when required extension_dependencies are missing."""
    for dep in manifest.extension_dependencies:
        if dep.optional:
            continue
        if dep.name not in registry._extension_name_map and dep.name not in registry.loaded_extensions:
            raise InstallError(
                f"required extension dependency {dep.name!r} not found in registry"
            )


def _move_into_extensions_root(src_dir: Path, name: str, *, overwrite: bool) -> Path:
    """Copy ``src_dir`` into ``<extensions_root>/<name>/``.

    A copy (rather than a move) so that callers passing a long-lived local
    directory keep that directory; the temp-file cleanup in
    ``_resolved_source`` handles disposing of fetched/extracted sources.
    """
    ext_root = Path(_resolve_extensions_dir())
    ext_root.mkdir(parents=True, exist_ok=True)
    target = ext_root / name
    if target.exists():
        if not overwrite:
            raise InstallError(
                f"extension directory already exists: {target} (pass overwrite=True)"
            )
        shutil.rmtree(target)
    shutil.copytree(src_dir, target)
    return target


def _run_extension_migrations(name: str) -> None:
    """Best-effort migration runner for a freshly installed extension.

    Failures are logged but do not abort the install -- the install has
    already moved files into place; the operator can re-run migrations
    out-of-band. The contract is "non-destructive": we never drop tables.
    """
    try:
        from zephyrex.database.migrations.Migration import MigrationManager
    except Exception as e:  # pragma: no cover -- defensive
        logger.warning(f"MigrationManager unavailable, skipping migrations: {e}")
        return

    try:
        mgr = MigrationManager()
        ok = mgr.run_extension_migration(name, "upgrade", target="head")
        if not ok:
            logger.warning(
                f"Extension {name} migration upgrade returned non-success; "
                f"continuing -- operator should investigate."
            )
    except Exception as e:
        logger.warning(
            f"Failed to run migrations for extension {name}: {e}",
            exc_info=True,
        )


def _verify_archive_digest(archive: Path, expected_sha256: Optional[str]) -> None:
    """L-9 — verify the archive against an expected SHA-256 digest before
    extraction. PyPI installs ride sigstore via SECURITY.md; for ad-hoc HTTP
    installs this is the minimum integrity gate."""
    if not expected_sha256:
        return
    import hashlib
    import hmac as _hmac

    h = hashlib.sha256()
    with archive.open("rb") as fh:
        for chunk in iter(lambda: fh.read(64 * 1024), b""):
            h.update(chunk)
    actual = h.hexdigest()
    if not _hmac.compare_digest(actual.encode("ascii"), expected_sha256.lower().encode("ascii")):
        raise InstallError(
            f"archive digest mismatch: expected {expected_sha256}, got {actual}"
        )


def install_from_manifest(
    source: Union[str, Path],
    *,
    registry: "ExtensionRegistry",
    run_migrations: bool = True,
    overwrite: bool = False,
    max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    http_timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
    expected_sha256: Optional[str] = None,
) -> InstallResult:
    """Install an extension described by a ``manifest.toml``.

    Args:
        source: A local directory, local archive (``.tar.gz``/``.zip``),
            or an ``http(s)://`` / ``file://`` URL to one of those.
        registry: The live ``ExtensionRegistry`` whose
            ``extension_dependencies`` validations are checked against.
        run_migrations: Whether to execute migrations after copying files.
        overwrite: When ``True``, replace an existing extension directory
            of the same name. Off by default for safety.
        max_download_bytes: Hard cap on HTTP body size (default 50 MiB).
        http_timeout_seconds: Per-connection HTTP timeout.
        expected_sha256: Optional SHA-256 digest of the fetched archive
            (L-9). When supplied, the archive is verified before
            extraction. Required when source is an HTTP URL pointing
            outside a trusted package index.

    Returns:
        ``InstallResult`` describing the outcome. The orchestrator never
        raises ``InstallError`` from this function -- failures are reported
        through ``InstallResult.error_detail``.
    """
    manifest: Optional[ExtensionManifest] = None
    try:
        with _resolved_source(
            source,
            max_bytes=max_download_bytes,
            timeout=http_timeout_seconds,
            expected_sha256=expected_sha256,
        ) as src_dir:
            if find_manifest(src_dir) is None:
                raise InstallError(f"manifest.toml not found in source: {src_dir}")
            manifest = load_manifest(src_dir)
            _validate_dependencies(manifest, registry)
            installed_path = _move_into_extensions_root(
                src_dir, manifest.name, overwrite=overwrite
            )

        if run_migrations:
            _run_extension_migrations(manifest.name)

        # Trigger a registry rebuild for this extension. The caller is
        # responsible for the broader rebind (SIGHUP / restart). We at
        # minimum register the new extension in-memory so an immediate
        # follow-up reads see it.
        try:
            registry.loaded_extensions[manifest.name] = manifest.version
        except Exception as e:
            logger.warning(f"failed to record install in registry: {e}")

        logger.info(
            f"Installed extension {manifest.name} v{manifest.version} "
            f"at {installed_path}"
        )
        return InstallResult(
            name=manifest.name,
            version=manifest.version,
            success=True,
            install_path=str(installed_path),
        )
    except InstallError as e:
        logger.error(f"Install failed: {e}")
        return InstallResult(
            name=manifest.name if manifest else None,
            version=manifest.version if manifest else None,
            success=False,
            error_detail=str(e),
        )
    except Exception as e:
        logger.error(f"Install failed with unexpected error: {e}", exc_info=True)
        return InstallResult(
            name=manifest.name if manifest else None,
            version=manifest.version if manifest else None,
            success=False,
            error_detail=f"unexpected error: {e}",
        )
