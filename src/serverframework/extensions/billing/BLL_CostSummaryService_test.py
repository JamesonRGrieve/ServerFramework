"""Tests for CostSummaryService — the nightly roll-up driver (Item 84)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Tuple

from serverframework.extensions.billing.BLL_CostSummary import CostSummaryModel
from serverframework.extensions.billing.BLL_CostSummaryService import (
    CostSummaryRollup,
    _default_period_key,
    make_cost_summary_scheduled_service,
)


# ---------- _default_period_key -----------------------------------------


def test_default_period_key_day():
    t = datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc)
    assert _default_period_key("day", now=t) == "2026-04-30"


def test_default_period_key_month():
    t = datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc)
    assert _default_period_key("month", now=t) == "2026-04"


def test_default_period_key_year():
    t = datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc)
    assert _default_period_key("year", now=t) == "2026"


def test_default_period_key_naive_treated_as_utc():
    naive = datetime(2026, 4, 30, 12, 0)
    assert _default_period_key("day", now=naive) == "2026-04-30"


# ---------- CostSummaryRollup -------------------------------------------


def _make_rollup(
    counter: Dict[Tuple[str, str, str], Decimal],
    *,
    upserts: List[CostSummaryModel.Create],
    audit: List[Dict[str, Any]] | None = None,
    upsert_fn=None,
) -> Tuple[CostSummaryRollup, Dict[str, int]]:
    """Build a rollup with the given starting counter; return (rollup, reset_state)."""
    reset_state = {"calls": 0}

    def get_counter() -> Dict[Tuple[str, str, str], Decimal]:
        return dict(counter)

    def reset_counter() -> None:
        reset_state["calls"] += 1
        counter.clear()

    def default_upsert(create: CostSummaryModel.Create) -> str:
        upserts.append(create)
        return f"row-{len(upserts)}"

    rollup = CostSummaryRollup(
        counter_provider=get_counter,
        counter_resetter=reset_counter,
        upsert_summary=upsert_fn or default_upsert,
        period="day",
        clock=lambda: datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc),
        audit_emit=audit.append if audit is not None else None,
    )
    return rollup, reset_state


def test_run_pass_reads_counter_and_emits_summary_rows():
    counter = {
        ("team1", "openai", "complete"): Decimal("0.50"),
        ("team1", "openai", "embed"): Decimal("0.10"),
        ("team2", "anthropic", "complete"): Decimal("0.20"),
    }
    upserts: List[CostSummaryModel.Create] = []
    rollup, _ = _make_rollup(counter, upserts=upserts)
    report = rollup.run_pass()

    assert report.rows_written == 3
    assert report.errors == []
    assert report.period == "day"
    assert report.period_key == "2026-04-30"

    # Each upsert is a CostSummaryModel.Create with the expected fields.
    by_key = {(c.tenant_id, c.provider, c.ability): c for c in upserts}
    assert by_key[("team1", "openai", "complete")].total_cost_usd == Decimal("0.50")
    assert by_key[("team1", "openai", "complete")].call_count == 1
    assert by_key[("team2", "anthropic", "complete")].total_cost_usd == Decimal("0.20")


def test_counter_is_reset_after_successful_pass():
    counter = {("team1", "openai", "complete"): Decimal("0.50")}
    upserts: List[CostSummaryModel.Create] = []
    rollup, reset_state = _make_rollup(counter, upserts=upserts)
    rollup.run_pass()

    assert reset_state["calls"] == 1
    assert counter == {}


def test_empty_counter_is_a_noop():
    counter: Dict[Tuple[str, str, str], Decimal] = {}
    upserts: List[CostSummaryModel.Create] = []
    rollup, reset_state = _make_rollup(counter, upserts=upserts)
    report = rollup.run_pass()

    assert report.rows_written == 0
    assert report.errors == []
    assert upserts == []
    # Reset is not called for an empty pass; counter was already empty.
    assert reset_state["calls"] == 0


def test_upsert_failure_prevents_counter_reset():
    counter = {("team1", "openai", "complete"): Decimal("0.50")}
    upserts: List[CostSummaryModel.Create] = []

    def boom(_create: CostSummaryModel.Create) -> str:
        raise RuntimeError("DB unreachable")

    rollup, reset_state = _make_rollup(counter, upserts=upserts, upsert_fn=boom)
    report = rollup.run_pass()

    assert report.rows_written == 0
    assert any("DB unreachable" in err for err in report.errors)
    # Counter NOT reset on failure -- the next pass should retry.
    assert reset_state["calls"] == 0
    assert counter != {}


def test_audit_emission_includes_period_and_counts():
    counter = {("team1", "openai", "complete"): Decimal("0.5")}
    upserts: List[CostSummaryModel.Create] = []
    audit: List[Dict[str, Any]] = []
    rollup, _ = _make_rollup(counter, upserts=upserts, audit=audit)
    rollup.run_pass()

    assert len(audit) == 1
    [event] = audit
    assert event["event_class"] == "cost_summary_rollup"
    assert event["period"] == "day"
    assert event["period_key"] == "2026-04-30"
    assert event["rows_written"] == 1
    assert event["errors"] == []


def test_counter_provider_failure_recorded_as_error_not_raised():
    upserts: List[CostSummaryModel.Create] = []

    def bad_provider() -> Dict[Tuple[str, str, str], Decimal]:
        raise RuntimeError("counter unreachable")

    def upsert(_create: CostSummaryModel.Create) -> str:
        return "row-1"

    rollup = CostSummaryRollup(
        counter_provider=bad_provider,
        counter_resetter=lambda: None,
        upsert_summary=upsert,
        period="day",
        clock=lambda: datetime(2026, 4, 30, tzinfo=timezone.utc),
    )
    report = rollup.run_pass()

    assert any("counter unreachable" in err for err in report.errors)
    assert report.rows_written == 0


def test_audit_emit_failure_does_not_crash_pass():
    counter: Dict[Tuple[str, str, str], Decimal] = {}
    upserts: List[CostSummaryModel.Create] = []

    def bad_audit(_event: Dict[str, Any]) -> None:
        raise RuntimeError("influx down")

    rollup = CostSummaryRollup(
        counter_provider=lambda: dict(counter),
        counter_resetter=lambda: None,
        upsert_summary=lambda c: "row-1",
        period="day",
        clock=lambda: datetime(2026, 4, 30, tzinfo=timezone.utc),
        audit_emit=bad_audit,
    )
    # Must not raise.
    rollup.run_pass()


# ---------- make_cost_summary_scheduled_service -------------------------


def test_factory_returns_scheduled_service_with_defaults():
    from serverframework.logic.AbstractService import ScheduledService

    counter: Dict[Tuple[str, str, str], Decimal] = {}

    svc = make_cost_summary_scheduled_service(
        service_id="cost-rollup-test",
        interval_seconds=86400,
        counter_provider=lambda: dict(counter),
        counter_resetter=lambda: None,
        upsert_summary=lambda c: "row",
    )
    assert isinstance(svc, ScheduledService)
    assert svc.service_id == "cost-rollup-test"
    assert svc.interval_seconds == 86400


def test_factory_default_interval_is_one_day():
    svc = make_cost_summary_scheduled_service(
        service_id="cost-rollup-test",
        counter_provider=lambda: {},
        counter_resetter=lambda: None,
        upsert_summary=lambda c: "row",
    )
    assert svc.interval_seconds == 24 * 60 * 60


def test_factory_requires_upsert_or_model_registry():
    import pytest

    with pytest.raises(ValueError):
        make_cost_summary_scheduled_service(
            service_id="cost-rollup-test",
            counter_provider=lambda: {},
            counter_resetter=lambda: None,
        )


def test_factory_update_runs_rollup_pass():
    import asyncio

    counter = {("t1", "openai", "complete"): Decimal("0.42")}
    upserts: List[CostSummaryModel.Create] = []

    def get_counter() -> Dict[Tuple[str, str, str], Decimal]:
        return dict(counter)

    def reset_counter() -> None:
        counter.clear()

    def upsert(create: CostSummaryModel.Create) -> str:
        upserts.append(create)
        return "row-1"

    svc = make_cost_summary_scheduled_service(
        service_id="cost-rollup-test",
        counter_provider=get_counter,
        counter_resetter=reset_counter,
        upsert_summary=upsert,
    )
    asyncio.run(svc.update())
    assert len(upserts) == 1
    assert upserts[0].tenant_id == "t1"
    assert counter == {}


def test_factory_lifecycle_start_stop():
    """ScheduledService lifecycle (start/stop/health) round-trips correctly."""
    from serverframework.logic.AbstractService import HealthStatus

    svc = make_cost_summary_scheduled_service(
        service_id="cost-rollup-test",
        counter_provider=lambda: {},
        counter_resetter=lambda: None,
        upsert_summary=lambda c: "row",
    )
    assert not svc.running
    assert svc.health().status == HealthStatus.DOWN

    svc.start()
    assert svc.running
    assert svc.health().status == HealthStatus.OK

    svc.pause()
    assert svc.health().status == HealthStatus.DEGRADED

    svc.resume()
    assert svc.health().status == HealthStatus.OK

    svc.stop()
    assert not svc.running
    assert svc.health().status == HealthStatus.DOWN
