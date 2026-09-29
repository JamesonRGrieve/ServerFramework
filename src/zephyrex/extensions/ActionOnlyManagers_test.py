# SPDX-License-Identifier: AGPL-3.0-or-later
"""Action-only managers (magic link, device pairing) in one app.

Both declare a ``request_route``. Custom routes used to register GraphQL
fields under the bare method name, so loading both extensions failed the
app build on a field collision; and every hooked manager with
``routes_to_register = []`` silently grew a ``PUT /{id}`` because the hook
wrapper puts ``update`` in the class ``__dict__``.
"""

import os

import pytest
from fastapi.routing import iter_route_contexts

os.environ.setdefault("JWT_SECRET", "test-jwt-secret-32-bytes-or-more-aaaaaa")


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from zephyrex.app import instance
    from zephyrex.pydantic2.sqlalchemy import prepare_test_registry

    tmp = tmp_path_factory.mktemp("action_only")
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    with pytest.MonkeyPatch.context() as env_patch:
        env_patch.setenv("DATABASE_TYPE", "sqlite")
        env_patch.setenv("SEED_DATA", "false")
        env_patch.setenv("DATABASE_NAME", f"action_only_{worker}_{os.getpid()}")
        env_patch.setenv("DATABASE_PATH", str(tmp))
        prepare_test_registry()
        yield instance(
            extensions="auth_magic_link,auth_device_pairing",
            db_prefix=f"action_only.{worker}.{os.getpid()}",
        )


def _routes(app, prefix):
    found = set()
    for context in iter_route_contexts(app.routes):
        path = getattr(context.route, "path", "")
        if path.startswith(prefix):
            for method in getattr(context.route, "methods", None) or ():
                found.add((method, path))
    return found


def test_magic_link_mounts_only_its_declared_routes(app):
    assert _routes(app, "/v1/auth/magic-link") == {
        ("POST", "/v1/auth/magic-link/request"),
        ("POST", "/v1/auth/magic-link/verify"),
    }


def test_device_pairing_mounts_only_its_declared_routes(app):
    assert _routes(app, "/v1/auth/pairing") == {
        ("POST", "/v1/auth/pairing/request"),
        ("POST", "/v1/auth/pairing/approve"),
        ("POST", "/v1/auth/pairing/deny"),
        ("GET", "/v1/auth/pairing/{pairing_id}/status"),
    }


def test_each_custom_route_has_its_own_graphql_field(app):
    schema = str(app.state.model_registry.gql)
    assert "magicLinkRequest" in schema
    assert "devicePairingRequest" in schema
