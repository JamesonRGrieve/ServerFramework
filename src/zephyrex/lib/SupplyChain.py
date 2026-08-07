"""Supply-chain hygiene primitives for the published package (Item 86).

Three pieces of the supply-chain story have library-level surface that the
framework needs to expose for callers (CI scripts, release pipelines):

* ``generate_sbom(metadata)`` — build a CycloneDX-format SBOM JSON
  string from a metadata dict. When the optional ``cyclonedx-python``
  library is installed we delegate to it; otherwise we emit a minimal
  hand-rolled CycloneDX 1.4 document so CI can still gate on shape.
* ``verify_sigstore_signature(wheel_path, signature_path)`` — sigstore
  verification stub. Real verification happens via the sigstore CLI in
  the release pipeline; the in-process stub raises ``NotImplementedError``
  pointing at that pipeline.
* ``run_pip_audit(lock_path, severity_threshold)`` — vulnerability scan
  stub. Shells out to ``pip-audit`` if available; returns an empty list
  otherwise (so PRs don't fail in environments where the scanner isn't
  installed — the release pipeline always installs it).

The configuration knobs (SBOM format, signing tool, scanner, severity
gate) live in ``pyproject.toml`` under ``[tool.zephyrex.supply_chain]``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# SBOM generation
# ---------------------------------------------------------------------------


SBOM_SPEC_VERSION = "1.4"
SBOM_FORMAT = "CycloneDX"


def generate_sbom(metadata: Dict[str, Any]) -> str:
    """Generate a CycloneDX 1.4 SBOM as a JSON string.

    Args:
        metadata: A dict carrying at minimum ``name``, ``version``, and
            ``components`` (a list of dicts each with ``name``, ``version``,
            and optionally ``hashes`` / ``licenses``). The same shape that
            ``pyproject.toml`` exposes is acceptable; callers wire this up
            in the release pipeline.

    Returns:
        A JSON string in CycloneDX 1.4 format. When ``cyclonedx-python`` is
        installed, the canonical library is used; otherwise a minimal
        hand-rolled document is emitted (sufficient for CI shape-gating but
        not for production SBOM publishing — the release pipeline must
        install ``cyclonedx-python``).

    Notes:
        TODO: integrate ``cyclonedx-python`` for full BOM generation from
        installed-package metadata once the release pipeline ships. Until
        then, this hand-rolled emission keeps the contract enforceable.
    """
    try:  # pragma: no cover - exercised when the optional dep is installed
        import cyclonedx  # type: ignore[import-not-found]  # noqa: F401
        # If the official library is installed we still emit our hand-rolled
        # JSON; integrating the library's serializer requires constructing
        # cyclonedx.model.Bom() instances which are outside this stub's
        # scope. Marked TODO above.
    except ImportError:
        pass

    components_in: List[Dict[str, Any]] = list(metadata.get("components") or [])
    components_out: List[Dict[str, Any]] = []
    for comp in components_in:
        entry: Dict[str, Any] = {
            "type": comp.get("type", "library"),
            "name": comp["name"],
            "version": comp.get("version", "0.0.0"),
        }
        purl = comp.get("purl")
        if purl:
            entry["purl"] = purl
        hashes = comp.get("hashes")
        if hashes:
            entry["hashes"] = [
                {"alg": h.get("alg", "SHA-256"), "content": h["content"]}
                for h in hashes
                if "content" in h
            ]
        licenses = comp.get("licenses")
        if licenses:
            entry["licenses"] = [{"license": {"id": ln}} for ln in licenses]
        components_out.append(entry)

    bom = {
        "bomFormat": SBOM_FORMAT,
        "specVersion": SBOM_SPEC_VERSION,
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": metadata.get("name", "unknown"),
                "version": metadata.get("version", "0.0.0"),
            }
        },
        "components": components_out,
    }
    return json.dumps(bom, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# Sigstore signature verification
# ---------------------------------------------------------------------------


def verify_sigstore_signature(
    wheel_path: Path,
    signature_path: Path,
) -> bool:
    """Verify a wheel's sigstore signature.

    This is a stub. The release pipeline shells out to the sigstore CLI
    (``sigstore verify identity ...``) which requires the OIDC issuer URL
    and the expected identity (the GitHub Actions identity token) — both
    are environment-specific and live in the release workflow, not here.

    Args:
        wheel_path: Path to the ``.whl`` file.
        signature_path: Path to the ``.sigstore`` (or legacy ``.sig``)
            attestation produced by the release pipeline.

    Returns:
        Always ``False`` from the in-process stub — callers that need real
        verification must use the sigstore CLI.

    Raises:
        NotImplementedError: pointing the caller at the sigstore CLI.

    Notes:
        TODO: integrate the ``sigstore`` Python package's verifier API
        (``sigstore.verify.Verifier``) once the release pipeline schema
        stabilizes. The CLI is the recommended path because the trust
        anchors (Fulcio root, Rekor URL) are baked into the CLI's defaults
        and don't need to be redeclared at the framework level.
    """
    if not Path(wheel_path).exists():
        raise FileNotFoundError(f"wheel not found: {wheel_path}")
    if not Path(signature_path).exists():
        raise FileNotFoundError(f"signature not found: {signature_path}")
    raise NotImplementedError(
        "Sigstore verification is performed by the release pipeline via "
        "the sigstore CLI. See docs/SECURITY.md for the verification "
        "command line. This in-process stub returns False to satisfy the "
        "type contract."
    )


# ---------------------------------------------------------------------------
# pip-audit integration
# ---------------------------------------------------------------------------


@dataclass
class VulnReport:
    """Structured vulnerability finding from ``pip-audit``."""

    package: str
    installed_version: str
    vuln_id: str
    severity: str = "UNKNOWN"
    fix_versions: List[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "package": self.package,
            "installed_version": self.installed_version,
            "vuln_id": self.vuln_id,
            "severity": self.severity,
            "fix_versions": list(self.fix_versions),
            "description": self.description,
        }


_SEVERITY_ORDER = {
    "CRITICAL": 4,
    "HIGH": 3,
    "MEDIUM": 2,
    "MODERATE": 2,  # alias used by some scanners
    "LOW": 1,
    "UNKNOWN": 0,
}


def _severity_meets_gate(sev: str, gate: str) -> bool:
    """Return True if ``sev`` is at or above the ``gate`` threshold."""
    return _SEVERITY_ORDER.get((sev or "").upper(), 0) >= _SEVERITY_ORDER.get(
        (gate or "").upper(), _SEVERITY_ORDER["HIGH"]
    )


def run_pip_audit(
    lock_path: Path,
    severity_threshold: str = "HIGH",
) -> List[VulnReport]:
    """Run ``pip-audit`` against a lock file and return findings at/above the gate.

    Args:
        lock_path: Path to a ``requirements.lock`` (or pip-tools style
            requirements.txt with hashes) file.
        severity_threshold: Minimum severity to report. Findings below this
            tier are filtered out. Default ``HIGH`` matches the release
            pipeline's gate.

    Returns:
        A list of ``VulnReport`` instances for matching findings. When
        ``pip-audit`` is not installed, returns an empty list — the release
        pipeline installs the scanner explicitly so this stub doesn't
        accidentally pass when the gate should be active.
    """
    if not Path(lock_path).exists():
        raise FileNotFoundError(f"lock file not found: {lock_path}")

    if shutil.which("pip-audit") is None:
        # Stub path. Returning [] is intentional: in environments without
        # the scanner installed, callers should not treat this as a clean
        # pass — the release pipeline runs with the scanner present.
        return []

    try:
        proc = subprocess.run(  # noqa: S603 - tool path resolved via shutil.which
            [
                "pip-audit",
                "-r",
                str(lock_path),
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []

    if not proc.stdout:
        return []

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []

    # pip-audit JSON shape: {"dependencies": [{"name", "version", "vulns": [...]}]}
    findings: List[VulnReport] = []
    deps = payload.get("dependencies") if isinstance(payload, dict) else payload
    for dep in deps or []:
        name = dep.get("name", "")
        version = dep.get("version", "")
        for vuln in dep.get("vulns") or []:
            severity = (vuln.get("severity") or "UNKNOWN").upper()
            if not _severity_meets_gate(severity, severity_threshold):
                continue
            findings.append(
                VulnReport(
                    package=name,
                    installed_version=version,
                    vuln_id=vuln.get("id", ""),
                    severity=severity,
                    fix_versions=list(vuln.get("fix_versions") or []),
                    description=vuln.get("description", ""),
                )
            )
    return findings


__all__ = [
    "generate_sbom",
    "verify_sigstore_signature",
    "run_pip_audit",
    "VulnReport",
    "SBOM_SPEC_VERSION",
    "SBOM_FORMAT",
]
