# SPDX-License-Identifier: AGPL-3.0-or-later
"""``zephyrex models`` and ``zephyrex record …``: generic CRUD on every
loaded model, through its manager, as the requester."""

from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List, Optional

from zephyrex.cli.Context import (
    CLIError,
    CommandContext,
    CommandGroup,
    ExitCode,
    cli_model_name,
)
from zephyrex.cli.Remote import RemoteClient
from zephyrex.lib.Preconditions import entity_etag, expect_versions


def register(
    subparsers: "argparse._SubParsersAction[Any]",
    options: argparse.ArgumentParser,
) -> None:
    group = CommandGroup(
        "record",
        subparsers,
        options,
        help="Create, read, update and delete any model's records.",
    )

    listing = group.command(
        "list", list_records, help="List a model's records.", remote=remote_list
    )
    listing.add_argument("model")
    listing.add_argument(
        "--filter",
        action="append",
        default=[],
        metavar="FIELD=VALUE",
        help="Equality filter (repeatable).",
    )
    listing.add_argument(
        "--search",
        default=None,
        metavar="JSON",
        help='Search operators, e.g. \'{"name": {"inc": "ann"}}\'.',
    )
    listing.add_argument("--fields", default=None, help="CSV of fields to return.")
    listing.add_argument(
        "--include", default=None, help="CSV of relationships to include."
    )
    listing.add_argument("--sort-by", default=None)
    listing.add_argument("--sort-order", choices=("asc", "desc"), default="asc")
    listing.add_argument("--limit", type=int, default=None)
    listing.add_argument("--offset", type=int, default=None)
    listing.add_argument(
        "--include-deleted", action="store_true", help="Also list soft-deleted records."
    )

    getting = group.command(
        "get", get_record, help="Show one record and its ETag.", remote=remote_get
    )
    getting.add_argument("model")
    getting.add_argument("id")
    getting.add_argument("--fields", default=None)
    getting.add_argument("--include", default=None)

    creating = group.command(
        "create",
        create_record,
        help="Create a record.",
        mutates=True,
        remote=remote_create,
    )
    creating.add_argument("model")
    creating.add_argument(
        "--data",
        required=True,
        metavar="JSON",
        help="The record as JSON, or - to read it from stdin.",
    )

    updating = group.command(
        "update",
        update_record,
        help="Update a record.",
        mutates=True,
        remote=remote_update,
    )
    updating.add_argument("model")
    updating.add_argument("id")
    updating.add_argument(
        "--data",
        required=True,
        metavar="JSON",
        help="The changed fields as JSON, or - for stdin.",
    )
    updating.add_argument(
        "--if-match",
        default=None,
        metavar="ETAG",
        help="Refuse (exit 4) if the record has changed since.",
    )

    deleting = group.command(
        "delete",
        delete_record,
        help="Soft-delete a record.",
        mutates=True,
        destructive=True,
        remote=remote_delete,
    )
    deleting.add_argument("model")
    deleting.add_argument("id")
    deleting.add_argument(
        "--if-match",
        default=None,
        metavar="ETAG",
        help="Refuse (exit 4) if the record has changed since.",
    )

    for parser in (listing, getting, creating, updating, deleting):
        parser.add_argument(
            "--path",
            default=None,
            help="--remote: the REST path when it isn't /v1/<model>.",
        )
        parser.add_argument(
            "--key",
            default=None,
            help="--remote: the envelope key when it isn't <model>.",
        )


def list_models(ctx: CommandContext, args: argparse.Namespace) -> List[Dict[str, Any]]:
    return [
        {
            "model": name,
            "class": model.__name__,
            "manager": manager.__name__,
            "module": manager.__module__,
        }
        for name, (model, manager) in sorted(ctx.models().items())
    ]


def list_records(ctx: CommandContext, args: argparse.Namespace) -> List[Any]:
    manager = ctx.manager(args.model)
    filters: Dict[str, Any] = dict(_field_value(item) for item in args.filter)
    if args.search:
        filters.update(_json_object(args.search, "--search"))
    if not args.include_deleted and hasattr(manager.DB, "deleted_at"):
        filters.setdefault("deleted_at", None)
    rows = manager.list(
        include=args.include,
        fields=args.fields,
        sort_by=args.sort_by,
        sort_order=args.sort_order,
        limit=args.limit,
        offset=args.offset,
        **filters,
    )
    return [_only(manager._visible_payload(row), args.fields) for row in rows]


def get_record(ctx: CommandContext, args: argparse.Namespace) -> Dict[str, Any]:
    manager = ctx.manager(args.model)
    entity = manager.get(id=args.id, include=args.include, fields=args.fields)
    record = _only(manager._visible_payload(entity), args.fields)
    return {"record": record, "etag": entity_etag(entity)}


