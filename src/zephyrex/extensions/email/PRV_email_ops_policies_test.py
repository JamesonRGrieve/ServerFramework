"""Unit tests for Items 96 + 97 — email ops policies + shared HTTP client.

Verifies that each email provider declares the expected ops-policy
ClassVars (`rate_limit`, `degradation_policy`, `cost_model`) and an
overridden `health_check`, and that `health_check` is defensive — it
returns a `HealthReport` with `DOWN` status (rather than raising) when
the upstream is unreachable.

These tests do not exercise real upstream calls; the offline-`health_check`
case relies on a non-routable URL / unconfigured-credentials path so the
probe fails fast without hitting any real network.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from zephyrex.extensions.AbstractExtensionProvider import HealthReport, HealthStatus
from zephyrex.extensions.billing.BLL_CostModel import ConstantCostModel
from zephyrex.extensions.ExternalErrors import DegradationMode
from zephyrex.extensions.RateLimit import RateLimit
from zephyrex.extensions.email.PRV_SendGrid_EMail import (
    SendgridProvider,
    StalwartProvider,
)
from zephyrex.extensions.email.PRV_SMTP2Go_EMail import Smtp2goProvider


pytestmark = pytest.mark.unit


PROVIDERS = [
    pytest.param(SendgridProvider, 10.0, 20, id="sendgrid"),
    pytest.param(StalwartProvider, 50.0, 100, id="stalwart"),
    pytest.param(Smtp2goProvider, 100.0, 200, id="smtp2go"),
]


# Env vars the offline test must clear so each provider's `health_check`
# follows its "missing-credentials" branch and does not contact the real
# upstream. Listed explicitly to avoid the deprecated `_env` dict surface.
PROVIDER_ENV_VARS = {
    "sendgrid": [
        "SENDGRID_API_KEY",
        "SENDGRID_FROM_EMAIL",
    ],
    "stalwart": [
        "STALWART_HOST",
        "STALWART_PORT",
        "STALWART_USERNAME",
        "STALWART_PASSWORD",
        "STALWART_FROM_EMAIL",
        "STALWART_USE_TLS",
    ],
    "smtp2go": [
        "SMTP2GO_API_KEY",
        "SMTP2GO_FROM_EMAIL",
        "SMTP2GO_API_URL",
    ],
}


class TestOpsPolicyDeclarations:
    """Each of the three providers declares the four ClassVars."""

    @pytest.mark.parametrize("provider,rps,burst", PROVIDERS)
    def test_rate_limit_declared_with_documented_defaults(
        self, provider, rps, burst
    ):
        assert isinstance(provider.rate_limit, RateLimit)
        assert provider.rate_limit.rps == rps
        assert provider.rate_limit.burst == burst

    @pytest.mark.parametrize("provider,rps,burst", PROVIDERS)
    def test_degradation_policy_is_fail_fast(self, provider, rps, burst):
        assert provider.degradation_policy is not None
        assert provider.degradation_policy.mode == DegradationMode.FAIL_FAST

    @pytest.mark.parametrize("provider,rps,burst", PROVIDERS)
    def test_cost_model_is_constant(self, provider, rps, burst):
        assert isinstance(provider.cost_model, ConstantCostModel)
        assert provider.cost_model.per_call_usd == Decimal("0.0001")

    @pytest.mark.parametrize("provider,rps,burst", PROVIDERS)
    def test_health_check_is_overridden_method(self, provider, rps, burst):
        # `health_check` is defined directly on each provider class (not
        # only inherited from `AbstractStaticProvider`).
        assert "health_check" in provider.__dict__


class TestHealthCheckOfflinePath:
    """`health_check` is defensive: with credentials missing or upstream
    unreachable, it returns a `HealthReport` with `DOWN` status rather
    than raising."""

    @pytest.mark.parametrize("provider,rps,burst", PROVIDERS)
    def test_returns_health_report_when_unconfigured(
        self, provider, rps, burst, monkeypatch
    ):
        # Strip every env var the provider knows about so the offline
        # path is exercised. `health_check` must not raise; it must
        # return a `HealthReport` with `DOWN` status.
        for var in PROVIDER_ENV_VARS[provider.name]:
            monkeypatch.delenv(var, raising=False)
        report = provider.health_check()
        assert isinstance(report, HealthReport)
        assert report.status is HealthStatus.DOWN
