# SPDX-License-Identifier: AGPL-3.0-or-later
import importlib.util
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
