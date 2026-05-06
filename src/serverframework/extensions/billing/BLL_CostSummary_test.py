"""Unit tests for CostSummaryModel + CostRollupAggregator (Item 84)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from serverframework.extensions.billing.BLL_CostSummary import (
    CostRollupAggregator,
    CostSummaryModel,
)


# ---------- CostSummaryModel ----------------------------------------------


def _now() -> datetime:
    return datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc)


def test_cost_summary_model_accepts_documented_fields():
    m = CostSummaryModel(
        id="cs-1",
        created_at=_now(),
        created_by_user_id="system",
        updated_at=None,
        updated_by_user_id=None,
        tenant_id="team-1",
        provider="openai",
        ability="complete",
        period="day",
        period_key="2026-04-30",
        total_cost_usd=Decimal("1.23"),
        call_count=5,
    )
    assert m.tenant_id == "team-1"
    assert m.provider == "openai"
    assert m.ability == "complete"
    assert m.period == "day"
    assert m.period_key == "2026-04-30"
    assert m.total_cost_usd == Decimal("1.23")
    assert m.call_count == 5


def test_cost_summary_model_period_literal_rejects_garbage():
    with pytest.raises(Exception):
        CostSummaryModel(
            id="cs-1",
            created_at=_now(),
            created_by_user_id="system",
            updated_at=None,
            updated_by_user_id=None,
            tenant_id="team-1",
            provider="openai",
            ability="complete",
            period="century",  # type: ignore[arg-type]
            period_key="2026",
            total_cost_usd=Decimal("0"),
            call_count=0,
        )


def test_cost_summary_model_permission_references_classvar():
    assert CostSummaryModel.permission_references == ["meta_logging"]


def test_cost_summary_repr_contains_key_fields():
    m = CostSummaryModel(
        id="cs-1",
        created_at=_now(),
        created_by_user_id="system",
        updated_at=None,
        updated_by_user_id=None,
        tenant_id="team-1",
        provider="openai",
        ability="complete",
        period="day",
        period_key="2026-04-30",
        total_cost_usd=Decimal("0.42"),
        call_count=2,
    )
    s = repr(m)
    assert "team-1" in s
    assert "openai" in s
    assert "2026-04-30" in s


# ---------- Create / Update / Search inner classes -----------------------


def test_create_inner_class_minimum_required_fields():
    create = CostSummaryModel.Create(
        tenant_id="t1",
        provider="openai",
        ability="complete",
        period="day",
        period_key="2026-04-30",
        total_cost_usd=Decimal("0.50"),
    )
    assert create.tenant_id == "t1"
    assert create.call_count == 0


def test_update_inner_class_all_fields_optional():
    u = CostSummaryModel.Update()
    assert u.total_cost_usd is None
    assert u.call_count is None


def test_update_accepts_partial_fields():
    u = CostSummaryModel.Update(total_cost_usd=Decimal("9.99"))
    assert u.total_cost_usd == Decimal("9.99")
    assert u.call_count is None


def test_search_inner_class_constructible_empty():
    s = CostSummaryModel.Search()
    assert s.tenant_id is None
    assert s.provider is None
    assert s.period is None


# ---------- CostRollupAggregator -----------------------------------------


def test_aggregator_starts_empty():
    agg = CostRollupAggregator()
    assert agg.is_empty()
    assert agg.into_summaries(period="day", period_key="2026-04-30") == []


def test_aggregator_accumulates_cost_and_call_count_per_key():
    agg = CostRollupAggregator()
    agg.add(tenant_id="t1", provider="openai", ability="complete", cost=Decimal("0.5"))
    agg.add(tenant_id="t1", provider="openai", ability="complete", cost=Decimal("0.25"))
    agg.add(tenant_id="t1", provider="openai", ability="complete", cost=Decimal("0.25"))
    rows = agg.into_summaries(period="day", period_key="2026-04-30")
    assert len(rows) == 1
    [row] = rows
    assert row.total_cost_usd == Decimal("1.00")
    assert row.call_count == 3


def test_aggregator_separate_keys_are_independent():
    agg = CostRollupAggregator()
    agg.add(tenant_id="t1", provider="openai", ability="complete", cost=Decimal("0.10"))
    agg.add(tenant_id="t2", provider="openai", ability="complete", cost=Decimal("0.20"))
    agg.add(tenant_id="t1", provider="anthropic", ability="complete", cost=Decimal("0.30"))
    agg.add(tenant_id="t1", provider="openai", ability="embed", cost=Decimal("0.40"))
    rows = agg.into_summaries(period="day", period_key="2026-04-30")
    assert len(rows) == 4
    by_key = {(r.tenant_id, r.provider, r.ability): r for r in rows}
    assert by_key[("t1", "openai", "complete")].total_cost_usd == Decimal("0.10")
    assert by_key[("t2", "openai", "complete")].total_cost_usd == Decimal("0.20")
    assert by_key[("t1", "anthropic", "complete")].total_cost_usd == Decimal("0.30")
    assert by_key[("t1", "openai", "embed")].total_cost_usd == Decimal("0.40")
    for row in rows:
        assert row.call_count == 1


def test_aggregator_into_summaries_uses_caller_period_and_key():
    agg = CostRollupAggregator()
    agg.add(tenant_id="t1", provider="openai", ability="x", cost=Decimal("1"))
    rows = agg.into_summaries(period="month", period_key="2026-04")
    assert rows[0].period == "month"
    assert rows[0].period_key == "2026-04"


def test_aggregator_handles_decimal_strings_too():
    agg = CostRollupAggregator()
    agg.add(tenant_id="t1", provider="p", ability="a", cost=Decimal("0.0001"))
    agg.add(tenant_id="t1", provider="p", ability="a", cost=Decimal("0.0002"))
    rows = agg.into_summaries(period="day", period_key="2026-04-30")
    assert rows[0].total_cost_usd == Decimal("0.0003")
