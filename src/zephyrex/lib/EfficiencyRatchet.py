"""MHz-normalized efficiency ratchet.

Two entry points:

- ``ratchet(key, fn, tolerance=)`` — in-process timing + ratchet.
- ``ratchet_subprocess(key, script, tolerance=)`` — fresh process.

Default tolerance is 15% (CPU-bound). IO-bound benchmarks should pass
a higher tolerance (e.g. 0.50).
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Callable, Sequence, cast

BASELINE_FILE = Path(__file__).resolve().parents[3] / ".efficiency-baseline.json"
DEFAULT_TOLERANCE = 0.15

# Absolute margin (in units of the complexity exponent) that a scaling ratchet
# tolerates before calling a regression. A genuine O(n) -> O(n log n) drift is
# ~+0.1–0.2 near these input sizes; O(n) -> O(n^2) is ~+1.0. 0.35 sits above
# timing noise yet well below a true order-of-growth regression.
DEFAULT_SCALING_MARGIN = 0.35


def _read_mhz() -> float:
    """CPU base frequency in MHz — stable across P-states and turbo.

    Live ``cpu MHz`` from ``/proc/cpuinfo`` swings 2.7–4.7 GHz with
    turbo and idle states, which amplifies variance instead of removing
    it. Base frequency is the constant normalizer: same within a machine
    (no run-to-run noise), different across machines (cross-hardware
    portability).
    """
    for path in (
        "/sys/devices/system/cpu/cpu0/cpufreq/base_frequency",
        "/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq",
    ):
        try:
            khz = int(Path(path).read_text().strip())
            return khz / 1000.0
        except (OSError, ValueError):
            continue
    return 3600.0


def _load() -> dict[str, float]:
    if BASELINE_FILE.exists():
        return cast("dict[str, float]", json.loads(BASELINE_FILE.read_text()))
    return {}


def _save(data: dict[str, float]) -> None:
    BASELINE_FILE.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def _check(
    key: str, normalized: float, elapsed: float, mhz: float, tolerance: float
) -> None:
    baselines = _load()
    best = baselines.get(key)

    if best is None or normalized < best:
        baselines[key] = normalized
        _save(baselines)
        return

    limit = best * (1.0 + tolerance)
    assert normalized <= limit, (
        f"Efficiency regression: {key}\n"
        f"  Current:  {elapsed:.6f}s × {mhz:.0f} MHz = {normalized:.2f} MHz·s\n"
        f"  Baseline: {best:.2f} MHz·s (limit: {limit:.2f}, +{tolerance:.0%})"
    )


def ratchet(
    key: str,
    fn: Callable[[], object],
    *,
    tolerance: float = DEFAULT_TOLERANCE,
    iterations: int = 1,
) -> None:
    """Time *fn*, normalize by base MHz, ratchet.

    For sub-millisecond operations, pass ``iterations`` > 1 to loop the
    function and divide, pushing the measurement above context-switch noise.
    """
    mhz = _read_mhz()
    start = time.perf_counter()
    for _ in range(iterations):
        fn()
    elapsed = (time.perf_counter() - start) / iterations
    _check(key, elapsed * mhz, elapsed, mhz, tolerance)


def ratchet_subprocess(
    key: str, script: str, *, tolerance: float = DEFAULT_TOLERANCE, timeout: float = 30
) -> None:
    """Run *script* in a fresh Python process, ratchet the result."""
    wrapper = (
        textwrap.dedent("""\
        import time, json
        from zephyrex.lib.EfficiencyRatchet import _read_mhz
    """)
        + script
        + textwrap.dedent("""
        print(json.dumps({"elapsed": _elapsed, "mhz": _mhz}))
    """)
    )

    result = subprocess.run(
        [sys.executable, "-c", wrapper],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(Path(__file__).resolve().parents[3]),
    )
    if result.returncode != 0:
        raise RuntimeError(f"Benchmark subprocess failed:\n{result.stderr}")

    data = json.loads(result.stdout.strip().splitlines()[-1])
    _check(key, data["elapsed"] * data["mhz"], data["elapsed"], data["mhz"], tolerance)


def _lstsq_slope(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Least-squares slope of ys vs xs (the log-log complexity exponent).

    With ``xs = log(N)`` and ``ys = log(time)``, the slope estimates ``k`` in
    ``time ∝ N**k`` — i.e. the empirical big-O exponent (O(n)→~1, O(n·log n)→~1,
    O(n²)→~2). Requires ≥ 2 points with non-zero spread in ``xs``.
    """
    n = len(xs)
    if n < 2:
        raise ValueError("need at least two measurement points")
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0.0:
        raise ValueError("all input sizes are identical — no scaling to fit")
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    return num / denom


def _check_scaling(key: str, exponent: float, margin: float) -> None:
    baselines = _load()
    best = baselines.get(key)

    if best is None or exponent < best:
        baselines[key] = exponent
        _save(baselines)
        return

    limit = best + margin
    assert exponent <= limit, (
        f"Complexity regression: {key}\n"
        f"  Current exponent:  {exponent:.3f} (time ∝ N^{exponent:.2f})\n"
        f"  Baseline exponent: {best:.3f} (limit: {limit:.3f}, +{margin} margin)"
    )


def ratchet_scaling(
    key: str,
    run: Callable[[int], object],
    *,
    sizes: Sequence[int],
    margin: float = DEFAULT_SCALING_MARGIN,
    iterations: int = 1,
) -> None:
    """Ratchet the empirical big-O *exponent* of ``run`` across input ``sizes``.

    ``run(n)`` must perform the work for problem size ``n`` (build its own input;
    keep per-call fixed overhead small relative to the scaling work). We time it
    at each size, fit ``k`` in ``time ∝ N**k`` via a log-log least-squares slope,
    and ratchet ``k`` downward-only: it may improve but never regress past
    ``baseline + margin``. This catches an O(n)→O(n²) regression even when the
    absolute (wall-time) ratchet still passes because inputs are small.

    Choose ``sizes`` (≥3 recommended, geometrically spaced e.g. 100/200/400/800)
    large enough that the scaling work dominates timer noise; bump ``iterations``
    for fast inner work. Pairs with :func:`ratchet` — use both on a hot,
    input-scaling function: one guards constant factor, one guards order of growth.
    """
    if len(sizes) < 2:
        raise ValueError("ratchet_scaling needs at least two sizes")
    mhz = _read_mhz()
    xs: list[float] = []
    ys: list[float] = []
    for n in sizes:
        start = time.perf_counter()
        for _ in range(iterations):
            run(n)
        elapsed = (time.perf_counter() - start) / iterations
        xs.append(math.log(n))
        # Floor the normalized time so a sub-microsecond sample can't produce a
        # nonsense log; callers are told to size the work above noise regardless.
        ys.append(math.log(max(elapsed * mhz, 1e-9)))
    _check_scaling(f"scaling:{key}", _lstsq_slope(xs, ys), margin)
