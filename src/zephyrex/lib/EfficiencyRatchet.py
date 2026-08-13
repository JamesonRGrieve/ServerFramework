"""MHz-normalized efficiency ratchet.

Two entry points:

- ``ratchet(key, fn, tolerance=)`` — in-process timing + ratchet.
- ``ratchet_subprocess(key, script, tolerance=)`` — fresh process.

Default tolerance is 15% (CPU-bound). IO-bound benchmarks should pass
a higher tolerance (e.g. 0.50).
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Callable

BASELINE_FILE = Path(__file__).resolve().parents[3] / ".efficiency-baseline.json"
DEFAULT_TOLERANCE = 0.15


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
        return json.loads(BASELINE_FILE.read_text())
    return {}


def _save(data: dict[str, float]) -> None:
    BASELINE_FILE.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def _check(key: str, normalized: float, elapsed: float, mhz: float, tolerance: float) -> None:
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


def ratchet(key: str, fn: Callable[[], object], *, tolerance: float = DEFAULT_TOLERANCE, iterations: int = 1) -> None:
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


def ratchet_subprocess(key: str, script: str, *, tolerance: float = DEFAULT_TOLERANCE, timeout: float = 30) -> None:
    """Run *script* in a fresh Python process, ratchet the result."""
    wrapper = textwrap.dedent("""\
        import time, json
        from zephyrex.lib.EfficiencyRatchet import _read_mhz
    """) + script + textwrap.dedent("""
        print(json.dumps({"elapsed": _elapsed, "mhz": _mhz}))
    """)

    result = subprocess.run(
        [sys.executable, "-c", wrapper],
        capture_output=True, text=True, timeout=timeout,
        cwd=str(Path(__file__).resolve().parents[3]),
    )
    if result.returncode != 0:
        raise RuntimeError(f"Benchmark subprocess failed:\n{result.stderr}")

    data = json.loads(result.stdout.strip().splitlines()[-1])
    _check(key, data["elapsed"] * data["mhz"], data["elapsed"], data["mhz"], tolerance)
