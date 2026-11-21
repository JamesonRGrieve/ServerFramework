# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared machinery of the admin CLI: the exit-code table, command groups,
the context a handler runs in, dry runs, audit attribution and output."""

from __future__ import annotations

import argparse
import getpass
import json
import socket
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import IntEnum
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Iterator,
    List,
    Optional,
    TextIO,
    Tuple,
)
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError

from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger

if TYPE_CHECKING:
    from zephyrex.cli.Remote import RemoteClient
    from zephyrex.database.DatabaseManager import RollbackScope
    from zephyrex.logic.AbstractLogicManager import AbstractBLLManager

REMOTE_KEY_ENV = "ZEPHYREX_ROOT_API_KEY"
REDACTED_OPTIONS = frozenset({"--data"})


class ExitCode(IntEnum):
    OK = 0
    ERROR = 1
    USAGE = 2
    NOT_FOUND = 3
    CONFLICT = 4
    PERMISSION = 5
    VALIDATION = 6
    DRY_RUN_REFUSED = 7


STATUS_EXIT_CODES: Dict[int, ExitCode] = {
    400: ExitCode.VALIDATION,
    401: ExitCode.PERMISSION,
    403: ExitCode.PERMISSION,
    404: ExitCode.NOT_FOUND,
    409: ExitCode.CONFLICT,
    412: ExitCode.CONFLICT,
    422: ExitCode.VALIDATION,
    428: ExitCode.CONFLICT,
}


def exit_code_for_status(status: int) -> ExitCode:
    return STATUS_EXIT_CODES.get(status, ExitCode.ERROR)


class CLIError(Exception):
    """A command failure with its exit code and a JSON-ready detail."""

    def __init__(self, code: ExitCode, message: str, detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


Handler = Callable[["CommandContext", argparse.Namespace], Any]
RemoteHandler = Callable[["RemoteClient", argparse.Namespace], Any]


@dataclass(frozen=True)
class Command:
    """A runnable subcommand. ``mutates`` commands get dry runs and audit
    rows; ``destructive`` ones need ``--yes`` (or a typed yes at a terminal);
    ``remote`` is the handler used under ``--remote``, None when the command
    only runs in-process."""

    name: str
    handler: Handler
    mutates: bool
    destructive: bool
    remote: Optional[RemoteHandler]


class Parser(argparse.ArgumentParser):
    """An ArgumentParser that takes only whole option names: ``--extension``
    must never be read as ``--extensions`` (scripts and agents type these).
    Subcommand parsers inherit the class through ``add_subparsers``."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)


def global_options() -> Parser:
    """Options every admin command accepts, before or after its name."""
    parent = Parser(add_help=False)
    parent.add_argument("--extensions", default=None, help="CSV of extensions to load.")
    parent.add_argument(
        "--extensions-path",
        default=None,
        help="Filesystem path to discover extensions.",
    )
    parent.add_argument(
        "--format", choices=("json", "table"), default="json", help="Output format."
    )
    parent.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and show the change, then roll back the transaction.",
    )
    parent.add_argument(
        "--as",
        dest="as_user",
        default=None,
        help="Act as this user (id or email) instead of ROOT.",
    )
    parent.add_argument(
        "--reason", default=None, help="Why: recorded on the audit row."
    )
    parent.add_argument(
        "--yes",
        action="store_true",
        help="Confirm a destructive command without a prompt.",
    )
    parent.add_argument(
        "--remote",
        default=None,
        metavar="URL",
        help=f"Run over HTTP against URL with the root key from {REMOTE_KEY_ENV}.",
    )
    return parent


class CommandGroup:
    """One ``zephyrex <group>`` and its subcommands. Core command modules and
    extensions' ``CLI_<Name>.py`` (``register(group)``) both build on it."""

    def __init__(
        self,
        name: str,
        subparsers: "argparse._SubParsersAction[Any]",
        options: argparse.ArgumentParser,
        help: str,
    ) -> None:
        self.name = name
        self.parser = subparsers.add_parser(name, help=help)
        self._actions = self.parser.add_subparsers(
            dest="action", metavar="action", required=True
        )
        self._options = options

    def command(
        self,
        name: str,
        handler: Handler,
        *,
        help: str,
        mutates: bool = False,
        destructive: bool = False,
        remote: Optional[RemoteHandler] = None,
    ) -> argparse.ArgumentParser:
        """Add ``zephyrex <group> <name>``; returns its parser for arguments."""
        parser: argparse.ArgumentParser = self._actions.add_parser(
            name, help=help, parents=[self._options]
        )
        parser.set_defaults(
            command=Command(
                f"{self.name} {name}", handler, mutates, destructive, remote
            )
        )
        return parser


