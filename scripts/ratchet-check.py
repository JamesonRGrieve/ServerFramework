#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""One-way ratchet checks for the pre-commit hook.

Each metric must be at least as good as the baseline. Any regression
blocks the commit. Improvements auto-update the baseline in the same
commit so the ratchet only moves forward.

Usage:
    python scripts/ratchet-check.py          # check mode (pre-commit)
    python scripts/ratchet-check.py --update # force-update baselines
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_FILE = REPO_ROOT / ".ratchet-baseline.json"
IGNORES = []
# Collecting the whole suite takes about 70s on the build runner.
COLLECT_TIMEOUT_SECONDS = 600
MYPY_TIMEOUT_SECONDS = 300
BLACK_TIMEOUT_SECONDS = 60


def _load_baseline() -> dict:
    if BASELINE_FILE.exists():
        return json.loads(BASELINE_FILE.read_text())
    return {}


def _save_baseline(data: dict) -> None:
    BASELINE_FILE.write_text(json.dumps(data, indent=2) + "\n")


def judge(old: int | None, current: int, direction: str, force: bool) -> str:
    """How ``current`` moves a baseline of ``old``: ``seed`` (no baseline
    yet), ``improved``, ``unchanged``, ``regression``, or ``forced`` (a
    regression accepted by ``--update``, for a baseline that legitimately
    moved, e.g. tests of a removed field).

    ``direction`` is ``increase`` when higher is better, else ``decrease``.
    """
    if old is None:
        return "seed"
    if current == old:
        return "unchanged"
    better = current > old if direction == "increase" else current < old
    if better:
        return "improved"
    return "forced" if force else "regression"


def _count_collected_tests() -> int:
    result = subprocess.run(
        # The same test paths the hook runs: pytest.ini's testpaths.
        [sys.executable, "-m", "pytest", "--co", "-q", "-n0"] + IGNORES,
        capture_output=True,
        text=True,
        timeout=COLLECT_TIMEOUT_SECONDS,
    )
    for line in result.stdout.splitlines():
        if "tests collected" in line or "test collected" in line:
            m = re.search(r"(\d+)\s+tests?\s+collected", line)
            if m:
                return int(m.group(1))
    return 0


def _count_mypy_errors() -> int:
    # Root mypy at ``src`` and target the package by name so intra-package
    # imports resolve. Running ``mypy src/zephyrex/`` from the repo root maps
    # the files to ``src.zephyrex.*`` while their imports say ``zephyrex.*``;
    # with --ignore-missing-imports every cross-module reference then collapses
    # to Any, hiding real errors and inventing spurious no-any-return ones.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "-p",
            "zephyrex",
            "--ignore-missing-imports",
            "--no-error-summary",
        ],
        capture_output=True,
        text=True,
        timeout=MYPY_TIMEOUT_SECONDS,
        cwd=str(REPO_ROOT),
        env={**os.environ, "MYPYPATH": str(REPO_ROOT / "src")},
    )
    return sum(1 for line in result.stdout.splitlines() if "error:" in line)


def _count_black_violations() -> int:
    result = subprocess.run(
        # No --quiet: it suppresses the "would reformat" lines counted below,
        # which made this metric read 0 regardless of the tree.
        [sys.executable, "-m", "black", "--check", "src/zephyrex/"],
        capture_output=True,
        text=True,
        timeout=BLACK_TIMEOUT_SECONDS,
    )
    return sum(1 for line in result.stderr.splitlines() if "would reformat" in line)


CHECKS: list[tuple[str, Callable[[], int], str, str]] = [
    ("test_count_minimum", _count_collected_tests, "increase", "tests collected"),
    ("mypy_error_maximum", _count_mypy_errors, "decrease", "mypy errors"),
    (
        "black_reformattable_maximum",
        _count_black_violations,
        "decrease",
        "black violations",
    ),
]


def evaluate(
    checks: list[tuple[str, Callable[[], int], str, str]],
    baseline: dict,
    update_mode: bool,
) -> tuple[list[str], bool]:
    """Measure each check against ``baseline`` (updated in place on an
    improvement). Returns the failures and whether the baseline moved.

    A metric that cannot be measured is a failure: the gate cannot show it
    did not regress, so it fails closed rather than skipping it.
    """
    failures = []
    updated = False
    for key, measure_fn, direction, label in checks:
        try:
            current = measure_fn()
        except Exception as e:
            failures.append(f"{label}: could not be measured ({e})")
            continue

        old = baseline.get(key)
        verdict = judge(old, current, direction, force=update_mode)
        if verdict == "regression":
            symbol = "<" if direction == "increase" else ">"
            failures.append(f"{label}: {current} {symbol} baseline {old} (regression)")
            continue
        if verdict != "unchanged":
            baseline[key] = current
            updated = True
        print(f"[ratchet] {verdict.upper()} {label}: {old} -> {current}")
    return failures, updated


def main() -> int:
    update_mode = "--update" in sys.argv
    baseline = _load_baseline()
    failures, updated = evaluate(CHECKS, baseline, update_mode)

    if updated or update_mode:
        _save_baseline(baseline)
        print(f"[ratchet] baseline updated: {BASELINE_FILE}")

    if failures and not update_mode:
        print("\n[ratchet] FAILED:")
        for f in failures:
            print(f"  {f}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
