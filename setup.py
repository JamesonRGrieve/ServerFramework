# SPDX-License-Identifier: AGPL-3.0-or-later
"""Packaging hooks pyproject.toml cannot express. Tests live beside the
modules they test (``*_test.py``, plus pytest's ``conftest.py``), and the
wheel ships the modules without them (the sdist keeps them). The build also
writes ``zephyrex/_provenance.json``: the digest of exactly what it ships and
the commit it was built from, which a running server checks itself against
(``zephyrex.lib.Provenance``). Everything else is configured in
pyproject.toml."""

import importlib.util
import json
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

SOURCE_TREE = Path(__file__).resolve().parent
PACKAGE = "zephyrex"


def _provenance_module():
    """``zephyrex.lib.Provenance`` loaded on its own: the build environment
    does not have the package's dependencies."""
    path = SOURCE_TREE / "src" / PACKAGE / "lib" / "Provenance.py"
    spec = importlib.util.spec_from_file_location("zephyrex_provenance", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TEST_MODULE_SUFFIX = "_test"
PYTEST_CONFIG_MODULE = "conftest"


def is_test_module(module: str) -> bool:
    return module.endswith(TEST_MODULE_SUFFIX) or module == PYTEST_CONFIG_MODULE


class BuildWithoutTests(build_py):
    def find_package_modules(self, package, package_dir):
        return [
            (pkg, module, path)
            for pkg, module, path in super().find_package_modules(package, package_dir)
            if not is_test_module(module)
        ]

    def run(self):
        super().run()
        provenance = _provenance_module()
        shipped = Path(self.build_lib) / PACKAGE
        manifest = provenance.build_manifest(shipped, SOURCE_TREE)
        (shipped / provenance.MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2) + "\n"
        )


if __name__ == "__main__":  # how setuptools' build backend runs this file
    setup(cmdclass={"build_py": BuildWithoutTests})
