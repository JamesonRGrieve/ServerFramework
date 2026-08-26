# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end tests for the auth_oauth2_server HTTP verbs.

Covers the authorization-code + PKCE flow, introspection/revocation, refresh
rotation, and the security invariants: PKCE required (S256) for public clients,
constant-time client-secret check (wrong secret -> 401), single-use codes, and
wrong-verifier rejection.
"""

import base64
import hashlib
import json
import os
import secrets

import pytest

os.environ.setdefault("JWT_SECRET", "test-jwt-secret-32-bytes-or-more-aaaaaa")
os.environ.setdefault("DATABASE_TYPE", "sqlite")


def _pkce_pair():
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    return verifier, challenge


def _body(resp):
    """Return the response payload, unwrapping a possible ``{"data": ...}``."""
    data = resp.json()
    if isinstance(data, dict) and "data" in data and isinstance(data["data"], dict):
        return data["data"]
    return data


@pytest.fixture(scope="class")
def oauth_server():
    from fastapi.testclient import TestClient

    from zephyrex.lib.Environment import refresh_settings
    from zephyrex.testing.fixtures import prepare_test_registry

    prepare_test_registry()
    refresh_settings()
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    from conftest import CORE_COMPANION_EXTENSIONS
    from zephyrex.app import instance

    # Load the core-companion extensions too (auth_session owns `sessions`, which
    # JWT auth needs) alongside the extension under test.
    exts = ",".join(["auth_oauth2_server", *CORE_COMPANION_EXTENSIONS])
    app = instance(db_prefix=f"test.oauth2srv.{worker}", extensions=exts)
    return TestClient(app)


@pytest.fixture(scope="class")
def admin(oauth_server):
    from zephyrex.testing.factories import make_admin_a

    return make_admin_a(oauth_server)


def _register_client(
    oauth_server,
    *,
    confidential=True,
    scopes="read write",
    redirect="https://app.example.com/cb",
):
    from zephyrex.extensions.auth_oauth2_server.BLL_Auth_OAuth2Server import (
        OAuth2ClientManager,
    )
    from zephyrex.lib.Environment import env

    mgr = OAuth2ClientManager(
        requester_id=env("ROOT_ID"),
        model_registry=oauth_server.app.state.model_registry,
    )
    c = mgr.create(
        name="Test Client",
        redirect_uris=json.dumps([redirect]),
        allowed_scopes=scopes,
        is_confidential=confidential,
    )
    return {
        "client_id": c.client_id,
        "client_secret": c.client_secret,
        "redirect_uri": redirect,
    }


class TestOAuth2AuthorizationCodeFlow:
    def _authorize(self, server, admin, client, challenge):
        return server.post(
            "/v1/oauth2/authorize",
            headers={"Authorization": f"Bearer {admin.jwt}"},
            json={
                "client_id": client["client_id"],
                "redirect_uri": client["redirect_uri"],
                "scope": "read",
                "state": "xyz",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )

    def test_full_confidential_pkce_flow(self, oauth_server, admin):
        client = _register_client(oauth_server, confidential=True)
        verifier, challenge = _pkce_pair()

        r = self._authorize(oauth_server, admin, client, challenge)
        assert r.status_code == 200, r.text
        auth_body = _body(r)
        code = auth_body["code"]
        assert auth_body["state"] == "xyz"

        # Exchange the code (with the matching PKCE verifier) for tokens.
        r = oauth_server.post(
            "/v1/oauth2/token",
            json={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": client["redirect_uri"],
                "client_id": client["client_id"],
                "client_secret": client["client_secret"],
                "code_verifier": verifier,
            },
        )
        assert r.status_code == 200, r.text
        tok = _body(r)
        assert tok["access_token"].startswith("oauth_access_")
        assert tok["refresh_token"].startswith("oauth_refresh_")
        assert tok["token_type"] == "Bearer"
        access = tok["access_token"]
        refresh = tok["refresh_token"]

        # Introspect: active.
        r = oauth_server.post(
            "/v1/oauth2/introspect",
            json={
                "token": access,
                "client_id": client["client_id"],
                "client_secret": client["client_secret"],
            },
        )
        assert r.status_code == 200, r.text
        assert _body(r)["active"] is True

        # Revoke, then introspect: inactive.
        r = oauth_server.post(
            "/v1/oauth2/revoke",
            json={
                "token": access,
                "client_id": client["client_id"],
                "client_secret": client["client_secret"],
            },
        )
        assert r.status_code == 200, r.text
        r = oauth_server.post(
            "/v1/oauth2/introspect",
            json={
                "token": access,
                "client_id": client["client_id"],
                "client_secret": client["client_secret"],
            },
        )
        assert _body(r)["active"] is False

        # Refresh rotation issues a fresh pair.
        r = oauth_server.post(
            "/v1/oauth2/token",
            json={
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": client["client_id"],
                "client_secret": client["client_secret"],
            },
        )
        assert r.status_code == 200, r.text
        assert _body(r)["access_token"] != access

    def test_authorization_code_is_single_use(self, oauth_server, admin):
        client = _register_client(oauth_server, confidential=True)
        verifier, challenge = _pkce_pair()
        code = _body(self._authorize(oauth_server, admin, client, challenge))["code"]
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": client["redirect_uri"],
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "code_verifier": verifier,
        }
        assert oauth_server.post("/v1/oauth2/token", json=payload).status_code == 200
        # Second redemption of the same code is rejected.
        assert oauth_server.post("/v1/oauth2/token", json=payload).status_code == 400


class TestOAuth2Security:
    def test_public_client_requires_pkce(self, oauth_server, admin):
        client = _register_client(oauth_server, confidential=False)
        r = oauth_server.post(
            "/v1/oauth2/authorize",
            headers={"Authorization": f"Bearer {admin.jwt}"},
            json={
                "client_id": client["client_id"],
                "redirect_uri": client["redirect_uri"],
                "scope": "read",
            },
        )
        assert r.status_code == 400, r.text

    def test_wrong_client_secret_is_401(self, oauth_server, admin):
        client = _register_client(oauth_server, confidential=True)
        verifier, challenge = _pkce_pair()
        code = _body(
            oauth_server.post(
                "/v1/oauth2/authorize",
                headers={"Authorization": f"Bearer {admin.jwt}"},
                json={
                    "client_id": client["client_id"],
                    "redirect_uri": client["redirect_uri"],
                    "scope": "read",
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                },
            )
        )["code"]
        r = oauth_server.post(
            "/v1/oauth2/token",
            json={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": client["redirect_uri"],
                "client_id": client["client_id"],
                "client_secret": "WRONG-SECRET",
                "code_verifier": verifier,
            },
        )
        assert r.status_code == 401, r.text

    def test_wrong_pkce_verifier_is_rejected(self, oauth_server, admin):
        client = _register_client(oauth_server, confidential=True)
        _, challenge = _pkce_pair()
        code = _body(
            oauth_server.post(
                "/v1/oauth2/authorize",
                headers={"Authorization": f"Bearer {admin.jwt}"},
                json={
                    "client_id": client["client_id"],
                    "redirect_uri": client["redirect_uri"],
                    "scope": "read",
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                },
            )
        )["code"]
        r = oauth_server.post(
            "/v1/oauth2/token",
            json={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": client["redirect_uri"],
                "client_id": client["client_id"],
                "client_secret": client["client_secret"],
                "code_verifier": "not-the-right-verifier",
            },
        )
        assert r.status_code == 400, r.text
