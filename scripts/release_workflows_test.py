# SPDX-License-Identifier: AGPL-3.0-or-later
"""The release and audit workflows keep the guarantees #249 and #251 fixed.

#249: pip-audit exits 1 both for vulnerabilities and on its own errors, and
the gate read an empty report from a failed run as clean. Both workflows now
pass pip-audit's exit status to the gate.

#251: TestPyPI took a re-tagged build with skip-existing, so the smoke test
ran against the old upload and PyPI got the new, never-smoked files; the
sdist was never smoke-tested. The workflow now publishes and smoke-tests the
one ``dist`` artifact, checks TestPyPI serves exactly those files, and
smoke-tests the sdist too."""

from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _load(name: str) -> Dict[str, Any]:
    loaded: Dict[str, Any] = yaml.safe_load((WORKFLOWS / name).read_text())
    return loaded


@pytest.fixture(scope="module")
def release() -> Dict[str, Any]:
    return _load("release.yml")


@pytest.fixture(scope="module")
def nightly() -> Dict[str, Any]:
    return _load("pip-audit-cron.yml")


def _steps(workflow: Dict[str, Any], job: str) -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = workflow["jobs"][job]["steps"]
    return steps


def _uses(step: Dict[str, Any], action: str) -> bool:
    return str(step.get("uses", "")).startswith(action)


def _downloads_dist(steps: List[Dict[str, Any]]) -> bool:
    return any(
        _uses(step, "actions/download-artifact")
        and step.get("with", {}).get("name") == "dist"
        for step in steps
    )


def _script(steps: List[Dict[str, Any]]) -> str:
    return "\n".join(str(step.get("run", "")) for step in steps)


class TestAuditGate:
    """#249."""

    def test_the_release_audit_passes_pip_audits_status(self, release):
        script = _script(_steps(release, "build"))
        assert "pip-audit" in script
        assert "|| status=$?" in script
        assert '--pip-audit-status "$status"' in script

    def test_the_nightly_audit_passes_pip_audits_status(self, nightly):
        [job] = nightly["jobs"].values()
        steps = job["steps"]
        audit = next(step for step in steps if step.get("id") == "audit")
        assert 'echo "rc=$rc"' in audit["run"]
        gate = next(step for step in steps if "audit_gate.py" in str(step.get("run")))
        assert "--pip-audit-status" in gate["run"]
        assert "steps.audit.outputs.rc" in gate["run"]


class TestReleaseSmokesWhatItPublishes:
    """#251."""

    def test_testpypi_does_not_skip_existing_files(self, release):
        [publish] = [
            step
            for step in _steps(release, "publish-testpypi")
            if _uses(step, "pypa/gh-action-pypi-publish")
        ]
        assert "skip-existing" not in publish.get("with", {})

    def test_every_publishing_job_uploads_the_dist_artifact(self, release):
        for job in ("publish-testpypi", "smoke-testpypi", "publish-pypi"):
            assert _downloads_dist(_steps(release, job)), job

    def test_pypi_waits_for_the_smoke_test(self, release):
        assert "smoke-testpypi" in release["jobs"]["publish-pypi"]["needs"]

    def test_the_smoke_test_checks_testpypi_serves_the_built_files(self, release):
        script = _script(_steps(release, "smoke-testpypi"))
        assert "hashlib.sha256" in script
        assert "if served != built" in script
        assert 'n.endswith(".tar.gz")' in script and 'n.endswith(".whl")' in script

    def test_the_wheel_and_sdist_smoked_are_the_artifacts_files(self, release):
        script = _script(_steps(release, "smoke-testpypi"))
        assert 'pip install "$(ls /tmp/dist/zephyrex-*.whl)"' in script
        assert 'pip install "$(ls /tmp/dist/zephyrex-*.tar.gz)"' in script
        assert "--index-url https://test.pypi.org" not in script
