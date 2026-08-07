"""Item 34 — tests for the per-provider health gauge emitter."""
from __future__ import annotations

from enum import Enum

import pytest

from zephyrex.extensions.AbstractExtensionProvider import HealthStatus
from zephyrex.lib.Metrics import (
    InMemoryMetricsBackend,
    emit_provider_health_gauge,
    reset_metrics_backend,
    set_metrics_backend,
)


@pytest.fixture(autouse=True)
def _restore_default_backend():
    reset_metrics_backend()
    yield
    reset_metrics_backend()


@pytest.mark.unit
def test_emit_health_gauge_ok_emits_one_point_zero():
    backend = InMemoryMetricsBackend()
    set_metrics_backend(backend)
    emit_provider_health_gauge("stripe", HealthStatus.OK)
    key = (
        "provider_health_status",
        frozenset({("provider", "stripe"), ("status", "ok")}),
    )
    assert backend.gauges[key] == 1.0


@pytest.mark.unit
def test_emit_health_gauge_degraded_emits_zero_point_five():
    backend = InMemoryMetricsBackend()
    set_metrics_backend(backend)
    emit_provider_health_gauge("paypal", HealthStatus.DEGRADED)
    key = (
        "provider_health_status",
        frozenset({("provider", "paypal"), ("status", "degraded")}),
    )
    assert backend.gauges[key] == 0.5


@pytest.mark.unit
def test_emit_health_gauge_down_emits_zero():
    backend = InMemoryMetricsBackend()
    set_metrics_backend(backend)
    emit_provider_health_gauge("stripe", HealthStatus.DOWN)
    key = (
        "provider_health_status",
        frozenset({("provider", "stripe"), ("status", "down")}),
    )
    assert backend.gauges[key] == 0.0


@pytest.mark.unit
def test_emit_health_gauge_labels_carry_provider_and_status():
    backend = InMemoryMetricsBackend()
    set_metrics_backend(backend)
    for status in (HealthStatus.OK, HealthStatus.DEGRADED, HealthStatus.DOWN):
        emit_provider_health_gauge("acme", status)
    # All three samples land under different (status) labels but the
    # same provider label; the backend keys them distinctly.
    statuses = {
        dict(label_set).get("status")
        for (name, label_set) in backend.gauges
        if name == "provider_health_status"
    }
    assert statuses == {"ok", "degraded", "down"}
    providers = {
        dict(label_set).get("provider")
        for (name, label_set) in backend.gauges
        if name == "provider_health_status"
    }
    assert providers == {"acme"}


@pytest.mark.unit
def test_emit_health_gauge_accepts_bare_string():
    backend = InMemoryMetricsBackend()
    set_metrics_backend(backend)
    emit_provider_health_gauge("plain", "OK")
    key = (
        "provider_health_status",
        frozenset({("provider", "plain"), ("status", "ok")}),
    )
    assert backend.gauges[key] == 1.0


@pytest.mark.unit
def test_emit_health_gauge_unknown_status_defaults_to_zero():
    backend = InMemoryMetricsBackend()
    set_metrics_backend(backend)
    emit_provider_health_gauge("plain", "garbled")
    key = (
        "provider_health_status",
        frozenset({("provider", "plain"), ("status", "garbled")}),
    )
    assert backend.gauges[key] == 0.0


@pytest.mark.unit
def test_emit_health_gauge_swallows_backend_failures():
    """A misbehaving backend's gauge must NEVER raise into the caller."""
    from zephyrex.lib.Metrics import MetricsBackend

    class BoomBackend(MetricsBackend):
        def counter(self, *a, **k):
            raise RuntimeError("boom")

        def gauge(self, *a, **k):
            raise RuntimeError("boom")

        def histogram(self, *a, **k):
            raise RuntimeError("boom")

        def span(self, *a, **k):
            raise RuntimeError("boom")

    set_metrics_backend(BoomBackend())
    # Should not raise.
    emit_provider_health_gauge("p", HealthStatus.OK)
