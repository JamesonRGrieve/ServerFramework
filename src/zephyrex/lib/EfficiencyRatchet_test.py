"""Tests for the efficiency-ratchet mechanism itself (not the hot-path benchmarks).

The complexity-exponent logic (`_lstsq_slope`, `_check_scaling`) is tested
deterministically with synthetic data so it can never be flaky. Two timing-based
integration tests exercise `ratchet_scaling` end-to-end; they assert only a wide
exponent threshold (>1.5 quadratic / <1.5 linear) that dominant-term work clears
with large margin, and they point the baseline at a tmp file so the real
`.efficiency-baseline.json` is never touched.
"""

from __future__ import annotations

import math

import pytest

from zephyrex.lib import EfficiencyRatchet as ER


class TestLstsqSlope:
    @pytest.mark.parametrize("exponent", [1.0, 1.5, 2.0, 3.0])
    def test_recovers_known_power_law(self, exponent):
        # time = N**exponent  =>  log-log slope == exponent, exactly.
        sizes = [1, 2, 4, 8, 16]
        xs = [math.log(n) for n in sizes]
        ys = [exponent * math.log(n) for n in sizes]
        assert ER._lstsq_slope(xs, ys) == pytest.approx(exponent, abs=1e-9)

    def test_constant_time_is_zero_slope(self):
        xs = [math.log(n) for n in (10, 100, 1000)]
        ys = [math.log(5.0)] * 3  # flat: O(1)
        assert ER._lstsq_slope(xs, ys) == pytest.approx(0.0, abs=1e-9)

    def test_rejects_too_few_points(self):
        with pytest.raises(ValueError):
            ER._lstsq_slope([1.0], [1.0])

    def test_rejects_zero_x_spread(self):
        with pytest.raises(ValueError):
            ER._lstsq_slope([2.0, 2.0], [1.0, 3.0])


class TestCheckScaling:
    @pytest.fixture(autouse=True)
    def _tmp_baseline(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ER, "BASELINE_FILE", tmp_path / ".efficiency-baseline.json")

    def test_first_measurement_sets_baseline(self):
        ER._check_scaling("scaling:demo", 1.02, margin=0.35)
        assert ER._load()["scaling:demo"] == pytest.approx(1.02)

    def test_improvement_lowers_baseline(self):
        ER._check_scaling("scaling:demo", 2.0, margin=0.35)
        ER._check_scaling("scaling:demo", 1.1, margin=0.35)  # improved
        assert ER._load()["scaling:demo"] == pytest.approx(1.1)

    def test_within_margin_passes_without_lowering(self):
        ER._check_scaling("scaling:demo", 1.0, margin=0.35)
        ER._check_scaling("scaling:demo", 1.3, margin=0.35)  # +0.3 < margin
        assert ER._load()["scaling:demo"] == pytest.approx(1.0)  # baseline unchanged

    def test_regression_beyond_margin_fails(self):
        ER._check_scaling("scaling:demo", 1.0, margin=0.35)
        with pytest.raises(AssertionError, match="Complexity regression"):
            ER._check_scaling("scaling:demo", 2.0, margin=0.35)  # O(n) -> O(n^2)


class TestRatchetScalingIntegration:
    @pytest.fixture(autouse=True)
    def _tmp_baseline(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ER, "BASELINE_FILE", tmp_path / ".efficiency-baseline.json")

    def test_keeps_the_fastest_repeat_per_size(self, monkeypatch):
        """A preempted first sample must not skew the fit: with a scripted
        clock whose first sample per size is a 100-unit outlier and whose other
        samples are linear in n, the recorded exponent is the linear one."""
        # Samples arrive round by round (each round times every size); the
        # first round is the outlier for every size.
        durations = []
        for round_index in range(ER.DEFAULT_SCALING_REPEATS):
            for n in (1, 2, 4):
                durations.append(100.0 if round_index == 0 else float(n))
        ticks = [0.0]
        for d in durations:
            ticks += [ticks[-1], ticks[-1] + d]
        clock = iter(ticks[1:])
        monkeypatch.setattr(ER.time, "thread_time", lambda: next(clock))
        monkeypatch.setattr(ER, "_read_mhz", lambda: 1.0)

        ER.ratchet_scaling("demo_repeats", lambda n: None, sizes=[1, 2, 4])

        assert ER._load()["scaling:demo_repeats"] == pytest.approx(1.0)

    def test_detects_quadratic(self):
        # O(n^2): membership scan against a growing list inside a loop.
        def quadratic(n: int) -> None:
            acc: list[int] = []
            for i in range(n):
                if i not in acc:  # O(len(acc)) each iteration -> O(n^2)
                    acc.append(i)

        # Sizes large enough that the O(n^2) C-level scans dwarf the O(n)
        # Python loop overhead; at a few hundred elements the two are close
        # and a loaded machine could fit an exponent near 1.
        ER.ratchet_scaling("demo_quad", quadratic, sizes=[1000, 2000, 4000])
        assert ER._load()["scaling:demo_quad"] > 1.5

    def test_a_contention_burst_is_discarded_for_every_size(self, monkeypatch):
        """Sizes are measured interleaved (each repeat times every size), so a
        burst of contention costs one sample of each size and the per-size
        minimum discards it. Measured size-by-size, the same burst lands on
        every sample of the first size and bends the fit."""
        sizes = [1, 2, 4]
        # A burst as long as one size's full set of repeats: sequential
        # measurement gives it every sample of the first size.
        burst_calls = ER.DEFAULT_SCALING_REPEATS
        state: dict[str, float] = {"calls": 0, "now": 0.0, "pending": 0.0}

        def run(n: int) -> None:
            slowdown = 10.0 if state["calls"] < burst_calls else 1.0
            state["pending"] = n * slowdown
            state["calls"] += 1

        def clock() -> float:
            state["now"] += state["pending"]
            state["pending"] = 0.0
            return state["now"]

        monkeypatch.setattr(ER.time, "thread_time", clock)
        monkeypatch.setattr(ER, "_read_mhz", lambda: 1.0)

        ER.ratchet_scaling("demo_burst", run, sizes=sizes)

        assert ER._load()["scaling:demo_burst"] == pytest.approx(1.0)

    def test_detects_linear(self):
        # O(n): the same dedup done with a set.
        def linear(n: int) -> None:
            seen: set[int] = set()
            for i in range(n):
                if i not in seen:  # O(1) each iteration -> O(n)
                    seen.add(i)

        ER.ratchet_scaling("demo_lin", linear, sizes=[300, 600, 1200])
        assert ER._load()["scaling:demo_lin"] < 1.5
