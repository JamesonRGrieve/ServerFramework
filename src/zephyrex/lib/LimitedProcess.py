# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run an external program under limits: address space, CPU time, the
size of any file it writes, and wall-clock time (after which it is
killed).

The limits are set in the child itself: this module, run by file path,
sets them and then replaces itself with the program (``execvp``), so no
code runs between fork and exec in the server (``preexec_fn`` is unsafe
in a threaded process) and the program inherits the limits. POSIX only.
"""

import asyncio
import os
import sys
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

OUTPUT_LIMIT_BYTES = 256 * 1024


@dataclass(frozen=True)
class Limits:
    memory_bytes: int
    cpu_seconds: int
    file_bytes: int
    wall_seconds: float


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: str
    stderr: str


class LimitExceeded(Exception):
    """The program ran past its wall-clock limit and was killed."""


async def run_limited(
    command: Sequence[str],
    limits: Limits,
    *,
    cwd: str,
    env: Mapping[str, str],
) -> Completed:
    """Run ``command`` (no shell) in ``cwd`` with only ``env``; its exit
    status and (bounded) output."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        os.path.abspath(__file__),
        str(limits.memory_bytes),
        str(limits.cpu_seconds),
        str(limits.file_bytes),
        "--",
        *command,
        cwd=cwd,
        env=dict(env),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=limits.wall_seconds
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise LimitExceeded(f"{command[0]} ran longer than {limits.wall_seconds}s")
    return Completed(
        returncode=process.returncode if process.returncode is not None else -1,
        stdout=stdout[:OUTPUT_LIMIT_BYTES].decode("utf-8", "replace"),
        stderr=stderr[:OUTPUT_LIMIT_BYTES].decode("utf-8", "replace"),
    )


def _limit_and_exec(argv: Sequence[str]) -> None:
    """``<memory> <cpu> <file> -- program args…``: set the limits, then
    become the program."""
    import resource

    memory, cpu, file_size, separator, *command = argv
    if separator != "--" or not command:
        raise SystemExit("usage: LimitedProcess.py MEMORY CPU FILE -- PROGRAM [ARGS]")
    resource.setrlimit(resource.RLIMIT_AS, (int(memory), int(memory)))
    resource.setrlimit(resource.RLIMIT_CPU, (int(cpu), int(cpu)))
    resource.setrlimit(resource.RLIMIT_FSIZE, (int(file_size), int(file_size)))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.execvp(command[0], command)


def available(program: str, path: Optional[str] = None) -> bool:
    """Whether ``program`` is on the PATH."""
    from shutil import which

    return which(program, path=path) is not None


if __name__ == "__main__":
    _limit_and_exec(sys.argv[1:])