@dataclass
class CommandContext:
    """What an in-process handler works with: the booted app, the parsed
    arguments and the requester every manager is built for. A handler that
    changes one record names it in ``resource`` for the audit row."""

    app: Any
    args: argparse.Namespace
    requester_id: str
    stdin: TextIO = sys.stdin
    resource: Tuple[str, Optional[str]] = ("", None)
    notes: List[str] = field(default_factory=list)
    touched: Dict[str, str] = field(default_factory=dict)

    @property
    def registry(self) -> Any:
        return self.app.state.model_registry

    def models(self) -> Dict[str, Tuple[type, type]]:
        """Every loaded model with a manager, by CLI name (``user_team``)."""
        found: Dict[str, Tuple[type, type]] = {}
        for relationship in self.registry.model_relationships:
            model_class, manager_class = relationship[0], relationship[-1]
            if manager_class is None:
                continue
            found[cli_model_name(model_class.__name__)] = (model_class, manager_class)
        return found

    def manager_class(self, model: str) -> type:
        models = self.models()
        key = cli_model_name(model)
        if key not in models:
            raise CLIError(
                ExitCode.NOT_FOUND,
                f"No loaded model {model!r}",
                {"models": sorted(models)},
            )
        return models[key][1]

    def manager(self, model: str) -> "AbstractBLLManager":
        """The model's manager as the requester. Its table is noted in
        ``touched`` (table -> wire name) so a mutating command clears the
        running servers' shared caches for it afterwards."""
        manager = self.manager_class(model)(
            model_registry=self.registry, requester_id=self.requester_id
        )
        self.touched[manager.DB.__tablename__] = cli_model_name(model)
        return manager  # type: ignore[no-any-return]

    def root_manager(self, model: str) -> "AbstractBLLManager":
        """The model's manager as ROOT, for the CLI's own lookups."""
        return self.manager_class(model)(model_registry=self.registry, requester_id=env("ROOT_ID"))  # type: ignore[no-any-return]


def cli_model_name(name: str) -> str:
    """A model's wire name, as REST envelopes and paths spell it:
    ``UserTeamModel``, ``UserTeam`` and ``user_team`` all name ``user_team``,
    ``APIKeyModel`` names ``api_key``."""
    from zephyrex.pydantic2.util import wire_resource_name

    if "_" in name or name.islower():
        return name.lower()
    return wire_resource_name(name, "Model")


def resolve_requester(app: Any, as_user: Optional[str]) -> str:
    """ROOT unless ``--as`` names a user (by id or email; ``root`` is ROOT)."""
    if as_user is None or as_user.lower() == "root":
        return env("ROOT_ID")
    from zephyrex.logic.BLL_Auth import UserManager

    users = UserManager(
        model_registry=app.state.model_registry, requester_id=env("ROOT_ID")
    )
    found = users.list(id=as_user) if _is_uuid(as_user) else users.list(email=as_user)
    matches = [user for user in found if getattr(user, "deleted_at", None) is None]
    if len(matches) != 1:
        raise CLIError(
            ExitCode.NOT_FOUND, f"--as {as_user!r} names no single live user"
        )
    return str(matches[0].id)


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


@contextmanager
def dry_run_scope(app: Any) -> Iterator["RollbackScope"]:
    """Roll back every write, skip AFTER hooks, and keep uncommitted rows
    out of the shared entity cache for the length of a dry run."""
    from zephyrex.logic.AbstractLogicManager import get_entity_cache, set_entity_cache

    cache = get_entity_cache()
    set_entity_cache(None)
    try:
        with app.state.model_registry.database_manager.rollback_scope() as scope:
            yield scope
    finally:
        set_entity_cache(cache)


