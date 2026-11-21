# SPDX-License-Identifier: AGPL-3.0-or-later
"""``zephyrex models`` and ``zephyrex record …`` against the real test
server's database: CRUD, If-Match, soft deletes, --as, --yes and dry runs."""

from __future__ import annotations

import io
import json
import uuid
from typing import Any, Dict, List, cast

import httpx

from zephyrex.cli import main
from zephyrex.cli.Context import ExitCode
from zephyrex.testing.factories import create_user


def run(server: Any, *argv: str, stdin: str = "") -> tuple[int, Dict[str, Any]]:
    """Run the CLI in-process against the test server; (exit code, envelope)."""
    out = io.StringIO()
    code = main(
        list(argv),
        app=server.app,
        stdin=io.StringIO(stdin),
        stdout=out,
        stderr=io.StringIO(),
    )
    return code, json.loads(out.getvalue())


def team_names(server: Any, name: str, *extra: str) -> List[str]:
    code, envelope = run(
        server, "record", "list", "team", "--filter", f"name={name}", *extra
    )
    assert code == 0, envelope
    return [row["name"] for row in envelope["result"]]


def test_models_lists_core_models(server):
    code, envelope = run(server, "models")
    assert code == ExitCode.OK
    names = {row["model"] for row in envelope["result"]}
    assert {"user", "team", "user_team"} <= names


def test_record_crud_round_trip_as_a_user(server):
    user = create_user(server)
    name = f"cli-{uuid.uuid4().hex[:8]}"

    code, created = run(
        server,
        "record",
        "create",
        "team",
        "--as",
        user.email,
        "--data",
        json.dumps({"name": name, "description": "first"}),
    )
    assert code == ExitCode.OK, created
    team_id = created["result"]["record"]["id"]
    etag = created["result"]["etag"]
    assert created["dry_run"] is False

    code, got = run(server, "record", "get", "team", team_id, "--as", user.id)
    assert code == ExitCode.OK
    assert got["result"]["record"]["description"] == "first"

    code, updated = run(
        server,
        "record",
        "update",
        "team",
        team_id,
        "--as",
        user.email,
        "--if-match",
        etag,
        "--data",
        '{"description": "second"}',
    )
    assert code == ExitCode.OK, updated
    assert updated["result"]["record"]["description"] == "second"

    code, stale = run(
        server,
        "record",
        "update",
        "team",
        team_id,
        "--as",
        user.email,
        "--if-match",
        etag,
        "--data",
        '{"description": "third"}',
    )
    assert code == ExitCode.CONFLICT
    assert stale["ok"] is False and stale["error"]["status"] == 412

    assert team_names(server, name) == [name]

    code, refused = run(server, "record", "delete", "team", team_id, "--as", user.email)
    assert code == ExitCode.USAGE
    assert "--yes" in refused["error"]["message"]

    code, deleted = run(
        server, "record", "delete", "team", team_id, "--as", user.email, "--yes"
    )
    assert code == ExitCode.OK, deleted
    assert team_names(server, name) == []
    assert team_names(server, name, "--include-deleted") == [name]


def test_data_from_stdin_and_table_output(server):
    name = f"cli-{uuid.uuid4().hex[:8]}"
    code, created = run(
        server,
        "record",
        "create",
        "team",
        "--data",
        "-",
        stdin=json.dumps({"name": name}),
    )
    assert code == ExitCode.OK, created

    out = io.StringIO()
    code = main(
        [
            "record",
            "list",
            "team",
            "--filter",
            f"name={name}",
            "--fields",
            "id,name",
            "--format",
            "table",
        ],
        app=server.app,
        stdin=io.StringIO(),
        stdout=out,
        stderr=io.StringIO(),
    )
    assert code == ExitCode.OK
    header, row = out.getvalue().splitlines()[:2]
    assert header.split() == ["id", "name"]
    assert name in row


