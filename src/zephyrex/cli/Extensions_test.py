# SPDX-License-Identifier: AGPL-3.0-or-later
"""Extension commands: ``CLI_<Name>.py`` in an extension's directory mounts
as ``zephyrex <extension> <command>`` and runs through the same machinery."""

from __future__ import annotations

import io
import json
import textwrap
from pathlib import Path

import pytest

from zephyrex.cli import _build_parser, main
from zephyrex.cli.Context import ExitCode
from zephyrex.cli.Extensions import cli_modules

DEMO_CLI = textwrap.dedent("""
    def register(group):
        hello = group.command("hello", hello_handler, help="Say hello.")
        hello.add_argument("who")
        group.command("wipe", wipe_handler, help="Pretend to wipe.", mutates=True, destructive=True)


    def hello_handler(ctx, args):
        return {"hello": args.who, "requester": ctx.requester_id}


    def wipe_handler(ctx, args):
        ctx.resource = ("demo", "all")
        return {"wiped": True}
    """)


@pytest.fixture
def demo_root(tmp_path: Path) -> Path:
    extension = tmp_path / "clidemo"
    extension.mkdir()
    (extension / "CLI_Demo.py").write_text(DEMO_CLI)
    (extension / "CLI_Demo_test.py").write_text(
        "raise AssertionError('tests are not commands')\n"
    )
    return tmp_path


def run(server, *argv: str) -> tuple[int, dict]:
    out = io.StringIO()
    code = main(
        list(argv),
        app=server.app,
        stdin=io.StringIO(),
        stdout=out,
        stderr=io.StringIO(),
    )
    return code, json.loads(out.getvalue())


def test_cli_modules_skips_tests(demo_root):
    assert [p.name for p in cli_modules("clidemo", str(demo_root))] == ["CLI_Demo.py"]


def test_extension_commands_mount_and_run(server, demo_root):
    flags = ["--extensions", "clidemo", "--extensions-path", str(demo_root)]
    code, envelope = run(server, "clidemo", "hello", "world", *flags)
    assert code == ExitCode.OK, envelope
    assert envelope["command"] == "clidemo hello"
    assert envelope["result"]["hello"] == "world"

    code, envelope = run(server, "clidemo", "wipe", *flags)
    assert code == ExitCode.USAGE

    code, envelope = run(server, "clidemo", "wipe", "--yes", *flags)
    assert code == ExitCode.OK and envelope["result"] == {"wiped": True}


def test_extension_commands_absent_when_not_loaded(demo_root):
    parser = _build_parser(["--extensions", "", "--extensions-path", str(demo_root)])
    with pytest.raises(SystemExit):
        parser.parse_args(["clidemo", "hello", "x"])


def test_extension_named_like_a_core_command_is_refused(tmp_path):
    (tmp_path / "record").mkdir()
    (tmp_path / "record" / "CLI_Record.py").write_text(DEMO_CLI)
    with pytest.raises(ValueError, match="core command"):
        _build_parser(["--extensions", "record", "--extensions-path", str(tmp_path)])
