# SPDX-License-Identifier: AGPL-3.0-or-later
"""``zephyrex api-key create|list|revoke``: issue, list and revoke API keys
from the server's shell. The raw key is printed once, at issue."""

from __future__ import annotations

import argparse
from datetime import datetime
from typing import Any, Dict, List, Optional

from zephyrex.cli.Admin import find_role, find_user
from zephyrex.cli.Context import CommandContext, CommandGroup

GROUP = "api-key"


def register(group: CommandGroup) -> None:
    p = group.command(
        "create",
        create_key,
        help="Issue a key (for a user, else a team); prints the raw key once.",
        mutates=True,
    )
    p.add_argument("--name", required=True)
    p.add_argument(
        "--user", default=None, help="Owner (id or email); default: the requester."
    )
    p.add_argument("--team", default=None, help="Team id, for a team-scoped key.")
    p.add_argument("--role", default=None, help="Role id or name the key acts with.")
    p.add_argument("--expires-at", default=None, help="ISO-8601 expiry.")
    p = group.command("list", list_keys, help="List keys (never their values).")
    p.add_argument("--user", default=None, help="Only this owner's keys.")
    p.add_argument("--include-revoked", action="store_true")
    p = group.command(
        "revoke", revoke_key, help="Revoke a key.", mutates=True, destructive=True
    )
    p.add_argument("key_id")


def create_key(ctx: CommandContext, args: argparse.Namespace) -> Dict[str, Any]:
    keys = ctx.manager("api_key")
    user_id: Optional[str] = str(find_user(ctx, args.user).id) if args.user else None
    role_id = str(find_role(ctx, args.role, args.team).id) if args.role else None
    issued = keys.issue_key(  # type: ignore[attr-defined]
        name=args.name,
        user_id=user_id,
        team_id=args.team,
        role_id=role_id,
        expires_at=datetime.fromisoformat(args.expires_at) if args.expires_at else None,
    )
    ctx.resource = ("api_key", str(issued.id))
    ctx.notes.append("The key is shown only now; store it securely.")
    return dict(issued.model_dump(mode="json"))


def list_keys(ctx: CommandContext, args: argparse.Namespace) -> List[Any]:
    keys = ctx.manager("api_key")
    filters: Dict[str, Any] = {}
    if args.user:
        filters["user_id"] = str(find_user(ctx, args.user).id)
    if not args.include_revoked:
        filters["is_revoked"] = False
    return [keys._visible_payload(k) for k in keys.list(**filters)]


def revoke_key(ctx: CommandContext, args: argparse.Namespace) -> Any:
    ctx.resource = ("api_key", args.key_id)
    revoked = ctx.manager("api_key").revoke_key(args.key_id)  # type: ignore[attr-defined]
    return ctx.manager("api_key")._visible_payload(revoked)
