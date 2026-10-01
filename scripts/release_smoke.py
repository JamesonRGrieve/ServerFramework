#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Release smoke test, run against an installed ``zephyrex``.

The release workflow installs the wheel it just published to TestPyPI into a
clean environment and runs this before anything reaches PyPI, twice:

- ``--core``, against plain ``zephyrex``: the server boots and answers
  ``/health`` and ``/readyz`` (the database is reachable) with nothing but
  core's own dependencies installed;
- then against ``zephyrex[all]``: every package any bundled extension
  declares is installed (what the ``all`` extra promises), and it boots.

Exits non-zero, naming what failed, otherwise. Run it outside the source
tree so the installed package, not the checkout, is what gets imported.

Usage:
    python scripts/release_smoke.py [--core]
"""

import os
import re
import sys
import tempfile
from importlib.metadata import PackageNotFoundError, distribution
from typing import List


def missing_requirements() -> List[str]:
    from zephyrex.extensions.sync_dependencies import declared_requirements

    missing = []
    for extension, requirements in sorted(declared_requirements().items()):
        for requirement in requirements:
            name = re.split(r"[<>=!~\[;\s]", requirement, maxsplit=1)[0]
            try:
                distribution(name)
            except PackageNotFoundError:
                missing.append(f"{extension}: {requirement}")
    return missing


def boot_failures() -> List[str]:
    from fastapi.testclient import TestClient

    from zephyrex.app import instance

    failures = []
    client = TestClient(instance(extensions=""))
    for path in ("/health", "/readyz"):
        response = client.get(path)
        if response.status_code != 200:
            failures.append(f"GET {path}: {response.status_code} {response.text}")
    return failures


def main(argv: List[str]) -> int:
    core_only = argv == ["--core"]
    if argv and not core_only:
        print("usage: release_smoke.py [--core]")
        return 2
    import zephyrex

    if "site-packages" not in (zephyrex.__file__ or ""):
        print(f"zephyrex imported from {zephyrex.__file__}, not an install")
        return 1
    with tempfile.TemporaryDirectory() as workdir:
        os.environ.setdefault("DATABASE_TYPE", "sqlite")
        os.environ.setdefault("DATABASE_PATH", workdir)
        os.environ.setdefault("JWT_SECRET", os.urandom(32).hex())
        problems = ([] if core_only else missing_requirements()) + boot_failures()
    for problem in problems:
        print(f"FAIL {problem}")
    print("release smoke: " + ("failed" if problems else "passed"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
