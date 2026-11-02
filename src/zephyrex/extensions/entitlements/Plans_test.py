# SPDX-License-Identifier: AGPL-3.0-or-later
"""Plans: registration, the free plan, which provider plans buy which plan,
products sold once, and what to upgrade to."""

import pytest

from zephyrex.extensions.entitlements import Plans
from zephyrex.extensions.entitlements.Plans import (
    FREE,
    Plan,
    Product,
    best,
    default_plan,
    plan_bought_by,
    plan_bought_with,
    plans_allowing,
    plans_with_feature,
    register_plan,
    register_product,
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
    monkeypatch.setattr(Plans, "PRODUCTS", {})
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


LIFETIME_PLUS = Product(
    id="plus_forever",
    plan="plus",
    amount="19.00",
    currency="USD",
    description="Plus, for good",
)


def test_a_product_buys_its_plan(plans):
    register_product(LIFETIME_PLUS)
    assert plan_bought_with("plus_forever") == PLUS
    assert plan_bought_with("nothing") is None
    assert plan_bought_with(None) is None


def test_a_product_must_buy_a_registered_plan(plans):
    with pytest.raises(ValueError, match="unregistered plan"):
        register_product(
            Product(
                id="gold", plan="gold", amount="5", currency="USD", description="Gold"
            )
        )


def test_registering_a_product_twice_differently_raises(plans):
    register_product(LIFETIME_PLUS)
    register_product(LIFETIME_PLUS)
    with pytest.raises(ValueError, match="already registered"):
        register_product(
            Product(
                id="plus_forever",
                plan="plus",
                amount="9.00",
                currency="USD",
                description="Cheaper",
            )
        )


@pytest.mark.parametrize(
    ("amount", "currency"),
    [("0", "USD"), ("-1", "USD"), ("nineteen", "USD"), ("19", "US"), ("19", "U5D")],
)
def test_a_product_needs_a_positive_amount_and_a_currency_code(amount, currency):
    with pytest.raises(ValueError):
        Product(
            id="bad", plan="plus", amount=amount, currency=currency, description="Bad"
        )
