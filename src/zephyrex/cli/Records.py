# SPDX-License-Identifier: AGPL-3.0-or-later
"""``zephyrex models`` and ``zephyrex record …``: generic CRUD on every
loaded model, through its manager, as the requester."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

from fastapi import HTTPException

from zephyrex.cli.Context import (
    CLIError,
    CommandContext,
    CommandGroup,
    ExitCode,
    cli_model_name,
    exit_code_for_status,
)
from zephyrex.cli.Remote import RemoteClient
from zephyrex.lib.Environment import plural_noun
from zephyrex.lib.Preconditions import entity_etag, expect_versions
from zephyrex.pydantic2.fastapi.limits import MAX_BATCH_SIZE, MAX_PAGE_SIZE

# How many records one batch write names: the REST bound, so an in-process
# import and one over --remote write in the same steps.
BATCH_SIZE = MAX_BATCH_SIZE


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
    source = creating.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--data",
        metavar="JSON",
        help="The record as JSON, or - to read it from stdin.",
    )
    source.add_argument(
        "--file",
        metavar="PATH",
        help=(
            "Many records: a JSON array or JSON Lines (one object per line), "
            "or - for stdin. Each is attempted and reported."
        ),
    )
    creating.add_argument(
        "--stop-on-error",
        action="store_true",
        help="--file: stop at the first record that fails.",
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
        help="Soft-delete records: by id, or every one a filter matches.",
        mutates=True,
        destructive=True,
        remote=remote_delete,
    )
    deleting.add_argument("model")
    deleting.add_argument("id", nargs="*", help="The records' ids.")
    deleting.add_argument(
        "--filter",
        action="append",
        default=[],
        metavar="FIELD=VALUE",
        help="Delete every live record matching (repeatable; in-process).",
    )
    deleting.add_argument(
        "--search",
        default=None,
        metavar="JSON",
        help="Delete every live record a search body matches.",
    )
    deleting.add_argument(
        "--if-match",
        default=None,
        metavar="ETAG",
        help=(
            "Refuse (exit 4) if the record has changed since. With several "
            "ids over --remote, '*' deletes them at whatever version."
        ),
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
    creating.add_argument(
        "--plural-key",
        default=None,
        help="--remote --file: the plural envelope key when it isn't <model>s.",
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
    if args.file is not None:
        ctx.resource = (args.model, None)
        items = read_items(args)
        return _created_report(_create_each(manager, items, args), total=len(items))
    entity = manager.create(**read_data(args.data))
    ctx.resource = (args.model, str(entity.id))
    return {"record": manager._visible_payload(entity), "etag": entity_etag(entity)}


def _create_each(
    manager: Any, items: List[Dict[str, Any]], args: argparse.Namespace
) -> List[Dict[str, Any]]:
    """Each item created in turn, as a plural POST creates them: its outcome
    (``created`` with the id, or ``error`` with the status and envelope the
    single-record route would answer)."""
    from zephyrex.pydantic2.fastapi.routes import _batch_item_error

    outcomes: List[Dict[str, Any]] = []
    for index, item in enumerate(items):
        try:
            entity = manager.create(**item)
        except Exception as err:  # this item's outcome; the import goes on
            outcomes.append(
                {"index": index, "outcome": "error", **_batch_item_error(err)}
            )
            if args.stop_on_error:
                break
            continue
        outcomes.append({"index": index, "outcome": "created", "id": str(entity.id)})
    return outcomes


def _created_report(
    outcomes: List[Dict[str, Any]], total: Optional[int] = None
) -> Dict[str, Any]:
    """The import's report; raises when any item failed (the exit code is the
    first failure's), carrying the same report, so a script sees both."""
    errors = [o for o in outcomes if o["outcome"] == "error"]
    report = {
        "created": sum(1 for o in outcomes if o["outcome"] == "created"),
        "failed": len(errors),
        "skipped": (total if total is not None else len(outcomes)) - len(outcomes),
        "results": outcomes,
    }
    if errors:
        raise CLIError(
            exit_code_for_status(errors[0].get("status_code", 500)),
            f"{len(errors)} of {len(outcomes)} records failed",
            report,
        )
    return report


