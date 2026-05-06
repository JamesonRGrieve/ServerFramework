# LIB.Backup — Backup, Restore, PITR (Item 79)

## What this module owns

`lib/Backup.py` is the framework's backup contract. It ships:

- A `BackupClass` Literal — every table declares one of `critical | recoverable | ephemeral`.
- A `BACKUP_REGISTRY` populated at module-import time via `register_backup_class(table, klass)`.
- A `BackupTarget` ABC with two implementations: `LocalFilesystemBackupTarget` (reference, suitable for self-hosted and CI) and `S3BackupTarget` (stub; concrete object-storage support lands with Item 43).
- A `BackupCommand` ABC with two engine implementations: `PgDumpBackupCommand` (Postgres) and `SqliteBackupCommand` (test/reference).
- `BackupService` — a `ScheduledService` flavor that runs `command.dump()` on a configured cadence and uploads the artifact under a timestamped key.
- `RestoreDrillService` — periodically downloads the latest snapshot, runs `command.restore` into a scratch DB, runs `command.smoke_test`, and discards the scratch DB.
- A pair of operational metrics: `backup_age_seconds` and `last_successful_restore_drill_age_seconds`. Both default to `inf` and are reset to `0.0` on success.

## Per-table classification

| Class | Semantics | Backup behavior |
|---|---|---|
| `critical` | Data loss is unacceptable (sessions, credentials, audit, quotas, outbox). | Nightly snapshot + (engine-permitting) continuous WAL archiving. |
| `recoverable` | Loss is recoverable from upstream sources (federated external entity mirrors). | Nightly snapshot only. |
| `ephemeral` | Cache, sticky-routing, in-flight sticky-session state. | Excluded from snapshots. |

Tables register themselves at import time:

```python
register_backup_class("provider_instances", "critical")
register_backup_class("federation_cache",   "ephemeral")
```

## RTO / RPO

- **RTO** is measured by `RestoreDrillService.run_drill().duration_seconds`. A green monthly drill demonstrates the restore path completes within the deployment's documented bound.
- **RPO** is bounded by `backup_age_seconds`. For Critical-tier tables, the deployment's RPO is the worst-case `backup_age_seconds` plus the in-flight transaction window since the last snapshot.

## Wiring `BackupService` into a deployment

Composition happens at deployment time; the framework does not auto-instantiate the service.

```python
from serverframework.extensions.backup_restore.BLL_Backup import (
    BackupService, LocalFilesystemBackupTarget, PgDumpBackupCommand,
)

backup = BackupService(
    requester_id=ROOT_ID,
    command=PgDumpBackupCommand(database_url="postgresql://..."),
    target=LocalFilesystemBackupTarget("/var/backups/serverframework"),
    interval_seconds=86_400,    # nightly
)

# Register with the framework's service supervisor at startup.
service_registry.add(backup)
```

## CI restore drill

`.github/workflows/restore-drill.yml` runs the drill on every push to `main` and on the 1st of each month at 04:00 UTC. It uses `SqliteBackupCommand` + `LocalFilesystemBackupTarget` so the only host requirement is `sqlite3` on PATH; no live Postgres, no cloud storage. The runner module is `serverframework.lib._restore_drill_runner` and writes a JSON report to `/tmp/restore_drill_report.json` that the workflow uploads as an artifact.

For Postgres deployments, replicate the workflow against a sandbox Postgres — the drill is identical except for the `BackupCommand` instance.

## Runbook: manual restore

1. Identify the snapshot key. From the `BackupTarget` (S3/GCS/local), list snapshots under the `snapshots/` prefix and pick the most recent successful one. The framework writes keys as `snapshots/snapshot-<UTC-ISO>-<8-char-uuid>.bin`.

2. Take the live application offline (or fail traffic over to a standby). Restoring on top of a live DB is undefined behavior.

3. Provision a scratch database. For Postgres: `createdb scratch_restore_<date>`. For SQLite: pick an empty path (`/var/restore/scratch.db`).

4. Run the framework's restore primitive:

    ```python
    from serverframework.extensions.backup_restore.BLL_Backup import (
        LocalFilesystemBackupTarget, PgDumpBackupCommand,
    )

    cmd = PgDumpBackupCommand(database_url="postgresql://restore@localhost/scratch_restore_2026")
    target = LocalFilesystemBackupTarget("/var/backups/serverframework")
    with target.download("snapshots/snapshot-...bin") as stream:
        artifact = stream.read()
    cmd.restore(artifact, target_db="postgresql://restore@localhost/scratch_restore_2026")
    assert cmd.smoke_test("postgresql://restore@localhost/scratch_restore_2026")
    ```

5. Verify integrity: run application-level smoke checks against the scratch DB before promoting it.

6. Promote the scratch DB to primary by updating the deployment's `DATABASE_*` env vars and bringing the application back up. PITR-capable Postgres deployments may further apply WAL archive segments accumulated since the snapshot before promotion; Item 79's `PgDumpBackupCommand` covers the snapshot path only.

## Outbox + Quota restore semantics

- **Outbox (Item 35).** Entries past their deadline are stale on restore. The recommended procedure is to mark every restored outbox entry as DLQ on first read, then process the DLQ manually. Re-firing them risks duplicate sends to upstreams that already received the original.
- **Quota (Item 19).** Counters are restored as-of the snapshot time. The window between snapshot and restore is a known small over-count window; a deployment that needs zero-overcount semantics has to either drain quota counters before the restore window or accept the bound and document it.

## Cross-references

- Item 28 — `ScheduledService` is the parent of `BackupService` / `RestoreDrillService`.
- Item 43 — `S3BackupTarget` becomes a real adapter once the object-storage abstract lands.
- Item 35 — outbox restore semantics.
- Item 19 — quota restore semantics.
- Item 56 — audit-log archival is independent of DB backup.
