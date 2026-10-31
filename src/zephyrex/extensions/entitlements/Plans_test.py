# SPDX-License-Identifier: AGPL-3.0-or-later
"""Plans: registration, the free plan, which provider plans buy which plan,
and what to upgrade to."""

import pytest

from zephyrex.extensions.entitlements import Plans
from zephyrex.extensions.entitlements.Plans import (
    FREE,
    Plan,
    best,
    default_plan,
    plan_bought_by,
    plans_allowing,
    plans_with_feature,
    register_plan,
)

FREE_PLAN = Plan(
    name="basic", rank=0, features=frozenset({"map"}), limits={"people": 10}
)
PLUS = Plan(
    name="plus",
    rank=1,
    features=frozenset({"map", "calendar_sync"}),
    limits={"people": None},
    seats=4,
)


@pytest.fixture
def plans(monkeypatch):
    monkeypatch.setattr(Plans, "PLANS", {})
    register_plan(FREE_PLAN)
    register_plan(PLUS)
    monkeypatch.setenv("ENTITLEMENT_PLUS_PLAN_IDS", "price_monthly, price_yearly")


def test_with_nothing_registered_everyone_has_an_empty_free_plan(monkeypatch):
    monkeypatch.setattr(Plans, "PLANS", {})
    plan = default_plan()
    assert (plan.name, plan.features, plan.seats) == (FREE, frozenset(), 0)


def test_the_cheapest_plan_is_the_default(plans):
    assert default_plan() is FREE_PLAN


def test_a_provider_plan_buys_the_plan_configured_for_it(plans):
    assert plan_bought_by("price_yearly") is PLUS
    assert plan_bought_by("price_unknown") is None
    assert plan_bought_by(None) is None


def test_the_best_plan_wins_and_none_means_the_default(plans):
    assert best([PLUS]) is PLUS
    assert best([]) is FREE_PLAN


def test_upgrade_targets_are_cheapest_first(plans):
    assert plans_with_feature("calendar_sync") == ["plus"]
    assert plans_with_feature("map") == ["basic", "plus"]
    assert plans_allowing("people", 11) == ["plus"]
    assert plans_allowing("people", 5) == ["basic", "plus"]


def test_a_limit_a_plan_does_not_name_is_zero(plans):
    assert FREE_PLAN.limit("seats_in_vault") == 0
    assert PLUS.limit("people") is None


def test_registering_a_name_twice_differently_raises(plans):
    with pytest.raises(ValueError, match="already registered"):
        register_plan(Plan(name="plus", rank=9))