def test_dry_run_rolls_the_write_back(server):
    name = f"cli-{uuid.uuid4().hex[:8]}"
    code, envelope = run(
        server,
        "record",
        "create",
        "team",
        "--dry-run",
        "--data",
        json.dumps({"name": name}),
    )
    assert code == ExitCode.OK, envelope
    assert envelope["dry_run"] is True
    assert envelope["result"]["record"]["name"] == name
    assert "audited" not in envelope
    assert team_names(server, name) == []


def test_dry_run_delete_needs_no_yes_and_keeps_the_record(server):
    name = f"cli-{uuid.uuid4().hex[:8]}"
    _, created = run(
        server, "record", "create", "team", "--data", json.dumps({"name": name})
    )
    team_id = created["result"]["record"]["id"]

    code, envelope = run(server, "record", "delete", "team", team_id, "--dry-run")
    assert code == ExitCode.OK, envelope
    assert team_names(server, name) == [name]


def test_errors_map_to_exit_codes(server):
    code, envelope = run(server, "record", "get", "team", str(uuid.uuid4()))
    assert code == ExitCode.NOT_FOUND and envelope["ok"] is False

    code, envelope = run(server, "record", "list", "no_such_model")
    assert code == ExitCode.NOT_FOUND
    assert "team" in envelope["error"]["detail"]["models"]

    code, envelope = run(server, "record", "create", "team", "--data", "[1]")
    assert code == ExitCode.USAGE

    code, envelope = run(
        server, "record", "list", "team", "--as", "nobody@example.invalid"
    )
    assert code == ExitCode.NOT_FOUND

    code, envelope = run(server, "record", "list", "team", "--sort-by", "no_such_field")
    assert code == ExitCode.VALIDATION


def test_remote_refuses_dry_run_and_as(server):
    code, envelope = run(
        server,
        "record",
        "create",
        "team",
        "--remote",
        "http://127.0.0.1:9",
        "--dry-run",
        "--data",
        "{}",
    )
    assert code == ExitCode.DRY_RUN_REFUSED, envelope


def remote(server: Any, *argv: str) -> tuple[int, Dict[str, Any]]:
    """Run a command over --remote, through the test server's HTTP stack."""
    from fastapi.testclient import TestClient

    from zephyrex.cli import _build_parser
    from zephyrex.cli.Context import execute
    from zephyrex.cli.Remote import RemoteClient
    from zephyrex.lib.Environment import env

    arguments = [*argv, "--remote", "http://testserver"]
    args = _build_parser(arguments).parse_args(arguments)
    http = TestClient(server.app, headers={"X-API-Key": env("ROOT_API_KEY")})
    client = RemoteClient(cast(httpx.Client, http))
    out = io.StringIO()
    code = execute(
        args.command,
        args,
        arguments,
        client=client,
        stdin=io.StringIO(),
        stdout=out,
        stderr=io.StringIO(),
    )
    return code, json.loads(out.getvalue())


def test_remote_crud_over_rest(server):
    name = f"cli-{uuid.uuid4().hex[:8]}"
    code, created = remote(
        server, "record", "create", "team", "--data", json.dumps({"name": name})
    )
    assert code == ExitCode.OK, created
    team_id = created["result"]["record"]["id"]

    code, got = remote(server, "record", "get", "team", team_id)
    assert code == ExitCode.OK, got
    assert got["result"]["record"].get("name") == name, got
    etag = got["result"]["etag"]
    assert etag

    code, missing = remote(
        server, "record", "update", "team", team_id, "--data", '{"description": "x"}'
    )
    assert code == ExitCode.CONFLICT, missing
    assert missing["error"]["detail"]["status"] == 428

    code, updated = remote(
        server,
        "record",
        "update",
        "team",
        team_id,
        "--if-match",
        etag,
        "--data",
        '{"description": "x"}',
    )
    assert code == ExitCode.OK, updated
    assert updated["result"]["record"]["description"] == "x"

    code, listed = remote(server, "record", "list", "team", "--limit", "5")
    assert code == ExitCode.OK, listed
    assert isinstance(listed["result"], list) and len(listed["result"]) <= 5

    search = json.dumps({"name": {"eq": name}})
    code, found = remote(server, "record", "list", "team", "--search", search)
    assert code == ExitCode.OK, found
    assert [row["id"] for row in found["result"]] == [team_id]

    code, refused = remote(server, "record", "list", "team", "--filter", f"name={name}")
    assert code == ExitCode.USAGE

    code, deleted = remote(
        server,
        "record",
        "delete",
        "team",
        team_id,
        "--yes",
        "--if-match",
        updated["result"]["etag"],
    )
    assert code == ExitCode.OK, deleted
    # ROOT's REST reads include tombstones; the in-process list hides them.
    assert team_names(server, name) == []


