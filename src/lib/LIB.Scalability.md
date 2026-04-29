# Scalability — Big-O Assertion Utilities

`lib.Scalability` provides the analytic core consumed by per-layer abstract scalability tests (`AbstractDBTest`, `AbstractBLLTest`, `AbstractEPTest`). It fits the workload's `T(n) = a · n^k` curve via log-log linear regression and asserts the observed Big-O exponent `k` stays within configured bounds across three orthogonal metric axes: wall-clock time, SQL query count, and peak memory.

## Why

Algorithmic regressions are silent until they hit production. The most common offenders in this framework:

- **N+1 query patterns** — accidentally fetching one row per related entity instead of joining/eager-loading. Looks fine at n=3 (test fixture sizes), explodes at n=10k.
- **Quadratic loops in hot paths** — nested iteration over a list that grew from "a few" to "a lot" since the code was written.
- **Per-row materialization in serializers** — payload memory growing super-linearly as new fields are added.

Catching these in CI requires running the suspect operation at multiple input sizes and asserting the *shape* of the scaling curve, not just an absolute time budget.

## Core Concepts

### `ScalingMetric` enum

Three independent axes; one assertion per axis so failures point at the regressing dimension:

- `TIME` — wall-clock seconds via `time.perf_counter`.
- `QUERY_COUNT` — SQLAlchemy cursor executions on the engine, via `before_cursor_execute` listener.
- `MEMORY` — peak `tracemalloc` allocation in bytes.

### `ScalabilityThreshold`

Per-metric bounds. Fields:

- `expected_exponent: float` — the ideal Big-O exponent (1.0 for linear time/memory; 0.0 for constant queries).
- `max_exponent: float` — the failure threshold; observed `k` above this is a regression.
- `min_r_squared: float` — minimum log-log-fit quality before the result is trusted (low-quality fits with too few points are passed through).
- `floor_value: float` — measurements below this are treated as noise and excluded from the fit.

### `DEFAULT_THRESHOLDS`

Reasonable defaults shipped with the module:

| Metric | expected_exponent | max_exponent | rationale |
|---|---|---|---|
| TIME | 1.0 | 1.4 | Linear with slack for `O(n·log n)` and JIT/GC noise on small `n`. |
| QUERY_COUNT | 0.0 | 0.4 | Near-constant. N+1 patterns produce `k ≈ 1.0`; threshold is far below to catch them. |
| MEMORY | 1.0 | 1.4 | Linear with slack matching TIME. |

Subclasses override per metric via `ScalabilityProfile.threshold_overrides`.

### `ScalabilityProfile`

Parameter bundle for a scalability assertion:

- `n_values: List[int]` — input sizes to probe (typical: `[5, 15, 50]` for integration tests, `[100, 1000, 10000]` for unit-level).
- `metrics: List[ScalingMetric]` — which axes to evaluate.
- `repetitions: int` — measurements per `n` for noise smoothing (default `3`).
- `threshold_overrides: Dict[ScalingMetric, ScalabilityThreshold]` — per-metric overrides.

Use `ScalabilityProfile.default()` for a sensible starting bundle.

### `assert_scaling_within_bounds(operation, profile, metric, engine=None, setup=None, teardown=None)`

User-facing entry point. Calls `setup(n)` once per size, runs `operation(n)` `repetitions` times under the metric's measurement context, fits the curve, and raises `AssertionError` with a human-readable summary if the observed exponent exceeds the configured maximum.

The `setup` hook is the seeding step (e.g., "ensure exactly N entities exist") and runs *outside* the timed/counted region, so seeding overhead never pollutes the measurement.

## Noise-Reduction Choices

- **Time samples**: best-of-runs (`min`) — wall-clock is bounded below by the algorithm and contaminated above by GC, scheduler, and OS jitter.
- **Query and memory samples**: `statistics.median` — already low-noise; mean would be biased by integer counts and one-time allocator behavior.

## Power-Law Fit

`fit_power_law` performs ordinary least-squares regression on `(log n, log value)` pairs. Returns `coefficient`, `exponent`, `r_squared`, and `point_count`. Zero/negative measurements are filtered (log-undefined). Fewer than 2 usable points returns a zero fit.

## Per-Layer Integration

Each abstract test class accepts an opt-in class attribute:

```python
class TestUserDB(AbstractDBTest):
    scalability_profile = ScalabilityProfile.default(n_values=[5, 15, 50])
```

Subclasses inherit `test_scalability_list_n_factor` (DB / BLL) or `test_scalability_GET_list_n_factor` (EP). Each is parametrized over `metric ∈ {TIME, QUERY_COUNT, MEMORY}` and skips for any metric not in the profile's `metrics` list.

## Testing the Tester

`Scalability_test.py` covers the analytic core with real workloads (linear loops, quadratic loops, constant operations, list construction, in-memory SQLite). No mocks — the philosophy mirrors the framework's no-mock pillar.
