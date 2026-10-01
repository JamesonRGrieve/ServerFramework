# SPDX-License-Identifier: AGPL-3.0-or-later
"""``pip install zephyrex`` installs everything core imports.

Found missing when dependencies were last upgraded: core imported faker (a
development dependency), defusedxml (declared nowhere) and filelock (present
only because tldextract happens to need it), so a plain install could not
boot. Extensions have their own check, ``Extension_imports_declared_test``.
"""

import ast
import re
import sys
import tomllib
from importlib.metadata import packages_distributions, requires
from pathlib import Path
from typing import Dict, Set

from packaging.requirements import Requirement

PACKAGE = Path(__file__).resolve().parent
PYPROJECT = PACKAGE.parents[1] / "pyproject.toml"
_FIRST_PARTY = ("zephyrex", "sdk")

# Modules only tests and tooling import: the test bases consumers subclass,
# their doubles and fixtures, and the mypy plugin.
_DEVELOPMENT_ONLY = re.compile(
    r"(^|/)(testing/.*|.*Test\.py|RotationTestDoubles\.py|mypy_plugin\.py|conftest\.py)$"
)


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _core_imports() -> Dict[str, Set[str]]:
    """Each third-party top-level import of core runtime code, with the
    modules that import it."""
    found: Dict[str, Set[str]] = {}
    for source in PACKAGE.rglob("*.py"):
        relative = source.relative_to(PACKAGE).as_posix()
        if (
            relative.startswith("extensions/")
            or source.name.endswith("_test.py")
            or _DEVELOPMENT_ONLY.search(relative)
        ):
            continue
        for node in ast.walk(ast.parse(source.read_text())):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            else:
                continue
            for name in names:
                if name not in sys.stdlib_module_names and name not in _FIRST_PARTY:
                    found.setdefault(name, set()).add(relative)
    return found


def _installed_by_core() -> Set[str]:
    """Every distribution ``pip install zephyrex`` (or one of core's own
    extras) installs directly, and each of those's own requirements."""
    project = tomllib.loads(PYPROJECT.read_text())["project"]
    extras = project["optional-dependencies"]
    core = list(project["dependencies"])
    for extra in ("cache", "mcp"):
        core += extras[extra]
    installed: Set[str] = set()
    for line in core:
        requirement = Requirement(line)
        installed.add(_normalized(requirement.name))
        for own in requires(requirement.name) or []:
            dependency = Requirement(own)
            wanted = [{"extra": extra} for extra in requirement.extras] or [
                {"extra": ""}
            ]
            if dependency.marker is None or any(
                dependency.marker.evaluate(environment) for environment in wanted
            ):
                installed.add(_normalized(dependency.name))
    return installed


def test_every_core_import_is_installed_by_core():
    distributions = packages_distributions()
    installed = _installed_by_core()
    undeclared = sorted(
        f"{name} ({', '.join(sorted(modules))})"
        for name, modules in _core_imports().items()
        if not {_normalized(d) for d in distributions.get(name, [name])} & installed
    )
    assert undeclared == []


def test_the_development_only_modules_exist():
    """A rename must not silently widen what the check skips."""
    for module in (
        "AbstractTest.py",
        "logic/RotationTestDoubles.py",
        "mypy_plugin.py",
        "testing/fixtures.py",
    ):
        assert (PACKAGE / module).is_file(), module
