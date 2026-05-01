"""Item 34 — tests for the idempotency-cache hit/miss counter."""
from __future__ import annotations

import pytest

from serverframework.lib.Metrics import (
    InMemoryMetricsBackend,
    reset_metrics_backend,
    set_metrics_backend,
)
from serverframework.logic.BLL_Providers import RotationManager, _safe_metric


@pytest.fixture(autouse=True)
def _reset_state():
    RotationManager.reset_observability_counters()
    reset_metrics_backend()
    yield
    RotationManager.reset_observability_counters()
    reset_metrics_backend()


@pytest.mark.unit
def test_fresh_key_records_a_miss():
    backend = InMemoryMetricsBackend()
    set_metrics_backend(backend)
    hit, _ = RotationManager._cached_idempotency_get("k1")
    assert hit is False
    hits, misses = RotationManager.get_idempotency_counters()
    assert hits == 0
    assert misses == 1
    miss_key = ("idempotency_cache.miss", frozenset())
    assert backend.counters[miss_key] == 1.0


@pytest.mark.unit
def test_reused_key_records_a_hit():
    backend = InMemoryMetricsBackend()
    set_metrics_backend(backend)
    RotationManager._cached_idempotency_get("k1")  # miss
    RotationManager._cached_idempotency_set("k1", "stored")
    hit, value = RotationManager._cached_idempotency_get("k1")  # hit
    assert hit is True
    assert value == "stored"
    hits, misses = RotationManager.get_idempotency_counters()
    assert hits == 1
    assert misses == 1
    hit_key = ("idempotency_cache.hit", frozenset())
    miss_key = ("idempotency_cache.miss", frozenset())
    assert backend.counters[hit_key] == 1.0
    assert backend.counters[miss_key] == 1.0


@pytest.mark.unit
def test_repeated_hits_accumulate():
    backend = InMemoryMetricsBackend()
    set_metrics_backend(backend)
    RotationManager._cached_idempotency_set("k1", "v")
    for _ in range(5):
        RotationManager._cached_idempotency_get("k1")
    hits, misses = RotationManager.get_idempotency_counters()
    assert hits == 5
    assert misses == 0
    hit_key = ("idempotency_cache.hit", frozenset())
    assert backend.counters[hit_key] == 5.0


@pytest.mark.unit
def test_distinct_keys_each_register_a_miss():
    backend = InMemoryMetricsBackend()
    set_metrics_backend(backend)
    for k in ("a", "b", "c"):
        RotationManager._cached_idempotency_get(k)
    hits, misses = RotationManager.get_idempotency_counters()
    assert misses == 3
    assert hits == 0


@pytest.mark.unit
def test_safe_metric_swallows_telemetry_exceptions():
    """`_safe_metric` shields callers from telemetry-side raises."""
    def boom(*_a, **_k):
        raise RuntimeError("telemetry imploded")

    # No exception should leak from `_safe_metric`.
    _safe_metric(boom, "x", value=1)


@pytest.mark.unit
def test_idempotency_cache_metrics_survive_misbehaving_backend():
    """When the backend's counter() raises, the in-process counters
    still increment because the metric emission goes through
    `_safe_metric`."""
    from serverframework.lib.Metrics import MetricsBackend

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
    RotationManager._cached_idempotency_get("k")  # miss
    RotationManager._cached_idempotency_set("k", "v")
    RotationManager._cached_idempotency_get("k")  # hit
    hits, misses = RotationManager.get_idempotency_counters()
    assert hits == 1
    assert misses == 1
