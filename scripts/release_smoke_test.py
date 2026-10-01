# SPDX-License-Identifier: AGPL-3.0-or-later
import importlib.util
from pathlib import Path

import pytest

import zephyrex

_SPEC = importlib.util.spec_from_file_location(
    "release_smoke", Path(__file__).with_name("release_smoke.py")
)
assert _SPEC and _SPEC.loader
release_smoke = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(release_smoke)


@pytest.fixture
def installed(monkeypatch):
    """As if zephyrex were imported from an install, with a boot that passes
    and every extension requirement missing: which checks ran shows."""
    monkeypatch.setattr(
        zephyrex, "__file__", "/venv/lib/python3.11/site-packages/zephyrex/__init__.py"
    )
    monkeypatch.setattr(release_smoke, "boot_failures", lambda: [])
    monkeypatch.setattr(
        release_smoke, "missing_requirements", lambda: ["auth-mfa: pyotp"]
    )


def test_core_checks_only_that_plain_zephyrex_boots(installed, capsys):
    """A plain install has none of the extras, so it is held to booting."""
    assert release_smoke.main(["--core"]) == 0
    assert "release smoke: passed" in capsys.readouterr().out


def test_all_also_requires_every_extension_requirement(installed, capsys):
    assert release_smoke.main([]) == 1
    assert "FAIL auth-mfa: pyotp" in capsys.readouterr().out


def test_an_unknown_argument_is_a_usage_error(capsys):
    assert release_smoke.main(["--all"]) == 2
    assert "usage" in capsys.readouterr().out


def test_the_checkout_is_not_an_install(monkeypatch, capsys):
    monkeypatch.setattr(zephyrex, "__file__", "/src/zephyrex/__init__.py")
    assert release_smoke.main(["--core"]) == 1
    assert "not an install" in capsys.readouterr().out
