"""CLI runner for the Item 79 monthly restore drill.

Invoked from `.github/workflows/restore-drill.yml`. Builds a tiny SQLite
database, takes a snapshot via `SqliteBackupCommand`, then drives
`RestoreDrillService` against the snapshot to verify the restore path
end-to-end. Writes a JSON report to `/tmp/restore_drill_report.json`
and exits non-zero on drill failure.

Designed to be self-contained — no framework BLL/extension surface, just
the `lib/Backup.py` primitives — so the workflow does not need a live
Postgres or any of the framework's optional dependencies.
"""

from __future__ import annotations

import json
import os
import sys
import sqlite3
import tempfile
from pathlib import Path

from zephyrex.extensions.backup_restore.BLL_Backup import (
    DrillReport,
    LocalFilesystemBackupTarget,
    RestoreDrillService,
    SqliteBackupCommand,
)


REPORT_PATH = "/tmp/restore_drill_report.json"


def _seed_source_db(path: str) -> None:
    """Create a small source DB so the dump has something to back up."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            "CREATE TABLE drill_probe (id INTEGER PRIMARY KEY, msg TEXT);"
            "INSERT INTO drill_probe (msg) VALUES ('item-79-drill');"
        )
        conn.commit()
    finally:
        conn.close()


def _run() -> DrillReport:
    workdir = Path(tempfile.mkdtemp(prefix="restore-drill-"))
    source = str(workdir / "source.db")
    scratch = str(workdir / "scratch.db")
    target_root = str(workdir / "target")

    _seed_source_db(source)

    target = LocalFilesystemBackupTarget(target_root)
    command = SqliteBackupCommand(db_path=source)

    # Take a snapshot first so the drill has something to restore.
    artifact = command.dump()
    import io

    target.upload(io.BytesIO(artifact), "snapshots/drill-snapshot.bin")

    drill = RestoreDrillService(
        requester_id="ci-restore-drill",
        command=command,
        scratch_db=scratch,
        target=target,
        # ScheduledService base requires interval; the drill is fired manually.
        interval_seconds=10,
    )
    return drill.run_drill()


def main() -> int:
    try:
        report = _run()
    except Exception as exc:  # noqa: BLE001
        Path(REPORT_PATH).write_text(
            json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"}),
            encoding="utf-8",
        )
        print(f"Drill runner crashed: {exc}", file=sys.stderr)
        return 2

    Path(REPORT_PATH).write_text(json.dumps(report.to_dict()), encoding="utf-8")
    if not report.success:
        print(f"Drill failed: {report.error or 'smoke test failed'}", file=sys.stderr)
        return 1
    print(f"Drill succeeded in {report.duration_seconds:.3f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
