# SPDX-License-Identifier: AGPL-3.0-or-later
"""The proxy_auth_consumer extension's declaration, config check and its
one ability."""

import uuid
from typing import Any, Callable

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.proxy_auth_consumer.BLL_ProxyAuthConsumer import (
    UserProxyAuthLinkManager,
)
from zephyrex.extensions.proxy_auth_consumer.EXT_ProxyAuthConsumer import (
    EXT_ProxyAuthConsumer,
)
from zephyrex.lib.Environment import env


class TestDeclaration:
    def test_identity(self) -> None:
        assert EXT_ProxyAuthConsumer.name == "proxy_auth_consumer"
        assert EXT_ProxyAuthConsumer.version == "2.0.0"

    def test_abilities(self) -> None:
        assert "proxy_auth_linked_identities" in EXT_ProxyAuthConsumer.get_abilities()

    def test_dependencies(self) -> None:
        assert EXT_ProxyAuthConsumer.pip_requirements() == []
        assert sorted(
            (dep.name, dep.optional) for dep in EXT_ProxyAuthConsumer.dependencies.ext
        ) == [("auth_invitations", True), ("auth_session", True)]


class TestValidateConfig:
    def test_an_unset_proxy_list_is_reported(
        self, set_env: Callable[[str, str], None]
    ) -> None:
        set_env("PROXY_AUTH_CONSUMER_TRUSTED_PROXIES", "")
        [issue] = EXT_ProxyAuthConsumer.validate_config()
        assert "unset" in issue

    def test_a_wildcard_proxy_list_is_reported(
        self, set_env: Callable[[str, str], None]
    ) -> None:
        set_env("PROXY_AUTH_CONSUMER_TRUSTED_PROXIES", "*")
        [issue] = EXT_ProxyAuthConsumer.validate_config()
        assert "wildcard" in issue

    def test_a_proxy_list_of_networks_is_valid(
        self, set_env: Callable[[str, str], None]
    ) -> None:
        set_env("PROXY_AUTH_CONSUMER_TRUSTED_PROXIES", "10.0.0.0/8, ::1")
        assert EXT_ProxyAuthConsumer.validate_config() == []


class TestLinkedIdentitiesAbility(ExtensionServerMixin):
    extension_class = EXT_ProxyAuthConsumer

    async def test_a_user_sees_their_own_links(
        self, server: Any, user_b: Any, admin_a: Any
    ) -> None:
        manager = UserProxyAuthLinkManager(
            model_registry=server.app.state.model_registry,
            requester_id=env("ROOT_ID"),
        )
        suffix = uuid.uuid4().hex[:8]
        manager.create(user_id=user_b.id, identity=f"ability-b-{suffix}")
        manager.create(user_id=admin_a.id, identity=f"ability-a-{suffix}")
        mine = await EXT_ProxyAuthConsumer.proxy_auth_linked_identities(
            requester_id=user_b.id
        )
        identities = [link["identity"] for link in mine]
        assert f"ability-b-{suffix}" in identities
        assert not any(identity.startswith("ability-a-") for identity in identities)

    async def test_without_a_requester_it_is_refused(self, server: Any) -> None:
        with pytest.raises(Exception) as refused:
            await EXT_ProxyAuthConsumer.proxy_auth_linked_identities(requester_id="")
        assert getattr(refused.value, "status_code", None) == 400