def test_remote_without_a_key_is_refused(server):
    from zephyrex.cli import _build_parser
    from zephyrex.cli.Context import execute
    from zephyrex.cli.Remote import RemoteClient

    arguments = ["record", "list", "team", "--remote", "http://127.0.0.1:9"]
    args = _build_parser(arguments).parse_args(arguments)
    out = io.StringIO()
    code = execute(
        args.command,
        args,
        arguments,
        client=RemoteClient.connect("http://127.0.0.1:9", ""),
        stdin=io.StringIO(),
        stdout=out,
        stderr=io.StringIO(),
    )
    assert code == ExitCode.USAGE
    assert "ZEPHYREX_ROOT_API_KEY" in json.loads(out.getvalue())["error"]["message"]


# Many records per command: ``record create --file`` and batch ``record delete``.


def _teams(tag: str, count: int) -> List[Dict[str, Any]]:
    return [{"name": f"{tag}-{n}", "description": tag} for n in range(count)]


def _jsonl(items: List[Dict[str, Any]]) -> str:
    return "\n".join(json.dumps(item) for item in items) + "\n"


def tagged(server: Any, tag: str) -> List[str]:
    code, envelope = run(
        server, "record", "list", "team", "--filter", f"description={tag}"
    )
    assert code == 0, envelope
    return sorted(row["name"] for row in envelope["result"])


def test_create_from_a_jsonl_file(server, tmp_path):
    tag = f"cli-{uuid.uuid4().hex[:8]}"
    path = tmp_path / "teams.jsonl"
    path.write_text(_jsonl(_teams(tag, 3)))

    code, envelope = run(server, "record", "create", "team", "--file", str(path))
    assert code == ExitCode.OK, envelope
    report = envelope["result"]
    assert (report["created"], report["failed"], report["skipped"]) == (3, 0, 0)
    assert [r["index"] for r in report["results"]] == [0, 1, 2]
    assert all(r["outcome"] == "created" and r["id"] for r in report["results"])
    assert tagged(server, tag) == [f"{tag}-{n}" for n in range(3)]


def test_create_from_a_json_array_on_stdin(server):
    tag = f"cli-{uuid.uuid4().hex[:8]}"
    code, envelope = run(
        server,
        "record",
        "create",
        "team",
        "--file",
        "-",
        stdin=json.dumps(_teams(tag, 2)),
    )
    assert code == ExitCode.OK, envelope
    assert tagged(server, tag) == [f"{tag}-0", f"{tag}-1"]


def test_a_failing_record_is_reported_and_the_rest_are_created(server):
    tag = f"cli-{uuid.uuid4().hex[:8]}"
    items = [*_teams(tag, 1), {"description": tag}, *_teams(f"{tag}x", 1)]

    code, envelope = run(
        server, "record", "create", "team", "--file", "-", stdin=_jsonl(items)
    )
    assert code == ExitCode.VALIDATION, envelope
    report = envelope["error"]["detail"]
    assert (report["created"], report["failed"]) == (2, 1)
    assert [r["outcome"] for r in report["results"]] == ["created", "error", "created"]
    assert report["results"][1]["status_code"] == 422

    code, envelope = run(
        server,
        "record",
        "create",
        "team",
        "--file",
        "-",
        "--stop-on-error",
        stdin=_jsonl([{"description": tag}, *_teams(f"{tag}y", 1)]),
    )
    assert code == ExitCode.VALIDATION, envelope
    report = envelope["error"]["detail"]
    assert (report["created"], report["failed"], report["skipped"]) == (0, 1, 1)
    assert tagged(server, f"{tag}y") == []


