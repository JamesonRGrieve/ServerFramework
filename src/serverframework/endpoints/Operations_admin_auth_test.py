"""C-1 — ``/admin/*`` routes must enforce explicit authentication.

The previous implementation relied on ``@rate_limit(scope="user")`` to
imply auth, but the rate-limit middleware skips enforcement when the
actor cannot be resolved — so an anonymous request fell through. Each
admin route now declares ``Depends(_require_admin_principal)`` which
accepts ROOT/SYSTEM/TEMPLATE API keys (constant-time match) or a JWT-
derived RequestContext user resolved to ROOT/SYSTEM.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _admin_env(monkeypatch):
    """The framework's ``env()`` reads from a cached ``AppSettings``
    snapshot taken at import time, so ``monkeypatch.setenv`` alone does
    not propagate. Patch both the os-environ and the cached settings
    object so the API-key comparison and the principal-id resolution
    both see the test values."""
    from serverframework.lib import Environment as _env_mod

    overrides = {
        "ROOT_API_KEY": "root-secret-1234567890",
        "SYSTEM_API_KEY": "system-secret-1234567890",
        "TEMPLATE_API_KEY": "template-secret-1234567890",
        "ROOT_ID": "00000000-0000-0000-0000-0000000000aa",
        "SYSTEM_ID": "00000000-0000-0000-0000-0000000000bb",
        "TEMPLATE_ID": "00000000-0000-0000-0000-0000000000cc",
    }
    for k, v in overrides.items():
        monkeypatch.setenv(k, v)
        if hasattr(_env_mod.settings, k):
            monkeypatch.setattr(_env_mod.settings, k, v, raising=False)
    yield


def _build_router_and_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from serverframework.endpoints.Operations import create_operations_router

    app = FastAPI()
    router = create_operations_router(
        dlq_lister=lambda **kw: [],
        dlq_replay=lambda eid: True,
        dlq_discard=lambda eid: True,
        failed_services_lister=lambda: [],
        service_reset=lambda name: True,
    )
    app.include_router(router)
    return TestClient(app)


def test_admin_dlq_anonymous_is_401():
    """Anonymous request to /admin/dlq returns 401, not 200 (the bug)."""
    client = _build_router_and_client()
    resp = client.get("/admin/dlq")
    assert resp.status_code == 401


def test_admin_dlq_with_invalid_api_key_is_401():
    client = _build_router_and_client()
    resp = client.get(
        "/admin/dlq",
        headers={"X-API-Key": "wrong-key"},
    )
    assert resp.status_code == 401


def test_admin_dlq_with_root_api_key_is_200():
    client = _build_router_and_client()
    resp = client.get(
        "/admin/dlq",
        headers={"X-API-Key": "root-secret-1234567890"},
    )
    assert resp.status_code == 200


def test_admin_dlq_replay_anonymous_is_401():
    client = _build_router_and_client()
    resp = client.post("/admin/dlq/abc/replay")
    assert resp.status_code == 401


def test_admin_dlq_replay_with_root_api_key_is_200():
    client = _build_router_and_client()
    resp = client.post(
        "/admin/dlq/abc/replay",
        headers={"X-API-Key": "root-secret-1234567890"},
    )
    assert resp.status_code == 200


def test_admin_services_reset_anonymous_is_401():
    client = _build_router_and_client()
    resp = client.post("/admin/services/foo/reset")
    assert resp.status_code == 401


def test_admin_services_reset_with_system_api_key_is_200():
    client = _build_router_and_client()
    resp = client.post(
        "/admin/services/foo/reset",
        headers={"X-API-Key": "system-secret-1234567890"},
    )
    assert resp.status_code == 200


def test_healthz_does_not_require_auth():
    """Liveness must remain unauthenticated — load balancers cannot
    present credentials, and this is a deliberate carve-out."""
    client = _build_router_and_client()
    resp = client.get("/healthz")
    assert resp.status_code == 200


def test_admin_bearer_jwt_shaped_falls_through_to_jwt_path():
    """When the bearer value is JWT-shaped (two dots), the dependency
    does NOT treat it as an API key — it falls through to the JWT
    middleware path. With no JWT middleware configured here, the
    RequestContext user is empty so we get 401."""
    client = _build_router_and_client()
    resp = client.get(
        "/admin/dlq",
        headers={"Authorization": "Bearer aaa.bbb.ccc"},
    )
    assert resp.status_code == 401
