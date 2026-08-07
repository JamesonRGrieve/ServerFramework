"""Pluggable metrics + distributed-tracing surface (Item 34).

This module is the framework's single seam for metrics emission and
span instrumentation. The contract is intentionally small:

    counter(name, value=1, labels=None)
    gauge(name, value, labels=None)
    histogram(name, value, labels=None)
    span(name, **tags) -> ContextManager

Concrete subclasses adapt that surface to a backend. The module ships
four:

* :class:`NoopMetricsBackend` — the default. Every call is a fast,
  silent no-op. Production deployments that have not opted into a
  metrics backend pay zero cost.
* :class:`InMemoryMetricsBackend` — keeps every counter, gauge,
  histogram sample, and span in plain dicts/lists. Useful for tests
  and low-traffic deployments that want introspection without an
  external dependency.
* :class:`PrometheusMetricsBackend` — adapts to ``prometheus_client``
  via lazy import; raises :class:`ImportError` at construction if the
  SDK is not installed.
* :class:`OpenTelemetryMetricsBackend` — adapts to the OpenTelemetry
  Python SDK via lazy import; raises :class:`ImportError` at
  construction if the SDK is not installed.

Span nesting works across ``await`` because the active span id is held
in a :class:`contextvars.ContextVar`. A child span opened inside a
parent gets its ``parent`` field populated with the parent's record id
automatically; the contextvar resets on exit so sibling spans do not
leak each other's parent pointer.

Telemetry must NEVER fail a request. Callers are expected to wrap
emission calls in ``try/except`` (the rotation manager does this); the
backends themselves swallow internal errors as a defense in depth.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from contextvars import ContextVar
from typing import (
    Any,
    ContextManager,
    Dict,
    FrozenSet,
    Iterator,
    List,
    Optional,
    Tuple,
)


# ---------------------------------------------------------------------------
# Span nesting — module-level so every backend's ``span()`` shares the
# same active-span tracking. A ContextVar lets nesting work correctly
# across ``asyncio`` task boundaries.
# ---------------------------------------------------------------------------

_current_span_id_var: ContextVar[Optional[int]] = ContextVar(
    "metrics_current_span_id", default=None
)


def _labels_key(labels: Optional[Dict[str, str]]) -> FrozenSet[Tuple[str, str]]:
    """Normalize a labels mapping to a hashable key for dict storage."""
    if not labels:
        return frozenset()
    # Cast values to str so callers don't accidentally key by int vs str.
    return frozenset((str(k), str(v)) for k, v in labels.items())


# ---------------------------------------------------------------------------
# Backend ABC
# ---------------------------------------------------------------------------


class MetricsBackend(ABC):
    """Pluggable metrics + tracing sink.

    Implementations must accept arbitrary label dicts and tag kwargs.
    All methods must be safe to call from any thread or coroutine and
    must never raise on the happy path — telemetry failures are silent.
    """

    @abstractmethod
    def counter(
        self,
        name: str,
        value: float = 1,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        """Increment a monotonic counter."""

    @abstractmethod
    def gauge(
        self,
        name: str,
        value: float,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        """Set a gauge to ``value``."""

    @abstractmethod
    def histogram(
        self,
        name: str,
        value: float,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        """Observe ``value`` into a histogram (e.g. latency in ms)."""

    @abstractmethod
    def span(self, name: str, **tags: Any) -> ContextManager[Any]:
        """Open a tracing span. Implementations return a context manager
        whose ``__enter__`` records the span and whose ``__exit__``
        finalizes it. Spans nest across ``await`` via the module-level
        :data:`_current_span_id_var`.
        """


# ---------------------------------------------------------------------------
# Noop backend — the default install
# ---------------------------------------------------------------------------


class _NoopSpan:
    """Minimal context manager for :class:`NoopMetricsBackend`. Tracks
    the active-span contextvar so nesting still resolves correctly
    even when no metrics are recorded — this keeps test fixtures that
    reuse the contextvar from leaking parent ids across sibling calls."""

    __slots__ = ("_token",)

    def __enter__(self) -> "_NoopSpan":
        self._token = _current_span_id_var.set(id(self))
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        _current_span_id_var.reset(self._token)


class NoopMetricsBackend(MetricsBackend):
    """Default backend. Every call is a silent no-op so production
    deployments that have not opted into metrics pay nothing."""

    def counter(
        self,
        name: str,
        value: float = 1,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        return None

    def gauge(
        self,
        name: str,
        value: float,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        return None

    def histogram(
        self,
        name: str,
        value: float,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        return None

    def span(self, name: str, **tags: Any) -> ContextManager[Any]:
        return _NoopSpan()


# ---------------------------------------------------------------------------
# In-memory backend — for tests and low-traffic deployments
# ---------------------------------------------------------------------------


class InMemoryMetricsBackend(MetricsBackend):
    """Stores counters, gauges, histograms, and spans in plain dicts/lists.

    Counter / gauge / histogram keys are
    ``(name, frozenset(label_items))`` so callers can match an
    individual ``(metric, labels)`` tuple without re-implementing label
    normalization. Histogram values accumulate as a list of samples;
    callers can compute their own quantiles or feed them to an external
    aggregator.

    Spans accumulate in :attr:`spans` as plain dicts shaped like::

        {
            "name": str,
            "tags": Dict[str, Any],
            "started_at": float,        # time.monotonic()
            "ended_at": float,          # time.monotonic()
            "duration_ms": float,
            "parent": Optional[int],    # id of enclosing span record
        }

    The ``parent`` pointer is populated from
    :data:`_current_span_id_var`, which is propagated via
    :class:`contextvars.ContextVar` so nested spans across ``await``
    boundaries report the correct enclosing span.
    """

    def __init__(self) -> None:
        self.counters: Dict[Tuple[str, FrozenSet[Tuple[str, str]]], float] = {}
        self.gauges: Dict[Tuple[str, FrozenSet[Tuple[str, str]]], float] = {}
        self.histograms: Dict[
            Tuple[str, FrozenSet[Tuple[str, str]]], List[float]
        ] = {}
        self.spans: List[Dict[str, Any]] = []

    # --- counters / gauges / histograms -----------------------------------

    def counter(
        self,
        name: str,
        value: float = 1,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        key = (name, _labels_key(labels))
        self.counters[key] = self.counters.get(key, 0.0) + float(value)

    def gauge(
        self,
        name: str,
        value: float,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        key = (name, _labels_key(labels))
        self.gauges[key] = float(value)

    def histogram(
        self,
        name: str,
        value: float,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        key = (name, _labels_key(labels))
        bucket = self.histograms.setdefault(key, [])
        bucket.append(float(value))

    # --- spans ------------------------------------------------------------

    @contextmanager
    def span(self, name: str, **tags: Any) -> Iterator[Dict[str, Any]]:
        """Open a span. Usage::

            with backend.span("rotate.attempt", provider="stripe", attempt=0):
                ...

        The yielded dict is mutable so callers can attach extra tags
        after enter (e.g. result codes) — those mutations land in
        :attr:`spans` when the context manager exits.
        """
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
            record["duration_ms"] = (
                record["ended_at"] - record["started_at"]
            ) * 1000.0
            self.spans.append(record)
            _current_span_id_var.reset(token)


# ---------------------------------------------------------------------------
# Prometheus backend — lazy import
# ---------------------------------------------------------------------------


class PrometheusMetricsBackend(MetricsBackend):
    """Adapts the framework's metrics surface to ``prometheus_client``.

    The dependency is optional — instantiating this backend without
    ``prometheus_client`` installed raises :class:`ImportError` at
    construction time so deployments fail loudly at startup rather than
    silently dropping metrics.

    Counters / gauges / histograms are auto-registered on first use and
    cached by ``(name, label-key-tuple)`` so subsequent emissions hit
    the same Prometheus collector. Span emission is best-effort: there
    is no native span concept in ``prometheus_client``, so spans degrade
    to a histogram of duration-ms samples per ``(name, tag-keys)``.
    Deployments that need real spans should choose the OpenTelemetry
    backend instead.
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
                # Surface span timing as a histogram so dashboards can
                # see attempt latency without a separate tracing pipe.
                label_dict = {k: str(v) for k, v in record["tags"].items()}
                self.histogram(f"{name}.duration_ms", duration_ms, labels=label_dict or None)
            except Exception:
                pass
            _current_span_id_var.reset(token)


