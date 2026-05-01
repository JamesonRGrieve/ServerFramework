"""Nightly cost roll-up scheduled service (Item 84).

Reads the per-process cost counter on ``RotationManager``, aggregates by
``(tenant_id, provider, ability)``, and persists one ``CostSummaryModel``
row per unique key for the current period bucket. After a successful
pass the counter is cleared so the next window starts at zero.

The service is intentionally a thin wrapper around a synchronous core
(:class:`CostSummaryRollup`) so the aggregation logic can be unit-tested
without standing up the asyncio scheduler.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional

from serverframework.extensions.CostSummary import (
    CostRollupAggregator,
    CostSummaryModel,
)
from serverframework.lib.Logging import logger


def _default_period_key(period: str, now: Optional[datetime] = None) -> str:
    """UTC period bucket key. Daily uses ``YYYY-MM-DD`` to match Quota."""
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    if period == "day":
        return now.strftime("%Y-%m-%d")
    if period == "week":
        iso = now.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if period == "month":
        return now.strftime("%Y-%m")
    if period == "year":
        return now.strftime("%Y")
    raise ValueError(f"Unknown period: {period!r}")


@dataclass
class CostSummaryPassReport:
    """Summary of a single roll-up pass."""

    period: str
    period_key: str
    rows_written: int = 0
    rows_updated: int = 0
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    errors: List[str] = field(default_factory=list)


class CostSummaryRollup:
    """Synchronous roll-up engine. Wrap with :func:`make_cost_summary_scheduled_service`
    for runtime scheduling."""

    def __init__(
        self,
        *,
        counter_provider: Callable[[], Dict[Any, Decimal]],
        counter_resetter: Callable[[], None],
        upsert_summary: Callable[[CostSummaryModel.Create], str],
        period: str = "day",
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        audit_emit: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self._counter_provider = counter_provider
        self._counter_resetter = counter_resetter
        self._upsert = upsert_summary
        self._period = period
        self._clock = clock
        self._audit_emit = audit_emit

    @property
    def period(self) -> str:
        return self._period

    def run_pass(self) -> CostSummaryPassReport:
        now = self._clock()
        period_key = _default_period_key(self._period, now=now)
        report = CostSummaryPassReport(
            period=self._period, period_key=period_key, started_at=now
        )

        try:
            counter = self._counter_provider()
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"counter_provider: {exc!r}")
            report.finished_at = self._clock()
            self._emit_self_audit(report)
            return report

        if not counter:
            report.finished_at = self._clock()
            self._emit_self_audit(report)
            return report

        aggregator = CostRollupAggregator()
        for key, cost in counter.items():
            tenant_id, provider, ability = key
            aggregator.add(
                tenant_id=tenant_id,
                provider=provider,
                ability=ability,
                cost=Decimal(str(cost)),
            )

        for create in aggregator.into_summaries(
            period=self._period, period_key=period_key
        ):
            try:
                self._upsert(create)
                report.rows_written += 1
            except Exception as exc:  # noqa: BLE001
                report.errors.append(f"upsert {create.tenant_id}/{create.provider}: {exc!r}")

        if not report.errors:
            try:
                self._counter_resetter()
            except Exception as exc:  # noqa: BLE001
                report.errors.append(f"counter_reset: {exc!r}")

        report.finished_at = self._clock()
        self._emit_self_audit(report)
        return report

    def _emit_self_audit(self, report: CostSummaryPassReport) -> None:
        if self._audit_emit is None:
            return
        payload = {
            "event_class": "cost_summary_rollup",
            "period": report.period,
            "period_key": report.period_key,
            "rows_written": report.rows_written,
            "rows_updated": report.rows_updated,
            "started_at": (
                report.started_at.isoformat() if report.started_at else None
            ),
            "finished_at": (
                report.finished_at.isoformat() if report.finished_at else None
            ),
            "errors": list(report.errors),
        }
        try:
            self._audit_emit(payload)
        except Exception:  # noqa: BLE001
            logger.warning(
                "CostSummaryRollup audit_emit failed for %s/%s",
                report.period,
                report.period_key,
                exc_info=True,
            )


def _make_default_upsert(
    model_registry: Any,
    requester_id: str,
) -> Callable[[CostSummaryModel.Create], str]:
    """Build an upsert callable that finds-or-creates a CostSummaryModel
    row keyed by (tenant_id, provider, ability, period, period_key)."""

    def upsert(create: CostSummaryModel.Create) -> str:
        DB = CostSummaryModel.DB(model_registry.DB.manager.Base)
        existing = DB.list(
            requester_id=requester_id,
            model_registry=model_registry,
            filters=[
                DB.tenant_id == create.tenant_id,
                DB.provider == create.provider,
                DB.ability == create.ability,
                DB.period == create.period,
                DB.period_key == create.period_key,
            ],
            return_type="dto",
            override_dto=CostSummaryModel,
        )
        if existing:
            row = existing[0]
            new_total = Decimal(str(row.total_cost_usd)) + create.total_cost_usd
            new_count = int(row.call_count) + int(create.call_count)
            DB.update(
                requester_id=requester_id,
                model_registry=model_registry,
                id=row.id,
                total_cost_usd=new_total,
                call_count=new_count,
            )
            return str(row.id)
        created = DB.create(
            requester_id=requester_id,
            model_registry=model_registry,
            tenant_id=create.tenant_id,
            provider=create.provider,
            ability=create.ability,
            period=create.period,
            period_key=create.period_key,
            total_cost_usd=create.total_cost_usd,
            call_count=create.call_count,
        )
        return str(getattr(created, "id", created))

    return upsert


def make_cost_summary_scheduled_service(
    *,
    service_id: str,
    interval_seconds: int = 24 * 60 * 60,
    model_registry: Optional[Any] = None,
    requester_id: str = "system",
    period: str = "day",
    upsert_summary: Optional[Callable[[CostSummaryModel.Create], str]] = None,
    counter_provider: Optional[Callable[[], Dict[Any, Decimal]]] = None,
    counter_resetter: Optional[Callable[[], None]] = None,
    clock: Optional[Callable[[], datetime]] = None,
    audit_emit_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    state_store: Optional[Callable[..., Any]] = None,
    cron_expression: Optional[str] = None,
    cron_evaluator: Optional[Callable[..., Any]] = None,
):
    """Wrap :class:`CostSummaryRollup` in a :class:`ScheduledService`.

    By default, reads from ``RotationManager.get_cost_counter()`` and
    resets via ``RotationManager.reset_observability_counters()``.
    Tests can inject ``counter_provider`` / ``counter_resetter`` and
    ``upsert_summary`` directly.
    """
    from serverframework.logic.AbstractService import ScheduledService

    if counter_provider is None or counter_resetter is None:
        from serverframework.logic.BLL_Providers import RotationManager

        if counter_provider is None:
            counter_provider = RotationManager.get_cost_counter
        if counter_resetter is None:
            counter_resetter = RotationManager.reset_observability_counters

    if upsert_summary is None:
        if model_registry is None:
            raise ValueError(
                "make_cost_summary_scheduled_service requires either "
                "upsert_summary or model_registry"
            )
        upsert_summary = _make_default_upsert(model_registry, requester_id)

    rollup_kwargs: Dict[str, Any] = {
        "counter_provider": counter_provider,
        "counter_resetter": counter_resetter,
        "upsert_summary": upsert_summary,
        "period": period,
        "audit_emit": audit_emit_callback,
    }
    if clock is not None:
        rollup_kwargs["clock"] = clock
    inner = CostSummaryRollup(**rollup_kwargs)

    class _CostSummaryScheduled(ScheduledService):
        async def update(self) -> None:
            inner.run_pass()

    svc = _CostSummaryScheduled(
        requester_id=requester_id,
        service_id=service_id,
        interval_seconds=interval_seconds,
        cron_expression=cron_expression,
        cron_evaluator=cron_evaluator,
        state_store=state_store,
    )
    svc._rollup = inner  # type: ignore[attr-defined]
    return svc


__all__ = [
    "CostSummaryRollup",
    "CostSummaryPassReport",
    "make_cost_summary_scheduled_service",
]
