# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pure utilities of the admin CLI: names, exit codes, argv redaction,
confirmation and table output."""

from __future__ import annotations

import argparse
import io

import pytest

from zephyrex.cli.Context import (
    CLIError,
    Command,
    ExitCode,
    _table,
    cli_model_name,
    confirm,
    exit_code_for_status,
    redacted_argv,
)


@pytest.mark.parametrize(
    "given, expected",
    [
        ("UserTeamModel", "user_team"),
        ("UserTeam", "user_team"),
        ("user_team", "user_team"),
        ("APIKeyModel", "api_key"),
        ("Team", "team"),
        ("team", "team"),
    ],
)
def test_cli_model_name_is_the_wire_name(given, expected):
    assert cli_model_name(given) == expected


@pytest.mark.parametrize(
    "status, code",
    [
        (404, ExitCode.NOT_FOUND),
        (412, ExitCode.CONFLICT),
        (428, ExitCode.CONFLICT),
        (409, ExitCode.CONFLICT),
        (403, ExitCode.PERMISSION),
        (401, ExitCode.PERMISSION),
        (422, ExitCode.VALIDATION),
        (400, ExitCode.VALIDATION),
        (500, ExitCode.ERROR),
    ],
)
def test_status_exit_codes(status, code):
    assert exit_code_for_status(status) is code


def test_redacted_argv_keeps_only_data_keys():
    argv = [
        "record",
        "create",
        "user_credential",
        "--data",
        '{"password": "s3cret", "user_id": "u"}',
    ]
    assert redacted_argv(argv) == [
        "record",
        "create",
        "user_credential",
        "--data",
        "<keys:password,user_id>",
    ]
    assert redacted_argv(['--data={"a": 1}']) == ["--data=<keys:a>"]
    assert redacted_argv(["--data", "-"]) == ["--data", "<redacted>"]
    assert "s3cret" not in " ".join(redacted_argv(argv))


def _args(**overrides) -> argparse.Namespace:
    return argparse.Namespace(**{"yes": False, "dry_run": False, **overrides})


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


DESTRUCTIVE = Command("record delete", lambda ctx, args: None, True, True, None)


def test_confirm_needs_yes_without_a_terminal():
    with pytest.raises(CLIError) as err:
        confirm(DESTRUCTIVE, _args(), io.StringIO(), io.StringIO())
    assert err.value.code is ExitCode.USAGE
    confirm(DESTRUCTIVE, _args(yes=True), io.StringIO(), io.StringIO())
    confirm(DESTRUCTIVE, _args(dry_run=True), io.StringIO(), io.StringIO())


def test_confirm_takes_a_typed_yes_at_a_terminal():
    prompt = io.StringIO()
    confirm(DESTRUCTIVE, _args(), _Terminal("yes\n"), prompt)
    assert "destructive" in prompt.getvalue()
    with pytest.raises(CLIError):
        confirm(DESTRUCTIVE, _args(), _Terminal("no\n"), io.StringIO())


def test_table_renders_rows_and_mappings():
    assert _table(
        [{"id": "1", "name": "a"}, {"id": "22", "name": "bb"}]
    ).splitlines() == ["id  name", "1   a   ", "22  bb  "]
    assert _table({"k": {"x": 1}}) == 'k  {"x": 1}'
    assert _table(None) == ""


class _KeyStore:
    """An in-memory stand-in for the redis asyncio client (scan_iter,
    delete, aclose), as the repo's Valkey cache tests use, since the runner
    has no Valkey server."""

    def __init__(self, keys):
        self.keys = set(keys)
        self.closed = False

    async def scan_iter(self, match, count=None):
        import fnmatch

        for key in sorted(self.keys):
            if fnmatch.fnmatch(key, match):
                yield key

    async def delete(self, *keys):
        self.keys.difference_update(keys)

    async def aclose(self):
        self.closed = True


def test_clear_shared_caches_needs_valkey(monkeypatch):
    from zephyrex.cli.Context import clear_shared_caches
    from zephyrex.lib.Environment import settings

    monkeypatch.setattr(settings, "VALKEY_URI", "", raising=False)
    assert clear_shared_caches({"teams": "team"}) == []


def test_clear_shared_caches_clears_touched_tables_only(monkeypatch):
    from redis import asyncio as redis_asyncio

    from zephyrex.cli.Context import clear_shared_caches
    from zephyrex.lib.Environment import settings

    store = _KeyStore(
        {
            "entity:teams:1",
            "idx:teams:name:a",
            "resp:v1/team:abc",
            "entity:users:1",
            "resp:v1/user:abc",
        }
    )
    monkeypatch.setattr(
        settings, "VALKEY_URI", "redis://cache.invalid:6379/0", raising=False
    )
    monkeypatch.setattr(redis_asyncio, "from_url", lambda uri, decode_responses: store)

    assert clear_shared_caches({"teams": "team"}) == ["teams"]
    assert store.keys == {"entity:users:1", "resp:v1/user:abc"}
    assert store.closed


def test_unwrap_envelopes():
    from zephyrex.cli.Remote import unwrap

    assert unwrap({"team": {"id": "1"}, "_links": {}}) == {"id": "1"}
    assert unwrap({"teams": [{"id": "1"}], "total": 1, "has_more": False}) == [
        {"id": "1"}
    ]
    assert unwrap({"a": 1, "b": 2}) == {"a": 1, "b": 2}
    assert unwrap(None) is None
