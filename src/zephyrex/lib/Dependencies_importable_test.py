# SPDX-License-Identifier: AGPL-3.0-or-later
"""``importable``: whether a provider's optional SDK or driver can be used."""

import sys

import pytest

from zephyrex.lib.Dependencies import importable


def test_an_importable_module():
    assert importable("json")


def test_a_missing_module():
    assert not importable("zephyrex_no_such_driver")


def test_a_missing_submodule():
    assert not importable("json.zephyrex_no_such_submodule")


def test_installed_but_failing_to_load(tmp_path, monkeypatch):
    """pyodbc installs without unixODBC and then raises ImportError on
    import: present on disk, still not a usable driver."""
    (tmp_path / "zephyrex_broken_driver.py").write_text(
        "raise ImportError('libodbc.so.2: cannot open shared object file')\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "zephyrex_broken_driver", raising=False)
    assert not importable("zephyrex_broken_driver")


@pytest.fixture(autouse=True)
def _forget_test_modules(monkeypatch):
    yield
    sys.modules.pop("zephyrex_broken_driver", None)
