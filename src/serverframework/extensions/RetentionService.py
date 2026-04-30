"""RetentionService — nightly archive + purge driver for Item 56.

Wraps :class:`logic.AbstractService.ScheduledService` so deployments can
schedule nightly retention passes against tagged audit-event classes.
The service walks every registered entry, identifies rows whose
retention window has expired, archives them via the deployment's
:func:`archive_callback`, then purges them via :func:`delete_callback`.
Legal holds suppress purge regardless of the window, per
:class:`extensions.RetentionPolicy.RetentionPolicy.under_legal_hold`.

Each retention pass emits an audit-of-the-audit event through the
optional :func:`audit_emit_callback`. That event itself carries the
``forever_default()`` retention policy so the regulator-defensible
chain is preserved.

The service does not own the storage layer. Callers wire the four
callbacks (:func:`expired_query_callback`, :func:`archive_callback`,
:func:`delete_callback`, :func:`audit_emit_callback`); the framework
provides the schedule, the legal-hold check, the archive-success gate,
and the audit emission. This keeps the service composable with any
audit backend (InfluxDB, Postgres, S3, etc.) without baking a
particular store into the runtime.

Cross-references:
  - Item 56 — retention contract.
  - Item 28 — :class:`ScheduledService` is the runtime.
  - Item 43 — object-storage abstract supplies the archive backend.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from serverframework.extensions.RetentionPolicy import RetentionPolicy, forever_default


@dataclass(frozen=True)
class RetentionRegistration:
    """One entry in the retention registry.

    Fields:
        name: a stable identifier for this registration; appears in the
            audit-of-the-audit event so operators can correlate passes
            across runs. Typically the audit-event class name.
        policy: the retention policy that governs this registration.
        expired_query: a callable returning expired rows as a list of
            dicts (or row-like mappings). Receives the cutoff ``datetime``
            past which rows are considered expired. The callable is
            responsible for honoring its own per-class filters.
        delete: a callable that purges the supplied rows from the live
            store and returns the count actually deleted.
        archive: optional callable that archives the supplied rows to
            ``policy.archive_to`` and returns ``True`` on success.
            Required when ``policy.archives()`` is True; ignored when
            the policy is purge-without-archive.
    """

    name: str
    policy: RetentionPolicy
    expired_query: Callable[[datetime], Sequence[Dict[str, Any]]]
    delete: Callable[[Sequence[Dict[str, Any]]], int]
    archive: Optional[Callable[[Sequence[Dict[str, Any]], str], bool]] = None


@dataclass
class RetentionPassReport:
    """Summary of a single retention pass.

    Returned by :meth:`RetentionService.run_pass` and emitted as the
    audit-of-the-audit event payload. The cryptographic digest is
    over the JSON-serialized archived batch (when archived) so a
    regulator can independently verify integrity.
    """

    name: str
    archived_count: int = 0
    purged_count: int = 0
    skipped_due_to_legal_hold: int = 0
    skipped_due_to_archive_failure: int = 0
    archive_digest: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    errors: List[str] = field(default_factory=list)


def _digest_rows(rows: Sequence[Dict[str, Any]]) -> str:
    """SHA-256 over a stable JSON serialization of the row batch."""
    payload = json.dumps(rows, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _cutoff_for(policy: RetentionPolicy, now: datetime) -> Optional[datetime]:
    """Return the cutoff datetime, or None if the policy is indefinite."""
    seconds = policy.window_seconds()
    if seconds is None:
        return None
    return now - timedelta(seconds=seconds)


class RetentionService:
    """Item 56 driver. Use :func:`make_retention_scheduled_service` to
    wrap this in a :class:`ScheduledService` for runtime scheduling.

    The class itself is synchronous and side-effecting through the
    callback contract so it can be unit-tested without standing up
    the full asyncio scheduler.
    """

    def __init__(
        self,
        registrations: Iterable[RetentionRegistration],
        *,
        audit_emit: Optional[Callable[[Dict[str, Any]], None]] = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._registrations: List[RetentionRegistration] = list(registrations)
        self._audit_emit = audit_emit
        self._clock = clock
        self._self_audit_policy = forever_default()

    @property
    def registrations(self) -> List[RetentionRegistration]:
        return list(self._registrations)

    def run_pass(self) -> List[RetentionPassReport]:
        """Walk every registration once. Returns one report per entry."""
        now = self._clock()
        reports: List[RetentionPassReport] = []
        for reg in self._registrations:
            report = RetentionPassReport(name=reg.name, started_at=now)
            try:
                self._run_one(reg, now, report)
            except Exception as exc:  # noqa: BLE001
                report.errors.append(repr(exc))
            report.finished_at = self._clock()
            reports.append(report)
            self._emit_self_audit(report)
        return reports

    def _run_one(
        self,
        reg: RetentionRegistration,
        now: datetime,
        report: RetentionPassReport,
    ) -> None:
        if reg.policy.under_legal_hold():
            cutoff = _cutoff_for(reg.policy, now)
            if cutoff is None:
                return
            try:
                rows = list(reg.expired_query(cutoff))
            except Exception as exc:  # noqa: BLE001
                report.errors.append(f"expired_query: {exc!r}")
                return
            report.skipped_due_to_legal_hold = len(rows)
            return

        cutoff = _cutoff_for(reg.policy, now)
        if cutoff is None:
            return

        try:
            rows = list(reg.expired_query(cutoff))
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"expired_query: {exc!r}")
            return

        if not rows:
            return

        if reg.policy.archives():
            if reg.archive is None:
                report.skipped_due_to_archive_failure = len(rows)
                report.errors.append(
                    "policy.archives() is True but no archive callback provided"
                )
                return
            try:
                ok = bool(reg.archive(rows, reg.policy.archive_to))
            except Exception as exc:  # noqa: BLE001
                report.skipped_due_to_archive_failure = len(rows)
                report.errors.append(f"archive: {exc!r}")
                return
            if not ok:
                report.skipped_due_to_archive_failure = len(rows)
                return
            report.archive_digest = _digest_rows(rows)
            report.archived_count = len(rows)

        try:
            purged = int(reg.delete(rows))
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"delete: {exc!r}")
            return
        report.purged_count = purged

    def _emit_self_audit(self, report: RetentionPassReport) -> None:
        if self._audit_emit is None:
            return
        payload = {
            "event_class": "retention_pass",
            "registration_name": report.name,
            "archived_count": report.archived_count,
            "purged_count": report.purged_count,
            "skipped_due_to_legal_hold": report.skipped_due_to_legal_hold,
            "skipped_due_to_archive_failure": report.skipped_due_to_archive_failure,
            "archive_digest": report.archive_digest,
            "started_at": (
                report.started_at.isoformat() if report.started_at else None
            ),
            "finished_at": (
                report.finished_at.isoformat() if report.finished_at else None
            ),
            "errors": list(report.errors),
            "retention_policy": {
                "window": self._self_audit_policy.window,
                "archive_to": self._self_audit_policy.archive_to,
            },
        }
        try:
            self._audit_emit(payload)
        except Exception:  # noqa: BLE001
            # Audit-of-the-audit emission failure must not abort the
            # retention pass; surface only via logger if available.
            try:
                from serverframework.lib.Logging import logger

                logger.warning(
                    "RetentionService audit_emit failed for %s",
                    report.name,
                    exc_info=True,
                )
            except Exception:  # noqa: BLE001
                pass


def make_retention_scheduled_service(
    *,
    service_id: str,
    interval_seconds: int,
    registrations: Iterable[RetentionRegistration],
    requester_id: str = "system",
    audit_emit: Optional[Callable[[Dict[str, Any]], None]] = None,
    state_store: Optional[Callable[..., Any]] = None,
    cron_expression: Optional[str] = None,
    cron_evaluator: Optional[Callable[..., Any]] = None,
):
    """Wrap a :class:`RetentionService` in a :class:`ScheduledService`.

    The returned instance honors ``cron_expression`` if given (with a
    cron evaluator), otherwise drains on a fixed ``interval_seconds``
    cadence (typically 24 * 60 * 60 for nightly). ``requester_id``
    defaults to ``"system"`` since retention is a system action; pass
    a different identifier when a deployment audits retention runs
    against a dedicated service-account user.
    """
    from serverframework.logic.AbstractService import ScheduledService

    inner = RetentionService(registrations, audit_emit=audit_emit)

    class _RetentionScheduled(ScheduledService):
        async def update(self) -> None:
            inner.run_pass()

    return _RetentionScheduled(
        requester_id=requester_id,
        service_id=service_id,
        interval_seconds=interval_seconds,
        cron_expression=cron_expression,
        cron_evaluator=cron_evaluator,
        state_store=state_store,
    )


__all__ = [
    "RetentionRegistration",
    "RetentionPassReport",
    "RetentionService",
    "make_retention_scheduled_service",
]
