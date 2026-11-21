# SPDX-License-Identifier: AGPL-3.0-or-later
"""Mutating commands write a meta_logging audit row (success or failure);
reads and dry runs write none."""

from __future__ import annotations

import io
import json
import uuid
from typing import Any, Dict, List

from zephyrex.cli import main
from zephyrex.cli.Context import ExitCode
from zephyrex.lib.Environment import env


def run(app: Any, *argv: str) -> tuple[int, Dict[str, Any]]:
    out = io.StringIO()
    code = main(
        list(argv), app=app, stdin=io.StringIO(), stdout=out, stderr=io.StringIO()
    )
    return code, json.loads(out.getvalue())


def audit_rows(app: Any, action: str) -> List[Any]:
    from zephyrex.extensions.meta_logging.BLL_Meta_Logging import AuditLogManager

    manager = AuditLogManager(
        model_registry=app.state.model_registry, requester_id=env("ROOT_ID")
    )
    return manager.list(action=action)


def test_mutations_are_audited_with_cli_attribution(isolated_extension_server):
    app = isolated_extension_server("meta_logging").app
    name = f"cli-{uuid.uuid4().hex[:8]}"
    before = len(audit_rows(app, "cli:record create"))

    code, envelope = run(
        app,
        "record",
        "create",
        "team",
        "--reason",
        "ticket 42",
        "--data",
        json.dumps({"name": name, "description": "secret-ish"}),
    )
    assert code == ExitCode.OK, envelope
    assert envelope["audited"] is True
    team_id = envelope["result"]["record"]["id"]

    rows = audit_rows(app, "cli:record create")
    assert len(rows) == before + 1
    row = next(r for r in rows if r.resource_id == team_id)
    assert row.success is True
    assert row.resource_type == "team"
    assert row.user_id == env("ROOT_ID")
    data = row.additional_data
    assert data["via"] == "cli" and data["reason"] == "ticket 42"
    assert data["host"] and data["os_user"]
    assert "<keys:description,name>" in data["argv"]
    assert "secret-ish" not in json.dumps(data)


def test_failed_mutations_are_audited_and_reads_and_dry_runs_are_not(
    isolated_extension_server,
):
    app = isolated_extension_server("meta_logging").app
    missing = str(uuid.uuid4())

    code, _ = run(
        app, "record", "update", "team", missing, "--data", '{"description": "x"}'
    )
    assert code == ExitCode.NOT_FOUND
    failed = [
        r for r in audit_rows(app, "cli:record update") if r.resource_id == missing
    ]
    assert len(failed) == 1 and failed[0].success is False
    assert failed[0].error_message.startswith("404")

    reads_before = len(audit_rows(app, "cli:record list"))
    run(app, "record", "list", "team")
    assert len(audit_rows(app, "cli:record list")) == reads_before

    creates_before = len(audit_rows(app, "cli:record create"))
    code, envelope = run(
        app, "record", "create", "team", "--dry-run", "--data", '{"name": "dry"}'
    )
    assert code == ExitCode.OK and envelope["dry_run"] is True
    assert len(audit_rows(app, "cli:record create")) == creates_before
