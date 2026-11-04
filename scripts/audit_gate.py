#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The dependency-audit gate shared by the nightly audit and the release.

Reads a ``pip-audit --format json`` report and fails on any known
vulnerability, whatever its severity (pip-audit's report seldom carries
one), writing the findings to ``gate-findings.json`` (and their count to
``$GITHUB_OUTPUT`` when set) for the workflow to report.

It fails closed: an empty or malformed report, or one that lists no audited
dependencies, means pip-audit did not finish, so the gate errors rather than
reading it as clean. Given pip-audit's exit status, it also errors on a
status pip-audit does not use for a finished audit (anything but 0, or 1 for
"vulnerabilities found"), and on a 1 whose report holds no vulnerability
(pip-audit exits 1 on its own errors too).

Exit status: 0 clean, 1 known vulnerabilities, 2 the audit itself failed.

Usage:
    status=0
    pip-audit -r requirements.lock --format json > audit-report.json || status=$?
    python scripts/audit_gate.py audit-report.json --pip-audit-status "$status"
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

FINDINGS_FILE = "gate-findings.json"
# pip-audit's exit statuses for an audit that ran to completion.
PIP_AUDIT_CLEAN = 0
PIP_AUDIT_VULNERABLE = 1
EXIT_CLEAN = 0
EXIT_VULNERABLE = 1
EXIT_AUDIT_FAILED = 2


class AuditFailed(Exception):
    """The audit did not run to completion: there is nothing to gate on."""


def audited_dependencies(report: Any) -> List[Dict[str, Any]]:
    """The ``dependencies`` of a pip-audit JSON report, which must list at
    least one audited dependency."""
    if not isinstance(report, dict):
        raise AuditFailed(f"report is a {type(report).__name__}, not an object")
    dependencies = report.get("dependencies")
    if not isinstance(dependencies, list):
        raise AuditFailed("report has no dependencies list")
    if not dependencies:
        raise AuditFailed("report audited no dependencies")
    if not all(isinstance(dependency, dict) for dependency in dependencies):
        raise AuditFailed("report has a dependency that is not an object")
    return dependencies


def gate_findings(report: Any) -> List[Dict[str, Any]]:
    return [
        {
            "package": dependency.get("name"),
            "version": dependency.get("version"),
            "id": vulnerability.get("id"),
            "severity": (vulnerability.get("severity") or "UNKNOWN").upper(),
            "fix_versions": vulnerability.get("fix_versions") or [],
        }
        for dependency in audited_dependencies(report)
        for vulnerability in dependency.get("vulns") or []
    ]


def read_findings(text: str, pip_audit_status: int | None) -> List[Dict[str, Any]]:
    """The findings in a report's ``text``, checked against pip-audit's exit
    status when it is given."""
    if pip_audit_status not in (None, PIP_AUDIT_CLEAN, PIP_AUDIT_VULNERABLE):
        raise AuditFailed(f"pip-audit exited {pip_audit_status}")
    if not text.strip():
        raise AuditFailed("report is empty")
    try:
        report = json.loads(text)
    except json.JSONDecodeError as error:
        raise AuditFailed(f"report is not JSON ({error})") from error
    findings = gate_findings(report)
    if pip_audit_status == PIP_AUDIT_VULNERABLE and not findings:
        raise AuditFailed("pip-audit exited 1 but reported no vulnerability")
    return findings


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Gate on a pip-audit JSON report.")
    parser.add_argument("report", type=Path)
    parser.add_argument("--pip-audit-status", type=int, default=None)
    args = parser.parse_args(argv)
    try:
        findings = read_findings(args.report.read_text(), args.pip_audit_status)
    except (AuditFailed, OSError) as error:
        print(f"Dependency audit failed, nothing to gate on: {error}", file=sys.stderr)
        return EXIT_AUDIT_FAILED
    Path(FINDINGS_FILE).write_text(json.dumps(findings, indent=2))
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as handle:
            handle.write(f"count={len(findings)}\n")
    if findings:
        print("Known vulnerabilities:")
        for finding in findings:
            fixes = ", ".join(finding["fix_versions"]) or "no fix yet"
            print(
                f"  - {finding['package']} {finding['version']} "
                f"{finding['id']} ({finding['severity']}; fixed in {fixes})"
            )
        return EXIT_VULNERABLE
    return EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
