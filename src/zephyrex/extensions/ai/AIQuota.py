"""Token-budget quota guard for AI inference.

Implements the pre-estimate -> reserve -> call -> true-up flow that the
framework's AI provider contract (``zephyrex.extensions.PRV_Abstract_AI``)
declares but leaves unimplemented. It enforces a *nested* budget: one OVERALL
token budget across all models plus optional PER-MODEL budgets. A call is
refused before dispatch (``QuotaExhaustedError``) when its conservative
pre-estimate would exceed EITHER the overall or the model budget; after the
call the ledger reconciles against actual usage, crediting back an
over-estimate and debiting an under-estimate.

Budgets are supplied by the caller (``None`` = unlimited), so the ledger is
self-contained and makes no assumption about where budgets are stored. A
durable / cross-process ledger and integration with the rotation-based
selection in ``zephyrex.extensions.quota.BLL_Quota`` are follow-ons; this
delivers the token-accounting core those systems consume.
"""

from __future__ import annotations

import threading
from typing import Any, Awaitable, Callable, Dict, Optional

from zephyrex.extensions.quota.BLL_Quota import QuotaExhaustedError

__all__ = [
    "TokenQuotaLedger",
    "estimate_tokens",
    "true_up_tokens",
    "guarded_inference",
]


class TokenQuotaLedger:
    """A nested token ledger: one overall budget plus optional per-model budgets.

    Thread-safe. A ``None`` budget means "unlimited" for that scope, so a ledger
    with no budgets configured never refuses — enforcement is strictly opt-in.
    """

    def __init__(
        self,
        overall_budget: Optional[int] = None,
        per_model_budgets: Optional[Dict[str, int]] = None,
    ) -> None:
        self._lock = threading.Lock()
        self._overall_budget = overall_budget
        self._overall_spent = 0
        self._model_budgets: Dict[str, int] = dict(per_model_budgets or {})
        self._model_spent: Dict[str, int] = {}

    def overall_remaining(self) -> Optional[int]:
        """Tokens left in the overall budget, or ``None`` when unlimited."""
        if self._overall_budget is None:
            return None
        return self._overall_budget - self._overall_spent

    def model_remaining(self, model: str) -> Optional[int]:
        """Tokens left for ``model``, or ``None`` when that model is unlimited."""
        budget = self._model_budgets.get(model)
        if budget is None:
            return None
        return budget - self._model_spent.get(model, 0)

    def reserve(self, model: str, estimate: int) -> None:
        """Pre-decrement the overall and per-model budgets by ``estimate``.

        Refuses the call with :class:`QuotaExhaustedError` when EITHER budget
        would be exceeded — the overall scope is checked first. On refusal no
        budget is spent (the reservation is atomic).
        """
        if estimate < 0:
            raise ValueError("estimate must be non-negative")
        with self._lock:
            overall_rem = self.overall_remaining()
            if overall_rem is not None and estimate > overall_rem:
                raise QuotaExhaustedError(
                    scope="overall", ability="inference", period="token-budget"
                )
            model_rem = self.model_remaining(model)
            if model_rem is not None and estimate > model_rem:
                raise QuotaExhaustedError(
                    scope=f"model:{model}",
                    ability="inference",
                    period="token-budget",
                )
            self._overall_spent += estimate
            self._model_spent[model] = self._model_spent.get(model, 0) + estimate

    def reconcile(self, model: str, estimate: int, actual: int) -> None:
        """Reconcile a prior :meth:`reserve` against the actual token usage.

        ``actual < estimate`` credits the over-estimate back; ``actual >
        estimate`` debits the overage. Spent balances never fall below zero.
        """
        if actual < 0:
            actual = 0
        delta = actual - estimate  # >0: under-estimated (spend more); <0: refund
        with self._lock:
            self._overall_spent = max(0, self._overall_spent + delta)
            self._model_spent[model] = max(0, self._model_spent.get(model, 0) + delta)


def estimate_tokens(
    prompt: str,
    max_tokens: Optional[int] = None,
    default_ceiling: int = 4096,
) -> int:
    """Conservative pre-estimate of the tokens a call MAY consume.

    A rough input-token count (~4 chars/token) plus the per-call output ceiling
    (``max_tokens``, or ``default_ceiling`` when unset). Providers with a real
    tokenizer should supply a tighter estimate; this upper bound keeps the
    pre-call quota check safe (never under-reserves).
    """
    input_tokens = max(1, len(prompt) // 4)
    ceiling = max_tokens if max_tokens is not None else default_ceiling
    return input_tokens + max(0, int(ceiling))


def true_up_tokens(response: Any, *, fallback: Optional[int] = None) -> int:
    """Actual tokens consumed, for reconciliation.

    Reads an upstream ``usage.total_tokens`` block when the response carries one;
    otherwise estimates a text response by length, or returns ``fallback`` when
    the shape is unknown and a fallback is given.
    """
    if isinstance(response, dict):
        usage = response.get("usage") or {}
        total = usage.get("total_tokens")
        if isinstance(total, (int, float)):
            return int(total)
    if isinstance(response, str):
        return max(1, len(response) // 4)
    if fallback is not None:
        return fallback
    return max(1, len(str(response)) // 4)


async def guarded_inference(
    ledger: TokenQuotaLedger,
    model: str,
    prompt: str,
    inference_fn: Callable[..., Awaitable[Any]],
    *,
    max_tokens: Optional[int] = None,
    default_ceiling: int = 4096,
    **inference_kwargs: Any,
) -> Any:
    """Run ``inference_fn(prompt, **inference_kwargs)`` under a nested token budget.

    Pre-estimates the call, reserves against the overall + per-model budgets
    (raising :class:`QuotaExhaustedError` *before* dispatch when either would be
    exceeded), invokes the inference, then reconciles the reservation against the
    actual usage. This is the pre-estimate -> reserve -> call -> true-up flow the
    framework's ``PRV_Abstract_AI`` contract declares; a provider opts in by
    calling it with its own ``inference`` coroutine.
    """
    estimate = estimate_tokens(prompt, max_tokens, default_ceiling)
    ledger.reserve(model, estimate)
    response = await inference_fn(prompt, **inference_kwargs)
    actual = true_up_tokens(response, fallback=estimate)
    ledger.reconcile(model, estimate, actual)
    return response
