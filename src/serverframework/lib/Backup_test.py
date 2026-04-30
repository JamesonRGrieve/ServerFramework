"""Tests for ``lib.Backup`` (Item 79).

Pure-utility tests; no BLL/extension surface is touched. The DB-engine
``BackupCommand`` subclasses are exercised via a fake :class:`BackupCommand`
that does not shell out, since shelling out to ``pg_dump`` / ``sqlite3``
would require external tooling on the test host.
"""

from __future__ import annotations

import asyncio
import io
from typing import List

import pytest

from serverframework.lib.Backup import (
    _backup_metrics,
    BACKUP_REGISTRY,
    BackupClass,
    BackupCommand,
    BackupService,
    BackupSpec,
    BackupTarget,
    DrillReport,
    LocalFilesystemBackupTarget,
    RestoreDrillService,
    S3BackupTarget,
    get_backup_metrics,
    get_tables_for_class,
    register_backup_class,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_registry_and_metrics():
    saved_registry = dict(BACKUP_REGISTRY)
    saved_metrics = dict(_backup_metrics)
    BACKUP_REGISTRY.clear()
    yield
    BACKUP_REGISTRY.clear()
    BACKUP_REGISTRY.update(saved_registry)
    _backup_metrics.clear()
    _backup_metrics.update(saved_metrics)


class FakeBackupCommand(BackupCommand):
    """In-memory fake; does not shell out."""

    def __init__(self, payload: bytes = b"FAKE-DUMP", smoke_pass: bool = True) -> None:
        self.payload = payload
        self.smoke_pass = smoke_pass
        self.dump_calls = 0
        self.restored: List[bytes] = []
        self.smoke_called = 0

    def dump(self) -> bytes:
        self.dump_calls += 1
        return self.payload

    def restore(self, artifact: bytes, target_db: str) -> None:
        self.restored.append(artifact)

    def smoke_test(self, target_db: str) -> bool:
        self.smoke_called += 1
        return self.smoke_pass


# ---------------------------------------------------------------------------
# BackupSpec / registry
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_backup_spec_is_frozen():
    spec = BackupSpec(table_name="users", backup_class="critical")
    with pytest.raises(Exception):
        spec.table_name = "other"


@pytest.mark.unit
def test_register_backup_class_and_lookup():
    register_backup_class("users", "critical")
    register_backup_class("sessions", "ephemeral")
    register_backup_class("federated_orgs", "recoverable")
    assert BACKUP_REGISTRY["users"] == "critical"
    assert get_tables_for_class("critical") == ["users"]
    assert get_tables_for_class("ephemeral") == ["sessions"]
    assert get_tables_for_class("recoverable") == ["federated_orgs"]


@pytest.mark.unit
def test_register_overwrites_existing_class():
    register_backup_class("t", "critical")
    register_backup_class("t", "ephemeral")
    assert BACKUP_REGISTRY["t"] == "ephemeral"


# ---------------------------------------------------------------------------
# LocalFilesystemBackupTarget
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_local_target_round_trip(tmp_path):
    target = LocalFilesystemBackupTarget(str(tmp_path / "backups"))
    target.upload(io.BytesIO(b"hello"), "snapshots/a.bin")
    keys = target.list_keys(prefix="snapshots/")
    assert keys == ["snapshots/a.bin"]
    with target.download("snapshots/a.bin") as fh:
        assert fh.read() == b"hello"


@pytest.mark.unit
def test_local_target_rejects_path_traversal(tmp_path):
    target = LocalFilesystemBackupTarget(str(tmp_path / "backups"))
    with pytest.raises(ValueError):
        target.upload(io.BytesIO(b"x"), "../../etc/passwd")


@pytest.mark.unit
def test_local_target_delete(tmp_path):
    target = LocalFilesystemBackupTarget(str(tmp_path / "backups"))
    target.upload(io.BytesIO(b"x"), "k.bin")
    assert "k.bin" in target.list_keys()
    target.delete("k.bin")
    assert target.list_keys() == []


@pytest.mark.unit
def test_local_target_list_keys_prefix_filtering(tmp_path):
    target = LocalFilesystemBackupTarget(str(tmp_path / "backups"))
    target.upload(io.BytesIO(b"a"), "alpha/1.bin")
    target.upload(io.BytesIO(b"b"), "beta/1.bin")
    assert target.list_keys(prefix="alpha/") == ["alpha/1.bin"]
    assert target.list_keys(prefix="beta/") == ["beta/1.bin"]
    assert sorted(target.list_keys()) == ["alpha/1.bin", "beta/1.bin"]


# ---------------------------------------------------------------------------
# S3BackupTarget stub
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_s3_target_is_stub_with_actionable_error():
    target = S3BackupTarget(bucket="b", prefix="p")
    with pytest.raises(NotImplementedError) as exc:
        target.upload(io.BytesIO(b"x"), "k")
    assert "Item 43" in str(exc.value)


# ---------------------------------------------------------------------------
# BackupService
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_backup_service_take_backup_uploads_and_updates_metric(tmp_path):
    target = LocalFilesystemBackupTarget(str(tmp_path / "backups"))
    cmd = FakeBackupCommand(payload=b"DUMP-1")
    svc = BackupService(
        requester_id="test",
        command=cmd,
        target=target,
        interval_seconds=3600,
    )
    key = svc.take_backup()
    assert cmd.dump_calls == 1
    assert key.startswith("snapshots/snapshot-")
    assert get_backup_metrics()["backup_age_seconds"] == 0.0
    with target.download(key) as fh:
        assert fh.read() == b"DUMP-1"


@pytest.mark.unit
async def test_backup_service_update_runs_take_backup(tmp_path):
    target = LocalFilesystemBackupTarget(str(tmp_path / "backups"))
    cmd = FakeBackupCommand(payload=b"DUMP-2")
    svc = BackupService(
        requester_id="test",
        command=cmd,
        target=target,
        interval_seconds=3600,
    )
    await svc.update()
    assert cmd.dump_calls == 1
    assert svc.last_backup_key is not None


@pytest.mark.unit
def test_backup_service_refresh_metric_increments_age(tmp_path):
    target = LocalFilesystemBackupTarget(str(tmp_path / "backups"))
    svc = BackupService(
        requester_id="test",
        command=FakeBackupCommand(),
        target=target,
        interval_seconds=3600,
    )
    svc.refresh_metric()
    assert get_backup_metrics()["backup_age_seconds"] == float("inf")
    svc.take_backup()
    svc.refresh_metric()
    # After a real backup the age is small but finite.
    assert 0.0 <= get_backup_metrics()["backup_age_seconds"] < 5.0


# ---------------------------------------------------------------------------
# RestoreDrillService
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_drill_success_path(tmp_path):
    target = LocalFilesystemBackupTarget(str(tmp_path / "backups"))
    cmd = FakeBackupCommand(payload=b"DUMP-RESTORED", smoke_pass=True)
    BackupService(
        requester_id="t", command=cmd, target=target, interval_seconds=3600
    ).take_backup()

    drill = RestoreDrillService(
        requester_id="drill",
        command=cmd,
        scratch_db="/tmp/scratch_does_not_exist",
        target=target,
        interval_seconds=3600,
    )
    report = drill.run_drill()
    assert isinstance(report, DrillReport)
    assert report.success is True
    assert report.smoke_test_pass is True
    assert report.duration_seconds >= 0.0
    assert cmd.smoke_called == 1
    assert get_backup_metrics()["last_successful_restore_drill_age_seconds"] == 0.0


@pytest.mark.unit
def test_run_drill_no_backup_keys_returns_failure(tmp_path):
    target = LocalFilesystemBackupTarget(str(tmp_path / "backups"))
    cmd = FakeBackupCommand()
    drill = RestoreDrillService(
        requester_id="drill",
        command=cmd,
        scratch_db="/tmp/scratch",
        target=target,
        interval_seconds=3600,
    )
    report = drill.run_drill()
    assert report.success is False
    assert report.smoke_test_pass is False
    assert report.error is not None
    assert "no backup" in report.error


@pytest.mark.unit
def test_run_drill_smoke_test_failure_is_reported(tmp_path):
    target = LocalFilesystemBackupTarget(str(tmp_path / "backups"))
    cmd = FakeBackupCommand(payload=b"X", smoke_pass=False)
    BackupService(
        requester_id="t", command=cmd, target=target, interval_seconds=3600
    ).take_backup()
    drill = RestoreDrillService(
        requester_id="drill",
        command=cmd,
        scratch_db="/tmp/scratch",
        target=target,
        interval_seconds=3600,
    )
    report = drill.run_drill()
    assert report.success is False
    assert report.smoke_test_pass is False


@pytest.mark.unit
def test_run_drill_invokes_cleanup_callable(tmp_path):
    target = LocalFilesystemBackupTarget(str(tmp_path / "backups"))
    cmd = FakeBackupCommand()
    BackupService(
        requester_id="t", command=cmd, target=target, interval_seconds=3600
    ).take_backup()
    cleaned: List[str] = []
    drill = RestoreDrillService(
        requester_id="drill",
        command=cmd,
        scratch_db="/tmp/scratch_db",
        target=target,
        cleanup=lambda db: cleaned.append(db),
        interval_seconds=3600,
    )
    drill.run_drill()
    assert cleaned == ["/tmp/scratch_db"]


@pytest.mark.unit
def test_run_drill_no_target_returns_failure(tmp_path):
    drill = RestoreDrillService(
        requester_id="drill",
        command=FakeBackupCommand(),
        scratch_db="/tmp/scratch",
        target=None,
        interval_seconds=3600,
    )
    report = drill.run_drill()
    assert report.success is False
    assert report.error is not None


@pytest.mark.unit
async def test_drill_service_update_raises_on_failure(tmp_path):
    target = LocalFilesystemBackupTarget(str(tmp_path / "backups"))
    drill = RestoreDrillService(
        requester_id="drill",
        command=FakeBackupCommand(),
        scratch_db="/tmp/scratch",
        target=target,
        interval_seconds=3600,
    )
    with pytest.raises(RuntimeError):
        await drill.update()


@pytest.mark.unit
def test_drill_report_to_dict_has_documented_fields():
    report = DrillReport(success=True, duration_seconds=1.5, smoke_test_pass=True)
    d = report.to_dict()
    assert d["success"] is True
    assert d["duration_seconds"] == 1.5
    assert d["smoke_test_pass"] is True
    assert "started_at" in d
