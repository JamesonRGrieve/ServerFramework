"""Self-tests for lib.Scalability.

Uses real workloads (linear loops, quadratic loops, constant-time ops, list
construction) — no mocks. Each test asserts the analytic core correctly
classifies a known-complexity workload.
"""

import math

import pytest
from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from lib.Scalability import (
    DEFAULT_THRESHOLDS,
    MeasurementSample,
    PowerLawFit,
    QueryCounter,
    ScalabilityProfile,
    ScalabilityThreshold,
    ScalingMetric,
    ScalingResult,
    assert_scaling_within_bounds,
    collect_samples,
    evaluate_scaling,
    fit_power_law,
    measure_memory,
    measure_time,
)


class TestFitPowerLaw:
    def test_perfect_linear(self):
        samples = [
            MeasurementSample(n=n, metric=ScalingMetric.TIME, value=2.5 * n)
            for n in (10, 100, 1000)
        ]
        fit = fit_power_law(samples)
        assert fit.point_count == 3
        assert math.isclose(fit.exponent, 1.0, abs_tol=1e-9)
        assert math.isclose(fit.coefficient, 2.5, abs_tol=1e-9)
        assert math.isclose(fit.r_squared, 1.0, abs_tol=1e-9)

    def test_perfect_quadratic(self):
        samples = [
            MeasurementSample(n=n, metric=ScalingMetric.TIME, value=0.001 * n * n)
            for n in (10, 100, 1000)
        ]
        fit = fit_power_law(samples)
        assert math.isclose(fit.exponent, 2.0, abs_tol=1e-9)
        assert math.isclose(fit.r_squared, 1.0, abs_tol=1e-9)

    def test_constant(self):
        samples = [
            MeasurementSample(n=n, metric=ScalingMetric.QUERY_COUNT, value=3.0)
            for n in (10, 100, 1000)
        ]
        fit = fit_power_law(samples)
        assert math.isclose(fit.exponent, 0.0, abs_tol=1e-9)

    def test_logarithmic_is_sublinear(self):
        samples = [
            MeasurementSample(n=n, metric=ScalingMetric.TIME, value=math.log(n))
            for n in (10, 100, 1000, 10000)
        ]
        fit = fit_power_law(samples)
        assert 0.0 < fit.exponent < 0.5

    def test_too_few_points(self):
        samples = [MeasurementSample(n=1, metric=ScalingMetric.TIME, value=0.1)]
        fit = fit_power_law(samples)
        assert fit == PowerLawFit(0.0, 0.0, 0.0, 1)

    def test_zero_and_negative_values_filtered(self):
        samples = [
            MeasurementSample(n=10, metric=ScalingMetric.TIME, value=0.0),
            MeasurementSample(n=100, metric=ScalingMetric.TIME, value=1.0),
            MeasurementSample(n=1000, metric=ScalingMetric.TIME, value=10.0),
        ]
        fit = fit_power_law(samples)
        assert fit.point_count == 2
        assert math.isclose(fit.exponent, 1.0, abs_tol=1e-9)