def test_a_dry_run_import_writes_nothing(server):
    tag = f"cli-{uuid.uuid4().hex[:8]}"
    code, envelope = run(
        server,
        "record",
        "create",
        "team",
        "--file",
        "-",
        "--dry-run",
        stdin=_jsonl(_teams(tag, 3)),
    )
    assert code == ExitCode.OK, envelope
    assert envelope["dry_run"] is True and envelope["result"]["created"] == 3
    assert tagged(server, tag) == []


def test_a_bad_file_is_a_usage_error(server, tmp_path):
    for text in ("", "[1, 2]", "{not json"):
        code, envelope = run(
            server, "record", "create", "team", "--file", "-", stdin=text
        )
        assert code == ExitCode.USAGE, (text, envelope)
    code, envelope = run(
        server, "record", "create", "team", "--file", str(tmp_path / "missing.jsonl")
    )
    assert code == ExitCode.USAGE, envelope


def test_delete_by_filter_and_by_ids(server):
    tag = f"cli-{uuid.uuid4().hex[:8]}"
    _, made = run(
        server, "record", "create", "team", "--file", "-", stdin=_jsonl(_teams(tag, 4))
    )
    ids = [r["id"] for r in made["result"]["results"]]

    code, envelope = run(server, "record", "delete", "team", ids[0], ids[1], "--yes")
    assert code == ExitCode.OK, envelope
    assert (envelope["result"]["matched"], envelope["result"]["deleted"]) == (2, 2)
    assert tagged(server, tag) == [f"{tag}-2", f"{tag}-3"]

    code, envelope = run(
        server, "record", "delete", "team", "--filter", f"description={tag}", "--yes"
    )
    assert code == ExitCode.OK, envelope
    assert envelope["result"]["deleted"] == 2
    assert tagged(server, tag) == []


def test_a_batch_delete_is_guarded(server):
    code, envelope = run(server, "record", "delete", "team", "--yes")
    assert code == ExitCode.USAGE, envelope  # names nothing: never "delete all"
    code, envelope = run(
        server, "record", "delete", "team", "a", "b", "--if-match", '"v1"', "--yes"
    )
    assert code == ExitCode.USAGE, envelope
    code, envelope = run(
        server, "record", "delete", "team", "--filter", "description=x"
    )
    assert code == ExitCode.USAGE, envelope  # destructive: needs --yes


def test_remote_import_in_batches_and_delete_by_search(server, monkeypatch, tmp_path):
    from zephyrex.cli import Records

    monkeypatch.setattr(Records, "BATCH_SIZE", 2)  # three records: two POSTs
    tag = f"cli-{uuid.uuid4().hex[:8]}"
    items = [*_teams(tag, 2), {"description": tag}, *_teams(f"{tag}z", 1)]
    path = tmp_path / "teams.jsonl"
    path.write_text(_jsonl(items))
    code, envelope = remote(server, "record", "create", "team", "--file", str(path))
    assert code == ExitCode.VALIDATION, envelope
    report = envelope["error"]["detail"]
    assert [r["index"] for r in report["results"]] == [0, 1, 2, 3]
    assert [r["outcome"] for r in report["results"]] == [
        "created",
        "created",
        "error",
        "created",
    ]
    assert tagged(server, tag) == [f"{tag}-0", f"{tag}-1"]

    code, refused = remote(server, "record", "delete", "team", "a", "b", "--yes")
    assert code == ExitCode.USAGE, refused

    search = json.dumps({"description": {"eq": tag}})
    code, deleted = remote(
        server, "record", "delete", "team", "--search", search, "--yes"
    )
    assert code == ExitCode.OK, deleted
    assert deleted["result"]["deleted"] == 2
    assert tagged(server, tag) == []

    [other] = [r["id"] for r in report["results"] if r["outcome"] == "created"][2:]
    code, deleted = remote(
        server, "record", "delete", "team", other, other, "--if-match", "*", "--yes"
    )
    assert code == ExitCode.OK, deleted
    assert tagged(server, f"{tag}z") == []
