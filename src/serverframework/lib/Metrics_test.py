"""Item 34 — tests for the pluggable metrics + tracing surface.

These tests exercise the public contract end-to-end without mocks:
``InMemoryMetricsBackend`` is a real backend that captures every
emission, and the Prometheus / OpenTelemetry tests skip when the
optional SDK is not installed (matching the no-mock pillar from
AGENTS.md).
"""
from __future__ import annotations

import importlib.util

import pytest

from serverframework.lib.Metrics import (
    InMemoryMetricsBackend,
    MetricsBackend,
    NoopMetricsBackend,
    OpenTelemetryMetricsBackend,
    PrometheusMetricsBackend,
    _current_span_id_var,
    get_metrics_backend,
    reset_metrics_backend,
    set_metrics_backend,
)


@pytest.fixture(autouse=True)
def _restore_default_backend():
    """Each test starts with the default Noop backend installed and
    restores it on teardown so test ordering can never leak state."""
    reset_metrics_backend()
    yield
    reset_metrics_backend()


# ---------------------------------------------------------------------------
# Noop backend
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_noop_backend_counter_gauge_histogram_are_no_ops():
    backend = NoopMetricsBackend()
    # None of these should raise; nothing observable to assert beyond that.
    backend.counter("x")
    backend.counter("x", value=2)
    backend.counter("x", labels={"provider": "p"})
    backend.gauge("y", 1.0)
    backend.gauge("y", 2.0, labels={"provider": "p"})
    backend.histogram("z", 12.5)
    backend.histogram("z", 12.5, labels={"provider": "p"})


@pytest.mark.unit
def test_noop_backend_span_returns_context_manager_doing_nothing():
    backend = NoopMetricsBackend()
    with backend.span("op", a="b") as s:
        # The Noop span yields itself but records nothing; it should
        # still set/reset the active-span contextvar so nesting works.
        assert _current_span_id_var.get() is not None
        assert s is not None
    assert _current_span_id_var.get() is None


# ---------------------------------------------------------------------------
# In-memory backend
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_inmemory_counter_accumulates_across_increments():
    backend = InMemoryMetricsBackend()
    backend.counter("calls", labels={"provider": "stripe"})
    backend.counter("calls", value=2, labels={"provider": "stripe"})
    backend.counter("calls", labels={"provider": "paypal"})

    stripe_key = ("calls", frozenset([("provider", "stripe")]))
    paypal_key = ("calls", frozenset([("provider", "paypal")]))
    assert backend.counters[stripe_key] == 3.0
    assert backend.counters[paypal_key] == 1.0


@pytest.mark.unit
def test_inmemory_gauge_overwrites_last_value():
    backend = InMemoryMetricsBackend()
    backend.gauge("up", 1.0, labels={"provider": "stripe"})
    backend.gauge("up", 0.0, labels={"provider": "stripe"})

    key = ("up", frozenset([("provider", "stripe")]))
    assert backend.gauges[key] == 0.0


@pytest.mark.unit
def test_inmemory_histogram_appends_each_sample():
    backend = InMemoryMetricsBackend()
    backend.histogram("latency_ms", 1.0, labels={"provider": "stripe"})
    backend.histogram("latency_ms", 2.0, labels={"provider": "stripe"})
    backend.histogram("latency_ms", 3.0, labels={"provider": "stripe"})

    key = ("latency_ms", frozenset([("provider", "stripe")]))
    assert backend.histograms[key] == [1.0, 2.0, 3.0]


@pytest.mark.unit
def test_inmemory_counter_no_labels_uses_empty_frozenset_key():
    backend = InMemoryMetricsBackend()
    backend.counter("global")
    backend.counter("global", value=4)
    key = ("global", frozenset())
    assert backend.counters[key] == 5.0


@pytest.mark.unit
def test_inmemory_span_records_basic_metadata():
    backend = InMemoryMetricsBackend()
    with backend.span("op", provider="stripe", attempt=0):
        pass
    assert len(backend.spans) == 1
    rec = backend.spans[0]
    assert rec["name"] == "op"
    assert rec["tags"] == {"provider": "stripe", "attempt": 0}
    assert rec["parent"] is None
    assert rec["duration_ms"] >= 0.0
    assert rec["ended_at"] >= rec["started_at"]


