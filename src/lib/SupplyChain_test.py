"""Tests for ``lib.SupplyChain`` (Item 86).

Pure-utility tests of SBOM/sigstore/pip-audit primitives. Tagged
``@pytest.mark.unit`` per AGENTS.md — no BLL/extension surface in this
module, so no real-DB fixtures needed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.SupplyChain import (
    SBOM_FORMAT,
    SBOM_SPEC_VERSION,
    VulnReport,
    generate_sbom,
    run_pip_audit,
    verify_sigstore_signature,
)


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# generate_sbom
# ---------------------------------------------------------------------------


class TestGenerateSbom:
    def test_minimal_metadata_emits_valid_cyclonedx(self):
        out = generate_sbom({"name": "example", "version": "1.2.3", "components": []})
        doc = json.loads(out)
        assert doc["bomFormat"] == SBOM_FORMAT
        assert doc["specVersion"] == SBOM_SPEC_VERSION
        assert doc["metadata"]["component"]["name"] == "example"
        assert doc["metadata"]["component"]["version"] == "1.2.3"
        assert doc["components"] == []

    def test_components_are_emitted(self):
        out = generate_sbom(
            {
                "name": "sf",
                "version": "0.1.0",
                "components": [
                    {
                        "name": "fastapi",
                        "version": "0.110.0",
                        "purl": "pkg:pypi/fastapi@0.110.0",
                        "hashes": [{"alg": "SHA-256", "content": "abc123"}],
                        "licenses": ["MIT"],
                    },
                    {"name": "pydantic", "version": "2.6.0"},
                ],
            }
        )
        doc = json.loads(out)
        names = [c["name"] for c in doc["components"]]
        assert names == ["fastapi", "pydantic"]
        fastapi_entry = doc["components"][0]
        assert fastapi_entry["purl"] == "pkg:pypi/fastapi@0.110.0"
        assert fastapi_entry["hashes"][0]["content"] == "abc123"
        assert fastapi_entry["licenses"] == [{"license": {"id": "MIT"}}]

    def test_missing_components_key_is_treated_as_empty(self):
        out = generate_sbom({"name": "x", "version": "0.0.1"})
        doc = json.loads(out)
        assert doc["components"] == []

    def test_default_name_and_version(self):
        # Defensive: missing name/version should produce known sentinels rather
        # than raise — release pipelines often build SBOMs from sparse metadata.
        out = generate_sbom({"components": []})
        doc = json.loads(out)
        assert doc["metadata"]["component"]["name"] == "unknown"
        assert doc["metadata"]["component"]["version"] == "0.0.0"


# ---------------------------------------------------------------------------
# verify_sigstore_signature
# ---------------------------------------------------------------------------


class TestVerifySigstoreSignature:
    def test_missing_wheel_raises(self, tmp_path: Path):
        sig = tmp_path / "x.sigstore"
        sig.write_text("{}")
        with pytest.raises(FileNotFoundError):
            verify_sigstore_signature(tmp_path / "missing.whl", sig)

    def test_missing_signature_raises(self, tmp_path: Path):
        wheel = tmp_path / "x.whl"
        wheel.write_text("")
        with pytest.raises(FileNotFoundError):
            verify_sigstore_signature(wheel, tmp_path / "missing.sigstore")

    def test_stub_raises_not_implemented(self, tmp_path: Path):
        wheel = tmp_path / "x.whl"
        wheel.write_text("")
        sig = tmp_path / "x.sigstore"
        sig.write_text("{}")
        with pytest.raises(NotImplementedError, match="sigstore CLI"):
            verify_sigstore_signature(wheel, sig)


# ---------------------------------------------------------------------------
# run_pip_audit
# ---------------------------------------------------------------------------


class TestRunPipAudit:
    def test_missing_lock_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            run_pip_audit(tmp_path / "no.lock")

    def test_returns_empty_when_scanner_absent(self, tmp_path: Path, monkeypatch):
        # Force shutil.which to report the scanner as absent.
        import lib.SupplyChain as sc

        monkeypatch.setattr(sc.shutil, "which", lambda name: None)
        lock = tmp_path / "requirements.lock"
        lock.write_text("# empty lock\n")
        assert run_pip_audit(lock) == []


# ---------------------------------------------------------------------------
# VulnReport
# ---------------------------------------------------------------------------


class TestVulnReport:
    def test_round_trip_dict(self):
        v = VulnReport(
            package="urllib3",
            installed_version="2.0.0",
            vuln_id="GHSA-x",
            severity="HIGH",
            fix_versions=["2.0.7"],
            description="redirect handling",
        )
        d = v.to_dict()
        assert d["package"] == "urllib3"
        assert d["severity"] == "HIGH"
        assert d["fix_versions"] == ["2.0.7"]

    def test_default_fix_versions_is_independent(self):
        v1 = VulnReport(package="a", installed_version="0", vuln_id="x")
        v2 = VulnReport(package="b", installed_version="0", vuln_id="y")
        v1.fix_versions.append("1.0")
        assert v2.fix_versions == []
