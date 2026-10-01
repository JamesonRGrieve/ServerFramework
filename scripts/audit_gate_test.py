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

    report.write_text("")
    assert audit_gate.main([str(report)]) == 0
