# SPDX-License-Identifier: AGPL-3.0-or-later
"""The curated admin commands: ``user``, ``team``, ``role``, ``extension``,
``cache`` and ``seed``. Each works through the managers like ``record``,
with the operations an operator needs named (passwords come from stdin,
never argv)."""

from __future__ import annotations

import argparse
import tomllib
from typing import Any, Dict, List, Optional

from zephyrex.cli.Context import (
    CLIError,
    Command,
    CommandContext,
    CommandGroup,
    ExitCode,
    clear_shared_caches,
    cli_model_name,
)


def register(
    subparsers: "argparse._SubParsersAction[Any]",
    options: argparse.ArgumentParser,
) -> None:
    user = CommandGroup("user", subparsers, options, help="Create and manage users.")
    p = user.command("get", user_get, help="Show a user (by id or email).")
    p.add_argument("user")
    p = user.command("create", user_create, help="Create a user.", mutates=True)
    p.add_argument("--email", required=True)
    p.add_argument("--username", default=None, help="Default: the email's local part.")
    p.add_argument("--first-name", default=None)
    p.add_argument("--last-name", default=None)
    p.add_argument("--display-name", default=None)
    p.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from stdin's first line.",
    )
    p = user.command(
        "set-password",
        user_set_password,
        help="Set a user's password from stdin's first line.",
        mutates=True,
    )
    p.add_argument("user")
    p = user.command(
        "disable",
        user_disable,
        help="Deactivate a user and revoke their sessions.",
        mutates=True,
    )
    p.add_argument("user")
    p = user.command("enable", user_enable, help="Reactivate a user.", mutates=True)
    p.add_argument("user")

    team = CommandGroup(
        "team", subparsers, options, help="Create teams and manage members."
    )
    p = team.command("create", team_create, help="Create a team.", mutates=True)
    p.add_argument("--name", required=True)
    p.add_argument("--description", default=None)
    p.add_argument("--parent", default=None, help="Parent team id.")
    p = team.command("members", team_members, help="List a team's memberships.")
    p.add_argument("team")
    p = team.command(
        "add-member",
        role_grant,
        help="Add a user to a team with a role (or change their role).",
        mutates=True,
    )
    _membership_arguments(p, with_role=True)
    p = team.command(
        "remove-member",
        role_revoke,
        help="Remove a user from a team.",
        mutates=True,
        destructive=True,
    )
    _membership_arguments(p, with_role=False)

    role = CommandGroup("role", subparsers, options, help="List and grant roles.")
    p = role.command("list", role_list, help="List roles (system roles, or a team's).")
    p.add_argument("--team", default=None)
    p = role.command(
        "grant",
        role_grant,
        help="Give a user a role in a team (adds the membership if needed).",
        mutates=True,
    )
    _membership_arguments(p, with_role=True)
    p = role.command(
        "revoke",
        role_revoke,
        help="Take a user's membership (and so role) in a team away.",
        mutates=True,
        destructive=True,
    )
    _membership_arguments(p, with_role=False)

    extension = CommandGroup(
        "extension", subparsers, options, help="Inspect extensions."
    )
    extension.command("list", extension_list, help="List the loaded extensions.")
    p = extension.command("info", extension_info, help="Show an extension's manifest.")
    p.add_argument("name")

    cache = CommandGroup("cache", subparsers, options, help="Shared (Valkey) caches.")
    p = cache.command(
        "clear",
        cache_clear,
        help="Clear the servers' shared entity and response caches.",
        destructive=True,
    )
    p.add_argument(
        "--model",
        action="append",
        default=[],
        help="Clear only these models' tables (repeatable). Default: every model.",
    )

    seed = subparsers.add_parser(
        "seed", help="Insert every model's missing seed rows.", parents=[options]
    )
    seed.set_defaults(command=Command("seed", seed_run, True, False, None))

    # Bare ``zephyrex migrate`` upgrades, as it always has (deploy scripts).
    up = Command("migrate up", migrate_up, True, False, None)
    migrate = subparsers.add_parser(
        "migrate",
        help="Upgrade (default), show or roll back database migrations.",
        parents=[options],
    )
    migrate.set_defaults(command=up)
    actions = migrate.add_subparsers(dest="action", metavar="action")
    actions.add_parser(
        "up", help="Upgrade core and extensions to head, then seed.", parents=[options]
    ).set_defaults(command=up)
    actions.add_parser(
        "status",
        help="Each component's heads and current revisions.",
        parents=[options],
    ).set_defaults(
        command=Command("migrate status", migrate_status, False, False, None)
    )
    down = actions.add_parser(
        "down", help="Downgrade core and extensions to a target.", parents=[options]
    )
    down.add_argument(
        "--to", required=True, help="Alembic target for every component (e.g. -1)."
    )
    down.set_defaults(command=Command("migrate down", migrate_down, True, True, None))
    revision = actions.add_parser(
        "revision",
        help="Write an extension's next revision, autogenerated from its models.",
        parents=[options],
    )
    revision.add_argument("extension")
    revision.add_argument("-m", "--message", required=True)
    revision.add_argument(
        "--empty", action="store_true", help="An empty revision, not autogenerated."
    )
    revision.set_defaults(
        command=Command("migrate revision", migrate_revision, False, False, None)
    )


