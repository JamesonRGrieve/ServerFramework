"""Big-O complexity assertion utilities for scalability tests.

Fits T(n) = a * n^k via log-log linear regression and asserts the observed
exponent k stays within configured bounds. Per-layer abstract scalability
tests (database, BLL, endpoints) consume this module to detect algorithmic
regressions: N+1 queries, accidental O(n^2) loops, memory blow-ups.

Three orthogonal metric axes are supported:

- TIME: wall-clock seconds via time.perf_counter
- QUERY_COUNT: SQLAlchemy cursor executions per call (catches N+1 explicitly)
- MEMORY: peak tracemalloc allocation in bytes

The user-facing entry point is `assert_scaling_within_bounds`. Tests pass an
operation `f(n)` that performs the workload at input size n, plus a
`ScalabilityProfile` declaring the n values, metrics, and thresholds to use.
The function executes the workload at every n, fits the curve, and raises
AssertionError with a human-readable summary if the observed exponent
exceeds the configured maximum.

Design choices:
- Time samples are best-of-runs (min) to reduce upward noise from GC / OS;
  query and memory samples use the median (already low-noise, mean is biased
  by integer counts).
- The fit uses ordinary least-squares in log-log space — robust enough for
  3-5 data points and zero-dependency.
- Defaults err on the strict side for query count (max k = 0.4, i.e.,
  near-constant queries) because N+1 regressions look exactly like k = 1
  scaling and we want them caught.
"""

import gc
import math
import statistics
import time
import tracemalloc
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, Iterator, List, Optional

from sqlalchemy import event
from sqlalchemy.engine import Engine


class ScalingMetric(str, Enum):
    TIME = "time"
    QUERY_COUNT = "query_count"
    MEMORY = "memory"


@dataclass(frozen=True)
class MeasurementSample:
    n: int
    metric: ScalingMetric
    value: float


@dataclass(frozen=True)
class PowerLawFit:
    coefficient: float
    exponent: float
    r_squared: float
    point_count: int


@dataclass(frozen=True)
class ScalabilityThreshold:
    metric: ScalingMetric
    expected_exponent: float
    max_exponent: float
    min_r_squared: float = 0.5
    floor_value: float = 0.0


@dataclass(frozen=True)
class ScalingResult:
    metric: ScalingMetric
    samples: List[MeasurementSample]
    fit: PowerLawFit
    headroom: float
    passed: bool
    threshold: ScalabilityThreshold

    def assertion_message(self) -> str:
        sample_str = ", ".join(f"n={s.n} -> {s.value:.6g}" for s in self.samples)
        return (
            f"{self.metric.value} scaling regressed: observed Big-O exponent "
            f"k={self.fit.exponent:.3f} (R^2={self.fit.r_squared:.3f}) exceeds "
            f"max {self.threshold.max_exponent} (ideal "
            f"{self.threshold.expected_exponent}). Samples: [{sample_str}]"
        )


DEFAULT_THRESHOLDS: Dict[ScalingMetric, ScalabilityThreshold] = {
    ScalingMetric.TIME: ScalabilityThreshold(
        metric=ScalingMetric.TIME,
        expected_exponent=1.0,
        max_exponent=1.4,
        min_r_squared=0.5,
        floor_value=1e-5,
    ),
    ScalingMetric.QUERY_COUNT: ScalabilityThreshold(
        metric=ScalingMetric.QUERY_COUNT,
        expected_exponent=0.0,
        max_exponent=0.4,
        min_r_squared=0.0,
        floor_value=0.0,
    ),
    ScalingMetric.MEMORY: ScalabilityThreshold(
        metric=ScalingMetric.MEMORY,
        expected_exponent=1.0,
        max_exponent=1.4,
        min_r_squared=0.5,
        floor_value=1.0,
    ),
}


def fit_power_law(samples: List[MeasurementSample]) -> PowerLawFit:
    usable = [s for s in samples if s.n > 0 and s.value > 0]
    if len(usable) < 2:
        return PowerLawFit(0.0, 0.0, 0.0, len(usable))
    log_n = [math.log(s.n) for s in usable]
    log_v = [math.log(s.value) for s in usable]
    count = len(usable)
    mean_x = sum(log_n) / count
    mean_y = sum(log_v) / count
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(log_n, log_v))
    var_x = sum((x - mean_x) ** 2 for x in log_n)
    if var_x == 0:
        return PowerLawFit(math.exp(mean_y), 0.0, 1.0, count)
    k = cov / var_x
    log_a = mean_y - k * mean_x
    ss_res = sum((y - (log_a + k * x)) ** 2 for x, y in zip(log_n, log_v))
    ss_tot = sum((y - mean_y) ** 2 for y in log_v)
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 1.0
    return PowerLawFit(math.exp(log_a), k, r_squared, count)


