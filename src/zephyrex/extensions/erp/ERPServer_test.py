# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ERP tests' server mixin, and a check that what an install ships for
testing against ERPNext imports from the install.

The mixin stands on the framework's test base, which needs the repository's
own conftest, so it lives here, in a test module the wheel does not ship;
ERPTestSupport (the site, its accounts, and instance factories) and
FrappeTestServer import from an install alone."""

import subprocess
import sys
from typing import Any, Iterator

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.erp.ERPTestSupport import erp_instance, standard_site
from zephyrex.extensions.erp.EXT_ERP import EXT_ERP
from zephyrex.extensions.erp.FrappeTestServer import FrappeServer
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

IMPORT_TIMEOUT_SECONDS = 120

# An interpreter without what only the repository has: its conftest, and
# pytest, which an install need not hold.
_INSTALL_LIKE_IMPORT = """
import importlib.abc
import sys

class Absent(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("conftest", "pytest", "_pytest"):
            raise ModuleNotFoundError(f"No module named {name!r}")
        return None

sys.meta_path.insert(0, Absent())
import zephyrex.extensions.erp.ERPTestSupport
import zephyrex.extensions.erp.FrappeTestServer
print("imported")
"""


class ERPServerMixin(ExtensionServerMixin):
    """The ERP extension's app (with webhooks and federation), and a Frappe
    site the SSRF guard lets it reach."""

    extension_class = EXT_ERP

    @pytest.fixture
    def frappe(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[FrappeServer]:
        with FrappeServer(standard_site()) as served:
            monkeypatch.setenv("EGRESS_ALLOWED_HOSTS", served.host)
            yield served

    @pytest.fixture
    def operator(
        self, model_registry: Any, frappe: FrappeServer
    ) -> ProviderInstanceModel:
        """The operator's instance, holding the operator account's key."""
        return erp_instance(model_registry, frappe.base_url)


def test_the_shipped_test_support_imports_without_the_repository():
    """ERPTestSupport held the server mixin, which imports the framework's
    test base and through it the repository's conftest: from 0.0.1a4's
    wheel it raised ModuleNotFoundError (found by the client's smoke)."""
    done = subprocess.run(
        [sys.executable, "-c", _INSTALL_LIKE_IMPORT],
        capture_output=True,
        text=True,
        timeout=IMPORT_TIMEOUT_SECONDS,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().splitlines()[-1] == "imported"
