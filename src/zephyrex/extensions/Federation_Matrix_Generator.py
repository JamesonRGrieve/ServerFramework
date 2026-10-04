# SPDX-License-Identifier: AGPL-3.0-or-later
"""Programmatic federation-matrix test generator (Item 16).

For every federation fixture a bundled extension ships, this module
generates a concrete :class:`AbstractFederationMatrixTest` subclass at
import time. Pytest collects the generated classes alongside hand-written
tests, so adding a new external upstream automatically buys you 4 quadrants
× 5 CRUD = 20 cells of homologation coverage.

How discovery works:

* Every bundled extension that ships a test-only
  ``federation_fixtures_test`` module contributes its
  ``federation_matrix_fixtures()`` (canned seed data stays out of the
  extension's production code). Payment's and email's REST providers do.
* The fixtures are turned into pytest classes named
  ``Test_Federation_<extension>_<type>_Matrix`` and inserted into the
  caller's module ``__dict__``.

Two execution modes:

* In-process upstreams (default). Each fixture module builds a tiny ASGI
  app serving its seed data as a deterministic upstream. This is the
  canonical CI path.
* Live upstreams. If the fixture's ``requires_credentials`` is True and
  ``credentials_present()`` returns True, the matrix runs against the real
  upstream URL; otherwise pytest auto-xfails the suite (per
  ``EXT.Test.External.md``).
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from zephyrex.extensions.AbstractFederationMatrixTest import (
    AbstractFederationMatrixTest,
    FederationFixture,
)

# The test-only module in which a bundled extension ships its fixtures.
TEST_ONLY_FIXTURES_MODULE = "federation_fixtures_test"

# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def bundled_fixture_modules() -> List[FederationFixture]:
    """Every bundled extension's ``federation_fixtures_test`` module's
    ``federation_matrix_fixtures()``, in extension-name order.

    Canned upstream seed data is test data: an extension ships it in this
    test-only module (never packaged with its code) rather than on its
    extension class. A module that fails to import or build its fixtures
    fails collection: a matrix that quietly generates nothing proves
    nothing."""
    import zephyrex.extensions as bundled

    fixtures: List[FederationFixture] = []
    module_files = sorted(
        path
        for root in bundled.__path__
        for path in Path(root).glob(f"*/{TEST_ONLY_FIXTURES_MODULE}.py")
    )
    for path in module_files:
        module = importlib.import_module(
            f"{bundled.__name__}.{path.parent.name}.{TEST_ONLY_FIXTURES_MODULE}"
        )
        fixtures.extend(module.federation_matrix_fixtures())
    return fixtures


# ---------------------------------------------------------------------------
# Test class generation
# ---------------------------------------------------------------------------


def generate_matrix_tests(
    *,
    fixtures: Optional[Iterable[FederationFixture]] = None,
    target_namespace: Optional[Dict[str, Any]] = None,
) -> Dict[str, type]:
    """Generate one :class:`AbstractFederationMatrixTest` subclass per fixture.

    ``fixtures`` defaults to the result of :func:`bundled_fixture_modules`,
    so ``generate_matrix_tests()`` with no args is the standard call site.
    ``target_namespace`` is the module ``__dict__`` to inject the generated
    classes into; in pytest this is typically the test file's globals so
    pytest's collection finds the classes automatically.

    Returns ``{class_name: class}`` for callers that prefer to inspect what
    was generated rather than rely on namespace injection.
    """

    fixtures = list(fixtures) if fixtures is not None else bundled_fixture_modules()
    out: Dict[str, type] = {}
    for fix in fixtures:
        cls_name = (
            "Test_Federation_"
            + fix.name.replace(".", "_").replace("-", "_")
            + "_Matrix"
        )
        cls = type(
            cls_name,
            (AbstractFederationMatrixTest,),
            {
                "_fixture_for_class": staticmethod(lambda f=fix: f),
                "fixture": (lambda self, f=fix: f),
            },
        )
        out[cls_name] = cls
        if target_namespace is not None:
            target_namespace[cls_name] = cls
    return out


__all__ = [
    "bundled_fixture_modules",
    "generate_matrix_tests",
]