class TestEvaluateScaling:
    def test_linear_passes_time_default(self):
        samples = [
            MeasurementSample(n=n, metric=ScalingMetric.TIME, value=0.0001 * n)
            for n in (10, 50, 100, 500)
        ]
        result = evaluate_scaling(samples, DEFAULT_THRESHOLDS[ScalingMetric.TIME])
        assert result.passed
        assert result.headroom > 0
        assert math.isclose(result.fit.exponent, 1.0, abs_tol=1e-6)

    def test_quadratic_fails_time_default(self):
        samples = [
            MeasurementSample(n=n, metric=ScalingMetric.TIME, value=0.0001 * n * n)
            for n in (10, 50, 100, 500)
        ]
        result = evaluate_scaling(samples, DEFAULT_THRESHOLDS[ScalingMetric.TIME])
        assert not result.passed
        assert result.headroom < 0
        assert "scaling regressed" in result.assertion_message()

    def test_n_plus_one_fails_query_default(self):
        samples = [
            MeasurementSample(n=n, metric=ScalingMetric.QUERY_COUNT, value=float(n + 1))
            for n in (10, 50, 100, 500)
        ]
        result = evaluate_scaling(samples, DEFAULT_THRESHOLDS[ScalingMetric.QUERY_COUNT])
        assert not result.passed
        assert result.fit.exponent > 0.4

    def test_constant_queries_pass(self):
        samples = [
            MeasurementSample(n=n, metric=ScalingMetric.QUERY_COUNT, value=2.0)
            for n in (10, 50, 100, 500)
        ]
        result = evaluate_scaling(samples, DEFAULT_THRESHOLDS[ScalingMetric.QUERY_COUNT])
        assert result.passed

    def test_metric_mismatch_raises(self):
        samples = [MeasurementSample(n=10, metric=ScalingMetric.TIME, value=0.1)]
        with pytest.raises(ValueError, match="metric mismatch"):
            evaluate_scaling(samples, DEFAULT_THRESHOLDS[ScalingMetric.MEMORY])

    def test_empty_samples_raises(self):
        with pytest.raises(ValueError, match="at least one sample"):
            evaluate_scaling([], DEFAULT_THRESHOLDS[ScalingMetric.TIME])


class TestMeasureTime:
    def test_records_elapsed(self):
        with measure_time() as get_elapsed:
            sum(range(100_000))
        elapsed = get_elapsed()
        assert elapsed > 0
        assert elapsed < 5.0

    def test_zero_when_unread_during_block(self):
        with measure_time() as get_elapsed:
            inside = get_elapsed()
        assert inside == 0.0


class TestMeasureMemory:
    def test_captures_peak_allocation(self):
        with measure_memory() as get_peak:
            big = [i for i in range(50_000)]
        peak = get_peak()
        assert peak > 0
        del big

    def test_nests_safely(self):
        with measure_memory() as outer:
            outer_data = list(range(10_000))
            with measure_memory() as inner:
                inner_data = list(range(20_000))
            assert inner() > 0
        assert outer() > 0
        del outer_data, inner_data


class TestQueryCounter:
    @pytest.fixture
    def engine(self):
        engine = create_engine("sqlite:///:memory:")
        base = declarative_base()

        class Item(base):
            __tablename__ = "scaling_test_item"
            id = Column(Integer, primary_key=True)
            name = Column(String, nullable=False)

        base.metadata.create_all(engine)
        return engine, Item

    def test_counts_executes(self, engine):
        eng, model = engine
        Session = sessionmaker(bind=eng)
        with QueryCounter(eng) as counter:
            with Session() as session:
                session.add(model(name="a"))
                session.add(model(name="b"))
                session.commit()
                session.query(model).all()
        assert counter.count >= 2

    def test_listener_removed_on_exit(self, engine):
        eng, model = engine
        with QueryCounter(eng) as counter:
            pass
        assert counter._listener is None
        Session = sessionmaker(bind=eng)
        with Session() as session:
            session.query(model).all()
        assert counter.count == 0