def clear_shared_caches(tables: Dict[str, str]) -> List[str]:
    """Clear the running servers' shared (Valkey) entity and response caches
    for each table a mutation touched (table -> wire name), so they don't
    serve the old record until its TTL. Managers in this process write
    through no shared cache: the servers wire it in their lifespan, which
    the CLI doesn't run. Returns the tables cleared; none without
    ``VALKEY_URI``. A whole table is cleared because a changed index field
    (a user's email) leaves index keys whose old values only the server saw."""
    uri = env("VALKEY_URI").strip()
    if not uri or not tables:
        return []
    import asyncio

    from redis import asyncio as redis_asyncio

    from zephyrex.extensions.database_memory.ValkeyEntityCache import (
        ValkeyEntityCache,
    )
    from zephyrex.extensions.database_memory.ValkeyResponseCache import (
        ValkeyResponseCache,
    )

    async def clear() -> None:
        client = redis_asyncio.from_url(uri, decode_responses=False)
        try:
            entities, responses = ValkeyEntityCache(client), ValkeyResponseCache(client)
            for table, wire_name in tables.items():
                await entities.invalidate_table(table)
                await responses.invalidate_prefix(f"/v1/{wire_name}")
        finally:
            await client.aclose()

    asyncio.run(clear())
    return sorted(tables)


def redacted_argv(argv: List[str]) -> List[str]:
    """argv for the audit row with each ``--data`` JSON reduced to its keys."""
    out: List[str] = []
    redact_next = False
    for token in argv:
        if redact_next:
            out.append(_json_keys(token))
            redact_next = False
            continue
        option, eq, value = token.partition("=")
        if option in REDACTED_OPTIONS:
            if eq:
                out.append(f"{option}={_json_keys(value)}")
            else:
                out.append(token)
                redact_next = True
            continue
        out.append(token)
    return out


def _json_keys(value: str) -> str:
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return "<redacted>"
    return (
        f"<keys:{','.join(sorted(parsed))}>"
        if isinstance(parsed, dict)
        else "<redacted>"
    )


def write_audit(
    ctx: CommandContext, command: Command, argv: List[str], error: Optional[str]
) -> bool:
    """Record a mutating command on the audit log, when meta_logging is
    loaded. Returns whether a row was written."""
    if "audit_log" not in ctx.models():
        return False
    from zephyrex.extensions.meta_logging.BLL_Meta_Logging import AuditLogModel

    resource_type, resource_id = ctx.resource
    ctx.root_manager("audit_log").log_audit_event(  # type: ignore[attr-defined]
        AuditLogModel.Create(
            user_id=ctx.requester_id,
            action=f"cli:{command.name}",
            resource_type=resource_type or command.name.split(" ")[0],
            resource_id=resource_id,
            success=error is None,
            error_message=error,
            additional_data={
                "via": "cli",
                "as": ctx.args.as_user,
                "reason": ctx.args.reason,
                "host": socket.gethostname(),
                "os_user": getpass.getuser(),
                "argv": redacted_argv(argv),
            },
        )
    )
    return True


def confirm(
    command: Command, args: argparse.Namespace, stdin: TextIO, stderr: TextIO
) -> None:
    """A destructive command runs only with ``--yes``, a typed ``yes`` at a
    terminal, or as a dry run."""
    if not command.destructive or args.yes or args.dry_run:
        return
    if not stdin.isatty():
        raise CLIError(
            ExitCode.USAGE,
            f"'{command.name}' is destructive: pass --yes when not at a terminal",
        )
    stderr.write(f"'{command.name}' is destructive. Type 'yes' to continue: ")
    stderr.flush()
    if stdin.readline().strip().lower() != "yes":
        raise CLIError(ExitCode.USAGE, "Not confirmed")


