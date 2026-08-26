# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the auth_oauth2_client SSO flow.

Covers the manager verbs (providers / connect / callback / connections /
disconnect) with the external-IdP adapter mocked, plus an endpoint mount smoke
test proving the custom_routes are reachable over HTTP.
"""

import os

import pytest
from fastapi import HTTPException

os.environ.setdefault("JWT_SECRET", "test-jwt-secret-32-bytes-or-more-aaaaaa")
os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("SEED_DATA", "false")


@pytest.fixture(scope="module")
def client_registry(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("oauth2_client_bll")
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    os.environ["DATABASE_NAME"] = f"oauth2_client_bll_{worker}_{os.getpid()}"
    os.environ["DATABASE_PATH"] = str(tmp)
    from zephyrex.app import instance

    app = instance(
        extensions="auth_oauth2_client", db_prefix=f"oauth2cli.{worker}.{os.getpid()}"
    )
    return app.state.model_registry


def _mgr(registry):
    from zephyrex.extensions.auth_oauth2_client.BLL_Auth_OAuth2Client import (
        UserOAuthManager,
    )
    from zephyrex.lib.Environment import env

    return UserOAuthManager(requester_id=env("ROOT_ID"), model_registry=registry)


class _FakeInstance:
    access_token = "ext-access-token"
    refresh_token = "ext-refresh-token"

    def get_user_info(self):
        return {
            "email": "sso.user@example.com",
            "id": "provider-uid-123",
            "first_name": "Sso",
            "last_name": "User",
        }


def _fake_adapter(returns_instance=True):
    inst = _FakeInstance() if returns_instance else None
    return type(
        "FakeAdapter",
        (),
        {"sso_handler": classmethod(lambda cls, code, redirect_uri=None: inst)},
    )


class TestUserOAuthSSO:
    def test_providers_lists_builtins(self, client_registry):
        result = _mgr(client_registry).providers_route()
        names = {p["name"] for p in result["providers"]}
        assert {"google", "github", "microsoft", "amazon"} <= names

    def test_connect_returns_authorize_url(self, client_registry, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
        monkeypatch.setenv("APP_URI", "https://app.example.com/cb")
        result = _mgr(client_registry).connect_route("google")
        url = result["authorize_url"]
        assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
        assert "client_id=test-client-id" in url
        assert "response_type=code" in url

    def test_connect_unknown_provider_is_404(self, client_registry):
        with pytest.raises(HTTPException) as exc:
            _mgr(client_registry).connect_route("nonesuch")
        assert exc.value.status_code == 404

    def test_callback_missing_code_is_400(self, client_registry):
        with pytest.raises(HTTPException) as exc:
            _mgr(client_registry).callback_route("google", {})
        assert exc.value.status_code == 400

    def test_callback_links_then_disconnect_removes(self, client_registry, monkeypatch):
        from zephyrex.extensions.auth_oauth2_client import (
            BLL_Auth_OAuth2Client as bll,
        )

        monkeypatch.setitem(bll.PROVIDER_REGISTRY, "github", _fake_adapter())
        mgr = _mgr(client_registry)

        result = mgr.callback_route("github", {"code": "auth-code"})
        assert result["linked"] is True
        assert result["email"] == "sso.user@example.com"

        conns = mgr.connections_route()["connections"]
        github = [c for c in conns if c["provider"] == "github"]
        assert len(github) == 1
        assert github[0]["account_email"] == "sso.user@example.com"
        # tokens must not leak through the connections view
        assert "access_token" not in github[0]

        # A second callback updates rather than duplicates the link.
        mgr.callback_route("github", {"code": "auth-code-2"})
        github = [
            c
            for c in mgr.connections_route()["connections"]
            if c["provider"] == "github"
        ]
        assert len(github) == 1

        mgr.disconnect_route("github")
        assert not [
            c
            for c in mgr.connections_route()["connections"]
            if c["provider"] == "github"
        ]

    def test_callback_provider_exchange_failure_is_401(
        self, client_registry, monkeypatch
    ):
        from zephyrex.extensions.auth_oauth2_client import (
            BLL_Auth_OAuth2Client as bll,
        )

        monkeypatch.setitem(
            bll.PROVIDER_REGISTRY, "amazon", _fake_adapter(returns_instance=False)
        )
        with pytest.raises(HTTPException) as exc:
            _mgr(client_registry).callback_route("amazon", {"code": "bad"})
        assert exc.value.status_code == 401


class TestOAuth2ClientEndpointsMount:
    def test_providers_endpoint_is_mounted(self, tmp_path_factory):
        """Smoke test: the custom_routes actually mount + require auth."""
        from fastapi.testclient import TestClient

        from zephyrex.lib.Environment import refresh_settings
        from zephyrex.testing.fixtures import prepare_test_registry

        prepare_test_registry()
        refresh_settings()
        worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
        from zephyrex.app import instance

        companions = (
            "metadata",
            "auth_lockout",
            "auth_recovery_questions",
            "auth_invitations",
            "auth_session",
            "acl_rbac",
        )
        app = instance(
            db_prefix=f"test.oauth2cli.{worker}",
            extensions=",".join(["auth_oauth2_client", *companions]),
        )
        client = TestClient(app)
        from zephyrex.testing.factories import make_admin_a

        admin = make_admin_a(client)

        # Authenticated -> 200 with the provider list.
        r = client.get(
            "/v1/oauth2_client/providers",
            headers={"Authorization": f"Bearer {admin.jwt}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        payload = body.get("data", body)
        names = {p["name"] for p in payload["providers"]}
        assert "google" in names

        # Unauthenticated -> rejected (route exists + is guarded, not a 404).
        r = client.get("/v1/oauth2_client/providers")
        assert r.status_code in (401, 403), r.text
