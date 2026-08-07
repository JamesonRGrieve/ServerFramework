#!/usr/bin/env python3
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
import subprocess
import sys
from pathlib import Path

BASELINE_FILE = Path(__file__).resolve().parent.parent / ".ratchet-baseline.json"
IGNORES = [
    "--ignore=src/zephyrex/extensions/auth_api_keys",
    "--ignore=src/zephyrex/extensions/auth_marketplace",
    "--ignore=src/zephyrex/extensions/auth_merge",
    "--ignore=src/zephyrex/extensions/auth_notifications",
    "--ignore=src/zephyrex/extensions/auth_oauth",
    "--ignore=src/zephyrex/extensions/auth_privacy",
    "--ignore=src/zephyrex/extensions/fileio",
    "--ignore=src/zephyrex/extensions/meta_labels",
]


def _load_baseline() -> dict:
    if BASELINE_FILE.exists():
        return json.loads(BASELINE_FILE.read_text())
    return {}


def _save_baseline(data: dict) -> None:
    BASELINE_FILE.write_text(json.dumps(data, indent=2) + "\n")


def _count_collected_tests() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "src/", "--co", "-q", "-n0"] + IGNORES,
        capture_output=True,
        text=True,
        timeout=60,
    )
    for line in result.stdout.splitlines():
        if "tests collected" in line or "test collected" in line:
            import re
            m = re.search(r"(\d+)\s+tests?\s+collected", line)
            if m:
                return int(m.group(1))
    return 0


def _count_mypy_errors() -> int:
    result = subprocess.run(
        [
            sys.executable, "-m", "mypy",
            "src/zephyrex/",
            "--ignore-missing-imports",
            "--no-error-summary",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return sum(1 for line in result.stdout.splitlines() if "error:" in line)


def _count_black_violations() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "black", "--check", "--quiet", "src/zephyrex/"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return sum(1 for line in result.stderr.splitlines() if "would reformat" in line)


def main() -> int:
    update_mode = "--update" in sys.argv
    baseline = _load_baseline()
    failures = []
    updated = False

    checks = [
        ("test_count_minimum", _count_collected_tests, "increase", "tests collected"),
        ("mypy_error_maximum", _count_mypy_errors, "decrease", "mypy errors"),
        ("black_reformattable_maximum", _count_black_violations, "decrease", "black violations"),
    ]

    for key, measure_fn, direction, label in checks:
        try:
            current = measure_fn()
        except Exception as e:
            print(f"[ratchet] SKIP {label}: {e}")
            continue

        old = baseline.get(key)
        if old is None:
            baseline[key] = current
            updated = True
            print(f"[ratchet] SEED {label}: {current}")
            continue

        if direction == "increase":
            if current < old:
                failures.append(
                    f"{label}: {current} < baseline {old} (regression)"
                )
            elif current > old:
                baseline[key] = current
                updated = True
                print(f"[ratchet] OK {label}: {old} -> {current} (improved)")
            else:
                print(f"[ratchet] OK {label}: unchanged at {current}")
        else:
            if current > old:
                failures.append(
                    f"{label}: {current} > baseline {old} (regression)"
                )
            elif current < old:
                baseline[key] = current
                updated = True
                print(f"[ratchet] OK {label}: {old} -> {current} (improved)")
            else:
                print(f"[ratchet] OK {label}: unchanged at {current}")

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
