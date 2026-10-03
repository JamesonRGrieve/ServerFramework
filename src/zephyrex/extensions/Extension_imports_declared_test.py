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

# Import names that differ from the distribution that provides them.
_DISTRIBUTION_OF = {
    "google": "google-auth",
    "googleapiclient": "google-api-python-client",
    "influxdb_client": "influxdb-client",
    "llama_cpp": "llama-cpp-python",
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
