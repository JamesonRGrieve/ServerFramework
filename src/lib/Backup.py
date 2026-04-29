"""Backup, restore, and point-in-time recovery primitive (Item 79).

This module owns the framework's backup contract. It is intentionally minimal in
external dependencies: callers wire concrete backup targets and backup
commands at composition time so the framework does not assume a particular
object-storage SDK or DB engine at module load.

RTO / RPO concepts
------------------
- **RTO (Recovery Time Objective):** the upper bound on how long the system
  may take to come back up after data loss. Measured by ``run_drill`` (a
  successful drill demonstrates the restore path completes within the
  documented bound).
- **RPO (Recovery Point Objective):** the upper bound on how much data may be
  lost. Measured by ``backup_age_seconds`` — the time since the last
  successful snapshot. For Critical-tier tables, the deployment's RPO is the
  worst-case ``backup_age_seconds`` plus the in-flight transaction window.

Per-table classification
------------------------
Each table declares a ``BackupClass`` via :func:`register_backup_class`:

- ``critical``: data loss is unacceptable. Included in nightly snapshots and
  (for engines that support it) continuous WAL archiving.
- ``recoverable``: data loss is recoverable from upstream sources (e.g.
  external entities resolved via federation). Included in nightly snapshots
  only.
- ``ephemeral``: cache/session/sticky-routing state. Excluded from backups.

Architecture
------------
``BackupTarget`` is the abstract object-storage destination. ``BackupCommand``
is the abstract DB-engine-specific dump/restore primitive. ``BackupService``
(a :class:`logic.AbstractService.ScheduledService` flavor) drives them on a
schedule. ``RestoreDrillService`` performs the restore-and-verify drill
non-destructively into a scratch DB.

This module's S3 target is a stub that points to Item 43 (object-storage
abstract) for the production implementation; the local-filesystem target is
the reference implementation usable in tests and self-hosted deployments.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from lib.Logging import logger


# ---------------------------------------------------------------------------
# Backup classification
# ---------------------------------------------------------------------------


BackupClass = Literal["critical", "recoverable", "ephemeral"]


class BackupSpec(BaseModel):
    """Per-table backup classification."""

    model_config = ConfigDict(frozen=True)

    table_name: str
    backup_class: BackupClass


# Module-level registry. Tables register themselves at import time via
# ``register_backup_class``. The framework reads this map when constructing
# the snapshot inclusion list.
BACKUP_REGISTRY: Dict[str, BackupClass] = {}


def register_backup_class(table_name: str, backup_class: BackupClass) -> None:
    """Register or overwrite a table's backup class."""
    BACKUP_REGISTRY[table_name] = backup_class


def get_tables_for_class(backup_class: BackupClass) -> List[str]:
    """Return all table names registered as ``backup_class``."""
    return sorted(t for t, c in BACKUP_REGISTRY.items() if c == backup_class)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


# Module-level mutable mapping that operational tooling reads to expose
# Prometheus gauges. Updated by ``BackupService`` and ``RestoreDrillService``.
_backup_metrics: Dict[str, float] = {
    "backup_age_seconds": float("inf"),
    "last_successful_restore_drill_age_seconds": float("inf"),
}


def get_backup_metrics() -> Dict[str, float]:
    """Return a snapshot of the current backup metric values."""
    return dict(_backup_metrics)


def _set_metric(name: str, value: float) -> None:
    _backup_metrics[name] = value


# ---------------------------------------------------------------------------
# BackupTarget
# ---------------------------------------------------------------------------


class BackupTarget(ABC):
    """Abstract destination for a backup artifact.

    Implementations stream the backup payload up/down without buffering the
    full artifact in memory. The contract intentionally exposes a binary
    stream so any DB-engine ``BackupCommand`` (text dump, binary basebackup,
    or compressed tarball) can use the same target.
    """

    @abstractmethod
    def upload(self, stream: IO[bytes], key: str) -> str:
        """Upload ``stream`` under ``key``. Returns the canonical URL or path."""

    @abstractmethod
    def download(self, key: str) -> IO[bytes]:
        """Open ``key`` for reading. Caller must close the returned stream."""

    @abstractmethod
    def list_keys(self, prefix: str = "") -> List[str]:
        """Return all keys whose path starts with ``prefix``."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove ``key`` from the target."""


