# SPDX-License-Identifier: AGPL-3.0-or-later
"""An ACL grant names only a user the granter can see, on a real database.

The hole this closes: ``PermissionManager.create_validation`` checked the
grantee as SYSTEM, which sees every account, so a granter who knew any
user's id could grant to them and learn by the answer that the account
exists. A grant now resolves its grantee as the granter: a user they share
no live team hierarchy with (and hold no grant on) is a 404, exactly as a
missing one, and no row is written. ROOT and SYSTEM, who see everyone, may
grant to anyone, which is how the server grants on its own account (a
conversation adding a participant)."""

import uuid
from typing import Any

import pytest
from fastapi import HTTPException

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.acl_rbac.BLL_ACL import PermissionManager
from zephyrex.extensions.acl_rbac.EXT_ACL import AclRbacExtension
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import RotationManager
from zephyrex.testing.factories import add_user_to_team, create_team, create_user


class TestGranteeVisibility(ExtensionServerMixin):
    extension_class = AclRbacExtension

    def _resource(self, model_registry: Any, owner: Any) -> Any:
        """A record the granter owns, so they hold SHARE on it."""
        return RotationManager(
            requester_id=owner.id, model_registry=model_registry
        ).create(name=f"grantee {uuid.uuid4().hex}", description="a record to share")

    def _grant(
        self, model_registry: Any, granter_id: str, resource: Any, user_id: str
    ) -> Any:
        return PermissionManager(
            requester_id=granter_id, model_registry=model_registry
        ).create(
            resource_type="rotations",
            resource_id=resource.id,
            user_id=user_id,
            can_view=True,
        )

    def _grants_to(self, model_registry: Any, resource: Any, user_id: str) -> list[Any]:
        return PermissionManager(
            requester_id=env("ROOT_ID"), model_registry=model_registry
        ).list(resource_type="rotations", resource_id=resource.id, user_id=user_id)

    def test_a_grant_to_an_invisible_user_is_404_and_writes_nothing(
        self, server, model_registry
    ):
        granter, stranger = create_user(server), create_user(server)
        create_team(server, granter.id, name=f"granter {uuid.uuid4().hex[:8]}")
        create_team(server, stranger.id, name=f"stranger {uuid.uuid4().hex[:8]}")
        resource = self._resource(model_registry, granter)

        with pytest.raises(HTTPException) as refused:
            self._grant(model_registry, granter.id, resource, stranger.id)

        assert refused.value.status_code == 404
        assert self._grants_to(model_registry, resource, stranger.id) == []

    def test_an_invisible_user_and_a_missing_one_answer_alike(
        self, server, model_registry
    ):
        granter, stranger = create_user(server), create_user(server)
        resource = self._resource(model_registry, granter)

        answers = []
        for user_id in (stranger.id, str(uuid.uuid4())):
            with pytest.raises(HTTPException) as refused:
                self._grant(model_registry, granter.id, resource, user_id)
            answers.append((refused.value.status_code, refused.value.detail))

        assert answers[0] == answers[1]

    def test_a_grant_to_a_teammate_works(self, server, model_registry):
        granter, teammate = create_user(server), create_user(server)
        team = create_team(server, granter.id, name=f"granter {uuid.uuid4().hex[:8]}")
        add_user_to_team(server, teammate.id, team.id, env("USER_ROLE_ID"))
        resource = self._resource(model_registry, granter)

        grant = self._grant(model_registry, granter.id, resource, teammate.id)

        assert grant.user_id == teammate.id
        shared = RotationManager(
            requester_id=teammate.id, model_registry=model_registry
        )
        assert shared.get(id=resource.id).id == resource.id

    def test_a_grant_to_a_sub_team_member_works(self, server, model_registry):
        """The granter sees down their team hierarchy, so they may grant
        there too."""
        granter, below = create_user(server), create_user(server)
        parent = create_team(server, granter.id, name=f"parent {uuid.uuid4().hex[:8]}")
        create_team(
            server, below.id, name=f"sub {uuid.uuid4().hex[:8]}", parent_id=parent.id
        )
        resource = self._resource(model_registry, granter)

        assert (
            self._grant(model_registry, granter.id, resource, below.id).user_id
            == below.id
        )

    @pytest.mark.parametrize("internal", ["ROOT_ID", "SYSTEM_ID"])
    def test_root_and_system_grant_to_anyone(
        self, server, model_registry, internal: str
    ):
        owner, stranger = create_user(server), create_user(server)
        resource = self._resource(model_registry, owner)

        grant = self._grant(model_registry, env(internal), resource, stranger.id)

        assert grant.user_id == stranger.id
        assert [
            g.id for g in self._grants_to(model_registry, resource, stranger.id)
        ] == [grant.id]
