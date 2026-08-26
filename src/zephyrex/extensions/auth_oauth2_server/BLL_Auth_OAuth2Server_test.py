# SPDX-License-Identifier: AGPL-3.0-or-later
"""Phase-1 tests for the auth_oauth2_server DB layer.

Proves the three entities (client / auth-code / token) register and round-trip
through their managers, and that OAuth2ClientManager generates ``client_id`` +
``client_secret`` server-side, ignoring any client-supplied value.
"""

import os

import pytest

os.environ.setdefault("JWT_SECRET", "test-jwt-secret-32-bytes-or-more-aaaaaa")
os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("SEED_DATA", "false")


@pytest.fixture(scope="module")
def oauth2_registry(tmp_path_factory):
    """Boot an app instance with only auth_oauth2_server loaded and hand back
    its model registry (worker-unique DB so xdist stays isolated)."""
    tmp = tmp_path_factory.mktemp("oauth2_server_bll")
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    os.environ["DATABASE_NAME"] = f"oauth2_server_bll_{worker}_{os.getpid()}"
    os.environ["DATABASE_PATH"] = str(tmp)
    from zephyrex.app import instance

    app = instance(
        extensions="auth_oauth2_server", db_prefix=f"oauth2srv.{worker}.{os.getpid()}"
    )
    return app.state.model_registry


def _client_mgr(registry):
    from zephyrex.extensions.auth_oauth2_server.BLL_Auth_OAuth2Server import (
        OAuth2ClientManager,
    )
    from zephyrex.lib.Environment import env

    return OAuth2ClientManager(requester_id=env("ROOT_ID"), model_registry=registry)


class TestOAuth2ClientManager:
    def test_create_generates_client_id_and_secret_ignoring_client_input(
        self, oauth2_registry
    ):
        mgr = _client_mgr(oauth2_registry)
        client = mgr.create(
            name="Test App",
            redirect_uris='["https://app.example.com/callback"]',
            allowed_scopes="read write",
            # A malicious caller trying to pin its own id/secret must be ignored.
            client_id="attacker-supplied-id",
            client_secret="attacker-supplied-secret",
        )
        assert client.client_id.startswith("oauth_client_")
        assert client.client_id != "attacker-supplied-id"
        assert (
            client.client_secret and client.client_secret != "attacker-supplied-secret"
        )
        assert len(client.client_secret) >= 32
        assert client.is_confidential is True
        assert client.name == "Test App"

        fetched = mgr.get(id=client.id)
        assert fetched.client_id == client.client_id
        assert fetched.client_secret == client.client_secret

    def test_two_clients_get_distinct_credentials(self, oauth2_registry):
        mgr = _client_mgr(oauth2_registry)
        a = mgr.create(name="A", redirect_uris='["https://a/cb"]')
        b = mgr.create(name="B", redirect_uris='["https://b/cb"]')
        assert a.client_id != b.client_id
        assert a.client_secret != b.client_secret

    def test_public_client_has_no_secret(self, oauth2_registry):
        mgr = _client_mgr(oauth2_registry)
        client = mgr.create(
            name="Public SPA",
            redirect_uris='["https://spa.example.com/cb"]',
            is_confidential=False,
        )
        assert client.is_confidential is False
        assert client.client_secret is None
        assert client.client_id.startswith("oauth_client_")

    def test_update_and_list(self, oauth2_registry):
        mgr = _client_mgr(oauth2_registry)
        client = mgr.create(name="Updatable", redirect_uris='["https://u/cb"]')
        mgr.update(id=client.id, allowed_scopes="read")
        assert mgr.get(id=client.id).allowed_scopes == "read"
        # client_id survives an unrelated update
        assert mgr.get(id=client.id).client_id == client.client_id
        listed = mgr.list(filters=[mgr.DB.id.in_([client.id])])
        assert any(c.id == client.id for c in listed)


class TestOAuth2AuthCodeAndToken:
    def _managers(self, registry):
        from zephyrex.extensions.auth_oauth2_server.BLL_Auth_OAuth2Server import (
            OAuth2AuthCodeManager,
            OAuth2TokenManager,
        )
        from zephyrex.lib.Environment import env

        rid = env("ROOT_ID")
        return (
            OAuth2AuthCodeManager(requester_id=rid, model_registry=registry),
            OAuth2TokenManager(requester_id=rid, model_registry=registry),
        )

    def test_authcode_crud_and_single_use_flag(self, oauth2_registry):
        code_mgr, _ = self._managers(oauth2_registry)
        code = code_mgr.create(
            client_id="oauth_client_x",
            code="the-auth-code",
            redirect_uri="https://x/cb",
            scopes="read",
            code_challenge="abc123",
            code_challenge_method="S256",
        )
        assert code.code == "the-auth-code"
        assert code.is_used is False
        assert code.code_challenge_method == "S256"
        code_mgr.update(id=code.id, is_used=True)
        assert code_mgr.get(id=code.id).is_used is True

    def test_token_crud_and_revocation_flag(self, oauth2_registry):
        _, token_mgr = self._managers(oauth2_registry)
        tok = token_mgr.create(
            client_id="oauth_client_x",
            token="opaque-access-token",
            token_type="access",
            scopes="read",
        )
        assert tok.token == "opaque-access-token"
        assert tok.token_type == "access"
        assert tok.is_revoked is False
        token_mgr.update(id=tok.id, is_revoked=True)
        assert token_mgr.get(id=tok.id).is_revoked is True