def _membership_arguments(parser: argparse.ArgumentParser, *, with_role: bool) -> None:
    parser.add_argument("team", help="Team id.")
    parser.add_argument("user", help="User id or email.")
    if with_role:
        parser.add_argument("role", help="Role id or name (e.g. user, admin).")


def _live(manager: Any, **filters: Any) -> List[Any]:
    """Rows matching ``filters`` that aren't soft-deleted."""
    if hasattr(manager.DB, "deleted_at"):
        filters.setdefault("deleted_at", None)
    return list(manager.list(**filters))


def find_user(ctx: CommandContext, user: str) -> Any:
    """One live user by id or email."""
    from zephyrex.cli.Context import _is_uuid

    users = ctx.manager("user")
    found = _live(users, id=user) if _is_uuid(user) else _live(users, email=user)
    if len(found) != 1:
        raise CLIError(ExitCode.NOT_FOUND, f"No single live user {user!r}")
    return found[0]


def find_role(ctx: CommandContext, role: str, team_id: Optional[str]) -> Any:
    """A role by id, else by name: the team's own first, then a system role."""
    from zephyrex.cli.Context import _is_uuid

    roles = ctx.manager("role")
    if _is_uuid(role):
        found = _live(roles, id=role)
    else:
        found = _live(roles, name=role, team_id=team_id) if team_id else []
        found = found or _live(roles, name=role, team_id=None)
    if len(found) != 1:
        raise CLIError(ExitCode.NOT_FOUND, f"No single role {role!r}")
    return found[0]


def _user_payload(ctx: CommandContext, user: Any) -> Any:
    return ctx.manager("user")._visible_payload(user)


def user_get(ctx: CommandContext, args: argparse.Namespace) -> Any:
    return _user_payload(ctx, find_user(ctx, args.user))


def _stdin_password(ctx: CommandContext) -> str:
    password = ctx.stdin.readline().rstrip("\r\n")
    if not password:
        raise CLIError(ExitCode.USAGE, "No password on stdin")
    return password


def _set_password(ctx: CommandContext, user_id: str, password: str) -> None:
    from zephyrex.logic.BLL_Auth import UserCredentialManager

    UserCredentialManager(model_registry=ctx.registry, requester_id=user_id).create(
        user_id=user_id, password=password
    )


def user_create(ctx: CommandContext, args: argparse.Namespace) -> Any:
    fields = {
        "email": args.email,
        "username": args.username or args.email.split("@")[0],
        "first_name": args.first_name,
        "last_name": args.last_name,
        "display_name": args.display_name,
    }
    if args.password_stdin:
        fields["password"] = _stdin_password(ctx)
    user = ctx.manager("user").create(
        **{k: v for k, v in fields.items() if v is not None}
    )
    ctx.resource = ("user", str(user.id))
    return {"user": _user_payload(ctx, user), "password_set": args.password_stdin}


