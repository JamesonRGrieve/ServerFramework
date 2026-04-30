"""CostModel contract for per-tenant cost observability (Item 84).

Each provider with billable upstream calls declares a ``CostModel``: a
``cost(request, response) -> Decimal`` callable that returns the cost
of a single call in the deployment's configured base currency (USD by
default). The framework consumes the cost model in two places:

  1. **Metric emission.** A counter
     ``provider_cost_usd_total{tenant, provider, ability}`` accumulates
     per-tenant spend so a finance dashboard can answer "which tenant
     spent the most on $PROVIDER last month?" without a custom report.
  2. **Audit log.** Per-request cost is written into the structured
     audit log (Item 56 retention applies; cost rows typically need
     7-year retention for finance compliance — use ``sox_default()``).

A daily roll-up service (built on Item 28's ``ScheduledService``)
aggregates audit-log rows into a ``CostSummary`` table for fast
queries; the audit log remains the source of truth.

This module ships the type surface and a few common cost models
(constant-per-call, token-based for AI/LLM, percentage-of-amount for
payment processors). The rotation-system and audit-log integrations
are separate landings.

Cross-references:
  - Item 19 — quota system extends to per-tenant USD caps (the same
    pre-decrement / post-true-up pattern Item 43 specifies for tokens
    applies to dollars).
  - Item 34 — metrics emission backend (Prometheus / OTel / Statsd).
  - Item 43 — AI/LLM template declares a token-based ``CostModel``.
  - Item 56 — audit retention applies to cost rows; SOX preset
    (`sox_default()`) is the typical choice (7 years).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Optional, Protocol


# ---------------------------------------------------------------------------
# CostModel protocol
# ---------------------------------------------------------------------------


class CostModel(Protocol):
    """Pure callable: ``(request, response) -> Decimal`` returning the
    cost of a single upstream call.

    Implementations must:
      - return a non-negative ``Decimal`` (zero is valid for free
        calls; negative is invalid and rejected at metric emission).
      - be **pure** — no I/O, no global state. The model is invoked
        from the rotation system's outbound-call path and must not
        block.
      - return ``Decimal`` (not ``float``) — finance arithmetic is
        not lossy. The framework rounds to deployment-configured
        precision at metric emission time, not in the model.

    Currency conversion is **out of scope**: a deployment with multi-
    currency upstreams declares one base currency and every CostModel
    returns in that currency. Conversion at the upstream boundary is
    the operator's responsibility.
    """

    def __call__(self, request: Any, response: Any) -> Decimal: ...


# ---------------------------------------------------------------------------
# Common cost models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConstantCostModel:
    """A flat per-call cost. Useful for upstreams with a fixed fee per
    request regardless of payload (some webhook ingestion services,
    some SMS providers)."""

    per_call_usd: Decimal

    def __call__(self, request: Any, response: Any) -> Decimal:
        return self.per_call_usd


@dataclass(frozen=True)
class TokenBasedCostModel:
    """AI/LLM-style cost: prompt tokens × prompt price + completion
    tokens × completion price + a fixed per-request component.

    The model is invoked **after** the response returns so token counts
    are available; for streaming completions the framework calls the
    model incrementally per chunk so a runaway stream is debited as it
    accumulates rather than after the full response (Item 43 tie-in).

    ``response`` must expose ``prompt_tokens`` and ``completion_tokens``
    attributes (or dict keys); typed Pydantic response models from the
    AI/LLM abstract template (Item 43) provide this shape uniformly.
    """

    prompt_price_per_1k: Decimal
    completion_price_per_1k: Decimal
    per_request_fixed_usd: Decimal = Decimal("0")

    @staticmethod
    def _get(obj: Any, key: str, default: int = 0) -> int:
        if obj is None:
            return default
        if isinstance(obj, dict):
            return int(obj.get(key, default))
        return int(getattr(obj, key, default))

    def __call__(self, request: Any, response: Any) -> Decimal:
        prompt = self._get(response, "prompt_tokens")
        completion = self._get(response, "completion_tokens")
        prompt_cost = (Decimal(prompt) / Decimal(1000)) * self.prompt_price_per_1k
        completion_cost = (
            Decimal(completion) / Decimal(1000)
        ) * self.completion_price_per_1k
        return prompt_cost + completion_cost + self.per_request_fixed_usd


@dataclass(frozen=True)
class PercentOfAmountCostModel:
    """Payment-processor-style cost: a percentage of the transaction
    amount plus a fixed per-transaction component (e.g. Stripe's
    "2.9% + $0.30").

    ``request`` must expose an ``amount`` attribute (or dict key) in
    the deployment's base currency. Negative amounts (refunds) produce
    negative costs by design — the framework's cost counter is a
    monotonic counter, so negative-cost rows are written to the audit
    log only and excluded from the metric stream (operators see
    refunds in audit but the counter does not de-accumulate)."""

    percent: Decimal
    per_transaction_usd: Decimal = Decimal("0")

    @staticmethod
    def _get(obj: Any, key: str, default: Decimal = Decimal("0")) -> Decimal:
        if obj is None:
            return default
        if isinstance(obj, dict):
            return Decimal(str(obj.get(key, default)))
        v = getattr(obj, key, default)
        return Decimal(str(v))

    def __call__(self, request: Any, response: Any) -> Decimal:
        amount = self._get(request, "amount")
        return (amount * self.percent / Decimal(100)) + self.per_transaction_usd


@dataclass(frozen=True)
class FreeCostModel:
    """Zero-cost. Provider explicitly declares "free" (e.g. health
    checks, public data fetches) so the absence of a cost model on the
    provider class is distinguishable from "this is free"."""

    def __call__(self, request: Any, response: Any) -> Decimal:
        return Decimal("0")


# ---------------------------------------------------------------------------
# Per-tenant cap (Item 84 / Item 19 interaction)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TenantCostCap:
    """Per-tenant USD ceiling that pairs with the ``CostModel``. When
    accumulated cost in the configured window exceeds the cap, the
    framework triggers the configured branch:

    - ``hard_fail``: subsequent calls raise ``QuotaExhaustedError``
      until the window rolls over or an operator extends the cap.
    - ``warn_and_continue``: subsequent calls succeed but emit a
      ``QuotaOverrunWarning`` in the response envelope; metrics surface
      the overrun for operator dashboarding.

    This is the dollar-side counterpart to Item 19's per-tenant token
    quotas and Item 43's pre-estimate / post-true-up pattern.
    """

    cap_usd: Decimal
    window: str = "30d"  # parsed via extensions.RetentionPolicy.parse_window
    behavior: str = "hard_fail"  # or "warn_and_continue"

    def __post_init__(self) -> None:
        if self.behavior not in ("hard_fail", "warn_and_continue"):
            raise ValueError(
                f"TenantCostCap.behavior must be 'hard_fail' or "
                f"'warn_and_continue'; got {self.behavior!r}"
            )
        # Validate window via the shared parser to fail at construction
        # time rather than at the first cap-check.
        from serverframework.extensions.RetentionPolicy import parse_window

        parse_window(self.window)


__all__ = [
    "CostModel",
    "ConstantCostModel",
    "TokenBasedCostModel",
    "PercentOfAmountCostModel",
    "FreeCostModel",
    "TenantCostCap",
]
