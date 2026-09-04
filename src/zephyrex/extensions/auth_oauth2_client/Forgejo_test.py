# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Forgejo OAuth2 provider and self-hosted authorize-URL resolution."""

from unittest.mock import MagicMock, patch

from zephyrex.extensions.auth_oauth2_client import BLL_Auth_OAuth2Client as bll
from zephyrex.extensions.auth_oauth2_client.Forgejo import ForgejoOAuthProvider


class TestRegistry:
    def test_forgejo_registered(self):
        assert bll.PROVIDER_REGISTRY["forgejo"] is ForgejoOAuthProvider

    def test_forgejo_in_provider_auth(self):
        assert bll._PROVIDER_AUTH["forgejo"]["client_id_env"] == "FORGEJO_CLIENT_ID"


class TestSelfHostedAuthUrl:
    def test_auth_url_from_base(self, monkeypatch):
        monkeypatch.delenv("FORGEJO_OAUTH_BASE_URL", raising=False)
        monkeypatch.setenv("FORGEJO_BASE_URL", "https://git.example.edu/")
        assert (
            bll._auth_url(bll._PROVIDER_AUTH["forgejo"])
            == "https://git.example.edu/login/oauth/authorize"
        )

    def test_oauth_base_overrides_api_base(self, monkeypatch):
        monkeypatch.setenv("FORGEJO_OAUTH_BASE_URL", "https://sso.example.edu")
        monkeypatch.setenv("FORGEJO_BASE_URL", "https://git.example.edu")
        assert (
            bll._auth_url(bll._PROVIDER_AUTH["forgejo"])
            == "https://sso.example.edu/login/oauth/authorize"
        )

    def test_static_auth_url_unchanged(self):
        assert (
            bll._auth_url(bll._PROVIDER_AUTH["github"])
            == "https://github.com/login/oauth/authorize"
        )


class TestUserInfo:
    def test_maps_forgejo_login_to_username(self, monkeypatch):
        monkeypatch.setenv("FORGEJO_BASE_URL", "https://git.example.edu")
        provider = ForgejoOAuthProvider(access_token="tok")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "id": 7,
            "login": "ada",
            "email": "ada@example.edu",
            "full_name": "Ada Lovelace",
            "avatar_url": "https://git.example.edu/avatars/ada",
        }
        with patch(
            "zephyrex.extensions.auth_oauth2_client.Forgejo.requests"
        ) as mock_requests:
            mock_requests.get.return_value = resp
            info = provider.get_user_info()
        assert info["username"] == "ada"
        assert info["email"] == "ada@example.edu"
        assert info["first_name"] == "Ada"
        assert info["last_name"] == "Lovelace"

    def test_no_token_returns_empty(self):
        assert ForgejoOAuthProvider(access_token=None).get_user_info() == {}