# ---------------------------------------------------------------------------
# OpenTelemetry backend — lazy import
# ---------------------------------------------------------------------------


class OpenTelemetryMetricsBackend(MetricsBackend):
    """Adapts the framework's metrics surface to the OpenTelemetry SDK.

    Both ``opentelemetry.metrics`` and ``opentelemetry.trace`` are
    imported lazily; missing either raises :class:`ImportError` at
    construction. Counters / gauges / histograms are created via the
    metrics meter and cached by name. Spans go through the tracer so
    nested ``with backend.span(...)`` blocks report a real parent-child
    tree to the configured exporter.
    """

    def __init__(self, service_name: str = "ZephyrexFrameworkServer") -> None:
        try:
            from opentelemetry import metrics as _otel_metrics  # type: ignore[import-not-found]
            from opentelemetry import trace as _otel_trace  # type: ignore[import-not-found]
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
            # OTel: observable_gauge is async-callback; we fall back to
            # an UpDownCounter where set is delta-ish. For simplicity use
            # create_gauge if available (>=1.23), otherwise UpDown.
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
                # UpDownCounter fallback — caller pushes a delta.
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
            self._get_histogram(name).record(
                float(value), attributes=labels or {}
            )
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
        otel_cm = None
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
            # Fall back to an unrecorded span so callers never see a
            # tracing failure leak through to business logic.
            yield record
        finally:
            record["ended_at"] = time.monotonic()
            record["duration_ms"] = (
                record["ended_at"] - record["started_at"]
            ) * 1000.0
            _current_span_id_var.reset(token)


