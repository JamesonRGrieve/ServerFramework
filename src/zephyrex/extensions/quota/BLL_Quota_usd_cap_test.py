"""Item 84 — tests for the per-tenant USD-cap fields on ``Quota`` and
the rotation-side pre-check / post-call true-up flow."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, List
from unittest.mock import MagicMock

import pytest

from zephyrex.extensions.billing.BLL_CostModel import ConstantCostModel
from zephyrex.logic.BLL_Providers import (
    RotationManager,
    reset_auth_cooldowns,
    reset_sticky_sessions,
)
from zephyrex.extensions.quota.BLL_Quota import Quota, QuotaExhaustedError


def _make_rotation_manager_with_fake_instances(instances: List[Any]) -> RotationManager:
    rm = RotationManager(model_registry=None, requester_id="r1")
    rm.target_id = "rotation-1"
    rm.requester = MagicMock(id="r1", team_id="team-7")
    fake_rpis = [
        MagicMock(provider_instance_id=f"pi-{i}") for i in range(len(instances))
    ]
    rm._get_ordered_rotation_provider_instances = lambda: fake_rpis  # type: ignore
    pi_map = {f"pi-{i}": inst for i, inst in enumerate(instances)}
    from zephyrex.logic import BLL_Providers as _bll_mod

    original_db = _bll_mod.ProviderInstanceModel.DB

    class _FakeDB:
        def __init__(self, *a, **k):
            pass

        def get(self, *a, **k):
            pi_id = k.get("id")
            return pi_map.get(str(pi_id))

    rm.model_registry = MagicMock()
    rm.model_registry.DB.Base = object()
    rm.model_registry.DB.get_session.return_value.__enter__ = lambda self_: None
    rm.model_registry.DB.get_session.return_value.__exit__ = lambda *a: None
    _bll_mod.ProviderInstanceModel.DB = lambda base: _FakeDB()
    rm._restore_db = lambda: setattr(
        _bll_mod.ProviderInstanceModel, "DB", original_db
    )
    return rm


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
def test_pre_check_exceeding_cap_raises_quota_exhausted():
    a = MagicMock()
    a.name = "prov-A"
    a.provider_class = type(
        "P", (), {"cost_model": ConstantCostModel(per_call_usd=Decimal("5.00"))}
    )
    rm = _make_rotation_manager_with_fake_instances([a])
    quota = Quota(
        ability="charge.create",
        period="day",
        period_key="2026-04-30",
        limit=1000,
        limit_usd=Decimal("4.00"),
        consumed_usd=Decimal("0"),
    )

    try:
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
    finally:
        rm._restore_db()


@pytest.mark.unit
def test_pre_check_within_cap_succeeds_and_post_call_increments_consumed_usd():
    a = MagicMock()
    a.name = "prov-A"
    a.provider_class = type(
        "P", (), {"cost_model": ConstantCostModel(per_call_usd=Decimal("0.25"))}
    )
    rm = _make_rotation_manager_with_fake_instances([a])
    quota = Quota(
        ability="charge.create",
        period="day",
        period_key="2026-04-30",
        limit=1000,
        limit_usd=Decimal("10.00"),
        consumed_usd=Decimal("0"),
    )

    try:
        result = rm.rotate(
            lambda inst: "ok",
            ability="charge.create",
            usd_quota=quota,
        )
        assert result == "ok"
        assert quota.consumed_usd == Decimal("0.25")
    finally:
        rm._restore_db()


@pytest.mark.unit
def test_pre_check_skipped_when_limit_usd_is_none_back_compat():
    """A `limit_usd=None` row preserves token/call-only behavior; the
    rotation never refuses on a USD basis even when a cost model exists."""
    a = MagicMock()
    a.name = "prov-A"
    a.provider_class = type(
        "P", (), {"cost_model": ConstantCostModel(per_call_usd=Decimal("100.00"))}
    )
    rm = _make_rotation_manager_with_fake_instances([a])
    quota = Quota(
        ability="charge.create",
        period="day",
        period_key="2026-04-30",
        limit=1000,
        # limit_usd left None — back-compat row.
    )

    try:
        result = rm.rotate(
            lambda inst: "ok",
            ability="charge.create",
            usd_quota=quota,
        )
        assert result == "ok"
        # consumed_usd must remain zero: no cap means no true-up either.
        assert quota.consumed_usd == Decimal("0")
    finally:
        rm._restore_db()


@pytest.mark.unit
def test_no_usd_quota_kwarg_preserves_existing_behavior():
    """Calls that omit the `usd_quota` kwarg work exactly as before."""
    a = MagicMock()
    a.name = "prov-A"
    a.provider_class = type(
        "P", (), {"cost_model": ConstantCostModel(per_call_usd=Decimal("0.25"))}
    )
    rm = _make_rotation_manager_with_fake_instances([a])

    try:
        result = rm.rotate(lambda inst: "ok", ability="charge.create")
        assert result == "ok"
    finally:
        rm._restore_db()


@pytest.mark.unit
def test_repeated_calls_accumulate_consumed_usd():
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
    try:
        for _ in range(5):
            rm = _make_rotation_manager_with_fake_instances([a])
            try:
                rm.rotate(
                    lambda inst: "ok",
                    ability="charge.create",
                    usd_quota=quota,
                )
            finally:
                rm._restore_db()
        assert quota.consumed_usd == Decimal("0.50")
    finally:
        # Make sure cleanup ran even if a rotate() call raised.
        pass