def read_items(args: argparse.Namespace) -> List[Dict[str, Any]]:
    """``--file``: a JSON array of objects, or JSON Lines (blank lines
    skipped); ``-`` was already read from stdin by ``execute``."""
    text = getattr(args, "file_text", None)
    if text is None:
        try:
            text = Path(args.file).read_text(encoding="utf-8")
        except OSError as err:
            raise CLIError(ExitCode.USAGE, f"--file {args.file}: {err}")
    stripped = text.strip()
    try:
        if stripped.startswith("["):
            items = json.loads(stripped)
        else:
            items = [json.loads(line) for line in stripped.splitlines() if line.strip()]
    except ValueError as err:
        raise CLIError(
            ExitCode.USAGE, f"--file is not a JSON array or JSON Lines: {err}"
        )
    bad = [n for n, item in enumerate(items) if not isinstance(item, dict)]
    if bad:
        raise CLIError(ExitCode.USAGE, f"--file items {bad[:10]} are not JSON objects")
    if not isinstance(items, list) or not items:
        raise CLIError(ExitCode.USAGE, "--file names no records")
    return [dict(item) for item in items]


def _chunks(values: Sequence[Any]) -> Iterator[Sequence[Any]]:
    for start in range(0, len(values), BATCH_SIZE):
        yield values[start : start + BATCH_SIZE]


def update_record(ctx: CommandContext, args: argparse.Namespace) -> Dict[str, Any]:
    manager = ctx.manager(args.model)
    ctx.resource = (args.model, args.id)
    with expect_versions(manager, _expectation(args), may_require=False):
        entity = manager.update(args.id, **read_data(args.data))
    return {"record": manager._visible_payload(entity), "etag": entity_etag(entity)}


def delete_record(ctx: CommandContext, args: argparse.Namespace) -> Dict[str, Any]:
    manager = ctx.manager(args.model)
    if _single_delete(args):
        [record_id] = args.id
        ctx.resource = (args.model, record_id)
        with expect_versions(
            manager,
            {record_id: args.if_match} if args.if_match else {},
            may_require=False,
        ):
            manager.delete(record_id)
        return {"deleted": record_id}
    _refuse_if_match_on_a_batch(args, remote=False)
    ids = list(dict.fromkeys([*args.id, *_matching_ids(manager, args)]))
    ctx.resource = (args.model, None)
    errors: List[Dict[str, Any]] = []
    for chunk in _chunks(ids):
        try:
            manager.batch_delete(ids=list(chunk))
        except HTTPException as err:
            if err.status_code != 207:
                raise
            detail: Dict[str, Any] = err.detail if isinstance(err.detail, dict) else {}
            errors.extend(detail.get("errors", []))
    return _deleted_report(ids, errors)


def _single_delete(args: argparse.Namespace) -> bool:
    """One id and no filter: the original single-record delete."""
    return len(args.id) == 1 and not args.filter and not args.search


def _refuse_if_match_on_a_batch(args: argparse.Namespace, *, remote: bool) -> None:
    if not args.id and not args.filter and not args.search:
        raise CLIError(ExitCode.USAGE, "Name the records: ids, --filter or --search")
    if args.if_match and not (remote and args.if_match == "*"):
        raise CLIError(
            ExitCode.USAGE,
            "--if-match names one record's version; with several, only '*' "
            "over --remote",
        )


def _matching_ids(manager: Any, args: argparse.Namespace) -> List[str]:
    """The ids of every live record ``--filter``/``--search`` matches, read
    in pages before anything is deleted."""
    if not args.filter and not args.search:
        return []
    filters: Dict[str, Any] = dict(_field_value(item) for item in args.filter)
    if args.search:
        filters.update(_json_object(args.search, "--search"))
    if hasattr(manager.DB, "deleted_at"):
        filters.setdefault("deleted_at", None)
    ids: List[str] = []
    while True:
        page = manager.list(
            fields="id", sort_by="id", limit=MAX_PAGE_SIZE, offset=len(ids), **filters
        )
        ids.extend(str(row.id) for row in page)
        if len(page) < MAX_PAGE_SIZE:
            return ids


def _deleted_report(ids: List[str], errors: List[Dict[str, Any]]) -> Dict[str, Any]:
    report = {
        "matched": len(ids),
        "deleted": len(ids) - len(errors),
        "failed": len(errors),
        "errors": errors,
    }
    if errors:
        raise CLIError(
            ExitCode.ERROR, f"{len(errors)} of {len(ids)} records not deleted", report
        )
    return report


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
    if args.file is not None:
        return _remote_create_each(client, args, read_items(args))
    body = {_remote_key(args): read_data(args.data)}
    record = client.call("POST", _remote_path(args), body=body)
    return {"record": record, "etag": client.last_etag}


