# SPDX-License-Identifier: AGPL-3.0-or-later
"""``zephyrex api-key …`` (this extension's CLI_ApiKeys) on a server with
auth_api_keys loaded: issue once, list without values, revoke."""

from __future__ import annotations

import io
import json
from typing import Any, Dict

from zephyrex.cli import main
from zephyrex.cli.Context import ExitCode
from zephyrex.testing.factories import create_user
from zephyrex.testing.fixtures import CORE_COMPANION_EXTENSIONS

EXTENSIONS = ",".join(["auth_api_keys", *CORE_COMPANION_EXTENSIONS])


def run(client: Any, *argv: str) -> tuple[int, Dict[str, Any]]:
    out = io.StringIO()
    code = main(
        [*argv, "--extensions", "auth_api_keys"],
        app=client.app,
        stdin=io.StringIO(),
        stdout=out,
        stderr=io.StringIO(),
    )
    return code, json.loads(out.getvalue())


def test_issue_list_and_revoke(isolated_extension_server):
    client = isolated_extension_server(EXTENSIONS)
    user = create_user(client)

    code, issued = run(
        client, "api-key", "create", "--name", "deploy", "--user", user.email
    )
    assert code == ExitCode.OK, issued
    key_id, raw = issued["result"]["id"], issued["result"]["key"]
    assert raw

    code, listed = run(client, "api-key", "list", "--user", user.id)
    assert code == ExitCode.OK, listed
    rows = {row["id"]: row for row in listed["result"]}
    assert key_id in rows
    assert raw not in json.dumps(listed)

    code, refused = run(client, "api-key", "revoke", key_id)
    assert code == ExitCode.USAGE

    code, revoked = run(client, "api-key", "revoke", key_id, "--yes")
    assert code == ExitCode.OK, revoked
    assert revoked["result"]["is_revoked"] is True

    code, listed = run(client, "api-key", "list", "--user", user.id)
    assert key_id not in {row["id"] for row in listed["result"]}


def test_dry_run_issue_stores_nothing(isolated_extension_server):
    client = isolated_extension_server(EXTENSIONS)
    code, issued = run(client, "api-key", "create", "--name", "dry", "--dry-run")
    assert code == ExitCode.OK, issued
    code, listed = run(client, "api-key", "list")
    assert issued["result"]["id"] not in {row["id"] for row in listed["result"]}
