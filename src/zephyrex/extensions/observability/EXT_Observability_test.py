# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the observability extension's env-driven wiring (#210, #215)."""

import pytest

from zephyrex.extensions.observability.ErrorReporters import (
    RollbarErrorReporter,
    SentryErrorReporter,
)
from zephyrex.extensions.observability.EXT_Observability import (
    EXT_Observability,
    render_metrics_exposition,
)
from zephyrex.lib.Logging import (
    NoopErrorReporter,
    get_error_reporter,
    set_error_reporter,
)
from zephyrex.lib.Metrics import (
    NoopMetricsBackend,
    get_metrics_backend,
    set_metrics_backend,
)


@pytest.fixture(autouse=True)
def _restore_globals():
    """Snapshot + restore the process-global metrics backend and error reporter
    so a wiring test never leaks a backend/reporter into unrelated tests."""
    saved_backend = get_metrics_backend()
    saved_reporter = get_error_reporter()._inner  # unwrap the enriching wrapper
    try:
        yield
    finally:
        set_metrics_backend(saved_backend)
        set_error_reporter(saved_reporter)


class TestMetricsWiring:
    def test_noop_leaves_backend_unchanged(self, monkeypatch):
        set_metrics_backend(NoopMetricsBackend())
        monkeypatch.setenv("METRICS_BACKEND", "noop")
        EXT_Observability._wire_metrics_backend()
        assert isinstance(get_metrics_backend(), NoopMetricsBackend)

    def test_unset_leaves_backend_unchanged(self, monkeypatch):
        set_metrics_backend(NoopMetricsBackend())
        monkeypatch.delenv("METRICS_BACKEND", raising=False)
        EXT_Observability._wire_metrics_backend()
        assert isinstance(get_metrics_backend(), NoopMetricsBackend)

    def test_unknown_backend_warns_and_leaves_noop(self, monkeypatch):
        set_metrics_backend(NoopMetricsBackend())
        monkeypatch.setenv("METRICS_BACKEND", "bogus")
        EXT_Observability._wire_metrics_backend()
        assert isinstance(get_metrics_backend(), NoopMetricsBackend)

    def test_prometheus_wires_backend_when_dep_available(self, monkeypatch):
        pytest.importorskip("prometheus_client")
        from zephyrex.extensions.observability.MetricsBackends import (
            PrometheusMetricsBackend,
        )

        set_metrics_backend(NoopMetricsBackend())
        monkeypatch.setenv("METRICS_BACKEND", "prometheus")
        EXT_Observability._wire_metrics_backend()
        assert isinstance(get_metrics_backend(), PrometheusMetricsBackend)


class TestErrorReporterWiring:
    def test_sentry_dsn_installs_sentry_reporter(self, monkeypatch):
        set_error_reporter(NoopErrorReporter())
        monkeypatch.setenv("SENTRY_DSN", "https://x@example.invalid/1")
        monkeypatch.delenv("ROLLBAR_TOKEN", raising=False)
        EXT_Observability._wire_error_reporter()
        assert isinstance(get_error_reporter()._inner, SentryErrorReporter)

    def test_rollbar_token_installs_rollbar_reporter(self, monkeypatch):
        set_error_reporter(NoopErrorReporter())
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        monkeypatch.setenv("ROLLBAR_TOKEN", "rb-token")
        EXT_Observability._wire_error_reporter()
        assert isinstance(get_error_reporter()._inner, RollbarErrorReporter)

    def test_sentry_preferred_over_rollbar(self, monkeypatch):
        set_error_reporter(NoopErrorReporter())
        monkeypatch.setenv("SENTRY_DSN", "https://x@example.invalid/1")
        monkeypatch.setenv("ROLLBAR_TOKEN", "rb-token")
        EXT_Observability._wire_error_reporter()
        assert isinstance(get_error_reporter()._inner, SentryErrorReporter)

    def test_neither_leaves_reporter_unchanged(self, monkeypatch):
        set_error_reporter(NoopErrorReporter())
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        monkeypatch.delenv("ROLLBAR_TOKEN", raising=False)
        EXT_Observability._wire_error_reporter()
        assert isinstance(get_error_reporter()._inner, NoopErrorReporter)


class TestMetricsExposition:
    def test_noop_backend_has_no_exposition(self):
        set_metrics_backend(NoopMetricsBackend())
        assert render_metrics_exposition() is None

    def test_backend_with_expose_renders(self):
        class _FakeExposer(NoopMetricsBackend):
            def expose(self):
                return (b"# fake\n", "text/plain")

        set_metrics_backend(_FakeExposer())
        assert render_metrics_exposition() == (b"# fake\n", "text/plain")
