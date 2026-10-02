# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compiling OpenSCAD code that an agent or a user wrote, safely.

OpenSCAD reads files named in the code (``include <…>``, ``use <…>``,
``import()``, ``surface()``, the ``dxf_*`` and ``import_*`` modules,
any ``file=`` argument) and libraries on ``OPENSCADPATH``; code naming
one is refused, and the program runs in an empty directory with an
emptied environment. A parameter override (``-D name=value``) takes a
number, a boolean or a plain string only: a value is an OpenSCAD
expression, where ``import("/etc/passwd")`` would be read. The program
runs under memory, CPU, output-size and wall-clock limits.
"""

import os
import re
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Mapping, Union

from zephyrex.lib.LimitedProcess import Limits, run_limited

MAX_CODE_CHARS = 200_000
MAX_PARAMETERS = 50
LIMITS = Limits(
    memory_bytes=2 * 1024**3,
    cpu_seconds=60,
    file_bytes=100 * 1024**2,
    wall_seconds=90,
)
# format -> (OpenSCAD's export format, media type)
FORMATS: Mapping[str, tuple] = {
    "stl": ("binstl", "model/stl"),
    "3mf": ("3mf", "model/3mf"),
    "off": ("off", "model/x-off"),
    "amf": ("amf", "application/x-amf"),
    "dxf": ("dxf", "image/vnd.dxf"),
    "svg": ("svg", "image/svg+xml"),
}
_FILE_ACCESS = re.compile(
    r"\b(?:include|use)\s*<" r"|\b(?:import\w*|surface|dxf_\w+)\s*\(" r"|\bfile\s*="
)
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_PLAIN_TEXT = re.compile(r'^[^"\\\x00-\x1f]{0,200}$')

Value = Union[bool, int, float, str]


class Refused(ValueError):
    """Code or a parameter this module will not hand to OpenSCAD."""


@dataclass(frozen=True)
class Compiled:
    ok: bool
    body: bytes
    log: str


def checked_code(code: str) -> str:
    if not code.strip():
        raise Refused("the model has no code")
    if len(code) > MAX_CODE_CHARS:
        raise Refused(f"model code is at most {MAX_CODE_CHARS} characters")
    found = _FILE_ACCESS.search(code)
    if found:
        raise Refused(
            f"model code may not read files ({found.group(0).strip()!r}); "
            "inline everything it needs"
        )
    return code


def definition(name: str, value: Value) -> str:
    """``name=value`` for ``-D``, the value a literal and nothing more."""
    if not _NAME.match(name):
        raise Refused(f"{name!r} is not a parameter name")
    if isinstance(value, bool):
        literal = "true" if value else "false"
    elif isinstance(value, (int, float)):
        if value != value or value in (float("inf"), float("-inf")):
            raise Refused(f"{name} must be a finite number")
        literal = repr(value)
    elif isinstance(value, str) and _PLAIN_TEXT.match(value):
        literal = f'"{value}"'
    else:
        raise Refused(f"{name} must be a number, a boolean or plain text")
    return f"{name}={literal}"


def definitions(parameters: Mapping[str, Value]) -> List[str]:
    if len(parameters) > MAX_PARAMETERS:
        raise Refused(f"at most {MAX_PARAMETERS} parameters")
    arguments: List[str] = []
    for name, value in parameters.items():
        arguments += ["-D", definition(name, value)]
    return arguments


def problems(log: str) -> List[str]:
    """The warnings and errors in OpenSCAD's output."""
    return [
        line.strip()
        for line in log.splitlines()
        if line.startswith(("WARNING", "ERROR", "TRACE"))
    ]


async def compile_model(
    executable: str,
    code: str,
    export_format: str,
    output_name: str,
    parameters: Mapping[str, Value],
) -> Compiled:
    """Run OpenSCAD on ``code`` and read what it wrote to ``output_name``."""
    arguments = definitions(parameters)
    with tempfile.TemporaryDirectory(prefix="zephyrex-openscad-") as workspace:
        source = os.path.join(workspace, "model.scad")
        with open(source, "w", encoding="utf-8") as handle:
            handle.write(checked_code(code))
        env: Dict[str, str] = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": workspace,
            "LANG": "C.UTF-8",
        }
        done = await run_limited(
            [
                executable,
                "--export-format",
                export_format,
                "-o",
                output_name,
                *arguments,
                "model.scad",
            ],
            LIMITS,
            cwd=workspace,
            env=env,
        )
        output = os.path.join(workspace, output_name)
        body = b""
        if done.returncode == 0 and os.path.exists(output):
            with open(output, "rb") as handle:
                body = handle.read()
        return Compiled(ok=done.returncode == 0, body=body, log=done.stderr)