# ---------------------------------------------------------------------------
# Module-level installed backend
# ---------------------------------------------------------------------------


_active_backend: MetricsBackend = NoopMetricsBackend()


def set_metrics_backend(backend: MetricsBackend) -> None:
    """Install ``backend`` as the process-wide active metrics backend.

    Called once at startup by the deployment wiring. Tests use this to
    swap in :class:`InMemoryMetricsBackend` and restore the prior
    backend on teardown.
    """
    global _active_backend
    if not isinstance(backend, MetricsBackend):
        raise TypeError(
            f"set_metrics_backend requires a MetricsBackend, got {type(backend).__name__}"
        )
    _active_backend = backend


def get_metrics_backend() -> MetricsBackend:
    """Return the currently-installed metrics backend.

    Defaults to :class:`NoopMetricsBackend` until
    :func:`set_metrics_backend` is called.
    """
    return _active_backend


def reset_metrics_backend() -> None:
    """Test helper: restore the default :class:`NoopMetricsBackend`."""
    global _active_backend
    _active_backend = NoopMetricsBackend()


# ---------------------------------------------------------------------------
# Item 34 — per-provider health gauge
# ---------------------------------------------------------------------------


_HEALTH_GAUGE_VALUES: Dict[str, float] = {
    "ok": 1.0,
    "degraded": 0.5,
    "down": 0.0,
}


def emit_provider_health_gauge(provider_name: str, status: Any) -> None:
    """Emit ``provider_health_status{provider, status}`` as a gauge.

    Numeric mapping: OK -> 1.0, DEGRADED -> 0.5, DOWN -> 0.0. The
    ``status`` argument accepts either a ``HealthStatus`` enum (whose
    ``.value`` is read) or a bare string. Unknown values map to 0.0
    so dashboards err on the safe side.
    """
    raw = getattr(status, "value", status)
    key = str(raw).lower()
    value = _HEALTH_GAUGE_VALUES.get(key, 0.0)
    backend = get_metrics_backend()
    try:
        backend.gauge(
            "provider_health_status",
            value,
            labels={"provider": str(provider_name), "status": key},
        )
    except Exception:
        return None