def user_set_password(ctx: CommandContext, args: argparse.Namespace) -> Any:
    user = find_user(ctx, args.user)
    ctx.resource = ("user", str(user.id))
    _set_password(ctx, str(user.id), _stdin_password(ctx))
    return {"user": str(user.id), "password_set": True}


def _set_active(ctx: CommandContext, user_ref: str, active: bool) -> Any:
    user = find_user(ctx, user_ref)
    ctx.resource = ("user", str(user.id))
    users = ctx.manager("user")
    updated = users.update_trusted(str(user.id), active=active)
    return _user_payload(ctx, updated)


def user_disable(ctx: CommandContext, args: argparse.Namespace) -> Any:
    payload = _set_active(ctx, args.user, False)
    ctx.manager("user").revoke_all_user_sessions(str(payload["id"]))  # type: ignore[attr-defined]
    return {"user": payload, "sessions_revoked": True}


def user_enable(ctx: CommandContext, args: argparse.Namespace) -> Any:
    return {"user": _set_active(ctx, args.user, True)}


def team_create(ctx: CommandContext, args: argparse.Namespace) -> Any:
    fields = {
        "name": args.name,
        "description": args.description,
        "parent_id": args.parent,
    }
    teams = ctx.manager("team")
    team = teams.create(**{k: v for k, v in fields.items() if v is not None})
    ctx.resource = ("team", str(team.id))
    return teams._visible_payload(team)


def team_members(ctx: CommandContext, args: argparse.Namespace) -> Any:
    memberships = ctx.manager("user_team")
    return [
        memberships._visible_payload(m) for m in _live(memberships, team_id=args.team)
    ]


def _membership(ctx: CommandContext, team_id: str, user_id: str) -> Optional[Any]:
    found = _live(ctx.manager("user_team"), team_id=team_id, user_id=user_id)
    return found[0] if found else None


def role_grant(ctx: CommandContext, args: argparse.Namespace) -> Any:
    user = find_user(ctx, args.user)
    role = find_role(ctx, args.role, args.team)
    memberships = ctx.manager("user_team")
    existing = _membership(ctx, args.team, str(user.id))
    if existing is None:
        membership = memberships.create(
            user_id=str(user.id), team_id=args.team, role_id=str(role.id)
        )
    else:
        membership = memberships.update(str(existing.id), role_id=str(role.id))
    ctx.resource = ("user_team", str(membership.id))
    return {
        "membership": memberships._visible_payload(membership),
        "created": existing is None,
    }


def role_revoke(ctx: CommandContext, args: argparse.Namespace) -> Any:
    user = find_user(ctx, args.user)
    existing = _membership(ctx, args.team, str(user.id))
    if existing is None:
        raise CLIError(
            ExitCode.NOT_FOUND, f"{args.user!r} is not a member of team {args.team}"
        )
    ctx.resource = ("user_team", str(existing.id))
    ctx.manager("user_team").delete(str(existing.id))
    return {"removed": str(existing.id)}


def role_list(ctx: CommandContext, args: argparse.Namespace) -> Any:
    roles = ctx.manager("role")
    return [roles._visible_payload(r) for r in _live(roles, team_id=args.team)]


def extension_list(ctx: CommandContext, args: argparse.Namespace) -> Any:
    registry = ctx.registry.extension_registry
    names = [n for n in (registry.csv if registry else "").split(",") if n]
    return [_manifest(name) | {"name": name} for name in names]


def extension_info(ctx: CommandContext, args: argparse.Namespace) -> Any:
    registry = ctx.registry.extension_registry
    loaded = args.name in (registry.csv if registry else "").split(",")
    return _manifest(args.name) | {"name": args.name, "loaded": loaded}


def _manifest(name: str) -> Dict[str, Any]:
    from zephyrex.lib.Paths import find_extension_dir

    path = find_extension_dir(name) / "manifest.toml"
    if not path.is_file():
        raise CLIError(ExitCode.NOT_FOUND, f"No extension {name!r} (no {path})")
    with path.open("rb") as handle:
        return tomllib.load(handle)


