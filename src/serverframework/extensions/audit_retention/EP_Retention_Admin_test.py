"""Tests for :mod:`endpoints.EP_Retention_Admin` (Item 56 — task 2)."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest
from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient

from serverframework.extensions.audit_retention.EP_Retention_Admin import (
    create_retention_admin_router,
    resolve_retention_service_from_registry,
)
from serverframework.extensions.audit_retention.BLL_RetentionService import RetentionService


class _FakePrincipal:
    def __init__(self, user_id: str) -> None:
        self.user_id = user_id


def _make_app(
    retention_service: RetentionService | None = None,
    *,
    admin_principal: Any = None,
    raise_unauthenticated: bool = False,
) -> TestClient:
    def admin_check() -> Any:
        if raise_unauthenticated:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="not authed"
            )
        return admin_principal

    app = FastAPI()
    router = create_retention_admin_router(
        retention_service=retention_service,
        admin_check=admin_check,
    )
    app.include_router(router)
    return TestClient(app)


def test_post_release_requires_admin_returns_401_when_unauthenticated():
    svc = RetentionService(registrations=[])
    client = _make_app(svc, raise_unauthenticated=True)

    resp = client.post(
        "/admin/retention/legal-hold/release",
        json={"registration_name": "audit_login", "reason": "test"},
    )

    assert resp.status_code == 401


def test_post_release_returns_200_with_event_id_on_success():
    captured: List[Dict[str, Any]] = []
    svc = RetentionService(registrations=[], audit_emit=captured.append)
    client = _make_app(svc, admin_principal=_FakePrincipal("admin-42"))

    resp = client.post(
        "/admin/retention/legal-hold/release",
        json={"registration_name": "audit_login", "reason": "case closed"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "released"
    assert body["registration_name"] == "audit_login"
    assert isinstance(body["event_id"], str) and body["event_id"]
    assert svc.is_released("audit_login")
    assert len(captured) == 1
    assert captured[0]["event_class"] == "retention_legal_hold_release"
    assert captured[0]["requester_id"] == "admin-42"
    assert captured[0]["reason"] == "case closed"


def test_get_release_lists_released_registrations():
    svc = RetentionService(registrations=[])
    svc.release_legal_hold("a", reason="x", requester_id="op")
    svc.release_legal_hold("b", reason="y", requester_id="op")
    client = _make_app(svc, admin_principal=_FakePrincipal("admin-1"))

    resp = client.get("/admin/retention/legal-hold/release")

    assert resp.status_code == 200
    body = resp.json()
    assert sorted(body["released"]) == ["a", "b"]
    assert body["total"] == 2


def test_delete_release_reengages_hold():
    svc = RetentionService(registrations=[])
    svc.release_legal_hold("audit_x", reason="r", requester_id="op")
    client = _make_app(svc, admin_principal=_FakePrincipal("admin-1"))

    resp = client.delete("/admin/retention/legal-hold/release/audit_x")

    assert resp.status_code == 200
    assert resp.json()["status"] == "reengaged"
    assert not svc.is_released("audit_x")


def test_delete_release_404_when_not_released():
    svc = RetentionService(registrations=[])
    client = _make_app(svc, admin_principal=_FakePrincipal("admin-1"))

    resp = client.delete("/admin/retention/legal-hold/release/nope")

    assert resp.status_code == 404


def test_post_release_validates_body():
    svc = RetentionService(registrations=[])
    client = _make_app(svc, admin_principal=_FakePrincipal("admin-1"))

    resp = client.post(
        "/admin/retention/legal-hold/release",
        json={"registration_name": "", "reason": ""},
    )

    assert resp.status_code in (400, 422)


def test_resolver_pulls_service_from_registry():
    """Smoke test for ``resolve_retention_service_from_registry``."""
    from serverframework.extensions.audit_retention.BLL_RetentionService import (
        make_retention_scheduled_service,
    )
    from serverframework.logic.AbstractService import ServiceRegistry

    scheduled = make_retention_scheduled_service(
        service_id="retention-admin-resolver-test",
        interval_seconds=86400,
        registrations=[],
    )
    ServiceRegistry.register("retention-admin-resolver-test", scheduled)
    try:
        resolver = resolve_retention_service_from_registry(
            "retention-admin-resolver-test"
        )
        resolved = resolver()
        assert isinstance(resolved, RetentionService)
        assert resolved is scheduled.retention_service
    finally:
        ServiceRegistry._services.pop("retention-admin-resolver-test", None)


def test_factory_requires_service_or_resolver():
    with pytest.raises(ValueError):
        create_retention_admin_router()


def test_router_with_resolver_returns_503_when_unresolved():
    def resolver():
        return None

    app = FastAPI()
    router = create_retention_admin_router(
        retention_service_resolver=resolver,
        admin_check=lambda: _FakePrincipal("admin-1"),
    )
    app.include_router(router)
    client = TestClient(app)

    resp = client.get("/admin/retention/legal-hold/release")

    assert resp.status_code == 503
