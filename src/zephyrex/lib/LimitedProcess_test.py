# SPDX-License-Identifier: AGPL-3.0-or-later
"""LimitedProcess: a program runs with only the environment given, and
each limit holds: memory, CPU time, file size and wall-clock time."""

import asyncio
import sys
from pathlib import Path

import pytest

from zephyrex.lib import LimitedProcess
from zephyrex.lib.LimitedProcess import (
    LimitExceeded,
    Limits,
    available,
    run_limited,
)

GENEROUS = Limits(
    memory_bytes=1024**3, cpu_seconds=10, file_bytes=1024**2, wall_seconds=20
)
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX limits")


async def python(code: str, limits: Limits, tmp_path: Path, **env: str):
    return await run_limited(
        [sys.executable, "-c", code],
        limits,
        cwd=str(tmp_path),
        env={"PATH": "/usr/bin:/bin", **env},
    )


async def test_a_program_runs_with_only_the_given_environment(tmp_path):
    done = await python(
        "import os; print(sorted(k for k in os.environ if k != 'LC_CTYPE'))",
        GENEROUS,
        tmp_path,
        ONLY="1",
    )
    assert done.returncode == 0
    assert done.stdout.strip() == "['ONLY', 'PATH']"


async def test_what_the_spawn_adds_never_reaches_the_program(tmp_path):
    """Sentry's subprocess integration adds its trace headers to every
    child's environment; the launcher used to pass the lot to the program.
    It now hands on only the names it was given."""
    launcher = await asyncio.create_subprocess_exec(
        sys.executable,
        LimitedProcess.__file__,
        str(GENEROUS.memory_bytes),
        str(GENEROUS.cpu_seconds),
        str(GENEROUS.file_bytes),
        "ONLY",
        "PATH",
        "--",
        sys.executable,
        "-c",
        "import os; print(sorted(k for k in os.environ if k != 'LC_CTYPE'))",
        cwd=str(tmp_path),
        env={"PATH": "/usr/bin:/bin", "ONLY": "1", "SUBPROCESS_SENTRY_TRACE": "x"},
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, _ = await launcher.communicate()
    assert launcher.returncode == 0
    assert stdout.decode().strip() == "['ONLY', 'PATH']"


async def test_memory_is_capped(tmp_path):
    limits = Limits(256 * 1024**2, 10, 1024**2, 20)
    done = await python("bytearray(1024 ** 3)", limits, tmp_path)
    assert done.returncode != 0 and "MemoryError" in done.stderr


async def test_cpu_time_is_capped(tmp_path):
    limits = Limits(1024**3, 1, 1024**2, 20)
    done = await python("while True: pass", limits, tmp_path)
    assert done.returncode != 0  # SIGXCPU


async def test_file_size_is_capped(tmp_path):
    """Python ignores SIGXFSZ and takes the short write; a C program such
    as openscad is killed by it. Either way nothing passes the cap."""
    limits = Limits(1024**3, 10, 1000, 20)
    await python("open('big', 'wb').write(b'x' * 5000)", limits, tmp_path)
    assert (tmp_path / "big").stat().st_size <= 1000


async def test_wall_time_kills_it(tmp_path):
    limits = Limits(1024**3, 10, 1024**2, 0.5)
    with pytest.raises(LimitExceeded):
        await python("import time; time.sleep(30)", limits, tmp_path)


def test_available():
    assert available("sh") and not available("no-such-program-zx")
