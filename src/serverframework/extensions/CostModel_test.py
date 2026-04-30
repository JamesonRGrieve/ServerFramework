"""Unit tests for the CostModel contract surface (Item 84)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import pytest

from serverframework.extensions.CostModel import (
    ConstantCostModel,
    CostModel,
    FreeCostModel,
    PercentOfAmountCostModel,
    TenantCostCap,
    TokenBasedCostModel,
)


# ---------- ConstantCostModel ------------------------------------------------


def test_constant_cost_returns_per_call_value():
    m = ConstantCostModel(per_call_usd=Decimal("0.001"))
    assert m(None, None) == Decimal("0.001")


def test_constant_cost_zero_is_valid():
    m = ConstantCostModel(per_call_usd=Decimal("0"))
    assert m(None, None) == Decimal("0")


def test_constant_cost_frozen():
    m = ConstantCostModel(per_call_usd=Decimal("1.00"))
    with pytest.raises(Exception):
        m.per_call_usd = Decimal("2.00")  # type: ignore[misc]


# ---------- TokenBasedCostModel ----------------------------------------------


@dataclass
class _LLMResponse:
    prompt_tokens: int
    completion_tokens: int


def test_token_cost_with_zero_tokens():
    m = TokenBasedCostModel(
        prompt_price_per_1k=Decimal("0.01"),
        completion_price_per_1k=Decimal("0.03"),
    )
    assert m(None, _LLMResponse(prompt_tokens=0, completion_tokens=0)) == Decimal("0")


def test_token_cost_prompt_only():
    m = TokenBasedCostModel(
        prompt_price_per_1k=Decimal("0.01"),
        completion_price_per_1k=Decimal("0.03"),
    )
    # 1000 prompt tokens × $0.01/1k = $0.01
    assert m(None, _LLMResponse(prompt_tokens=1000, completion_tokens=0)) == Decimal(
        "0.01"
    )


def test_token_cost_mixed():
    m = TokenBasedCostModel(
        prompt_price_per_1k=Decimal("0.01"),
        completion_price_per_1k=Decimal("0.03"),
    )
    # 500 prompt × $0.01/1k + 250 completion × $0.03/1k
    # = $0.005 + $0.0075 = $0.0125
    cost = m(None, _LLMResponse(prompt_tokens=500, completion_tokens=250))
    assert cost == Decimal("0.0125")


def test_token_cost_with_per_request_fixed():
    m = TokenBasedCostModel(
        prompt_price_per_1k=Decimal("0.01"),
        completion_price_per_1k=Decimal("0.03"),
        per_request_fixed_usd=Decimal("0.001"),
    )
    cost = m(None, _LLMResponse(prompt_tokens=0, completion_tokens=0))
    assert cost == Decimal("0.001")


def test_token_cost_accepts_dict_response():
    m = TokenBasedCostModel(
        prompt_price_per_1k=Decimal("0.01"),
        completion_price_per_1k=Decimal("0.02"),
    )
    response = {"prompt_tokens": 1000, "completion_tokens": 500}
    cost = m(None, response)
    assert cost == Decimal("0.02")  # 0.01 + 0.01


# ---------- PercentOfAmountCostModel -----------------------------------------


@dataclass
class _PaymentRequest:
    amount: Decimal


def test_percent_of_amount_stripe_like():
    # Stripe's classic "2.9% + $0.30"
    m = PercentOfAmountCostModel(
        percent=Decimal("2.9"),
        per_transaction_usd=Decimal("0.30"),
    )
    cost = m(_PaymentRequest(amount=Decimal("100.00")), None)
    # 2.9% of $100 = $2.90, + $0.30 = $3.20
    assert cost == Decimal("3.20")


def test_percent_of_amount_zero_amount():
    m = PercentOfAmountCostModel(
        percent=Decimal("2.9"),
        per_transaction_usd=Decimal("0.30"),
    )
    cost = m(_PaymentRequest(amount=Decimal("0")), None)
    assert cost == Decimal("0.30")


def test_percent_of_amount_negative_for_refunds():
    # Negative amounts produce negative costs by design — the framework
    # writes these to audit but excludes them from the monotonic
    # counter stream.
    m = PercentOfAmountCostModel(
        percent=Decimal("2.9"),
        per_transaction_usd=Decimal("0"),
    )
    cost = m(_PaymentRequest(amount=Decimal("-50.00")), None)
    assert cost == Decimal("-1.45")


def test_percent_of_amount_dict_request():
    m = PercentOfAmountCostModel(
        percent=Decimal("3.0"),
    )
    cost = m({"amount": "200.00"}, None)
    assert cost == Decimal("6.00")


# ---------- FreeCostModel ----------------------------------------------------


def test_free_cost_is_zero():
    m = FreeCostModel()
    assert m(None, None) == Decimal("0")


def test_free_cost_distinguishable_from_no_model():
    # The whole reason FreeCostModel exists: a provider explicitly
    # declares "this is free" so absence of a cost_model on the
    # provider class is distinguishable from "this is free". A
    # provider declaring `cost_model = FreeCostModel()` opts into the
    # cost-emission pipeline (with zero recorded cost) — a provider
    # leaving `cost_model = None` opts out of cost emission entirely.
    m = FreeCostModel()
    assert callable(m)
    assert m(None, None) == Decimal("0")


# ---------- TenantCostCap ----------------------------------------------------


def test_tenant_cap_default_behavior_is_hard_fail():
    cap = TenantCostCap(cap_usd=Decimal("1000"))
    assert cap.behavior == "hard_fail"
    assert cap.window == "30d"


def test_tenant_cap_warn_and_continue():
    cap = TenantCostCap(
        cap_usd=Decimal("1000"),
        behavior="warn_and_continue",
    )
    assert cap.behavior == "warn_and_continue"


def test_tenant_cap_invalid_behavior_rejected_at_construction():
    with pytest.raises(ValueError):
        TenantCostCap(cap_usd=Decimal("100"), behavior="bogus")


def test_tenant_cap_invalid_window_rejected_at_construction():
    # Window validation reuses extensions.RetentionPolicy.parse_window
    # so the contract for retention windows and cost-cap windows is one
    # contract.
    with pytest.raises(ValueError):
        TenantCostCap(cap_usd=Decimal("100"), window="30days")


def test_tenant_cap_frozen():
    cap = TenantCostCap(cap_usd=Decimal("100"))
    with pytest.raises(Exception):
        cap.cap_usd = Decimal("200")  # type: ignore[misc]


# ---------- Protocol conformance --------------------------------------------


def test_constant_model_satisfies_callable_protocol():
    m: CostModel = ConstantCostModel(per_call_usd=Decimal("0.01"))
    assert m(None, None) == Decimal("0.01")


def test_custom_callable_satisfies_protocol():
    """Any callable returning Decimal is a CostModel — the protocol is
    structural, not nominal."""

    def custom(request, response) -> Decimal:
        return Decimal("0.05")

    m: CostModel = custom
    assert m(None, None) == Decimal("0.05")
