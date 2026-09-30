#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The dependency-audit gate shared by the nightly audit and the release.

Reads a ``pip-audit --format json`` report and fails on any finding at or
above HIGH severity, writing those findings to ``gate-findings.json`` (and
their count to ``$GITHUB_OUTPUT`` when set) for the workflow to report.

Usage:
    pip-audit -r requirements.lock --format json > audit-report.json
    python scripts/audit_gate.py audit-report.json
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

SEVERITY_RANK = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "MODERATE": 2, "LOW": 1}
GATE_RANK = SEVERITY_RANK["HIGH"]
FINDINGS_FILE = "gate-findings.json"


def gate_findings(report: Any) -> List[Dict[str, Any]]:
    dependencies = report.get("dependencies") if isinstance(report, dict) else report
    findings = []
    for dependency in dependencies or []:
        for vulnerability in dependency.get("vulns") or []:
            severity = (vulnerability.get("severity") or "UNKNOWN").upper()
            if SEVERITY_RANK.get(severity, 0) >= GATE_RANK:
                findings.append(
                    {
                        "package": dependency.get("name"),
                        "version": dependency.get("version"),
                        "id": vulnerability.get("id"),
                        "severity": severity,
                        "fix_versions": vulnerability.get("fix_versions") or [],
                    }
                )
    return findings


def main(argv: List[str]) -> int:
    text = Path(argv[0]).read_text()
    findings = gate_findings(json.loads(text or "{}"))
    Path(FINDINGS_FILE).write_text(json.dumps(findings, indent=2))
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as handle:
            handle.write(f"count={len(findings)}\n")
    if findings:
        print("HIGH+ vulnerabilities detected:")
        for finding in findings:
            print(
                f"  - {finding['package']} {finding['version']} "
                f"{finding['id']} ({finding['severity']})"
            )
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
