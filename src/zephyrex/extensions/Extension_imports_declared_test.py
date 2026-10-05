# SPDX-License-Identifier: AGPL-3.0-or-later
"""``pip install zephyrex[<extension>]`` installs everything the extension
imports, and the manifest and extra say the same as the code.

Found missing when extras were introduced: observability declared none of
its four backends, payment's Square SDK range admitted the incompatible v42,
and database, fileio and auth_oauth2_client each imported an undeclared
package.
"""

import ast
import re
import sys
from pathlib import Path
from typing import Dict, List, Set

import pytest

from zephyrex.extensions.sync_dependencies import (
    declared_requirements,
    drift,
    extension_folders,
)

_RELEASE_CHECK_TIMEOUT_SECONDS = 120

# Import names that differ from the distribution that provides them.
_DISTRIBUTION_OF = {
    "azure": "azure-storage-blob",
    "google": "google-auth",
    "googleapiclient": "google-api-python-client",
    "influxdb_client": "influxdb-client",
    "llama_cpp": "llama-cpp-python",
    "markdown_it": "markdown-it-py",
    "mysql": "mysql-connector-python",
    "opentelemetry": "opentelemetry-api",
    "prometheus_client": "prometheus-client",
    "psycopg2": "psycopg2-binary",
    "saml2": "pysaml2",
    "sentry_sdk": "sentry-sdk",
    "square": "squareup",
}
# Provided to every extension by core's own dependencies.
_CORE_IMPORTS = frozenset(
    {
        "aiosqlite",
        "alembic",
        "anyio",
        "bcrypt",
        "broadcaster",
        "colorama",
        "cryptography",
        "defusedxml",
        "distro",
        "dotenv",
        "email_validator",
        "fastapi",
        "graphql",
        "greenlet",
        "httpx",
        "inflect",
        "jwt",
        "loguru",
        "networkx",
        "numpy",
        "ordered_set",
        "pydantic",
        "pytest",
        "resolvelib",
        "semver",
        "sqlalchemy",
        "starlette",
        "strawberry",
        "stringcase",
        "tldextract",
        "tomli_w",
        "toon_format",
        "typing_extensions",
        "uvicorn",
        "yaml",
    }
)
_FOLDERS = extension_folders()


@pytest.fixture(scope="module")
def requirements() -> Dict[str, List[str]]:
    return declared_requirements()


def _distribution(requirement: str) -> str:
    return re.split(r"[<>=!~\[;\s]", requirement, maxsplit=1)[0].lower()


def _third_party_imports(folder: Path) -> Set[str]:
    found: Set[str] = set()
    for source in folder.rglob("*.py"):
        if source.name.endswith("_test.py") or "migrations" in source.parts:
            continue
        for node in ast.walk(ast.parse(source.read_text())):
            if isinstance(node, ast.Import):
                found.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return {
        name
        for name in found
        if name not in sys.stdlib_module_names
        and name not in _CORE_IMPORTS
        and name not in ("zephyrex", "extensions", "sdk")
        and not (folder / f"{name}.py").exists()
        and not (folder / name).is_dir()
    }


@pytest.mark.parametrize("folder", _FOLDERS, ids=[f.name for f in _FOLDERS])
def test_every_imported_package_is_declared(folder: Path, requirements) -> None:
    declared = {_distribution(r) for r in requirements[folder.name]}
    undeclared = sorted(
        name
        for name in _third_party_imports(folder)
        if _DISTRIBUTION_OF.get(name, name).replace("_", "-") not in declared
    )
    assert undeclared == []


def test_manifests_and_extras_match_the_code() -> None:
    """Run ``python -m zephyrex.extensions.sync_dependencies`` to fix."""
    assert [str(path) for path, _ in drift()] == []


_RELEASE_CHECK = """
import sys
from zephyrex.extensions.sync_dependencies import main
code = main(["--check", "--from-manifests"])
loaded = sorted(
    name for name in sys.modules
    if name.startswith("zephyrex.extensions.") and name.count(".") >= 3
)
print(code, loaded)
"""


def test_the_release_check_imports_no_extension() -> None:
    """A release checks the extras on a core-only install, where an
    extension's optional packages (croniter for ai_agents) are absent: the
    check read the code, imported every extension, and failed on the first.
    It holds pyproject's extras to the manifests without importing one."""
    import subprocess

    done = subprocess.run(
        [sys.executable, "-c", _RELEASE_CHECK],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=_RELEASE_CHECK_TIMEOUT_SECONDS,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().splitlines()[-1] == "0 []"
