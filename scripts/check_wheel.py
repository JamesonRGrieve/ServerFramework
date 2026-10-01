#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Release check on a built wheel: it ships no tests, and its provenance
manifest (zephyrex/_provenance.json) vouches for the commit being released,
built from a clean tree.

Usage:
    python scripts/check_wheel.py <dist-dir> <commit-sha>
"""

import json
import sys
import zipfile
from pathlib import Path
from typing import List

MANIFEST = "zephyrex/_provenance.json"


def problems(wheel: Path, commit: str) -> List[str]:
    found = []
    archive = zipfile.ZipFile(wheel)
    names = archive.namelist()
    tests = [n for n in names if n.endswith("_test.py") or n.endswith("/conftest.py")]
    if tests:
        found.append(f"ships {len(tests)} test modules, e.g. {tests[0]}")
    if MANIFEST not in names:
        return found + [f"has no {MANIFEST}"]
    manifest = json.loads(archive.read(MANIFEST))
    if manifest.get("commit") != commit:
        found.append(f"manifest commit {manifest.get('commit')} is not {commit}")
    if manifest.get("git_status") != "clean":
        found.append(f"built from a {manifest.get('git_status')} tree")
    return found


def main(argv: List[str]) -> int:
    dist, commit = Path(argv[0]), argv[1]
    (wheel,) = dist.glob("*.whl")
    found = problems(wheel, commit)
    for problem in found:
        print(f"{wheel.name}: {problem}")
    print(f"{wheel.name}: " + ("refused" if found else "ok"))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
