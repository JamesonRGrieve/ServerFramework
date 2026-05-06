"""Tests for RetentionService legal-hold release semantics (Item 56 — task 2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Sequence

import pytest

from serverframework.extensions.audit_retention.BLL_RetentionPolicy import RetentionPolicy
from serverframework.extensions.audit_retention.BLL_RetentionService import (
    RetentionRegistration,
    RetentionService,
)


def _row(idx: int, age_days: int, now: datetime) -> Dict[str, Any]:
    return {
        "id": f"row-{idx}",
        "created_at": (now - timedelta(days=age_days)).isoformat(),
    }


class _Bucket:
    def __init__(self, rows: Sequence[Dict[str, Any]]) -> None:
        self._rows: List[Dict[str, Any]] = list(rows)
        self.archive_calls: List[Dict[str, Any]] = []

    def expired_query(self, cutoff: datetime) -> List[Dict[str, Any]]:
        return [
            r for r in self._rows
            if datetime.fromisoformat(r["created_at"]) < cutoff
        ]

    def archive(self, rows: Sequence[Dict[str, Any]], target: str) -> bool:
        self.archive_calls.extend(rows)
        return True

    def delete(self, rows: Sequence[Dict[str, Any]]) -> int:
        ids = {r["id"] for r in rows}
        before = len(self._rows)
        self._rows = [r for r in self._rows if r["id"] not in ids]
        return before - len(self._rows)

    @property
    def remaining(self) -> List[Dict[str, Any]]:
        return list(self._rows)


def test_release_legal_hold_marks_registration_as_released():
    svc = RetentionService(registrations=[])
    svc.release_legal_hold("audit_login", reason="case-closed", requester_id="op-1")
    assert svc.is_released("audit_login")
    assert svc.list_released() == ["audit_login"]


def test_release_legal_hold_emits_audit_event():
    captured: List[Dict[str, Any]] = []
    svc = RetentionService(registrations=[], audit_emit=captured.append)

    event_id = svc.release_legal_hold(
        "audit_login",
        reason="subpoena lifted 2026-04-30",
        requester_id="admin-7",
    )

    assert isinstance(event_id, str) and event_id
    assert len(captured) == 1
    evt = captured[0]
    assert evt["event_class"] == "retention_legal_hold_release"
    assert evt["registration_name"] == "audit_login"
    assert evt["reason"] == "subpoena lifted 2026-04-30"
    assert evt["requester_id"] == "admin-7"
    assert evt["event_id"] == event_id
    assert evt["retention_policy"]["window"] == "forever"


def test_run_pass_bypasses_legal_hold_when_released():
    now = datetime(2026, 4, 30, tzinfo=timezone.utc)
    bucket = _Bucket([_row(i, 90, now) for i in range(3)])

    svc = RetentionService(
        registrations=[
            RetentionRegistration(
                name="audit_subpoena",
                policy=RetentionPolicy(
                    window="30d",
                    archive_to="s3://audit/",
                    legal_hold="case_2026_q1",
                ),
                expired_query=bucket.expired_query,
                delete=bucket.delete,
                archive=bucket.archive,
            )
        ],
        clock=lambda: now,
    )

    svc.release_legal_hold(
        "audit_subpoena", reason="case closed", requester_id="ops"
    )

    [report] = svc.run_pass()

    assert report.archived_count == 3
    assert report.purged_count == 3
    assert report.skipped_due_to_legal_hold == 0
    assert bucket.remaining == []


def test_run_pass_still_holds_when_not_released():
    """Sanity check: without release, the existing legal-hold suppression
    still applies."""
    now = datetime(2026, 4, 30, tzinfo=timezone.utc)
    bucket = _Bucket([_row(i, 90, now) for i in range(3)])

    svc = RetentionService(
        registrations=[
            RetentionRegistration(
                name="audit_subpoena",
                policy=RetentionPolicy(
                    window="30d",
                    archive_to="s3://audit/",
                    legal_hold="case_2026_q1",
                ),
                expired_query=bucket.expired_query,
                delete=bucket.delete,
                archive=bucket.archive,
            )
        ],
        clock=lambda: now,
    )

    [report] = svc.run_pass()

    assert report.skipped_due_to_legal_hold == 3
    assert report.purged_count == 0
    assert len(bucket.remaining) == 3


def test_clear_release_re_engages_hold():
    now = datetime(2026, 4, 30, tzinfo=timezone.utc)
    bucket = _Bucket([_row(i, 90, now) for i in range(2)])

    svc = RetentionService(
        registrations=[
            RetentionRegistration(
                name="audit_subpoena",
                policy=RetentionPolicy(
                    window="30d",
                    archive_to="s3://audit/",
                    legal_hold="case_xyz",
                ),
                expired_query=bucket.expired_query,
                delete=bucket.delete,
                archive=bucket.archive,
            )
        ],
        clock=lambda: now,
    )

    svc.release_legal_hold(
        "audit_subpoena", reason="temporary release", requester_id="ops"
    )
    assert svc.is_released("audit_subpoena")

    svc.clear_release("audit_subpoena")
    assert not svc.is_released("audit_subpoena")

    [report] = svc.run_pass()
    assert report.skipped_due_to_legal_hold == 2
    assert report.purged_count == 0
    assert len(bucket.remaining) == 2


def test_release_is_scoped_to_registration_name():
    svc = RetentionService(registrations=[])
    svc.release_legal_hold("a", reason="x", requester_id="op")
    assert svc.is_released("a")
    assert not svc.is_released("b")


def test_clear_release_on_unreleased_is_silent():
    svc = RetentionService(registrations=[])
    # Should not raise.
    svc.clear_release("never-released")
    assert not svc.is_released("never-released")


def test_audit_emit_failure_does_not_propagate():
    def boom(_payload):
        raise RuntimeError("audit pipe broke")

    svc = RetentionService(registrations=[], audit_emit=boom)
    # Should not raise.
    event_id = svc.release_legal_hold("a", reason="x", requester_id="op")
    assert isinstance(event_id, str)
    assert svc.is_released("a")


def test_make_retention_scheduled_service_exposes_inner():
    from serverframework.extensions.audit_retention.BLL_RetentionService import (
        make_retention_scheduled_service,
    )

    scheduled = make_retention_scheduled_service(
        service_id="retention-test-2",
        interval_seconds=86400,
        registrations=[],
    )

    assert hasattr(scheduled, "retention_service")
    assert isinstance(scheduled.retention_service, RetentionService)
    scheduled.retention_service.release_legal_hold(
        "x", reason="r", requester_id="u"
    )
    assert scheduled.retention_service.is_released("x")
