"""Canonical CLI entry point for the ZephyrexFrameworkServer package.

Both ``zephyrex-server`` (console script) and ``python -m zephyrex``
dispatch through :func:`main`.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zephyrex-server")
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

    migrate_p = sub.add_parser("migrate", help="Run database migrations (pre-deploy).")
    migrate_p.add_argument("--extensions", default=None, help="CSV of extensions to migrate.")
    migrate_p.add_argument(
        "--extensions-path", default=None, help="Filesystem path to discover extensions."
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

        bootstrap_path = Path(__file__).resolve().parent.parent / "zephyrex.bootstrap.py"
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


def _cmd_migrate(args: argparse.Namespace) -> int:
    import os

    os.environ["RUN_MIGRATIONS"] = "false"

    from zephyrex import instance, set_extensions_root

    if args.extensions_path:
        set_extensions_root(args.extensions_path)

    kwargs = {}
    if args.extensions:
        kwargs["extensions"] = args.extensions

    app = instance(**kwargs)

    from zephyrex.database.migrations.Migration import MigrationManager

    registry = app.state.model_registry
    db_mgr = registry.database_manager
    db_info = {
        "type": db_mgr.DATABASE_TYPE,
        "name": db_mgr.DATABASE_NAME,
        "url": db_mgr.DATABASE_URI,
        "file_path": getattr(db_mgr, "_database_file_path", None),
    }
    mgr = MigrationManager(custom_db_info=db_info, model_registry=registry)
    ext_csv = registry.extension_registry.csv if registry.extension_registry else ""
    success = mgr.run_all_migrations(
        "upgrade", "head",
        extensions=ext_csv.split(",") if ext_csv else [],
    )
    if success:
        print("Migrations applied successfully.")
        return 0
    print("Migration failed.", file=sys.stderr)
    return 1


_DISPATCH = {
    "run": _cmd_run,
    "migrate": _cmd_migrate,
    "bootstrap": _cmd_bootstrap,
    "version": _cmd_version,
}


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Canonical CLI entrypoint. Returns a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_usage(sys.stderr)
        print("zephyrex-server: error: a subcommand is required", file=sys.stderr)
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

            logger.exception(f"zephyrex-server: command '{args.command}' failed: {exc}")
        except Exception:
            print(f"zephyrex-server: command '{args.command}' failed: {exc}", file=sys.stderr)
        return 1
