# SPDX-License-Identifier: AGPL-3.0-or-later
import importlib.util
import subprocess
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "ratchet_check", Path(__file__).with_name("ratchet-check.py")
)
assert _SPEC and _SPEC.loader
ratchet_check = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ratchet_check)
judge = ratchet_check.judge


@pytest.mark.parametrize(
    "old, current, direction, expected",
    [
        (None, 5, "increase", "seed"),
        (10, 10, "increase", "unchanged"),
        (10, 11, "increase", "improved"),
        (10, 9, "increase", "regression"),
        (3, 2, "decrease", "improved"),
        (3, 4, "decrease", "regression"),
    ],
)
def test_check_mode(old, current, direction, expected):
    assert judge(old, current, direction, force=False) == expected


@pytest.mark.parametrize("direction, current", [("increase", 9), ("decrease", 4)])
def test_update_mode_forces_a_regression(direction, current):
    old = 10 if direction == "increase" else 3
    assert judge(old, current, direction, force=True) == "forced"


def test_update_mode_still_reports_improvements():
    assert judge(10, 11, "increase", force=True) == "improved"


def _unmeasurable() -> int:
    raise subprocess.TimeoutExpired(cmd="pytest --co", timeout=60)


@pytest.mark.parametrize("update_mode", [False, True])
def test_an_unmeasurable_metric_fails_the_gate(update_mode):
    # A collection that timed out used to print SKIP and pass the commit,
    # leaving the test floor unenforced.
    baseline = {"test_count_minimum": 10}
    failures, updated = ratchet_check.evaluate(
        [("test_count_minimum", _unmeasurable, "increase", "tests collected")],
        baseline,
        update_mode,
    )
    assert failures == [
        "tests collected: could not be measured "
        "(Command 'pytest --co' timed out after 60 seconds)"
    ]
    assert not updated
    assert baseline == {"test_count_minimum": 10}


def test_evaluate_reports_a_regression_and_keeps_the_baseline():
    baseline = {"test_count_minimum": 10}
    failures, updated = ratchet_check.evaluate(
        [("test_count_minimum", lambda: 9, "increase", "tests collected")],
        baseline,
        False,
    )
    assert failures == ["tests collected: 9 < baseline 10 (regression)"]
    assert not updated
    assert baseline == {"test_count_minimum": 10}


def test_evaluate_raises_the_baseline_on_an_improvement():
    baseline = {"test_count_minimum": 10}
    failures, updated = ratchet_check.evaluate(
        [("test_count_minimum", lambda: 12, "increase", "tests collected")],
        baseline,
        False,
    )
    assert failures == []
    assert updated
    assert baseline == {"test_count_minimum": 12}


def test_collection_has_room_for_the_whole_suite():
    # The runner collects the suite in about 70s; 60s timed it out.
    assert ratchet_check.COLLECT_TIMEOUT_SECONDS >= 300