def cache_clear(ctx: CommandContext, args: argparse.Namespace) -> Any:
    models = ctx.models()
    chosen = [cli_model_name(m) for m in args.model] or sorted(models)
    unknown = [m for m in chosen if m not in models]
    if unknown:
        raise CLIError(ExitCode.NOT_FOUND, f"No loaded model {unknown[0]!r}")
    tables: Dict[str, str] = {}
    for name in chosen:
        tables[ctx.root_manager(name).DB.__tablename__] = name
    cleared = clear_shared_caches(tables)
    if not cleared:
        ctx.notes.append("VALKEY_URI is not set: there is no shared cache to clear")
    return {"cleared": cleared}


def seed_run(ctx: CommandContext, args: argparse.Namespace) -> Any:
    ctx.resource = ("seed", None)
    ctx.registry._seed()
    return {"seeded": True}


def _migrations(ctx: CommandContext) -> Any:
    """A MigrationManager on the app's own database and registry."""
    from zephyrex.database.migrations.Migration import MigrationManager

    db_mgr = ctx.registry.database_manager
    db_info = {
        "type": db_mgr.DATABASE_TYPE,
        "name": db_mgr.DATABASE_NAME,
        "url": db_mgr.DATABASE_URI,
        "file_path": getattr(db_mgr, "_database_file_path", None),
    }
    return MigrationManager(
        custom_db_info=db_info,
        model_registry=ctx.registry,
        extensions_path=ctx.args.extensions_path,
    )


def _loaded_extensions(ctx: CommandContext) -> List[str]:
    registry = ctx.registry.extension_registry
    return [n for n in (registry.csv if registry else "").split(",") if n]


def _refuse_dry_run(ctx: CommandContext) -> None:
    if ctx.args.dry_run:
        raise CLIError(
            ExitCode.DRY_RUN_REFUSED,
            "Migrations run on alembic's own connection and can't be rolled back;"
            " see `migrate status` instead",
        )


def migrate_status(ctx: CommandContext, args: argparse.Namespace) -> Any:
    return _migrations(ctx).status(_loaded_extensions(ctx))


def migrate_up(ctx: CommandContext, args: argparse.Namespace) -> Any:
    _refuse_dry_run(ctx)
    ctx.resource = ("migrations", "head")
    if not _migrations(ctx).run_all_migrations(
        "upgrade", "head", extensions=_loaded_extensions(ctx)
    ):
        raise CLIError(ExitCode.ERROR, "Migration failed; see the log on stderr")
    ctx.registry._seed()
    return {"migrated": True, "seeded": True}


def migrate_down(ctx: CommandContext, args: argparse.Namespace) -> Any:
    _refuse_dry_run(ctx)
    ctx.resource = ("migrations", args.to)
    if not _migrations(ctx).run_all_migrations(
        "downgrade", args.to, extensions=_loaded_extensions(ctx)
    ):
        raise CLIError(ExitCode.ERROR, "Downgrade failed; see the log on stderr")
    return {"downgraded_to": args.to}


def migrate_revision(ctx: CommandContext, args: argparse.Namespace) -> Any:
    """A new revision file in the extension's ``migrations/versions``: the
    first one starts its own branch (``ext_<name>``). Autogenerate diffs the
    models against this database, so for a complete 001 point it at an
    empty one."""
    _refuse_dry_run(ctx)
    migrations = _migrations(ctx)
    versions = migrations._resolve_extension_versions_dir(args.extension)
    before = set(versions.glob("*.py")) if versions.exists() else set()
    if not migrations.create_extension_migration(
        args.extension, args.message, auto=not args.empty
    ):
        raise CLIError(
            ExitCode.ERROR,
            f"No revision written for {args.extension!r}; see the log on stderr",
        )
    return {
        "extension": args.extension,
        "files": sorted(str(p) for p in set(versions.glob("*.py")) - before),
    }