class LocalFilesystemBackupTarget(BackupTarget):
    """Reference :class:`BackupTarget` that writes to a local directory.

    Suitable for self-hosted deployments and the reference restore drill in
    CI. Production deployments should use the object-storage target wired via
    Item 43.
    """

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        # Reject path-traversal in ``key``.
        candidate = (self.path / key).resolve()
        try:
            candidate.relative_to(self.path.resolve())
        except ValueError as e:
            raise ValueError(f"key {key!r} escapes backup target root") from e
        return candidate

    def upload(self, stream: IO[bytes], key: str) -> str:
        dest = self._resolve(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("wb") as fh:
            shutil.copyfileobj(stream, fh)
        return str(dest)

    def download(self, key: str) -> IO[bytes]:
        return self._resolve(key).open("rb")

    def list_keys(self, prefix: str = "") -> List[str]:
        root = self.path
        keys: List[str] = []
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            rel = str(p.relative_to(root))
            if rel.startswith(prefix):
                keys.append(rel)
        return sorted(keys)

    def delete(self, key: str) -> None:
        target = self._resolve(key)
        if target.exists():
            target.unlink()


class S3BackupTarget(BackupTarget):
    """Stub that defers to Item 43's object-storage abstract.

    This class exists so callers can declare their intent today, but every
    method raises :class:`NotImplementedError`. When Item 43 lands, this
    stub will be replaced with a thin adapter over the framework's
    object-storage interface.
    """

    def __init__(self, bucket: str, prefix: str = "") -> None:
        self.bucket = bucket
        self.prefix = prefix

    def _not_yet(self) -> NotImplementedError:
        return NotImplementedError(
            "S3BackupTarget is a stub; concrete object-storage support lands "
            "with Item 43 (object-storage abstract). Use "
            "LocalFilesystemBackupTarget for now or wire Item 43's adapter."
        )

    def upload(self, stream: IO[bytes], key: str) -> str:  # pragma: no cover - stub
        raise self._not_yet()

    def download(self, key: str) -> IO[bytes]:  # pragma: no cover - stub
        raise self._not_yet()

    def list_keys(self, prefix: str = "") -> List[str]:  # pragma: no cover - stub
        raise self._not_yet()

    def delete(self, key: str) -> None:  # pragma: no cover - stub
        raise self._not_yet()


# ---------------------------------------------------------------------------
# BackupCommand
# ---------------------------------------------------------------------------


class BackupCommand(ABC):
    """Engine-specific dump/restore primitive."""

    @abstractmethod
    def dump(self) -> bytes:
        """Produce a snapshot artifact for the configured DB."""

    @abstractmethod
    def restore(self, artifact: bytes, target_db: str) -> None:
        """Restore ``artifact`` into ``target_db`` (a scratch database identifier)."""

    @abstractmethod
    def smoke_test(self, target_db: str) -> bool:
        """Run a minimal post-restore query. Return True on success."""


class PgDumpBackupCommand(BackupCommand):
    """Postgres reference implementation using ``pg_dump`` / ``pg_restore``.

    Builds an in-process subprocess invocation. The DB connection details
    are passed as a libpq URI to keep the surface small. For continuous
    WAL archiving (the production option), wire ``pg_basebackup`` plus a
    separate continuous-archive job — this class is the nightly-snapshot
    reference path only.
    """

    def __init__(
        self,
        database_url: str,
        pg_dump_path: str = "pg_dump",
        psql_path: str = "psql",
    ) -> None:
        self.database_url = database_url
        self.pg_dump_path = pg_dump_path
        self.psql_path = psql_path

    def dump(self) -> bytes:
        result = subprocess.run(  # noqa: S603 - explicit args, no shell
            [self.pg_dump_path, self.database_url],
            check=True,
            capture_output=True,
        )
        return result.stdout

    def restore(self, artifact: bytes, target_db: str) -> None:
        subprocess.run(  # noqa: S603
            [self.psql_path, target_db],
            input=artifact,
            check=True,
            capture_output=True,
        )

    def smoke_test(self, target_db: str) -> bool:
        result = subprocess.run(  # noqa: S603
            [self.psql_path, target_db, "-c", "SELECT 1"],
            check=False,
            capture_output=True,
        )
        return result.returncode == 0


class SqliteBackupCommand(BackupCommand):
    """SQLite reference implementation using ``sqlite3 .dump``.

    The dump is text-mode SQL; restore re-executes the SQL into a fresh
    DB file. Both directions are streamable but small enough for the
    common SQLite deployments we ship to.
    """

    def __init__(self, db_path: str, sqlite3_path: str = "sqlite3") -> None:
        self.db_path = db_path
        self.sqlite3_path = sqlite3_path

    def dump(self) -> bytes:
        result = subprocess.run(  # noqa: S603
            [self.sqlite3_path, self.db_path, ".dump"],
            check=True,
            capture_output=True,
        )
        return result.stdout

    def restore(self, artifact: bytes, target_db: str) -> None:
        # Ensure target is empty.
        if os.path.exists(target_db):
            os.remove(target_db)
        subprocess.run(  # noqa: S603
            [self.sqlite3_path, target_db],
            input=artifact,
            check=True,
            capture_output=True,
        )

    def smoke_test(self, target_db: str) -> bool:
        result = subprocess.run(  # noqa: S603
            [self.sqlite3_path, target_db, "SELECT 1;"],
            check=False,
            capture_output=True,
        )
        return result.returncode == 0


# ---------------------------------------------------------------------------
# Service base resolution
# ---------------------------------------------------------------------------


def _resolve_service_base() -> Any:
    """Return ``ScheduledService`` if available, else fall back to ``AbstractService``.

    The fallback path lets this module be importable even if Batch C has not
    yet finalized the scheduled service flavor. Resolving lazily also keeps
    the module load graph free of a strict logic-layer dependency.
    """
    try:
        from logic.AbstractService import ScheduledService

        return ScheduledService
    except ImportError:  # pragma: no cover - defensive
        from logic.AbstractService import AbstractService

        return AbstractService


# ---------------------------------------------------------------------------
# BackupService
# ---------------------------------------------------------------------------


_BackupServiceBase = _resolve_service_base()


class BackupService(_BackupServiceBase):  # type: ignore[misc, valid-type]
    """Scheduled service that drives a :class:`BackupCommand` into a target.

    On each tick the service runs ``command.dump()`` and uploads the artifact
    under a timestamped key. ``backup_age_seconds`` is reset on success.
    """

    def __init__(
        self,
        requester_id: str,
        command: BackupCommand,
        target: BackupTarget,
        key_prefix: str = "snapshots",
        interval_seconds: int = 86_400,  # nightly default
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("interval_seconds", interval_seconds)
        super().__init__(requester_id=requester_id, **kwargs)
        self.command = command
        self.target = target
        self.key_prefix = key_prefix
        self.last_backup_at: Optional[datetime] = None
        self.last_backup_key: Optional[str] = None

    def _build_key(self) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{self.key_prefix}/snapshot-{ts}-{uuid.uuid4().hex[:8]}.bin"

    def take_backup(self) -> str:
        """Synchronous take-a-backup entrypoint. Returns the key uploaded."""
        artifact = self.command.dump()
        key = self._build_key()
        stream = io.BytesIO(artifact)
        url = self.target.upload(stream, key)
        now = datetime.now(timezone.utc)
        self.last_backup_at = now
        self.last_backup_key = key
        _set_metric("backup_age_seconds", 0.0)
        logger.info(
            f"BackupService: snapshot uploaded key={key} url={url} bytes={len(artifact)}"
        )
        return key

    async def update(self) -> None:
        try:
            self.take_backup()
        except Exception as e:  # noqa: BLE001
            logger.error(f"BackupService: snapshot failed: {e}")
            raise

    def refresh_metric(self) -> None:
        """Update ``backup_age_seconds`` from ``last_backup_at`` (or infinity)."""
        if self.last_backup_at is None:
            _set_metric("backup_age_seconds", float("inf"))
            return
        age = (datetime.now(timezone.utc) - self.last_backup_at).total_seconds()
        _set_metric("backup_age_seconds", age)


# ---------------------------------------------------------------------------
# RestoreDrillService
# ---------------------------------------------------------------------------


@dataclass
class DrillReport:
    """Outcome of a single restore drill."""

    success: bool
    duration_seconds: float
    smoke_test_pass: bool
    error: Optional[str] = None
    restored_key: Optional[str] = None
    started_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "duration_seconds": self.duration_seconds,
            "smoke_test_pass": self.smoke_test_pass,
            "error": self.error,
            "restored_key": self.restored_key,
            "started_at": self.started_at.isoformat(),
        }


class RestoreDrillService(_BackupServiceBase):  # type: ignore[misc, valid-type]
    """Periodic restore drill: take the latest backup, restore, smoke-test, discard.

    The drill is non-destructive against production: ``command.restore`` is
    invoked with a scratch DB identifier supplied at construction. The drill
    cleans up the scratch DB on completion regardless of outcome.
    """

    def __init__(
        self,
        requester_id: str,
        command: BackupCommand,
        scratch_db: str,
        target: Optional[BackupTarget] = None,
        key_prefix: str = "snapshots",
        interval_seconds: int = 30 * 86_400,  # monthly default
        cleanup: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("interval_seconds", interval_seconds)
        super().__init__(requester_id=requester_id, **kwargs)
        self.command = command
        self.scratch_db = scratch_db
        self.target = target
        self.key_prefix = key_prefix
        self._cleanup = cleanup
        self.last_drill_at: Optional[datetime] = None
        self.last_drill_report: Optional[DrillReport] = None

    def _latest_key(self, target: BackupTarget) -> Optional[str]:
        keys = target.list_keys(prefix=self.key_prefix)
        return keys[-1] if keys else None

    def run_drill(self, target: Optional[BackupTarget] = None) -> DrillReport:
        """Run a single drill against ``target``. Returns a :class:`DrillReport`."""
        active_target = target if target is not None else self.target
        if active_target is None:
            return DrillReport(
                success=False,
                duration_seconds=0.0,
                smoke_test_pass=False,
                error="no backup target configured",
            )
        started = time.monotonic()
        report = DrillReport(
            success=False,
            duration_seconds=0.0,
            smoke_test_pass=False,
        )
        try:
            key = self._latest_key(active_target)
            if key is None:
                report.error = "no backup keys present at target"
                return report
            with active_target.download(key) as stream:
                artifact = stream.read()
            self.command.restore(artifact, self.scratch_db)
            smoke = self.command.smoke_test(self.scratch_db)
            report.smoke_test_pass = smoke
            report.success = bool(smoke)
            report.restored_key = key
        except Exception as e:  # noqa: BLE001
            report.error = f"{type(e).__name__}: {e}"
        finally:
            report.duration_seconds = time.monotonic() - started
            try:
                if self._cleanup is not None:
                    self._cleanup(self.scratch_db)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"RestoreDrillService cleanup failed: {e}")
            now = datetime.now(timezone.utc)
            self.last_drill_at = now
            self.last_drill_report = report
            if report.success:
                _set_metric("last_successful_restore_drill_age_seconds", 0.0)
        return report

    async def update(self) -> None:
        report = self.run_drill()
        if not report.success:
            raise RuntimeError(
                f"RestoreDrillService: drill failed: {report.error or 'smoke test failed'}"
            )

    def refresh_metric(self) -> None:
        if self.last_drill_at is None or (
            self.last_drill_report and not self.last_drill_report.success
        ):
            return
        age = (datetime.now(timezone.utc) - self.last_drill_at).total_seconds()
        _set_metric("last_successful_restore_drill_age_seconds", age)


__all__ = [
    "BackupClass",
    "BackupSpec",
    "BACKUP_REGISTRY",
    "register_backup_class",
    "get_tables_for_class",
    "BackupTarget",
    "LocalFilesystemBackupTarget",
    "S3BackupTarget",
    "BackupCommand",
    "PgDumpBackupCommand",
    "SqliteBackupCommand",
    "BackupService",
    "RestoreDrillService",
    "DrillReport",
    "get_backup_metrics",
]