class TestCollectSamples:
    def test_time_linear_workload(self):
        def linear(n: int) -> None:
            total = 0
            for i in range(n):
                total += i

        samples = collect_samples(
            linear, [100, 1000, 10000], ScalingMetric.TIME, repetitions=3
        )
        assert len(samples) == 3
        assert all(s.metric == ScalingMetric.TIME for s in samples)
        assert all(s.value > 0 for s in samples)

    def test_memory_linear_workload(self):
        def alloc(n: int) -> None:
            data = list(range(n * 10))
            del data

        samples = collect_samples(
            alloc, [100, 1000, 10000], ScalingMetric.MEMORY, repetitions=2
        )
        fit = fit_power_law(samples)
        assert 0.5 < fit.exponent < 1.5

    def test_rejects_short_n_values(self):
        with pytest.raises(ValueError, match="at least 2"):
            collect_samples(lambda n: None, [10], ScalingMetric.TIME, 1)

    def test_rejects_zero_repetitions(self):
        with pytest.raises(ValueError, match="repetitions"):
            collect_samples(lambda n: None, [10, 20], ScalingMetric.TIME, 0)

    def test_query_count_requires_engine(self):
        with pytest.raises(ValueError, match="engine"):
            collect_samples(lambda n: None, [10, 20], ScalingMetric.QUERY_COUNT, 1)

    def test_setup_called_once_per_n_before_repetitions(self):
        setup_log: list = []
        op_log: list = []

        def setup(n):
            setup_log.append(n)

        def operation(n):
            op_log.append(n)

        collect_samples(
            operation,
            [10, 20, 30],
            ScalingMetric.TIME,
            repetitions=4,
            setup=setup,
        )
        assert setup_log == [10, 20, 30]
        assert op_log == [10] * 4 + [20] * 4 + [30] * 4

    def test_teardown_called_once_per_n_after_repetitions(self):
        teardown_log: list = []
        order: list = []

        def operation(n):
            order.append(("op", n))

        def teardown(n):
            order.append(("teardown", n))
            teardown_log.append(n)

        collect_samples(
            operation,
            [10, 20],
            ScalingMetric.TIME,
            repetitions=2,
            teardown=teardown,
        )
        assert teardown_log == [10, 20]
        assert order == [
            ("op", 10),
            ("op", 10),
            ("teardown", 10),
            ("op", 20),
            ("op", 20),
            ("teardown", 20),
        ]


class TestAssertScalingWithinBounds:
    def test_passes_for_linear(self):
        def linear(n: int) -> None:
            sum(range(n))

        result = assert_scaling_within_bounds(
            linear,
            ScalabilityProfile.default(
                n_values=[100, 1000, 10000], metrics=[ScalingMetric.TIME]
            ),
            ScalingMetric.TIME,
        )
        assert isinstance(result, ScalingResult)
        assert result.passed

    def test_raises_on_quadratic_with_strict_threshold(self):
        def quadratic(n: int) -> None:
            total = 0
            for i in range(n):
                for j in range(n):
                    total += i + j

        profile = ScalabilityProfile(
            n_values=[50, 100, 200],
            metrics=[ScalingMetric.TIME],
            repetitions=2,
            threshold_overrides={
                ScalingMetric.TIME: ScalabilityThreshold(
                    metric=ScalingMetric.TIME,
                    expected_exponent=1.0,
                    max_exponent=1.5,
                    min_r_squared=0.5,
                    floor_value=1e-5,
                )
            },
        )
        with pytest.raises(AssertionError, match="scaling regressed"):
            assert_scaling_within_bounds(quadratic, profile, ScalingMetric.TIME)


class TestProfile:
    def test_default_profile(self):
        profile = ScalabilityProfile.default()
        assert len(profile.n_values) >= 2
        assert ScalingMetric.TIME in profile.metrics
        assert profile.repetitions >= 1

    def test_threshold_override_takes_precedence(self):
        custom = ScalabilityThreshold(
            metric=ScalingMetric.TIME,
            expected_exponent=1.0,
            max_exponent=2.0,
        )
        profile = ScalabilityProfile(
            n_values=[10, 20],
            metrics=[ScalingMetric.TIME],
            threshold_overrides={ScalingMetric.TIME: custom},
        )
        assert profile.threshold_for(ScalingMetric.TIME) is custom

    def test_threshold_falls_back_to_default(self):
        profile = ScalabilityProfile(n_values=[10, 20], metrics=[ScalingMetric.TIME])
        assert profile.threshold_for(ScalingMetric.MEMORY) is DEFAULT_THRESHOLDS[
            ScalingMetric.MEMORY
        ]
