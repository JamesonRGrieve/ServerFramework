# SPDX-License-Identifier: AGPL-3.0-or-later
"""The extension's abilities act for the user ``requester_id`` names, under
that user's permissions; the operator alone rotates the signing keys."""

import pytest
from fastapi import HTTPException

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.oauth_provider import Config
from zephyrex.extensions.oauth_provider.BLL_OAuthProvider import (
    OauthClientManager,
    OauthGrantManager,
)
from zephyrex.extensions.oauth_provider.EXT_OAuthProvider import EXT_OAuthProvider
from zephyrex.lib.Environment import env
from zephyrex.pydantic2.registry import ModelRegistry

REDIRECT = "https://rp.example.test/callback"


class TestAbilities(ExtensionServerMixin):
    extension_class = EXT_OAuthProvider

    @pytest.fixture(autouse=True)
    def configured(self, server, set_env, monkeypatch):
        """This module's app is the one the abilities act in (each test
        module builds its own app in the one process)."""
        set_env(Config.ISSUER, "https://id.example.test")
        set_env(Config.CONSENT_URL, "https://ui.example.test/consent")
        registry = server.app.state.model_registry
        monkeypatch.setattr(
            ModelRegistry, "attached", classmethod(lambda cls: registry)
        )

    def test_abilities_are_declared(self):
        assert EXT_OAuthProvider.get_abilities() >= {
            "oauth_provider_register_client",
            "oauth_provider_list_grants",
            "oauth_provider_revoke_grant",
            "oauth_provider_rotate_signing_keys",
        }

    async def test_register_client_for_the_requester(self, server, admin_a):
        registered = await EXT_OAuthProvider.register_client(
            admin_a.id, "Agent App", [REDIRECT], allowed_scopes=["openid"]
        )
        assert registered["client_secret"].startswith("zxcs_")
        owned = OauthClientManager(
            requester_id=admin_a.id, model_registry=server.app.state.model_registry
        ).get(id=registered["id"])
        assert owned.user_id == admin_a.id

    async def test_an_ability_needs_a_requester(self, server):
        with pytest.raises(HTTPException) as refused:
            await EXT_OAuthProvider.list_grants("")
        assert refused.value.status_code == 400

    async def test_grants_are_the_users_own(self, server, admin_a, user_b):
        registered = await EXT_OAuthProvider.register_client(
            admin_a.id, "Consented App", [REDIRECT], allowed_scopes=["openid"]
        )
        registry = server.app.state.model_registry
        client = OauthClientManager(
            requester_id=admin_a.id, model_registry=registry
        ).get(id=registered["id"])
        grant = OauthGrantManager(
            requester_id=user_b.id, model_registry=registry
        ).consent(client, ["openid"])
        mine = await EXT_OAuthProvider.list_grants(user_b.id)
        assert [g["id"] for g in mine if g["client_id"] == client.client_id] == [
            grant.id
        ]
        theirs = await EXT_OAuthProvider.list_grants(admin_a.id)
        assert grant.id not in [g["id"] for g in theirs]
        with pytest.raises(HTTPException) as refused:
            await EXT_OAuthProvider.revoke_grant(admin_a.id, grant.id)
        assert refused.value.status_code in (403, 404)
        assert (await EXT_OAuthProvider.revoke_grant(user_b.id, grant.id))["revoked"]
        assert grant.id not in [
            g["id"] for g in await EXT_OAuthProvider.list_grants(user_b.id)
        ]

    async def test_a_grant_cannot_be_made_for_someone_else(
        self, server, admin_a, user_b
    ):
        registry = server.app.state.model_registry
        created = OauthGrantManager(
            requester_id=user_b.id, model_registry=registry
        ).create(
            entities=[
                {
                    "client_id": "zxc_x",
                    "client_name": "x",
                    "scopes": "openid",
                    "user_id": admin_a.id,
                }
            ]
        )
        assert [grant.user_id for grant in created] == [user_b.id]

    async def test_only_the_operator_rotates_keys(self, server, admin_a):
        with pytest.raises(HTTPException) as refused:
            await EXT_OAuthProvider.rotate_signing_keys(admin_a.id)
        assert refused.value.status_code == 403
        rotated = await EXT_OAuthProvider.rotate_signing_keys(env("ROOT_ID"))
        assert len(rotated["kids"]) == 1

    def test_validate_config(self, set_env):
        assert EXT_OAuthProvider.validate_config() == []
        set_env(Config.ISSUER, "http://id.example.test")
        set_env(Config.CONSENT_URL, "")
        issues = EXT_OAuthProvider.validate_config()
        assert any(Config.ISSUER in issue for issue in issues)
        assert any(Config.CONSENT_URL in issue for issue in issues)
