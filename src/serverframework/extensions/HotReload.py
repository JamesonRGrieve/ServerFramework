"""Manifest-driven registry rebuild on SIGHUP (Item 20).

The refinement at the bottom of Item 20 narrows the original ambition: true
in-process hot reload of *code* (importlib.reload + class-identity
preservation across SQLAlchemy mappers, Pydantic validators, Strawberry
schemas, etc.) is out of scope. Instead, SIGHUP triggers:

    1. Walk the configured extensions root.
    2. Compute ``RegistryDiff`` against the live registry's
       ``loaded_extensions`` snapshot (added / removed / changed_version).
    3. Build a fresh ``ExtensionRegistry`` from the new on-disk state.
    4. The supervisor (systemd / k8s / our ``app.py`` wrapper) interprets
       the SIGHUP either as a clean process exit (graceful-restart
       fallback) or as a same-process rebind. Both modes share the same
       diff computation.

Removed extensions: their hooks/routers/services are torn down by
discarding the old registry object. The new registry only registers
what is currently on disk, so a re-bound FastAPI ``app`` after
``rebuild_registry`` reflects exactly the new state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

from serverframework.extensions.Manifest import (
    ExtensionManifest,
    find_manifest,
    load_manifest,
)
from serverframework.lib.Logging import logger
from serverframework.lib.Paths import extensions_dir as _resolve_extensions_dir

if TYPE_CHECKING:
    from serverframework.extensions.AbstractExtensionProvider import ExtensionRegistry


@dataclass
class RegistryDiff:
    """Difference between the previous registry snapshot and the new disk state."""

    added: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    changed_version: List[Tuple[str, str, str]] = field(default_factory=list)
    unchanged: List[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.changed_version)

    def summary(self) -> str:
        parts = []
        if self.added:
            parts.append(f"added={self.added}")
        if self.removed:
            parts.append(f"removed={self.removed}")
        if self.changed_version:
            parts.append(
                "changed_version=" +
                ", ".join(f"{n}({old}->{new})" for n, old, new in self.changed_version)
            )
        if not parts:
            return "no changes"
        return "; ".join(parts)


def discover_on_disk_versions(
    extensions_root: Optional[str] = None,
) -> Dict[str, str]:
    """Walk the extensions root and return ``{name: version}``.

    Versions come from ``manifest.toml`` when present. When absent, the
    extension is still listed (with version ``"unknown"``) so SIGHUP
    keeps working for legacy extensions.
    """
    root = Path(_resolve_extensions_dir(extensions_root))
    out: Dict[str, str] = {}
    if not root.exists():
        return out
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("__") or child.name.startswith("."):
            continue
        # Only treat directories that LOOK like extensions: either contain
        # an EXT_*.py or a manifest.toml. This avoids picking up unrelated
        # subdirectories (e.g. a stray "node_modules") under the root.
        manifest_path = find_manifest(child)
        ext_files = list(child.glob("EXT_*.py"))
        if manifest_path is None and not ext_files:
            continue
        if manifest_path is not None:
            try:
                m: ExtensionManifest = load_manifest(manifest_path)
                out[m.name] = m.version
                continue
            except Exception as e:
                logger.warning(
                    f"manifest.toml at {manifest_path} unreadable, falling back: {e}"
                )
        out[child.name] = "unknown"
    return out


def compute_diff(
    previous: Dict[str, str],
    current: Dict[str, str],
) -> RegistryDiff:
    """Compute added/removed/changed-version diff between two snapshots."""
    prev_names: Set[str] = set(previous)
    cur_names: Set[str] = set(current)
    diff = RegistryDiff()
    diff.added = sorted(cur_names - prev_names)
    diff.removed = sorted(prev_names - cur_names)
    for name in sorted(prev_names & cur_names):
        old = previous[name]
        new = current[name]
        if old != new:
            diff.changed_version.append((name, old, new))
        else:
            diff.unchanged.append(name)
    return diff


def rebuild_registry(
    previous_registry: "ExtensionRegistry",
    *,
    extensions_csv: Optional[str] = None,
    run_migrations: bool = True,
) -> Tuple["ExtensionRegistry", RegistryDiff]:
    """Rebuild an ``ExtensionRegistry`` from the current on-disk state.

    Args:
        previous_registry: The currently-active registry. Its
            ``loaded_extensions`` snapshot is used as the "before" side
            of the diff.
        extensions_csv: Override CSV of extension names to load. When
            ``None``, defaults to the union of the previous registry's
            extensions and any extensions newly discovered on disk via
            their manifest.
        run_migrations: When True, run any pending migrations for newly
            added or version-changed extensions (best-effort).

    Returns:
        ``(new_registry, diff)`` -- the fresh registry and the diff vs the
        previous snapshot. Removed extensions are simply absent from the
        new registry; their hooks/routers/services live on the old object
        which the caller is expected to drop.
    """
    from serverframework.extensions.AbstractExtensionProvider import ExtensionRegistry

    previous_snapshot = dict(previous_registry.loaded_extensions)
    extensions_path = previous_registry.extensions_path
    on_disk = discover_on_disk_versions(extensions_path)

    if extensions_csv is None:
        # Default: keep the same set of extensions the operator originally
        # configured, plus any newly added on disk. The csv ordering
        # matches the union of (previous registry extensions) + sorted
        # additions.
        previously_configured = [e.name for e in previous_registry.extensions]
        additions = sorted(set(on_disk.keys()) - set(previously_configured))
        configured_names = previously_configured + additions
        extensions_csv = ",".join(configured_names)

    new_registry = ExtensionRegistry(
        extensions_csv,
        extensions_path=extensions_path,
    )

    diff = compute_diff(previous_snapshot, dict(new_registry.loaded_extensions))

    if run_migrations and (diff.added or diff.changed_version):
        _run_migrations_for(diff)

    logger.info(f"Registry rebuild complete: {diff.summary()}")
    return new_registry, diff


def _run_migrations_for(diff: RegistryDiff) -> None:
    """Best-effort migration runner for diff additions/version bumps."""
    try:
        from serverframework.database.migrations.Migration import MigrationManager
    except Exception as e:  # pragma: no cover -- defensive
        logger.warning(f"MigrationManager unavailable, skipping migrations: {e}")
        return
    mgr = MigrationManager()
    targets = list(diff.added) + [n for n, _, _ in diff.changed_version]
    for name in targets:
        try:
            mgr.run_extension_migration(name, "upgrade", target="head")
        except Exception as e:
            logger.warning(
                f"Migration upgrade for {name} failed: {e}", exc_info=True
            )
