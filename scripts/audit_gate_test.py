# SPDX-License-Identifier: AGPL-3.0-or-later
import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "audit_gate", Path(__file__).with_name("audit_gate.py")
)
assert _SPEC and _SPEC.loader
audit_gate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(audit_gate)


def _report(*severities):
    return {
        "dependencies": [
            {
                "name": "pkg",
                "version": "1.0",
                "vulns": [
                    {"id": f"V-{i}", "severity": severity, "fix_versions": ["1.1"]}
                    for i, severity in enumerate(severities)
                ],
            }
        ]
    }


@pytest.mark.parametrize(
    "severities",
    [["CRITICAL"], ["LOW"], [None], ["MEDIUM", "high", None]],
    ids=["critical", "low", "no-severity", "mixed"],
)
def test_every_known_vulnerability_is_gated(severities):
    """Any severity, or none: pip-audit's report seldom carries one, so a
    severity threshold would let nearly everything through."""
    findings = audit_gate.gate_findings(_report(*severities))
    assert [f["id"] for f in findings] == [f"V-{i}" for i in range(len(severities))]


def test_a_clean_report_passes():
    assert (
        audit_gate.gate_findings({"dependencies": [{"name": "pkg", "vulns": []}]}) == []
    )


def test_main_writes_findings_and_fails_on_them(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    report = tmp_path / "audit-report.json"

    report.write_text(json.dumps(_report("HIGH")))
    assert audit_gate.main([str(report)]) == 1
    assert json.loads((tmp_path / "gate-findings.json").read_text())[0]["id"] == "V-0"
    assert output.read_text() == "count=1\n"

    report.write_text(json.dumps({"dependencies": [{"name": "pkg", "vulns": []}]}))
    assert audit_gate.main([str(report)]) == 0
    assert json.loads((tmp_path / "gate-findings.json").read_text()) == []


@pytest.mark.parametrize(
    "text",
    [
        "",
        "\n",
        "pip-audit: error: could not resolve requirements",
        "{}",
        "[]",
        '{"dependencies": null}',
        '{"dependencies": []}',
        '{"dependencies": ["pkg"]}',
    ],
    ids=[
        "empty",
        "blank",
        "not-json",
        "no-dependencies",
        "not-an-object",
        "null-dependencies",
        "no-dependency-audited",
        "dependency-not-an-object",
    ],
)
def test_a_report_from_a_failed_audit_fails_the_gate(tmp_path, monkeypatch, text):
    """pip-audit erroring left an empty report, which the gate read as clean
    and the release went on unaudited (#249)."""
    monkeypatch.chdir(tmp_path)
    report = tmp_path / "audit-report.json"
    report.write_text(text)
    assert audit_gate.main([str(report)]) == audit_gate.EXIT_AUDIT_FAILED
    assert not (tmp_path / "gate-findings.json").exists()


def test_a_missing_report_fails_the_gate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert audit_gate.main([str(tmp_path / "absent.json")]) == 2


@pytest.mark.parametrize(
    "status, report, expected",
    [
        (0, {"dependencies": [{"name": "pkg", "vulns": []}]}, 0),
        (1, _report("HIGH"), 1),
        # pip-audit exits 1 on its own errors too: no vulnerability, no pass.
        (1, {"dependencies": [{"name": "pkg", "vulns": []}]}, 2),
        # Not a status pip-audit gives a finished audit.
        (2, {"dependencies": [{"name": "pkg", "vulns": []}]}, 2),
        (137, _report("HIGH"), 2),
    ],
    ids=["clean", "vulnerable", "error-exit-1", "error-exit-2", "killed"],
)
def test_pip_audits_exit_status_is_checked(
    tmp_path, monkeypatch, status, report, expected
):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "audit-report.json"
    path.write_text(json.dumps(report))
    assert audit_gate.main([str(path), "--pip-audit-status", str(status)]) == expected
