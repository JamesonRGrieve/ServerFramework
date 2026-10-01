# SPDX-License-Identifier: AGPL-3.0-or-later
"""Safe symbolic mathematics: the whitelist parser (no code runs, no
unbounded power), the four operations, and the worker process (its
timeout, its recovery, its memory ceiling)."""

import os
import sys
import time

import pytest
import sympy

from zephyrex.extensions.math.Symbolic import (
    MAX_EXPRESSION_LENGTH,
    WORKER_MEMORY_BYTES,
    RefusedInput,
    SymbolicTimeout,
    SymbolicWorker,
    answer,
    calculate,
    differentiate,
    integrate,
    parse_equation,
    parse_expression,
    solve,
)

# SymPy spends well over the test timeout on this integral.
SLOW_INTEGRAND = "exp(x^x)*sin(x)^7/(1+x^9)"
x = sympy.Symbol("x")


@pytest.fixture
def worker():
    started = SymbolicWorker()
    yield started
    started.stop()


class TestParser:
    @pytest.mark.parametrize(
        "text, value",
        [
            ("2 + 3 * 4", 14),
            ("2^10", 1024),
            ("-3 + 5", 2),
            ("7 % 3", 1),
            ("sqrt(16)", 4),
            ("log(8, 2)", 3),
            ("abs(-2.5)", sympy.Float(2.5)),
        ],
    )
    def test_numbers(self, text, value):
        assert parse_expression(text) == value

    def test_symbols_and_constants(self):
        assert parse_expression("x^2 + pi") == x**2 + sympy.pi
        assert parse_expression("e^x") == sympy.exp(x)
        assert parse_expression("I^2") == -1

    @pytest.mark.parametrize(
        "text",
        [
            "__import__('os').getpid()",
            "x.__class__",
            "().__class__.__bases__",
            "[x for x in range(9)]",
            "lambda: 1",
            "open('/etc/passwd')",
            "Symbol('x')",
            "sin(x, y)",
            "sin(x=1)",
            "'text'",
            "True",
            "x if 1 else 2",
            "sin",
        ],
    )
    def test_anything_beyond_the_whitelist_is_refused(self, text):
        """sympify would eval these; the old solve_equation did."""
        with pytest.raises(RefusedInput):
            parse_expression(text)

    def test_no_code_runs(self):
        """The pid would come back if the text were evaluated."""
        with pytest.raises(RefusedInput):
            solve("__import__('os').getpid()", None)

    @pytest.mark.parametrize("text", ["9^9^9^9", "10^100000", "2^(10^6)"])
    def test_a_huge_power_is_refused_before_it_is_computed(self, text):
        started = time.monotonic()
        with pytest.raises(RefusedInput):
            parse_expression(text)
        assert time.monotonic() - started < 1

    def test_a_large_but_bounded_power(self):
        assert parse_expression("2^1000") == 2**1000

    def test_length_limit(self):
        with pytest.raises(RefusedInput):
            parse_expression("1+" * MAX_EXPRESSION_LENGTH + "1")

    def test_equations(self):
        assert parse_equation("x^2 = 4") == sympy.Eq(x**2, 4)
        assert parse_equation("x^2 == 4") == sympy.Eq(x**2, 4)
        assert parse_equation("x - 1") == sympy.Eq(x - 1, 0)
        with pytest.raises(RefusedInput):
            parse_equation("x = 1 = 2")


class TestOperations:
    def test_calculate(self):
        assert calculate("sqrt(8)") == {
            "result": "2*sqrt(2)",
            "decimal": "2.82842712474619",
        }

    def test_calculate_takes_numbers_only(self):
        with pytest.raises(RefusedInput):
            calculate("x + 1")

    def test_division_by_zero_is_complex_infinity(self):
        assert calculate("1/0")["result"] == "zoo"

    def test_solve(self):
        assert solve("x^2 = 4", None) == {"variable": "x", "solutions": ["-2", "2"]}
        assert solve("a*y - 6", "y") == {"variable": "y", "solutions": ["6/a"]}

    def test_solve_needs_the_variable_when_there_are_several(self):
        with pytest.raises(RefusedInput):
            solve("a*y - 6", None)

    def test_differentiate(self):
        assert differentiate("x^3", "x", 1) == {"result": "3*x**2"}
        assert differentiate("x^3", "x", 2) == {"result": "6*x"}

    def test_integrate(self):
        assert integrate("2*x", "x", None, None) == {
            "result": "x**2",
            "evaluated": True,
        }
        assert integrate("x^2", "x", "0", "3") == {"result": "9", "evaluated": True}
        assert integrate("exp(-x^2)", "x", "-oo", "oo")["result"] == "sqrt(pi)"

    def test_answer_types_sympys_refusal(self):
        status, reason = answer("solve", ["x^x^x - 3*sin(x) = log(x)", None])
        assert status == "refused" and "generators" in reason

    def test_answer_refuses_an_unknown_operation(self):
        assert answer("format_disk", [])[0] == "error"


class TestWorker:
    def test_round_trip(self, worker):
        assert worker.run("calculate", "6*7") == (
            "ok",
            {"result": "42", "decimal": "42.0000000000000"},
        )
        assert worker.run("calculate", "x")[0] == "refused"

    def test_a_long_computation_is_killed_and_the_worker_recovers(self):
        worker = SymbolicWorker(timeout_seconds=2.0)
        try:
            started = time.monotonic()
            with pytest.raises(SymbolicTimeout):
                worker.run("integrate", SLOW_INTEGRAND, "x", None, None)
            assert time.monotonic() - started < 5
            assert worker.run("calculate", "1+1")[0] == "ok"
        finally:
            worker.stop()

    @pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
    def test_the_worker_has_a_memory_ceiling(self, worker):
        worker.run("calculate", "1")
        assert worker._process is not None
        with open(f"/proc/{worker._process.pid}/limits") as limits:
            line = next(row for row in limits if row.startswith("Max address space"))
        assert line.split()[3] == str(WORKER_MEMORY_BYTES)

    def test_a_dead_worker_is_replaced(self, worker):
        worker.run("calculate", "1")
        assert worker._process is not None
        os.kill(worker._process.pid, 9)
        worker._process.wait()
        assert worker.run("calculate", "2")[0] == "ok"
