# SPDX-License-Identifier: AGPL-3.0-or-later
"""The curated admin commands against the real test server: users and
passwords, teams and memberships, roles, extensions, caches and seeding."""

from __future__ import annotations

import io
import json
import uuid
from typing import Any, Dict

from zephyrex.cli import main
from zephyrex.cli.Context import ExitCode
from zephyrex.testing.factories import TEST_PASSWORD, authorize_user, create_user

NEW_PASSWORD = "Cli-Pass-" + uuid.uuid4().hex[:12] + "!9"


def run(server: Any, *argv: str, stdin: str = "") -> tuple[int, Dict[str, Any]]:
    out = io.StringIO()
    code = main(
        list(argv),
        app=server.app,
        stdin=io.StringIO(stdin),
        stdout=out,
        stderr=io.StringIO(),
    )
    return code, json.loads(out.getvalue())


def _email() -> str:
    return f"cli-{uuid.uuid4().hex[:10]}@example.com"


def test_user_create_with_password_and_get(server):
    email = _email()
    code, created = run(
        server,
        "user",
        "create",
        "--email",
        email,
        "--first-name",
        "Cli",
        "--password-stdin",
        stdin=TEST_PASSWORD + "\n",
    )
    assert code == ExitCode.OK, created
    assert created["result"]["password_set"] is True
    assert created["result"]["user"]["email"] == email
    assert TEST_PASSWORD not in json.dumps(created)
    assert authorize_user(server, email)

    code, got = run(server, "user", "get", email)
    assert (
        code == ExitCode.OK and got["result"]["id"] == created["result"]["user"]["id"]
    )


def test_user_create_needs_a_password_on_stdin(server):
    code, envelope = run(
        server, "user", "create", "--email", _email(), "--password-stdin"
    )
    assert code == ExitCode.USAGE, envelope


def test_set_password_from_stdin(server):
    user = create_user(server)
    code, envelope = run(
        server, "user", "set-password", user.email, stdin=NEW_PASSWORD + "\n"
    )
    assert code == ExitCode.OK, envelope
    assert authorize_user(server, user.email, NEW_PASSWORD)


def test_disable_and_enable(server):
    user = create_user(server)
    code, disabled = run(server, "user", "disable", user.email)
    assert code == ExitCode.OK, disabled
    assert disabled["result"]["user"]["active"] is False
    assert disabled["result"]["sessions_revoked"] is True

    code, enabled = run(server, "user", "enable", user.id)
    assert code == ExitCode.OK, enabled
    assert enabled["result"]["user"]["active"] is True


def test_team_membership_lifecycle(server):
    user = create_user(server)
    code, team = run(server, "team", "create", "--name", f"cli-{uuid.uuid4().hex[:8]}")
    assert code == ExitCode.OK, team
    team_id = team["result"]["id"]

    code, added = run(server, "team", "add-member", team_id, user.email, "user")
    assert code == ExitCode.OK, added
    assert added["result"]["created"] is True
    membership_id = added["result"]["membership"]["id"]

    code, members = run(server, "team", "members", team_id)
    assert code == ExitCode.OK
    assert membership_id in {m["id"] for m in members["result"]}

    code, granted = run(server, "role", "grant", team_id, user.id, "admin")
    assert code == ExitCode.OK, granted
    assert granted["result"]["created"] is False
    assert granted["result"]["membership"]["id"] == membership_id

    code, refused = run(server, "team", "remove-member", team_id, user.email)
    assert code == ExitCode.USAGE

    code, removed = run(server, "role", "revoke", team_id, user.email, "--yes")
    assert code == ExitCode.OK, removed
    code, members = run(server, "team", "members", team_id)
    assert membership_id not in {m["id"] for m in members["result"]}

    code, missing = run(server, "team", "remove-member", team_id, user.email, "--yes")
    assert code == ExitCode.NOT_FOUND


def test_role_list_has_system_roles(server):
    code, roles = run(server, "role", "list")
    assert code == ExitCode.OK, roles
    assert {"user", "admin"} <= {r["name"] for r in roles["result"]}


