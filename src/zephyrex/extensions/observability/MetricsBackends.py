# SPDX-License-Identifier: AGPL-3.0-or-later
"""Concrete metrics backends for the ``observability`` extension.

The metrics *facade* (``MetricsBackend`` ABC, ``set_metrics_backend`` /
``get_metrics_backend``, the Noop/InMemory defaults) stays in core
``lib/Metrics`` — ``BLL_Providers`` and ``app`` depend on it. The concrete
``prometheus_client`` / OpenTelemetry adapters live here so the framework wheel
never carries those optional deps; the extension wires the chosen backend from
``METRICS_BACKEND`` at ``on_load``.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional, Tuple

from zephyrex.lib.Metrics import MetricsBackend, _current_span_id_var


class PrometheusMetricsBackend(MetricsBackend):
    """Adapts the framework's metrics surface to ``prometheus_client``.

    The dependency is optional — instantiating this backend without
    ``prometheus_client`` installed raises :class:`ImportError` at construction
    time so deployments fail loudly at startup rather than silently dropping
    metrics.

    Counters / gauges / histograms are auto-registered on first use and cached
    by ``(name, label-key-tuple)`` so subsequent emissions hit the same
    Prometheus collector. Span emission is best-effort: there is no native span
    concept in ``prometheus_client``, so spans degrade to a histogram of
    duration-ms samples. Deployments that need real spans should choose the
    OpenTelemetry backend instead.
    """

    def __init__(self) -> None:
        try:
            import prometheus_client  # type: ignore[import-not-found]
        except ImportError as e:
            raise ImportError(
                "PrometheusMetricsBackend requires the optional "
                "'prometheus_client' dependency"
            ) from e
        self._client = prometheus_client
        self._counters: Dict[str, Any] = {}
        self._gauges: Dict[str, Any] = {}
        self._histograms: Dict[str, Any] = {}

    @staticmethod
    def _label_keys(labels: Optional[Dict[str, str]]) -> Tuple[str, ...]:
        if not labels:
            return ()
        return tuple(sorted(labels.keys()))

    def _get_counter(self, name: str, label_keys: Tuple[str, ...]) -> Any:
        cached = self._counters.get(name)
        if cached is None:
            cached = self._client.Counter(name, name, list(label_keys))
            self._counters[name] = cached
        return cached

    def _get_gauge(self, name: str, label_keys: Tuple[str, ...]) -> Any:
        cached = self._gauges.get(name)
        if cached is None:
            cached = self._client.Gauge(name, name, list(label_keys))
            self._gauges[name] = cached
        return cached

    def _get_histogram(self, name: str, label_keys: Tuple[str, ...]) -> Any:
        cached = self._histograms.get(name)
        if cached is None:
            cached = self._client.Histogram(name, name, list(label_keys))
            self._histograms[name] = cached
        return cached

    def counter(
        self,
        name: str,
        value: float = 1,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        try:
            keys = self._label_keys(labels)
            metric = self._get_counter(name, keys)
            if labels:
                metric = metric.labels(**labels)
            metric.inc(float(value))
        except Exception:
            return None

    def gauge(
        self,
        name: str,
        value: float,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        try:
            keys = self._label_keys(labels)
            metric = self._get_gauge(name, keys)
            if labels:
                metric = metric.labels(**labels)
            metric.set(float(value))
        except Exception:
            return None

    def histogram(
        self,
        name: str,
        value: float,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        try:
            keys = self._label_keys(labels)
            metric = self._get_histogram(name, keys)
            if labels:
                metric = metric.labels(**labels)
            metric.observe(float(value))
        except Exception:
            return None

    @contextmanager
    def span(self, name: str, **tags: Any) -> Iterator[Dict[str, Any]]:
        record: Dict[str, Any] = {
            "name": name,
            "tags": dict(tags),
            "started_at": time.monotonic(),
            "parent": _current_span_id_var.get(),
        }
        token = _current_span_id_var.set(id(record))
        try:
            yield record
        finally:
            record["ended_at"] = time.monotonic()
            duration_ms = (record["ended_at"] - record["started_at"]) * 1000.0
            record["duration_ms"] = duration_ms
            try:
                label_dict = {k: str(v) for k, v in record["tags"].items()}
                self.histogram(
                    f"{name}.duration_ms", duration_ms, labels=label_dict or None
                )
            except Exception:
                pass
            _current_span_id_var.reset(token)

    def expose(self) -> Tuple[bytes, str]:
        """Render the current metrics for a Prometheus scrape (``/metrics``).

        Returns ``(payload, content_type)`` from ``prometheus_client`` so the
        host app can serve the exposition without importing the optional dep.
        """
        return (self._client.generate_latest(), self._client.CONTENT_TYPE_LATEST)


class OpenTelemetryMetricsBackend(MetricsBackend):
    """Adapts the framework's metrics surface to the OpenTelemetry SDK.

    Both ``opentelemetry.metrics`` and ``opentelemetry.trace`` are imported
    lazily; missing either raises :class:`ImportError` at construction.
    Counters / gauges / histograms are created via the metrics meter and cached
    by name. Spans go through the tracer so nested ``with backend.span(...)``
    blocks report a real parent-child tree to the configured exporter.
    """

    def __init__(self, service_name: str = "ZephyrexFrameworkServer") -> None:
        try:
            from opentelemetry import (  # type: ignore[import-not-found]
                metrics as _otel_metrics,
            )
            from opentelemetry import (  # type: ignore[import-not-found]
                trace as _otel_trace,
            )
        except ImportError as e:
            raise ImportError(
                "OpenTelemetryMetricsBackend requires the optional "
                "'opentelemetry-api' / 'opentelemetry-sdk' dependencies"
            ) from e
        self._otel_metrics = _otel_metrics
        self._otel_trace = _otel_trace
        self._meter = _otel_metrics.get_meter(service_name)
        self._tracer = _otel_trace.get_tracer(service_name)
        self._counters: Dict[str, Any] = {}
        self._gauges: Dict[str, Any] = {}
        self._histograms: Dict[str, Any] = {}

    def _get_counter(self, name: str) -> Any:
        cached = self._counters.get(name)
        if cached is None:
            cached = self._meter.create_counter(name)
            self._counters[name] = cached
        return cached

    def _get_gauge(self, name: str) -> Any:
        cached = self._gauges.get(name)
        if cached is None:
            if hasattr(self._meter, "create_gauge"):
                cached = self._meter.create_gauge(name)
            else:
                cached = self._meter.create_up_down_counter(name)
            self._gauges[name] = cached
        return cached

    def _get_histogram(self, name: str) -> Any:
        cached = self._histograms.get(name)
        if cached is None:
            cached = self._meter.create_histogram(name)
            self._histograms[name] = cached
        return cached

    def counter(
        self,
        name: str,
        value: float = 1,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        try:
            self._get_counter(name).add(float(value), attributes=labels or {})
        except Exception:
            return None

    def gauge(
        self,
        name: str,
        value: float,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        try:
            metric = self._get_gauge(name)
            if hasattr(metric, "set"):
                metric.set(float(value), attributes=labels or {})
            else:
                metric.add(float(value), attributes=labels or {})
        except Exception:
            return None

    def histogram(
        self,
        name: str,
        value: float,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        try:
            self._get_histogram(name).record(float(value), attributes=labels or {})
        except Exception:
            return None

    @contextmanager
    def span(self, name: str, **tags: Any) -> Iterator[Any]:
        record: Dict[str, Any] = {
            "name": name,
            "tags": dict(tags),
            "started_at": time.monotonic(),
            "parent": _current_span_id_var.get(),
        }
        token = _current_span_id_var.set(id(record))
        try:
            otel_cm = self._tracer.start_as_current_span(name)
            otel_span = otel_cm.__enter__()
            try:
                for k, v in tags.items():
                    try:
                        otel_span.set_attribute(k, v)
                    except Exception:
                        continue
            except Exception:
                pass
            try:
                yield record
            finally:
                try:
                    otel_cm.__exit__(None, None, None)
                except Exception:
                    pass
        except Exception:
            yield record
        finally:
            record["ended_at"] = time.monotonic()
            record["duration_ms"] = (record["ended_at"] - record["started_at"]) * 1000.0
            _current_span_id_var.reset(token)
