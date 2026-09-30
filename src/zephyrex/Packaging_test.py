# SPDX-License-Identifier: AGPL-3.0-or-later
"""The wheel ships the framework without its tests.

Tests live beside the modules they test, and setuptools' package discovery
cannot exclude single modules, so the built wheel carried all 242 of them
(and pytest's conftest.py files) until setup.py filtered them out of
build_py.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SETUP = Path(__file__).resolve().parents[2] / "setup.py"


@pytest.fixture(scope="module")
def setup_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("zephyrex_setup", _SETUP)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "module, excluded",
    [
        ("BLL_Auth_test", True),
        ("conftest", True),
        ("BLL_Auth", False),
        ("AbstractEXTTest", False),
        ("testing", False),
    ],
)
def test_only_test_modules_are_left_out(setup_module, module, excluded):
    assert setup_module.is_test_module(module) is excluded


def test_build_py_drops_test_modules(setup_module, tmp_path):
    from setuptools import Distribution

    package = tmp_path / "pkg"
    package.mkdir()
    for name in ("__init__", "core", "core_test", "conftest"):
        (package / f"{name}.py").write_text("")
    distribution = Distribution({"packages": ["pkg"], "script_name": "setup.py"})
    command = setup_module.BuildWithoutTests(distribution)

    modules = command.find_package_modules("pkg", str(package))
    assert sorted(module for _, module, _ in modules) == ["__init__", "core"]
