"""Tests for the AI token-budget quota guard (AIQuota).

Covers the nested overall + per-model reserve/refuse, reconcile credit/debit,
and the guarded_inference orchestration (pre-call refusal + true-up).
"""

import pytest

from zephyrex.extensions.ai.AIQuota import (
    TokenQuotaLedger,
    estimate_tokens,
    guarded_inference,
    true_up_tokens,
)
from zephyrex.extensions.quota.BLL_Quota import QuotaExhaustedError


class TestTokenQuotaLedger:
    def test_per_model_exhaustion_refuses_atomically(self):
        ledger = TokenQuotaLedger(overall_budget=100, per_model_budgets={"gpt": 40})
        ledger.reserve("gpt", 30)
        assert ledger.overall_remaining() == 70
        assert ledger.model_remaining("gpt") == 10

        with pytest.raises(QuotaExhaustedError) as exc:
            ledger.reserve("gpt", 20)  # exceeds the 10 left for the model
        assert "model:gpt" in str(exc.value)
        # A refused reservation spends nothing.
        assert ledger.overall_remaining() == 70
        assert ledger.model_remaining("gpt") == 10

    def test_overall_exhaustion_refuses_even_when_model_unlimited(self):
        ledger = TokenQuotaLedger(overall_budget=50)
        ledger.reserve("x", 40)
        with pytest.raises(QuotaExhaustedError) as exc:
            ledger.reserve("y", 20)
        assert "overall" in str(exc.value)
        assert ledger.overall_remaining() == 10

    def test_reconcile_credits_back_over_estimate(self):
        ledger = TokenQuotaLedger(overall_budget=100, per_model_budgets={"a": 100})
        ledger.reserve("a", 50)
        ledger.reconcile("a", 50, 30)  # actual < estimate -> refund 20
        assert ledger.overall_remaining() == 70
        assert ledger.model_remaining("a") == 70

    def test_reconcile_debits_under_estimate(self):
        ledger = TokenQuotaLedger(overall_budget=100, per_model_budgets={"a": 100})
        ledger.reserve("a", 10)
        ledger.reconcile("a", 10, 25)  # under by 15 -> spend 15 more
        assert ledger.overall_remaining() == 75
        assert ledger.model_remaining("a") == 75

    def test_unlimited_ledger_never_refuses(self):
        ledger = TokenQuotaLedger()
        ledger.reserve("z", 10**9)  # no budget configured -> unlimited
        assert ledger.overall_remaining() is None
        assert ledger.model_remaining("z") is None

    def test_negative_estimate_rejected(self):
        with pytest.raises(ValueError):
            TokenQuotaLedger(overall_budget=10).reserve("a", -1)


class TestEstimateAndTrueUp:
    def test_estimate_is_conservative_upper_bound(self):
        # ~4 chars/token input estimate plus the output ceiling.
        assert estimate_tokens("a" * 40, max_tokens=100) == 10 + 100
        assert estimate_tokens("hi") == 1 + 4096  # default ceiling
        assert estimate_tokens("hi", max_tokens=None, default_ceiling=256) == 1 + 256

    def test_true_up_reads_usage_block(self):
        assert true_up_tokens({"usage": {"total_tokens": 123}}) == 123

    def test_true_up_falls_back_to_length_for_text(self):
        assert true_up_tokens("x" * 40) == 10

    def test_true_up_uses_fallback_for_unknown_shape(self):
        assert true_up_tokens(object(), fallback=7) == 7


class TestGuardedInference:
    @pytest.mark.asyncio
    async def test_reconciles_to_actual_usage(self):
        ledger = TokenQuotaLedger(overall_budget=1000, per_model_budgets={"gpt": 1000})

        async def fake(prompt, **kw):
            return {"usage": {"total_tokens": 5}}

        result = await guarded_inference(ledger, "gpt", "hello", fake, max_tokens=20)
        assert result["usage"]["total_tokens"] == 5
        # estimate=estimate_tokens("hello",20)=1+20=21; reconciled to actual 5.
        assert ledger.overall_remaining() == 995
        assert ledger.model_remaining("gpt") == 995

    @pytest.mark.asyncio
    async def test_refuses_pre_call_without_invoking_model(self):
        ledger = TokenQuotaLedger(overall_budget=5)
        calls = {"n": 0}

        async def fake(prompt, **kw):
            calls["n"] += 1
            return "x"

        with pytest.raises(QuotaExhaustedError):
            await guarded_inference(ledger, "gpt", "p", fake, max_tokens=100)
        assert calls["n"] == 0  # the model was never dispatched
