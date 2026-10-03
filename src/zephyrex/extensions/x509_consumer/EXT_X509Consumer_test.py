# SPDX-License-Identifier: AGPL-3.0-or-later
"""The x509_consumer extension's declaration and its one ability."""

from typing import Any

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.x509_consumer.BLL_X509Consumer import (
    UserX509LinkManager,
    X509TrustAnchorModel,
)
from zephyrex.extensions.x509_consumer.EXT_X509Consumer import EXT_X509Consumer
from zephyrex.lib.Environment import env


class TestDeclaration:
    def test_identity(self) -> None:
        assert EXT_X509Consumer.name == "x509_consumer"
        assert EXT_X509Consumer.version == "2.0.0"

    def test_abilities(self) -> None:
        assert "x509_linked_identities" in EXT_X509Consumer.get_abilities()

    def test_dependencies(self) -> None:
        assert EXT_X509Consumer.pip_requirements() == ["cryptography>=45.0.0"]
        assert sorted(
            (dep.name, dep.optional) for dep in EXT_X509Consumer.dependencies.ext
        ) == [("auth_invitations", True), ("auth_session", True)]


class TestLinkedIdentitiesAbility(ExtensionServerMixin):
    extension_class = EXT_X509Consumer

    async def test_a_user_sees_their_own_links(
        self, server: Any, user_b: Any, admin_a: Any
    ) -> None:
        registry = server.app.state.model_registry
        manager = UserX509LinkManager(
            model_registry=registry, requester_id=env("ROOT_ID")
        )
        # Links need an anchor to exist; this one signs no one in.
        anchor = X509TrustAnchorModel.DB(registry.DB.manager.Base).create(
            requester_id=env("SYSTEM_ID"),
            model_registry=registry,
            return_type="dto",
            override_dto=X509TrustAnchorModel,
            name="ability-anchor",
            ca_cert_pem="not used",
            is_enabled=False,
        )
        manager.create(user_id=user_b.id, trust_anchor_id=str(anchor.id), identity="b")
        manager.create(user_id=admin_a.id, trust_anchor_id=str(anchor.id), identity="a")
        mine = await EXT_X509Consumer.x509_linked_identities(requester_id=user_b.id)
        assert [link["identity"] for link in mine] == ["b"]

    async def test_without_a_requester_it_is_refused(self, server: Any) -> None:
        with pytest.raises(Exception) as refused:
            await EXT_X509Consumer.x509_linked_identities(requester_id="")
        assert getattr(refused.value, "status_code", None) == 400
