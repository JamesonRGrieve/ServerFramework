"""Item 84 — tests for the per-tenant USD-cap fields on ``Quota`` and
the rotation-side pre-check / post-call true-up flow."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from zephyrex.extensions.billing.BLL_CostModel import ConstantCostModel
from zephyrex.logic.BLL_Providers import (
    RotationManager,
    reset_auth_cooldowns,
    reset_sticky_sessions,
)
from zephyrex.logic.RotationTestDoubles import fake_rotation_manager
from zephyrex.extensions.quota.BLL_Quota import Quota, QuotaExhaustedError


@pytest.fixture(autouse=True)
def _reset_state():
    reset_auth_cooldowns()
    reset_sticky_sessions()
    RotationManager.reset_observability_counters()
    yield
    reset_auth_cooldowns()
    reset_sticky_sessions()
    RotationManager.reset_observability_counters()


# ----- Field shape tests ---------------------------------------------------


def test_quota_limit_usd_accepts_decimal():
    q = Quota(
        ability="x",
        period="day",
        period_key="2026-04-30",
        limit=100,
        limit_usd=Decimal("25.50"),
    )
    assert q.limit_usd == Decimal("25.50")


def test_quota_consumed_usd_defaults_to_zero():
    q = Quota(
        ability="x",
        period="day",
        period_key="2026-04-30",
        limit=100,
    )
    assert q.consumed_usd == Decimal("0")
    assert q.limit_usd is None


def test_quota_remaining_usd_when_no_cap():
    q = Quota(
        ability="x",
        period="day",
        period_key="2026-04-30",
        limit=100,
    )
    assert q.remaining_usd() is None
    assert q.is_usd_exhausted(additional=Decimal("1000")) is False


def test_quota_remaining_usd_with_cap():
    q = Quota(
        ability="x",
        period="day",
        period_key="2026-04-30",
        limit=100,
        limit_usd=Decimal("10.00"),
        consumed_usd=Decimal("3.25"),
    )
    assert q.remaining_usd() == Decimal("6.75")
    assert q.is_usd_exhausted(additional=Decimal("6.74")) is False
    assert q.is_usd_exhausted(additional=Decimal("6.76")) is True


# ----- Rotation pre-check / post-update tests -------------------------------


@pytest.mark.unit
def test_pre_check_exceeding_cap_raises_quota_exhausted(monkeypatch):
    a = MagicMock()
    a.name = "prov-A"
    a.provider_class = type(
        "P", (), {"cost_model": ConstantCostModel(per_call_usd=Decimal("5.00"))}
    )
    rm = fake_rotation_manager(monkeypatch, [a], team_id="team-7")
    quota = Quota(
        ability="charge.create",
        period="day",
        period_key="2026-04-30",
        limit=1000,
        limit_usd=Decimal("4.00"),
        consumed_usd=Decimal("0"),
    )

    with pytest.raises(QuotaExhaustedError) as ei:
        rm.rotate(
            lambda inst: "ok",
            ability="charge.create",
            usd_quota=quota,
        )
    assert ei.value.scope == "usd"
    assert ei.value.ability == "charge.create"
    # Not consumed: pre-check refused.
    assert quota.consumed_usd == Decimal("0")


@pytest.mark.unit
def test_pre_check_within_cap_succeeds_and_post_call_increments_consumed_usd(
    monkeypatch,
):
    a = MagicMock()
    a.name = "prov-A"
    a.provider_class = type(
        "P", (), {"cost_model": ConstantCostModel(per_call_usd=Decimal("0.25"))}
    )
    rm = fake_rotation_manager(monkeypatch, [a], team_id="team-7")
    quota = Quota(
        ability="charge.create",
        period="day",
        period_key="2026-04-30",
        limit=1000,
        limit_usd=Decimal("10.00"),
        consumed_usd=Decimal("0"),
    )

    result = rm.rotate(
        lambda inst: "ok",
        ability="charge.create",
        usd_quota=quota,
    )
    assert result == "ok"
    assert quota.consumed_usd == Decimal("0.25")


@pytest.mark.unit
def test_pre_check_skipped_when_limit_usd_is_none_back_compat(monkeypatch):
    """A `limit_usd=None` row preserves token/call-only behavior; the
    rotation never refuses on a USD basis even when a cost model exists."""
    a = MagicMock()
    a.name = "prov-A"
    a.provider_class = type(
        "P", (), {"cost_model": ConstantCostModel(per_call_usd=Decimal("100.00"))}
    )
    rm = fake_rotation_manager(monkeypatch, [a], team_id="team-7")
    quota = Quota(
        ability="charge.create",
        period="day",
        period_key="2026-04-30",
        limit=1000,
        # limit_usd left None — back-compat row.
    )

    result = rm.rotate(
        lambda inst: "ok",
        ability="charge.create",
        usd_quota=quota,
    )
    assert result == "ok"
    # consumed_usd must remain zero: no cap means no true-up either.
    assert quota.consumed_usd == Decimal("0")


@pytest.mark.unit
def test_no_usd_quota_kwarg_preserves_existing_behavior(monkeypatch):
    """Calls that omit the `usd_quota` kwarg work exactly as before."""
    a = MagicMock()
    a.name = "prov-A"
    a.provider_class = type(
        "P", (), {"cost_model": ConstantCostModel(per_call_usd=Decimal("0.25"))}
    )
    rm = fake_rotation_manager(monkeypatch, [a], team_id="team-7")

    result = rm.rotate(lambda inst: "ok", ability="charge.create")
    assert result == "ok"


@pytest.mark.unit
def test_repeated_calls_accumulate_consumed_usd(monkeypatch):
    a = MagicMock()
    a.name = "prov-A"
    a.provider_class = type(
        "P", (), {"cost_model": ConstantCostModel(per_call_usd=Decimal("0.10"))}
    )
    quota = Quota(
        ability="charge.create",
        period="day",
        period_key="2026-04-30",
        limit=1000,
        limit_usd=Decimal("1.00"),
    )
    for _ in range(5):
        rm = fake_rotation_manager(monkeypatch, [a], team_id="team-7")
        rm.rotate(
            lambda inst: "ok",
            ability="charge.create",
            usd_quota=quota,
        )
    assert quota.consumed_usd == Decimal("0.50")
