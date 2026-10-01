# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the observability extension's env-driven wiring (#210, #215)."""

import warnings

import pytest
import sentry_sdk
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

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
    installed_error_reporter,
    set_error_reporter,
)
from zephyrex.lib.Metrics import (
    NoopMetricsBackend,
    get_metrics_backend,
    set_metrics_backend,
)


@pytest.fixture(autouse=True)
def _restore_globals():
    """Snapshot + restore the process-global metrics backend, error reporter
    and Sentry client so a wiring test never leaks one into unrelated tests."""
    saved_backend = get_metrics_backend()
    saved_reporter = installed_error_reporter()
    saved_sentry_client = sentry_sdk.get_client()
    try:
        yield
    finally:
        set_metrics_backend(saved_backend)
        set_error_reporter(saved_reporter)
        if sentry_sdk.get_client() is not saved_sentry_client:
            sentry_sdk.get_client().close()
            sentry_sdk.get_global_scope().set_client(saved_sentry_client)


class _CapturingTransport(Transport):
    """Keeps every envelope Sentry would send."""

    def __init__(self) -> None:
        super().__init__()
        self.envelopes: list[Envelope] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        self.envelopes.append(envelope)


class TestSentryReporter:
    def test_report_sends_the_exception_with_its_context(self):
        """The reporter uses sentry-sdk 2.x's scope API: ``Hub`` and
        ``push_scope`` are deprecated there, and the next major removes them."""
        transport = _CapturingTransport()
        sentry_sdk.init(
            dsn="https://key@example.invalid/1",
            transport=transport,
            default_integrations=False,
        )
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            reporter = SentryErrorReporter()
            reporter.report(ValueError("boom"), {"request_id": "r-1"})

        events = [e.get_event() for e in transport.envelopes]
        assert len(events) == 1
        event = events[0]
        assert event is not None
        assert event["exception"]["values"][0]["value"] == "boom"
        assert event["extra"]["request_id"] == "r-1"


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

    def test_repeated_initialization_keeps_the_installed_backend(self, monkeypatch):
        """Every app build re-initializes the extension; a second Prometheus
        backend would collide with the first one's collectors in the
        process-global registry, so the installed backend must be kept."""
        pytest.importorskip("prometheus_client")
        set_metrics_backend(NoopMetricsBackend())
        monkeypatch.setenv("METRICS_BACKEND", "prometheus")
        assert EXT_Observability.on_initialize() is True
        installed = get_metrics_backend()
        assert EXT_Observability.on_initialize() is True
        assert get_metrics_backend() is installed


class TestErrorReporterWiring:
    def test_sentry_dsn_installs_sentry_reporter(self, monkeypatch):
        set_error_reporter(NoopErrorReporter())
        monkeypatch.setenv("SENTRY_DSN", "https://x@example.invalid/1")
        monkeypatch.delenv("ROLLBAR_TOKEN", raising=False)
        EXT_Observability._wire_error_reporter()
        assert isinstance(installed_error_reporter(), SentryErrorReporter)

    def test_rollbar_token_installs_rollbar_reporter(self, monkeypatch):
        set_error_reporter(NoopErrorReporter())
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        monkeypatch.setenv("ROLLBAR_TOKEN", "rb-token")
        EXT_Observability._wire_error_reporter()
        assert isinstance(installed_error_reporter(), RollbarErrorReporter)

    def test_sentry_preferred_over_rollbar(self, monkeypatch):
        set_error_reporter(NoopErrorReporter())
        monkeypatch.setenv("SENTRY_DSN", "https://x@example.invalid/1")
        monkeypatch.setenv("ROLLBAR_TOKEN", "rb-token")
        EXT_Observability._wire_error_reporter()
        assert isinstance(installed_error_reporter(), SentryErrorReporter)

    def test_neither_leaves_reporter_unchanged(self, monkeypatch):
        set_error_reporter(NoopErrorReporter())
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        monkeypatch.delenv("ROLLBAR_TOKEN", raising=False)
        EXT_Observability._wire_error_reporter()
        assert isinstance(installed_error_reporter(), NoopErrorReporter)


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