@pytest.mark.unit
def test_inmemory_span_records_nested_parent_pointers():
    backend = InMemoryMetricsBackend()
    with backend.span("rotation.rotate") as parent_record:
        with backend.span("rotation.attempt", attempt=0):
            pass
        with backend.span("rotation.attempt", attempt=1):
            pass

    # Three spans recorded — order is post-exit, so children land first
    # then the parent.
    assert len(backend.spans) == 3
    children = [s for s in backend.spans if s["name"] == "rotation.attempt"]
    parents = [s for s in backend.spans if s["name"] == "rotation.rotate"]
    assert len(children) == 2
    assert len(parents) == 1

    parent = parents[0]
    parent_id = id(parent_record)  # same dict object
    assert parent is parent_record
    for child in children:
        assert child["parent"] == parent_id
    assert parent["parent"] is None


@pytest.mark.unit
def test_inmemory_span_resets_contextvar_on_exit():
    backend = InMemoryMetricsBackend()
    assert _current_span_id_var.get() is None
    with backend.span("op"):
        assert _current_span_id_var.get() is not None
    assert _current_span_id_var.get() is None


@pytest.mark.unit
def test_inmemory_sibling_spans_do_not_leak_parent_pointers():
    backend = InMemoryMetricsBackend()
    with backend.span("a"):
        pass
    with backend.span("b"):
        pass
    assert all(s["parent"] is None for s in backend.spans)


# ---------------------------------------------------------------------------
# set/get_metrics_backend
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_get_metrics_backend_default_is_noop():
    assert isinstance(get_metrics_backend(), NoopMetricsBackend)


@pytest.mark.unit
def test_set_get_metrics_backend_round_trip():
    inmem = InMemoryMetricsBackend()
    set_metrics_backend(inmem)
    assert get_metrics_backend() is inmem


@pytest.mark.unit
def test_set_metrics_backend_rejects_non_backend():
    with pytest.raises(TypeError):
        set_metrics_backend("not a backend")  # type: ignore[arg-type]


@pytest.mark.unit
def test_metrics_backend_is_abstract():
    # Direct instantiation must fail — the ABC has unimplemented methods.
    with pytest.raises(TypeError):
        MetricsBackend()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Prometheus / OpenTelemetry — lazy-import behavior
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prometheus_backend_import_or_skip():
    """Either ``prometheus_client`` is installed and the backend
    constructs successfully, or it isn't and construction raises
    ``ImportError`` — either path satisfies the contract."""
    if importlib.util.find_spec("prometheus_client") is None:
        with pytest.raises(ImportError):
            PrometheusMetricsBackend()
        return
    # SDK present — instantiate and exercise the surface.
    backend = PrometheusMetricsBackend()
    backend.counter("test_counter_total", labels={"provider": "stripe"})
    backend.gauge("test_gauge", 1.0, labels={"provider": "stripe"})
    backend.histogram(
        "test_histogram_ms", 12.5, labels={"provider": "stripe"}
    )
    with backend.span("op", provider="stripe"):
        pass


@pytest.mark.unit
def test_opentelemetry_backend_import_or_skip():
    """Either OpenTelemetry is installed and the backend constructs, or
    it isn't and construction raises ``ImportError``."""
    has_api = importlib.util.find_spec("opentelemetry") is not None
    if not has_api:
        with pytest.raises(ImportError):
            OpenTelemetryMetricsBackend()
        return
    # SDK present — instantiate and exercise the surface.
    try:
        backend = OpenTelemetryMetricsBackend()
    except ImportError:
        # `opentelemetry` namespace is present but `metrics` / `trace`
        # submodules may be partial in older installs — treat as skip.
        pytest.skip("opentelemetry installed but metrics/trace missing")
    backend.counter("test_counter", labels={"provider": "stripe"})
    backend.gauge("test_gauge", 1.0, labels={"provider": "stripe"})
    backend.histogram("test_histogram_ms", 12.5)
    with backend.span("op", provider="stripe"):
        pass