def test_unknown_role_and_user(server):
    code, _ = run(
        server, "role", "grant", str(uuid.uuid4()), "nobody@example.invalid", "admin"
    )
    assert code == ExitCode.NOT_FOUND


def test_extension_list_and_info(server):
    code, listed = run(server, "extension", "list")
    assert code == ExitCode.OK, listed
    names = {e["name"] for e in listed["result"]}
    assert "metadata" in names

    code, info = run(server, "extension", "info", "metadata")
    assert code == ExitCode.OK and info["result"]["loaded"] is True
    assert info["result"]["version"]

    code, _ = run(server, "extension", "info", "no_such_extension")
    assert code == ExitCode.NOT_FOUND


def test_cache_clear_without_valkey_notes_it(server):
    code, refused = run(server, "cache", "clear")
    assert code == ExitCode.USAGE

    code, envelope = run(server, "cache", "clear", "--model", "team", "--yes")
    assert code == ExitCode.OK, envelope
    assert envelope["result"]["cleared"] == []

    code, _ = run(server, "cache", "clear", "--model", "no_such_model", "--yes")
    assert code == ExitCode.NOT_FOUND


def test_seed_dry_run(server):
    code, envelope = run(server, "seed", "--dry-run")
    assert code == ExitCode.OK, envelope
    assert envelope["dry_run"] is True and envelope["result"] == {"seeded": True}


def test_migrate_status_up_and_guards(server):
    code, status = run(server, "migrate", "status")
    assert code == ExitCode.OK, status
    components = {row["component"]: row for row in status["result"]}
    assert components["core"]["heads"]
    assert components["core"]["up_to_date"] is True

    code, envelope = run(server, "migrate", "--dry-run")
    assert code == ExitCode.DRY_RUN_REFUSED, envelope

    code, envelope = run(server, "migrate", "down", "--to", "-1")
    assert code == ExitCode.USAGE, envelope

    code, envelope = run(server, "migrate")
    assert code == ExitCode.OK, envelope
    assert envelope["command"] == "migrate up"
    assert envelope["result"] == {"migrated": True, "seeded": True}


def test_migrate_revision_writes_an_empty_first_revision(server, tmp_path, monkeypatch):
    from zephyrex.cli import Admin
    from zephyrex.database.migrations.Migration import MigrationManager

    real = Admin._migrations

    def in_tmp(ctx):
        # The real manager, writing into a temporary versions root instead of
        # the package's source tree.
        manager = real(ctx)
        return MigrationManager(
            test_mode=True,
            test_versions_root=tmp_path,
            custom_db_info=manager.db_info,
            model_registry=ctx.registry,
        )

    monkeypatch.setattr(Admin, "_migrations", in_tmp)
    code, envelope = run(
        server,
        "migrate",
        "revision",
        "metadata",
        "-m",
        "cli check",
        "--empty",
    )
    assert code == ExitCode.OK, envelope
    (written,) = envelope["result"]["files"]
    text = open(written).read()
    assert "cli check" in text and "ext_metadata" in text

    code, envelope = run(
        server,
        "migrate",
        "revision",
        "no_such_ext",
        "-m",
        "x",
        "--empty",
    )
    assert code == ExitCode.ERROR


def test_migrate_verify_and_stamp(server):
    code, verified = run(server, "migrate", "verify", "metadata")
    assert code == ExitCode.OK, verified
    # metadata's migrations and models disagree on indexes only (advisory).
    assert verified["result"]["blocking"] == [], verified
    assert verified["result"]["matches"] is True

    code, envelope = run(server, "migrate", "stamp", "metadata", "--dry-run")
    assert code == ExitCode.DRY_RUN_REFUSED, envelope

    code, stamped = run(server, "migrate", "stamp", "metadata")
    assert code == ExitCode.OK, stamped
    (row,) = stamped["result"]["status"]
    assert row["component"] == "metadata" and row["up_to_date"] is True

    code, envelope = run(server, "migrate", "verify", "no_such_extension")
    assert code == ExitCode.NOT_FOUND, envelope
