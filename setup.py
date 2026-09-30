# SPDX-License-Identifier: AGPL-3.0-or-later
"""Packaging hook pyproject.toml cannot express: tests live beside the modules
they test (``*_test.py``, plus pytest's ``conftest.py``), and the wheel ships
the modules without them. The sdist keeps them. Everything else is configured
in pyproject.toml."""

from setuptools import setup
from setuptools.command.build_py import build_py

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


if __name__ == "__main__":  # how setuptools' build backend runs this file
    setup(cmdclass={"build_py": BuildWithoutTests})