def create_record(ctx: CommandContext, args: argparse.Namespace) -> Dict[str, Any]:
    manager = ctx.manager(args.model)
    entity = manager.create(**read_data(args.data))
    ctx.resource = (args.model, str(entity.id))
    return {"record": manager._visible_payload(entity), "etag": entity_etag(entity)}


def update_record(ctx: CommandContext, args: argparse.Namespace) -> Dict[str, Any]:
    manager = ctx.manager(args.model)
    ctx.resource = (args.model, args.id)
    with expect_versions(manager, _expectation(args), may_require=False):
        entity = manager.update(args.id, **read_data(args.data))
    return {"record": manager._visible_payload(entity), "etag": entity_etag(entity)}


def delete_record(ctx: CommandContext, args: argparse.Namespace) -> Dict[str, Any]:
    manager = ctx.manager(args.model)
    ctx.resource = (args.model, args.id)
    with expect_versions(manager, _expectation(args), may_require=False):
        manager.delete(args.id)
    return {"deleted": args.id}


def _only(payload: Any, fields: Optional[str]) -> Any:
    """The payload trimmed to ``--fields`` (the manager loads only those
    columns but serialises every field)."""
    if not fields or not isinstance(payload, dict):
        return payload
    wanted = [name.strip() for name in fields.split(",") if name.strip()]
    return {name: payload[name] for name in wanted if name in payload}


def _expectation(args: argparse.Namespace) -> Dict[str, Optional[str]]:
    return {args.id: args.if_match} if args.if_match else {}


def read_data(value: str) -> Dict[str, Any]:
    """``--data``: a JSON object (``-`` was already replaced with stdin by
    ``execute``)."""
    return _json_object(value, "--data")


def _json_object(value: str, option: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(value)
    except ValueError as err:
        raise CLIError(ExitCode.USAGE, f"{option} is not valid JSON: {err}")
    if not isinstance(parsed, dict):
        raise CLIError(ExitCode.USAGE, f"{option} must be a JSON object")
    return parsed


def _field_value(item: str) -> tuple[str, Any]:
    """``FIELD=VALUE``; the value is read as JSON when it parses (numbers,
    true/false, null), else taken as a string."""
    name, eq, raw = item.partition("=")
    if not eq or not name:
        raise CLIError(ExitCode.USAGE, f"--filter {item!r} is not FIELD=VALUE")
    try:
        return name, json.loads(raw)
    except ValueError:
        return name, raw


def _remote_path(args: argparse.Namespace, *rest: str) -> str:
    base = args.path or f"/v1/{cli_model_name(args.model)}"
    return "/".join([base.rstrip("/"), *rest])


def _remote_key(args: argparse.Namespace) -> str:
    return str(args.key or cli_model_name(args.model))


def _remote_if_match(args: argparse.Namespace) -> Dict[str, str]:
    return {"If-Match": args.if_match} if args.if_match else {}


def remote_list(client: RemoteClient, args: argparse.Namespace) -> Any:
    """GET the list route; ``--search`` POSTs to its ``/search``. Equality
    ``--filter`` is in-process only (REST filters through ``--search``)."""
    if args.filter or args.include_deleted:
        raise CLIError(
            ExitCode.USAGE,
            "--filter and --include-deleted are in-process only; use --search",
        )
    params = {
        "include": args.include,
        "fields": args.fields,
        "sort_by": args.sort_by,
        "sort_order": args.sort_order,
        "limit": args.limit,
        "offset": args.offset,
    }
    if args.search:
        body = {_remote_key(args): _json_object(args.search, "--search")}
        return client.call(
            "POST", _remote_path(args, "search"), body=body, params=params
        )
    return client.call("GET", _remote_path(args), params=params)


def remote_get(client: RemoteClient, args: argparse.Namespace) -> Dict[str, Any]:
    params = {"include": args.include, "fields": args.fields}
    record = client.call("GET", _remote_path(args, args.id), params=params)
    return {"record": record, "etag": client.last_etag}


def remote_create(client: RemoteClient, args: argparse.Namespace) -> Dict[str, Any]:
    body = {_remote_key(args): read_data(args.data)}
    record = client.call("POST", _remote_path(args), body=body)
    return {"record": record, "etag": client.last_etag}


def remote_update(client: RemoteClient, args: argparse.Namespace) -> Dict[str, Any]:
    body = {_remote_key(args): read_data(args.data)}
    record = client.call(
        "PUT", _remote_path(args, args.id), body=body, headers=_remote_if_match(args)
    )
    return {"record": record, "etag": client.last_etag}


def remote_delete(client: RemoteClient, args: argparse.Namespace) -> Dict[str, Any]:
    client.call("DELETE", _remote_path(args, args.id), headers=_remote_if_match(args))
    return {"deleted": args.id}
