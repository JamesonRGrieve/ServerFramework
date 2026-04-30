"""Canonical CLI entry point for the ServerFramework package.

Both ``server-framework`` (console script) and ``python -m serverframework``
dispatch through :func:`main`.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="server-framework")
    sub = parser.add_subparsers(dest="command", metavar="command")

    run_p = sub.add_parser("run", help="Boot the framework with uvicorn.")
    run_p.add_argument("--extensions", default=None, help="CSV of extensions to load.")
    run_p.add_argument(
        "--extensions-path", default=None, help="Filesystem path to discover extensions."
    )
    run_p.add_argument("--host", default=None, help="Bind host.")
    run_p.add_argument("--port", type=int, default=None, help="Bind port.")
    run_p.add_argument("--workers", type=int, default=None, help="Worker count.")
    reload_grp = run_p.add_mutually_exclusive_group()
    reload_grp.add_argument(
        "--reload", dest="reload", action="store_true", default=None,
        help="Enable uvicorn auto-reload."
    )
    reload_grp.add_argument(
        "--no-reload", dest="reload", action="store_false", default=None,
        help="Disable uvicorn auto-reload."
    )
    run_p.add_argument("--log-level", default=None, help="Log level.")
    run_p.add_argument(
        "--no-proxy-headers", dest="proxy_headers", action="store_false", default=True,
        help="Disable proxy header trust."
    )

    sub.add_parser("bootstrap", help="Run the venv + dependency bootstrap.")
    sub.add_parser("version", help="Print the framework version.")

    return parser


def _cmd_run(args: argparse.Namespace) -> int:
    from serverframework import run as _run

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
        from bootstrap import run_venv_bootstrap
    except Exception:
        import subprocess
        from pathlib import Path

        bootstrap_path = Path(__file__).resolve().parent.parent / "bootstrap.py"
        completed = subprocess.run([sys.executable, str(bootstrap_path)])
        return completed.returncode
    return 0 if run_venv_bootstrap() else 1


def _cmd_version(_args: argparse.Namespace) -> int:
    version: Optional[str] = None
    try:
        import serverframework as _sf

        version = getattr(_sf, "__version__", None)
    except Exception:
        version = None

    if not version:
        try:
            from importlib.metadata import PackageNotFoundError, version as _pkg_version

            try:
                version = _pkg_version("serverframework")
            except PackageNotFoundError:
                try:
                    version = _pkg_version("server")
                except PackageNotFoundError:
                    version = None
        except Exception:
            version = None

    if not version:
        print("serverframework version: unknown")
    else:
        print(f"serverframework {version}")
    return 0


_DISPATCH = {
    "run": _cmd_run,
    "bootstrap": _cmd_bootstrap,
    "version": _cmd_version,
}


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Canonical CLI entrypoint. Returns a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_usage(sys.stderr)
        print("server-framework: error: a subcommand is required", file=sys.stderr)
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
            from lib.Logging import logger

            logger.exception(f"server-framework: command '{args.command}' failed: {exc}")
        except Exception:
            print(f"server-framework: command '{args.command}' failed: {exc}", file=sys.stderr)
        return 1
