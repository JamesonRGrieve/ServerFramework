"""Tests for unified Quota model + helpers (Item 19)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from zephyrex.extensions.quota.BLL_Quota import (
    NoInJurisdictionProviderError,
    NoProviderInstanceError,
    Quota,
    QuotaExhaustedError,
    derive_period_key,
    find_matching_quotas,
    resolve_provider_instance,
    try_consume,
)


def test_derive_period_key_formats():
    t = datetime(2026, 4, 28, 15, 30, 0, tzinfo=timezone.utc)
    assert derive_period_key("minute", now=t) == "2026-04-28T15:30"
    assert derive_period_key("hour", now=t) == "2026-04-28T15:00"
    assert derive_period_key("day", now=t) == "2026-04-28"
    assert derive_period_key("month", now=t) == "2026-04"


def test_derive_period_key_billing_cycle_passthrough():
    assert derive_period_key("billing_cycle") == "billing_cycle"


def test_derive_period_key_unknown():
    with pytest.raises(ValueError):
        derive_period_key("year")


def test_derive_period_key_naive_datetime_treated_as_utc():
    naive = datetime(2026, 4, 28, 15, 30, 0)
    assert derive_period_key("hour", now=naive) == "2026-04-28T15:00"


def _row(**kwargs) -> Quota:
    base = dict(
        ability="openai.complete",
        period="day",
        period_key="2026-04-28",
        limit=100,
        consumed=0,
        unit="token",
    )
    base.update(kwargs)
    return Quota(**base)


def test_find_matching_user_only():
    now = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)
    q = _row(user_id="u1", team_id=None)
    matches = find_matching_quotas(
        [q], user_id="u1", team_id=None, ability="openai.complete", now=now
    )
    assert matches == [q]


def test_find_matching_team_only():
    now = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)
    q = _row(user_id=None, team_id="t1")
    matches = find_matching_quotas(
        [q], user_id="u1", team_id="t1", ability="openai.complete", now=now
    )
    assert matches == [q]


def test_find_matching_user_within_team():
    now = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)
    q = _row(user_id="u1", team_id="t1")
    matches = find_matching_quotas(
        [q], user_id="u1", team_id="t1", ability="openai.complete", now=now
    )
    assert matches == [q]


def test_find_matching_excludes_other_user():
    now = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)
    q = _row(user_id="other", team_id=None)
    assert (
        find_matching_quotas(
            [q], user_id="u1", team_id=None, ability="openai.complete", now=now
        )
        == []
    )


def test_find_matching_period_key_mismatch():
    now = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)
    q = _row(user_id="u1", team_id=None, period_key="2026-04-27")
    assert (
        find_matching_quotas(
            [q], user_id="u1", team_id=None, ability="openai.complete", now=now
        )
        == []
    )


def test_find_matching_returns_all_overlapping_rows():
    now = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)
    rows = [
        _row(user_id="u1", team_id=None),
        _row(user_id=None, team_id="t1"),
        _row(user_id="u1", team_id="t1"),
        _row(user_id="other", team_id=None),
    ]
    matches = find_matching_quotas(
        rows, user_id="u1", team_id="t1", ability="openai.complete", now=now
    )
    assert len(matches) == 3


def test_try_consume_decrements_all_rows():
    a = _row(user_id="u1", team_id=None, limit=10, consumed=0)
    b = _row(user_id=None, team_id="t1", limit=20, consumed=5)
    assert try_consume([a, b], amount=3) is True
    assert a.consumed == 3
    assert b.consumed == 8


def test_try_consume_refuses_if_any_row_exhausted_and_leaves_unchanged():
    a = _row(user_id="u1", team_id=None, limit=10, consumed=8)
    b = _row(user_id=None, team_id="t1", limit=5, consumed=4)
    # b has only 1 left, requesting 3 must fail and leave both unchanged.
    assert try_consume([a, b], amount=3) is False
    assert a.consumed == 8
    assert b.consumed == 4


def test_try_consume_no_quotas_returns_false():
    assert try_consume([], amount=1) is False


def test_try_consume_negative_amount_raises():
    a = _row(user_id="u1", team_id=None)
    with pytest.raises(ValueError):
        try_consume([a], amount=-1)


def test_quota_predicates():
    q = _row(limit=10, consumed=7)
    assert q.remaining() == 3
    assert not q.is_exhausted(additional=3)
    assert q.is_exhausted(additional=4)


def test_errors_carry_attributes():
    e = QuotaExhaustedError(scope="user", ability="openai.complete", period="day")
    assert e.scope == "user"
    assert e.ability == "openai.complete"
    assert e.period == "day"

    npi = NoProviderInstanceError(requester_id="u1", ability="x")
    assert npi.requester_id == "u1"

    nij = NoInJurisdictionProviderError(
        requester_id="u1", ability="x", jurisdiction="EU"
    )
    assert nij.jurisdiction == "EU"


def test_resolve_provider_instance_is_stub():
    with pytest.raises(NotImplementedError):
        resolve_provider_instance(requester_id="u1", ability="openai.complete")