def execute(
    command: Command,
    args: argparse.Namespace,
    argv: List[str],
    *,
    app: Any = None,
    client: Optional["RemoteClient"] = None,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """Run one command in-process (``app``) or over HTTP (``client``), print
    its envelope and return its exit code."""
    envelope: Dict[str, Any] = {
        "ok": True,
        "command": command.name,
        "dry_run": bool(args.dry_run and command.mutates),
    }
    code = ExitCode.OK
    try:
        confirm(command, args, stdin, stderr)
        if getattr(args, "data", None) == "-":
            args.data = stdin.read()
        if client is not None:
            if args.dry_run and command.mutates:
                raise CLIError(
                    ExitCode.DRY_RUN_REFUSED,
                    "A dry run needs the in-process database; drop --remote",
                )
            if args.as_user:
                raise CLIError(
                    ExitCode.USAGE,
                    "--as is in-process only; --remote acts as the root key",
                )
            if command.remote is None:
                raise CLIError(
                    ExitCode.USAGE,
                    f"'{command.name}' runs only in-process, not over --remote",
                )
            envelope["result"] = command.remote(client, args)
        else:
            envelope.update(_run_in_process(command, args, argv, app, stdin))
    except CLIError as err:
        code = err.code
        envelope.update(ok=False, error={"message": err.message, "detail": err.detail})
    except HTTPException as err:
        code = exit_code_for_status(err.status_code)
        envelope.update(
            ok=False, error={"status": err.status_code, "detail": err.detail}
        )
    except ValidationError as err:
        code = ExitCode.VALIDATION
        envelope.update(
            ok=False, error={"message": "Validation error", "detail": err.errors()}
        )
    envelope["exit_code"] = int(code)
    logger.info(
        "cli %s",
        json.dumps(
            {
                "command": command.name,
                "exit_code": int(code),
                "dry_run": envelope["dry_run"],
                "remote": client is not None,
            },
            default=str,
        ),
    )
    render(envelope, args.format, stdout)
    return int(code)


def _run_in_process(
    command: Command,
    args: argparse.Namespace,
    argv: List[str],
    app: Any,
    stdin: TextIO,
) -> Dict[str, Any]:
    ctx = CommandContext(
        app=app,
        args=args,
        requester_id=resolve_requester(app, args.as_user),
        stdin=stdin,
    )
    if not command.mutates:
        return {"result": command.handler(ctx, args)}
    if args.dry_run:
        with dry_run_scope(app) as scope:
            result = command.handler(ctx, args)
        return {
            "result": result,
            "skipped_hooks": scope.skipped_hooks,
            "notes": ctx.notes,
        }
    try:
        result = command.handler(ctx, args)
    except Exception as err:
        write_audit(ctx, command, argv, error=_error_text(err))
        raise
    return {
        "result": result,
        "audited": write_audit(ctx, command, argv, error=None),
        "caches_cleared": clear_shared_caches(ctx.touched),
        "notes": ctx.notes,
    }


def _error_text(err: Exception) -> str:
    if isinstance(err, HTTPException):
        return f"{err.status_code}: {err.detail}"
    return f"{type(err).__name__}: {err}"


def render(envelope: Dict[str, Any], fmt: str, stdout: TextIO) -> None:
    if fmt == "json":
        stdout.write(json.dumps(envelope, default=str, indent=2) + "\n")
        return
    if not envelope["ok"]:
        stdout.write(
            f"error ({envelope['exit_code']}): {json.dumps(envelope['error'], default=str)}\n"
        )
        return
    stdout.write(_table(envelope.get("result")) + "\n")
    for hook in envelope.get("skipped_hooks", []):
        stdout.write(f"dry run skipped: {hook}\n")


def _table(result: Any) -> str:
    if (
        isinstance(result, list)
        and result
        and all(isinstance(row, dict) for row in result)
    ):
        columns = list(dict.fromkeys(key for row in result for key in row))
        cells = [[_cell(row.get(c)) for c in columns] for row in result]
        widths = [
            max(len(c), *(len(r[i]) for r in cells)) for i, c in enumerate(columns)
        ]
        lines = ["  ".join(c.ljust(w) for c, w in zip(columns, widths))]
        lines += ["  ".join(v.ljust(w) for v, w in zip(row, widths)) for row in cells]
        return "\n".join(lines)
    if isinstance(result, dict):
        width = max((len(str(k)) for k in result), default=0)
        return "\n".join(
            f"{str(k).ljust(width)}  {_cell(v)}" for k, v in result.items()
        )
    return _cell(result)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return str(value)