def _remote_create_each(
    client: RemoteClient, args: argparse.Namespace, items: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Plural POSTs of at most ``BATCH_SIZE`` items; the server attempts and
    reports each, and its per-item results are merged at their file index."""
    key = args.plural_key or plural_noun(_remote_key(args))
    outcomes: List[Dict[str, Any]] = []
    for chunk_start in range(0, len(items), BATCH_SIZE):
        chunk = items[chunk_start : chunk_start + BATCH_SIZE]
        try:
            answer = client.call(
                "POST", _remote_path(args), body={key: chunk}, raw=True
            )
        except CLIError as err:
            # Every item of the chunk failed: the server answers as its first
            # item would alone, so each is reported with that answer.
            detail = err.detail if isinstance(err.detail, dict) else {}
            outcomes.extend(
                {
                    "index": chunk_start + n,
                    "outcome": "error",
                    "status_code": detail.get("status", 500),
                    "error": detail.get("detail"),
                }
                for n in range(len(chunk))
            )
        else:
            if client.last_status == 207 and isinstance(answer, dict):
                for result in answer.get("results", []):
                    outcomes.append({**result, "index": chunk_start + result["index"]})
            else:
                records = answer.get(key, []) if isinstance(answer, dict) else []
                outcomes.extend(
                    {
                        "index": chunk_start + n,
                        "outcome": "created",
                        "id": record.get("id"),
                    }
                    for n, record in enumerate(records)
                )
        if args.stop_on_error and any(o["outcome"] == "error" for o in outcomes):
            break
    return _created_report(outcomes, total=len(items))


def remote_update(client: RemoteClient, args: argparse.Namespace) -> Dict[str, Any]:
    body = {_remote_key(args): read_data(args.data)}
    record = client.call(
        "PUT", _remote_path(args, args.id), body=body, headers=_remote_if_match(args)
    )
    return {"record": record, "etag": client.last_etag}


def remote_delete(client: RemoteClient, args: argparse.Namespace) -> Dict[str, Any]:
    if _single_delete(args):
        [record_id] = args.id
        client.call(
            "DELETE", _remote_path(args, record_id), headers=_remote_if_match(args)
        )
        return {"deleted": record_id}
    _refuse_if_match_on_a_batch(args, remote=True)
    if args.filter:
        raise CLIError(ExitCode.USAGE, "--filter is in-process only; use --search")
    if args.id and args.if_match != "*":
        raise CLIError(
            ExitCode.USAGE,
            "Several ids over --remote need each one's version: pass "
            "--if-match '*' to delete them at whatever version, or --search",
        )
    versions: Dict[str, str] = {record_id: "*" for record_id in args.id}
    versions.update(_remote_matching_versions(client, args))
    ids = list(versions)
    errors: List[Dict[str, Any]] = []
    for chunk in _chunks(ids):
        if_match = ", ".join(sorted({versions[record_id] for record_id in chunk}))
        answer = client.call(
            "DELETE",
            _remote_path(args),
            params={"target_ids": ",".join(chunk)},
            headers={"If-Match": if_match},
            raw=True,
        )
        if client.last_status == 207 and isinstance(answer, dict):
            errors.extend(answer.get("errors", []))
    return _deleted_report(ids, errors)


def _remote_matching_versions(
    client: RemoteClient, args: argparse.Namespace
) -> Dict[str, str]:
    """``--search``: each live matching record's id and ETag, read in pages
    before anything is deleted (ROOT's REST reads include tombstones)."""
    if not args.search:
        return {}
    body = {_remote_key(args): _json_object(args.search, "--search")}
    found: Dict[str, str] = {}
    offset = 0
    while True:
        page = client.call(
            "POST",
            _remote_path(args, "search"),
            body=body,
            params={"limit": MAX_PAGE_SIZE, "offset": offset, "sort_by": "id"},
        )
        rows = page if isinstance(page, list) else []
        for row in rows:
            if isinstance(row, dict) and row.get("id") and not row.get("deleted_at"):
                found[str(row["id"])] = entity_etag(row) or "*"
        offset += len(rows)
        if len(rows) < MAX_PAGE_SIZE:
            return found
