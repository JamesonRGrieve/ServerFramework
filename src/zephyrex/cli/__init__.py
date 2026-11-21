# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical CLI entry point for the ZephyrexFrameworkServer package.

Both ``zephyrex`` (console script) and ``python -m zephyrex`` dispatch
through :func:`main`: the server commands (run, bootstrap, version)
and the admin commands (``migrate`` among them) (``models``, ``record``, the curated
groups and each extension's ``CLI_<Name>.py``), which print one JSON
envelope and exit with the code table in ``CLI.md``.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, List, Optional, Sequence, TextIO

from zephyrex.cli.Context import (
    Command,
    CommandContext,
    CommandGroup,
    ExitCode,
    Parser,
)

__all__ = ["Command", "CommandContext", "CommandGroup", "ExitCode", "main"]

SERVER_COMMANDS = ("run", "bootstrap", "version")


def _extension_options(argv: Sequence[str]) -> tuple[List[str], Optional[str]]:
    """The extensions (``--extensions`` else ``APP_EXTENSIONS``) and path an
    invocation names, read before the full parse so their commands mount."""
    from zephyrex.lib.Environment import env

    pre = Parser(add_help=False)
    pre.add_argument("--extensions", default=None)
    pre.add_argument("--extensions-path", default=None)
    known, _ = pre.parse_known_args(list(argv))
    csv = known.extensions if known.extensions is not None else env("APP_EXTENSIONS")
    path = known.extensions_path or os.environ.get("EXTENSIONS_PATH") or None
    return [name.strip() for name in (csv or "").split(",") if name.strip()], path


def _build_parser(argv: Sequence[str] = ()) -> argparse.ArgumentParser:
    from zephyrex.cli import Admin, Records
    from zephyrex.cli.Context import global_options
    from zephyrex.cli.Extensions import register_extension_groups

    parser = Parser(prog="zephyrex")
    sub = parser.add_subparsers(dest="command", metavar="command")
    options = global_options()

    models = sub.add_parser(
        "models", help="List the loaded models and their managers.", parents=[options]
    )
    models.set_defaults(
        command=Command("models", Records.list_models, False, False, None)
    )
    Records.register(sub, options)
    Admin.register(sub, options)
    extensions, path = _extension_options(argv)
    register_extension_groups(
        sub, options, extensions, path, taken=[*SERVER_COMMANDS, *sub.choices]
    )

    run_p = sub.add_parser("run", help="Boot the framework with uvicorn.")
    run_p.add_argument("--extensions", default=None, help="CSV of extensions to load.")
    run_p.add_argument(
        "--extensions-path",
        default=None,
        help="Filesystem path to discover extensions.",
    )
    run_p.add_argument("--host", default=None, help="Bind host.")
    run_p.add_argument("--port", type=int, default=None, help="Bind port.")
    run_p.add_argument("--workers", type=int, default=None, help="Worker count.")
    reload_grp = run_p.add_mutually_exclusive_group()
    reload_grp.add_argument(
        "--reload",
        dest="reload",
        action="store_true",
        default=None,
        help="Enable uvicorn auto-reload.",
    )
    reload_grp.add_argument(
        "--no-reload",
        dest="reload",
        action="store_false",
        default=None,
        help="Disable uvicorn auto-reload.",
    )
    run_p.add_argument("--log-level", default=None, help="Log level.")
    run_p.add_argument(
        "--no-proxy-headers",
        dest="proxy_headers",
        action="store_false",
        default=True,
        help="Disable proxy header trust.",
    )

    sub.add_parser("bootstrap", help="Run the venv + dependency bootstrap.")
    sub.add_parser("version", help="Print the framework version.")

    return parser


def _cmd_run(args: argparse.Namespace) -> int:
    from zephyrex import run as _run

    kwargs = {
        "extensions": args.extensions,
        "extensions_path": args.extensions_path,
        "workers": args.workers,
        "reload": args.reload,
        "log_level": args.log_level,
        "proxy_headers": args.proxy_headers,
    }
    if args.host is not None:
        kwargs["host"] = args.host
    if args.port is not None:
        kwargs["port"] = args.port
    _run(**kwargs)
    return 0


def _cmd_bootstrap(_args: argparse.Namespace) -> int:
    try:
        from zephyrex.bootstrap import run_venv_bootstrap
    except Exception:
        import subprocess
        from pathlib import Path

        bootstrap_path = (
            Path(__file__).resolve().parent.parent / "zephyrex.bootstrap.py"
        )
        completed = subprocess.run([sys.executable, str(bootstrap_path)])
        return completed.returncode
    return 0 if run_venv_bootstrap() else 1


def _cmd_version(_args: argparse.Namespace) -> int:
    version: str | None = None
    try:
        import zephyrex as _sf

        version = getattr(_sf, "__version__", None)
    except Exception:
        version = None

    if not version:
        from zephyrex import get_framework_version

        resolved = get_framework_version()
        version = resolved if resolved != "0.0.0" else None

    if not version:
        print("zephyrex version: unknown")
    else:
        print(f"zephyrex {version}")
    return 0


_DISPATCH = {
    "run": _cmd_run,
    "bootstrap": _cmd_bootstrap,
    "version": _cmd_version,
}


def _boot(args: argparse.Namespace) -> Any:
    """The app an admin command runs against: booted without uvicorn,
    background services, migrations or seeding, so a read changes nothing
    (``zephyrex migrate`` migrates, ``zephyrex seed`` seeds)."""
    os.environ["RUN_MIGRATIONS"] = "false"
    from zephyrex import instance
    from zephyrex.app import environment_overrides

    with environment_overrides({"SEED_DATA": "false"}):
        return instance(
            extensions=args.extensions, extensions_path=args.extensions_path
        )


def _run_admin(
    args: argparse.Namespace,
    argv: List[str],
    app: Any,
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    from zephyrex.cli.Context import REMOTE_KEY_ENV, execute
    from zephyrex.lib.Logging import route_logs_to

    previous = route_logs_to(stderr)
    try:
        command: Command = args.command
        if args.remote:
            from zephyrex.cli.Remote import RemoteClient

            client = RemoteClient.connect(
                args.remote, os.environ.get(REMOTE_KEY_ENV, "")
            )
            return execute(
                command,
                args,
                argv,
                client=client,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
            )
        return execute(
            command,
            args,
            argv,
            app=app if app is not None else _boot(args),
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
        )
    finally:
        route_logs_to(previous)


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    app: Any = None,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """Canonical CLI entrypoint. Returns a process exit code. ``app`` runs
    admin commands against an already-booted app (tests, embedding)."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser(arguments)
    args = parser.parse_args(arguments)

    if isinstance(args.command, Command):
        return _run_admin(args, arguments, app, stdin, stdout, stderr)

    if not args.command:
        parser.print_usage(sys.stderr)
        print("zephyrex: error: a subcommand is required", file=sys.stderr)
        return 2

    handler = _DISPATCH[args.command]

    try:
        return handler(args)
    except KeyboardInterrupt:
        return 130
    except SystemExit:
        raise
    except Exception as exc:
        try:
            from zephyrex.lib.Logging import logger

            logger.exception(f"zephyrex: command '{args.command}' failed: {exc}")
        except Exception:
            print(
                f"zephyrex: command '{args.command}' failed: {exc}",
                file=sys.stderr,
            )
        return 1
