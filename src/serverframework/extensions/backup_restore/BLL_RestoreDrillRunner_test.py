"""Integration test for the Item 79 restore-drill runner.

Drives the full runner end-to-end: seed a SQLite DB, take a snapshot,
restore into a scratch DB, smoke-test, write the JSON report. Skipped
when ``sqlite3`` is not on PATH (CI hosts and most dev hosts have it).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest


@pytest.fixture
def _have_sqlite3():
    if shutil.which("sqlite3") is None:
        pytest.skip("sqlite3 binary not present on PATH")


def test_runner_reports_success(tmp_path, monkeypatch, _have_sqlite3):
    """End-to-end runner success path: seed → snapshot → restore → smoke
    → report. The runner writes the report to ``/tmp/restore_drill_report.json``
    by module-level constant; we patch it to a tmp path here."""
    from serverframework.extensions.backup_restore import BLL_RestoreDrillRunner as runner

    report_path = tmp_path / "report.json"
    monkeypatch.setattr(runner, "REPORT_PATH", str(report_path))

    rc = runner.main()
    assert rc == 0
    payload = json.loads(report_path.read_text())
    assert payload["success"] is True
    assert payload["smoke_test_pass"] is True
    assert payload["duration_seconds"] >= 0
