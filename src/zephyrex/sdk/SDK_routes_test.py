# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every route the Python SDK calls exists on a server with every
in-framework extension loaded: each resource's collection or item path,
and each hand-written ``_request`` (method and path). An SDK call to a route
the server does not serve fails at runtime with a 404 or 405, so drift
between the two fails here instead."""

import inspect
import os
import re
from pathlib import Path
from types import ModuleType
from typing import Iterator, List, Set, Tuple, Type

import pytest

from zephyrex.sdk import SDK_Auth, SDK_Extensions, SDK_Providers
from zephyrex.sdk.AbstractSDKHandler import AbstractSDKHandler

_SDK_MODULES = (SDK_Auth, SDK_Extensions, SDK_Providers)
_EXTENSIONS_DIR = Path(__file__).resolve().parents[1] / "extensions"
_PARAMETER = re.compile(r"\{([^}]*)\}")
# `self._request("VERB", "path")` or `self._request("VERB", f"path")`.
_RAW_REQUEST = re.compile(r'_request\(\s*"(\w+)",\s*f?"([^"]+)"')


def _placeholders(path: str) -> str:
    return _PARAMETER.sub("{}", path)


@pytest.fixture(scope="module")
def served() -> Set[Tuple[str, str]]:
    """(normalised path, METHOD) for every operation the app documents."""
    from zephyrex.app import instance
    from zephyrex.pydantic2.sqlalchemy import prepare_test_registry

    prepare_test_registry()
    extensions = sorted(
        entry.name
        for entry in _EXTENSIONS_DIR.iterdir()
        if entry.is_dir() and not entry.name.startswith("__")
    )
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    app = instance(
        db_prefix=f"test.sdkroutes.{worker}", extensions=",".join(extensions)
    )
    return {
        (_placeholders(path), method.upper())
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }


def _handlers(module: ModuleType) -> Iterator[Type[AbstractSDKHandler]]:
    for _, cls in inspect.getmembers(module, inspect.isclass):
        if issubclass(cls, AbstractSDKHandler) and cls.__module__ == module.__name__:
            yield cls


def _resource_endpoints() -> List[Tuple[str, str]]:
    endpoints = []
    for module in _SDK_MODULES:
        for cls in _handlers(module):
            # Configs are declarative: no connection needed to read them.
            handler = cls.__new__(cls)
            for key, config in handler._configure_resources().items():
                endpoints.append((f"{cls.__name__}.{key}", config.endpoint))
    return endpoints


def _raw_requests() -> List[Tuple[str, str, str]]:
    requests = []
    for module in _SDK_MODULES:
        constants = {
            name: value
            for name, value in vars(module).items()
            if name.isupper() or (name.startswith("_") and name[1:].isupper())
            if isinstance(value, str)
        }
        for cls in _handlers(module):
            for name, method in inspect.getmembers(cls, inspect.isfunction):
                if method.__module__ != module.__name__:
                    continue
                for verb, path in _RAW_REQUEST.findall(inspect.getsource(method)):
                    resolved = _PARAMETER.sub(
                        lambda m: constants.get(m.group(1), "{" + m.group(1) + "}"),
                        path,
                    )
                    requests.append((f"{cls.__name__}.{name}", verb.upper(), resolved))
    return requests


@pytest.mark.parametrize("where, endpoint", _resource_endpoints())
def test_resource_endpoints_are_served(served, where, endpoint):
    path = _placeholders(endpoint)
    paths = {served_path for served_path, _ in served}
    assert path in paths or f"{path}/{{}}" in paths, f"{where}: {endpoint}"


@pytest.mark.parametrize("where, verb, path", _raw_requests())
def test_raw_requests_are_served(served, where, verb, path):
    assert (_placeholders(path), verb) in served, f"{where}: {verb} {path}"