def evaluate_scaling(
    samples: List[MeasurementSample], threshold: ScalabilityThreshold
) -> ScalingResult:
    if not samples:
        raise ValueError("evaluate_scaling requires at least one sample")
    if any(s.metric != threshold.metric for s in samples):
        raise ValueError(
            f"sample metric mismatch: threshold is for {threshold.metric.value}"
        )
    cleaned = [s for s in samples if s.value > threshold.floor_value]
    fit = fit_power_law(cleaned if len(cleaned) >= 2 else samples)
    headroom = threshold.max_exponent - fit.exponent
    if fit.exponent <= threshold.expected_exponent:
        passed = True
    elif fit.exponent <= threshold.max_exponent:
        passed = fit.r_squared >= threshold.min_r_squared or fit.point_count < 3
    else:
        passed = False
    return ScalingResult(
        metric=threshold.metric,
        samples=samples,
        fit=fit,
        headroom=headroom,
        passed=passed,
        threshold=threshold,
    )


@contextmanager
def measure_time() -> Iterator[Callable[[], float]]:
    gc.collect()
    elapsed = [0.0]
    start = time.perf_counter()
    try:
        yield lambda: elapsed[0]
    finally:
        elapsed[0] = time.perf_counter() - start


@contextmanager
def measure_memory() -> Iterator[Callable[[], int]]:
    started_here = not tracemalloc.is_tracing()
    if started_here:
        tracemalloc.start()
    tracemalloc.reset_peak()
    peak = [0]
    try:
        yield lambda: peak[0]
    finally:
        _, peak_now = tracemalloc.get_traced_memory()
        peak[0] = peak_now
        if started_here:
            tracemalloc.stop()


class QueryCounter:
    """Counts SQLAlchemy cursor executions on an engine for the lifetime of
    the context. Listener is removed on exit so concurrent counters never
    cross-contaminate."""

    def __init__(self, engine: Engine):
        self._engine = engine
        self._count = 0
        self._listener: Optional[Callable] = None

    def __enter__(self) -> "QueryCounter":
        self._count = 0

        def _on_execute(conn, cursor, statement, parameters, context, executemany):
            self._count += 1

        self._listener = _on_execute
        event.listen(self._engine, "before_cursor_execute", self._listener)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:  # type: ignore[exit-return]
        if self._listener is not None:
            event.remove(self._engine, "before_cursor_execute", self._listener)
            self._listener = None
        return False

    @property
    def count(self) -> int:
        return self._count


def collect_samples(
    operation: Callable[[int], None],
    n_values: List[int],
    metric: ScalingMetric,
    repetitions: int,
    engine: Optional[Engine] = None,
    setup: Optional[Callable[[int], None]] = None,
    teardown: Optional[Callable[[int], None]] = None,
) -> List[MeasurementSample]:
    if repetitions < 1:
        raise ValueError("repetitions must be >= 1")
    if len(n_values) < 2:
        raise ValueError("need at least 2 n_values to fit a curve")
    if any(n <= 0 for n in n_values):
        raise ValueError("n_values must be positive")
    if metric == ScalingMetric.QUERY_COUNT and engine is None:
        raise ValueError("query_count metric requires a SQLAlchemy engine")

    samples: List[MeasurementSample] = []
    for n in n_values:
        if setup is not None:
            setup(n)
        runs: List[float] = []
        for _ in range(repetitions):
            if metric == ScalingMetric.TIME:
                with measure_time() as get_value:
                    operation(n)
                runs.append(get_value())
            elif metric == ScalingMetric.MEMORY:
                with measure_memory() as get_value:
                    operation(n)
                runs.append(float(get_value()))
            else:
                with QueryCounter(engine) as counter:  # type: ignore[arg-type]
                    operation(n)
                runs.append(float(counter.count))
        if teardown is not None:
            teardown(n)
        value = min(runs) if metric == ScalingMetric.TIME else statistics.median(runs)
        samples.append(MeasurementSample(n=n, metric=metric, value=value))
    return samples


@dataclass(frozen=True)
class ScalabilityProfile:
    """Parameter bundle for a scalability assertion: the n values to probe,
    the metrics to measure, repetitions per n for noise smoothing, and
    optional per-metric threshold overrides."""

    n_values: List[int]
    metrics: List[ScalingMetric]
    repetitions: int = 3
    threshold_overrides: Dict[ScalingMetric, ScalabilityThreshold] = field(
        default_factory=dict
    )

    def threshold_for(self, metric: ScalingMetric) -> ScalabilityThreshold:
        return self.threshold_overrides.get(metric) or DEFAULT_THRESHOLDS[metric]

    @classmethod
    def default(
        cls,
        n_values: Optional[List[int]] = None,
        metrics: Optional[List[ScalingMetric]] = None,
        repetitions: int = 3,
    ) -> "ScalabilityProfile":
        return cls(
            n_values=n_values or [10, 25, 50, 100],
            metrics=metrics
            or [ScalingMetric.TIME, ScalingMetric.QUERY_COUNT, ScalingMetric.MEMORY],
            repetitions=repetitions,
        )


def assert_scaling_within_bounds(
    operation: Callable[[int], None],
    profile: ScalabilityProfile,
    metric: ScalingMetric,
    engine: Optional[Engine] = None,
    setup: Optional[Callable[[int], None]] = None,
    teardown: Optional[Callable[[int], None]] = None,
) -> ScalingResult:
    samples = collect_samples(
        operation,
        profile.n_values,
        metric,
        profile.repetitions,
        engine,
        setup=setup,
        teardown=teardown,
    )
    result = evaluate_scaling(samples, profile.threshold_for(metric))
    if not result.passed:
        raise AssertionError(result.assertion_message())
    return result
