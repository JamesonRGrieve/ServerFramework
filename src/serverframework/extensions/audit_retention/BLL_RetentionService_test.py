"""Tests for :mod:`extensions.audit_retention.BLL_RetentionService`.

Covers the four behaviors that matter for an Item 56 driver:
1. Expired rows are archived and purged.
2. Legal hold suppresses purge while in effect.
3. Archive failure aborts purge for that batch.
4. Indefinite (``forever``) policy is a no-op.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Sequence

import pytest

from serverframework.extensions.audit_retention.BLL_RetentionPolicy import RetentionPolicy
from serverframework.extensions.audit_retention.BLL_RetentionService import (
    RetentionRegistration,
    RetentionService,
    _digest_rows,
    make_retention_scheduled_service,
)


def _row(idx: int, age_days: int, now: datetime) -> Dict[str, Any]:
    return {
        "id": f"row-{idx}",
        "created_at": (now - timedelta(days=age_days)).isoformat(),
    }


class _Bucket:
    """In-memory store of audit-event-like rows."""

    def __init__(self, rows: Sequence[Dict[str, Any]]) -> None:
        self._rows: List[Dict[str, Any]] = list(rows)
        self.archive_calls: List[Dict[str, Any]] = []
        self.archive_targets: List[str] = []
        self.delete_calls: List[List[Dict[str, Any]]] = []

    def expired_query(self, cutoff: datetime) -> List[Dict[str, Any]]:
        return [
            row
            for row in self._rows
            if datetime.fromisoformat(row["created_at"]) < cutoff
        ]

    def archive_ok(
        self, rows: Sequence[Dict[str, Any]], target: str
    ) -> bool:
        self.archive_calls.extend(rows)
        self.archive_targets.append(target)
        return True

    def archive_fail(
        self, rows: Sequence[Dict[str, Any]], target: str
    ) -> bool:
        return False

    def delete(self, rows: Sequence[Dict[str, Any]]) -> int:
        self.delete_calls.append(list(rows))
        ids_to_purge = {r["id"] for r in rows}
        before = len(self._rows)
        self._rows = [r for r in self._rows if r["id"] not in ids_to_purge]
        return before - len(self._rows)

    @property
    def remaining(self) -> List[Dict[str, Any]]:
        return list(self._rows)


def test_expired_rows_archived_then_purged():
    now = datetime(2026, 4, 30, tzinfo=timezone.utc)
    bucket = _Bucket([_row(i, 90 if i < 3 else 5, now) for i in range(5)])
    captured_audit: List[Dict[str, Any]] = []

    svc = RetentionService(
        registrations=[
            RetentionRegistration(
                name="audit_login",
                policy=RetentionPolicy(window="30d", archive_to="s3://audit/"),
                expired_query=bucket.expired_query,
                delete=bucket.delete,
                archive=bucket.archive_ok,
            )
        ],
        audit_emit=captured_audit.append,
        clock=lambda: now,
    )

    [report] = svc.run_pass()

    assert report.archived_count == 3
    assert report.purged_count == 3
    assert report.archive_digest is not None
    assert report.errors == []
    assert len(bucket.remaining) == 2
    assert bucket.archive_targets == ["s3://audit/"]
    assert len(captured_audit) == 1
    audit = captured_audit[0]
    assert audit["event_class"] == "retention_pass"
    assert audit["registration_name"] == "audit_login"
    assert audit["archived_count"] == 3
    assert audit["purged_count"] == 3
    assert audit["archive_digest"] == report.archive_digest


def test_legal_hold_suppresses_purge():
    now = datetime(2026, 4, 30, tzinfo=timezone.utc)
    bucket = _Bucket([_row(i, 365, now) for i in range(4)])

    svc = RetentionService(
        registrations=[
            RetentionRegistration(
                name="audit_subpoena",
                policy=RetentionPolicy(
                    window="30d",
                    archive_to="s3://audit/",
                    legal_hold="subpoena_2026_q1",
                ),
                expired_query=bucket.expired_query,
                delete=bucket.delete,
                archive=bucket.archive_ok,
            )
        ],
        clock=lambda: now,
    )

    [report] = svc.run_pass()

    assert report.archived_count == 0
    assert report.purged_count == 0
    assert report.skipped_due_to_legal_hold == 4
    assert bucket.delete_calls == []
    assert bucket.archive_calls == []
    assert len(bucket.remaining) == 4


def test_archive_failure_aborts_purge():
    now = datetime(2026, 4, 30, tzinfo=timezone.utc)
    bucket = _Bucket([_row(i, 90, now) for i in range(3)])

    svc = RetentionService(
        registrations=[
            RetentionRegistration(
                name="audit_login",
                policy=RetentionPolicy(window="30d", archive_to="s3://audit/"),
                expired_query=bucket.expired_query,
                delete=bucket.delete,
                archive=bucket.archive_fail,
            )
        ],
        clock=lambda: now,
    )

    [report] = svc.run_pass()

    assert report.archived_count == 0
    assert report.purged_count == 0
    assert report.skipped_due_to_archive_failure == 3
    assert bucket.delete_calls == []
    assert len(bucket.remaining) == 3


def test_archives_required_when_target_is_not_none():
    now = datetime(2026, 4, 30, tzinfo=timezone.utc)
    bucket = _Bucket([_row(i, 90, now) for i in range(2)])

    svc = RetentionService(
        registrations=[
            RetentionRegistration(
                name="audit_login",
                policy=RetentionPolicy(window="30d", archive_to="s3://audit/"),
                expired_query=bucket.expired_query,
                delete=bucket.delete,
                archive=None,
            )
        ],
        clock=lambda: now,
    )

    [report] = svc.run_pass()

    assert report.skipped_due_to_archive_failure == 2
    assert any("no archive callback" in e for e in report.errors)


def test_purge_without_archive_when_target_is_none():
    now = datetime(2026, 4, 30, tzinfo=timezone.utc)
    bucket = _Bucket([_row(i, 90, now) for i in range(2)])

    svc = RetentionService(
        registrations=[
            RetentionRegistration(
                name="audit_debug",
                policy=RetentionPolicy(window="30d", archive_to="none"),
                expired_query=bucket.expired_query,
                delete=bucket.delete,
                archive=None,
            )
        ],
        clock=lambda: now,
    )

    [report] = svc.run_pass()

    assert report.archived_count == 0
    assert report.archive_digest is None
    assert report.purged_count == 2
    assert len(bucket.remaining) == 0


def test_forever_policy_is_noop():
    now = datetime(2026, 4, 30, tzinfo=timezone.utc)
    bucket = _Bucket([_row(i, 365 * 10, now) for i in range(3)])

    svc = RetentionService(
        registrations=[
            RetentionRegistration(
                name="audit_critical",
                policy=RetentionPolicy(window="forever", archive_to="s3://audit/"),
                expired_query=bucket.expired_query,
                delete=bucket.delete,
                archive=bucket.archive_ok,
            )
        ],
        clock=lambda: now,
    )

    [report] = svc.run_pass()

    assert report.archived_count == 0
    assert report.purged_count == 0
    assert bucket.delete_calls == []


def test_no_expired_rows_is_silent():
    now = datetime(2026, 4, 30, tzinfo=timezone.utc)
    bucket = _Bucket([_row(i, 5, now) for i in range(3)])

    svc = RetentionService(
        registrations=[
            RetentionRegistration(
                name="audit_login",
                policy=RetentionPolicy(window="30d", archive_to="none"),
                expired_query=bucket.expired_query,
                delete=bucket.delete,
            )
        ],
        clock=lambda: now,
    )

    [report] = svc.run_pass()

    assert report.archived_count == 0
    assert report.purged_count == 0
    assert report.errors == []


def test_digest_is_stable_for_same_input():
    rows = [{"id": f"r-{i}", "ts": "2026-01-01"} for i in range(5)]
    assert _digest_rows(rows) == _digest_rows(list(rows))


def test_digest_changes_when_rows_change():
    rows_a = [{"id": "r-1"}]
    rows_b = [{"id": "r-2"}]
    assert _digest_rows(rows_a) != _digest_rows(rows_b)


def test_query_failure_recorded_as_error_not_raised():
    now = datetime(2026, 4, 30, tzinfo=timezone.utc)

    def boom(_cutoff: datetime) -> List[Dict[str, Any]]:
        raise RuntimeError("db unreachable")

    svc = RetentionService(
        registrations=[
            RetentionRegistration(
                name="audit_login",
                policy=RetentionPolicy(window="30d", archive_to="none"),
                expired_query=boom,
                delete=lambda _rows: 0,
            )
        ],
        clock=lambda: now,
    )

    [report] = svc.run_pass()
    assert any("db unreachable" in e for e in report.errors)
    assert report.purged_count == 0


def test_make_retention_scheduled_service_returns_scheduled_service():
    svc = make_retention_scheduled_service(
        service_id="retention-test",
        interval_seconds=86400,
        registrations=[],
    )
    # Ensure the wrapper inherits ScheduledService.
    from serverframework.logic.AbstractService import ScheduledService

    assert isinstance(svc, ScheduledService)
    assert svc.service_id == "retention-test"
    assert svc.interval_seconds == 86400
