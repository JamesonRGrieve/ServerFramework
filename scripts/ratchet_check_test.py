# SPDX-License-Identifier: AGPL-3.0-or-later
import importlib.util
import subprocess
import sys
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


def _completed(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


@pytest.mark.parametrize("returncode, count", [(0, 0), (1, 3)], ids=["clean", "found"])
def test_a_finished_check_is_counted(returncode, count):
    assert ratchet_check.counted("mypy", _completed(returncode), count) == count


@pytest.mark.parametrize(
    "returncode, count",
    [(1, 0), (2, 0), (2, 4), (123, 0), (0, 2)],
    ids=[
        "found-nothing",
        "crash",
        "crash-with-errors",
        "black-error",
        "clean-but-found",
    ],
)
def test_a_failed_check_is_not_a_measurement(returncode, count):
    # A crash or a missing tool counted 0 errors, read as IMPROVED, and the
    # baseline was rewritten to 0 (#250).
    with pytest.raises(ratchet_check.ToolFailed, match=f"exited {returncode}"):
        ratchet_check.counted("mypy", _completed(returncode, stderr="boom"), count)


def test_a_missing_tool_fails_the_gate():
    """``python -m <tool>`` without the tool installed exits 1 with nothing
    on stdout: 0 counted errors, which must not pass for a clean run."""
    result = subprocess.run(
        [sys.executable, "-m", "zephyrex_no_such_checker"],
        capture_output=True,
        text=True,
    )
    with pytest.raises(ratchet_check.ToolFailed, match="No module named"):
        ratchet_check.counted("mypy", result, 0)


def test_a_file_black_cannot_parse_fails_the_gate(tmp_path):
    """black reports ``error: cannot format`` and exits 123, with no "would
    reformat" line: 0 violations, which must not pass for a clean run."""
    broken = tmp_path / "broken.py"
    broken.write_text("def broken(:\n")
    result = subprocess.run(
        [sys.executable, "-m", "black", "--check", str(broken)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 123
    with pytest.raises(ratchet_check.ToolFailed, match="cannot format"):
        ratchet_check.counted("black", result, 0)


def test_a_tool_failure_fails_evaluate_and_keeps_the_baseline():
    def crashed_mypy() -> int:
        return ratchet_check.counted("mypy", _completed(2, stderr="crash"), 0)

    baseline = {"mypy_error_maximum": 5}
    failures, updated = ratchet_check.evaluate(
        [("mypy_error_maximum", crashed_mypy, "decrease", "mypy errors")],
        baseline,
        False,
    )
    assert len(failures) == 1
    assert failures[0].startswith("mypy errors: could not be measured")
    assert not updated
    assert baseline == {"mypy_error_maximum": 5}


def test_collection_has_room_for_the_whole_suite():
    # The runner collects the suite in about 70s; 60s timed it out.
    assert ratchet_check.COLLECT_TIMEOUT_SECONDS >= 300
