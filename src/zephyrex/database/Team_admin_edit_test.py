# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who administers a team's records, on a real database.

A team member may edit, delete and share the team's records when the role
they hold is the admin role or extends it: ``ADMIN_ROLE_ID`` is among its
``parent_id`` ancestors, by id. That is the rule ``TeamAuthority`` applies to
the team's membership, and the record filter must apply the same one.

The hole this closes: the record filter ranked roles by their depth in the
role tree, keyed by name. A team's own ``mod`` role, extending ``user``, sits
at the admin's depth, so whoever held it administered every team record; and
since names are not unique, any member could create a role named ``user``
one level down in their team and lift every plain member of every team to
the admin's rank (or one named ``admin`` further down, and take every real
admin's edit rights away)."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import pytest
from fastapi import HTTPException

from zephyrex.database.StaticPermissions import (
    PermissionResult,
    PermissionType,
    check_permission,
)
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Auth import (
    RoleManager,
    RoleModel,
    TeamModel,
    UserTeamManager,
    UserTeamModel,
)
from zephyrex.logic.BLL_Providers import RotationManager, RotationModel
from zephyrex.testing.factories import add_user_to_team, create_team, create_user


def _name(label: str) -> str:
    return f"{label} {uuid.uuid4().hex[:8]}"


def _refused(statuses: set, call: Any, *args: Any, **kwargs: Any) -> None:
    with pytest.raises(HTTPException) as refused:
        call(*args, **kwargs)
    assert refused.value.status_code in statuses, refused.value.detail


class _Team:
    """A team its owner (a team admin) holds one record in."""

    def __init__(self, server: Any, model_registry: Any) -> None:
        self.server = server
        self.model_registry = model_registry
        self.owner = create_user(server)
        self.team = create_team(server, self.owner.id, name=_name("team admin"))
        self.rotation = self.rotations(self.owner.id).create(
            name=_name("team record"), description="before", team_id=self.team.id
        )
        self.memberships: Dict[str, str] = {}

    def rotations(self, requester_id: str) -> RotationManager:
        return RotationManager(
            requester_id=requester_id, model_registry=self.model_registry
        )

    def roles(self, requester_id: str) -> RoleManager:
        return RoleManager(
            requester_id=requester_id, model_registry=self.model_registry
        )

    def member(self, role_id: str, granted_by: Optional[str] = None) -> Any:
        """A new member holding ``role_id``, granted through TeamAuthority by
        ``granted_by`` (the system, unless named)."""
        user = create_user(self.server)
        if granted_by is None:
            membership = add_user_to_team(self.server, user.id, self.team.id, role_id)
        else:
            membership = self.memberships_as(granted_by).create(
                user_id=user.id, team_id=self.team.id, role_id=role_id
            )
        self.memberships[user.id] = membership.id
        return user

    def memberships_as(self, requester_id: str) -> UserTeamManager:
        return UserTeamManager(
            requester_id=requester_id, model_registry=self.model_registry
        )

    def parent_of(self, role_id: str) -> Optional[str]:
        role = self.roles(self.owner.id).get(id=role_id)
        return None if role.parent_id is None else str(role.parent_id)

    def role(self, requester_id: str, parent_id: Optional[str], name: str) -> Any:
        return self.roles(requester_id).create(
            name=name, parent_id=parent_id, team_id=self.team.id
        )

    def edits(self, user_id: str) -> bool:
        """Whether ``user_id`` may edit the team's record; a refusal leaves
        it as it was."""
        description = f"edited by {user_id}"
        try:
            self.rotations(user_id).update(self.rotation.id, description=description)
        except HTTPException as refused:
            assert refused.status_code in {403, 404}, refused.detail
            current = self.rotations(self.owner.id).get(id=self.rotation.id)
            assert current.description != description
            return False
        return True

    def administers(self, user_id: str) -> bool:
        """Whether the permission rule gives ``user_id`` delete and share on
        the team's record, as it gives edit (deleting a record also takes
        being its creator, so the rule is asked directly)."""
        manager = self.model_registry.DB.manager
        rotation_db = RotationModel.DB(manager.Base)
        session = manager.get_session()
        try:
            results = {
                check_permission(
                    user_id, rotation_db, self.rotation.id, session, manager.Base, level
                )[0]
                for level in (PermissionType.DELETE, PermissionType.SHARE)
            }
        finally:
            session.close()
        assert len(results) == 1, results
        return results == {PermissionResult.GRANTED}


@pytest.fixture
def team(server, model_registry) -> _Team:
    return _Team(server, model_registry)


class TestTeamRecordAdmins:
    def test_a_role_at_the_admins_depth_is_not_an_admin(self, team):
        """A team's ``mod``, extending ``user``, is ranked where ``admin``
        is, and the owner may grant it; holding it administers nothing."""
        mod = team.role(team.owner.id, env("USER_ROLE_ID"), _name("mod"))
        moderator = team.member(mod.id, granted_by=team.owner.id)

        assert team.rotations(moderator.id).get(id=team.rotation.id)
        assert not team.edits(moderator.id)
        assert not team.administers(moderator.id)

    def test_a_plain_member_is_not_an_admin(self, team):
        member = team.member(env("USER_ROLE_ID"))
        assert team.rotations(member.id).get(id=team.rotation.id)
        assert not team.edits(member.id)
        assert not team.administers(member.id)

    def test_the_admin_and_superadmin_roles_administer(self, team):
        for role_id in (env("ADMIN_ROLE_ID"), env("SUPERADMIN_ROLE_ID")):
            holder = team.member(role_id)
            assert team.edits(holder.id)
            assert team.administers(holder.id)

    def test_a_custom_role_extending_admin_administers(self, team):
        lead = team.role(team.owner.id, env("ADMIN_ROLE_ID"), _name("lead"))
        # ``lead`` ranks above the owner (an admin), so only a superadmin may
        # create a role under it.
        superadmin = team.member(env("SUPERADMIN_ROLE_ID"))
        deputy = team.role(superadmin.id, lead.id, _name("deputy"))
        for role in (lead, deputy):
            holder = team.member(role.id)
            assert team.edits(holder.id)
            assert team.administers(holder.id)

    def test_a_role_named_user_one_level_down_lifts_nobody(
        self, server, model_registry, team
    ):
        """Names are not unique and rank nothing. Another team names a role
        there ``user``, extending ``user``: ranked by name, that put the
        built-in ``user`` role at the admin's depth, and every plain member
        of every team administered its records."""
        elsewhere = _Team(server, model_registry)
        stranger = elsewhere.member(env("USER_ROLE_ID"))
        # A plain member could name it once; now only the team's admin may
        # create roles (TestCreatingARole), and the name still ranks nothing.
        elsewhere.role(elsewhere.owner.id, env("USER_ROLE_ID"), "user")

        assert not team.edits(team.member(env("USER_ROLE_ID")).id)
        assert not elsewhere.edits(stranger.id)
        assert team.edits(team.member(env("ADMIN_ROLE_ID")).id)

    def test_a_role_named_admin_at_the_root_takes_nothing_away(
        self, server, model_registry, team
    ):
        """A role named ``admin`` with no parent ranks nothing either way:
        the real admins keep their rights and its holders gain none."""
        elsewhere = _Team(server, model_registry)
        fake_admin = elsewhere.role(elsewhere.owner.id, None, "admin")
        holder = elsewhere.member(fake_admin.id, granted_by=elsewhere.owner.id)

        assert not elsewhere.edits(holder.id)
        assert team.edits(team.member(env("ADMIN_ROLE_ID")).id)

    def test_a_cyclic_role_tree_ends_the_walk(self, model_registry, team):
        """A cycle written below the manager (which refuses one) neither
        hangs the rule nor makes its roles extend admin."""
        first = team.role(team.owner.id, env("USER_ROLE_ID"), _name("first"))
        second = team.role(team.owner.id, first.id, _name("second"))
        RoleModel.DB(model_registry.DB.manager.Base).update(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            id=first.id,
            new_properties={"parent_id": second.id},
        )
        assert not team.edits(team.member(second.id).id)
        assert team.edits(team.member(env("ADMIN_ROLE_ID")).id)


class TestReparentingARole:
    """A role's parent sets what everyone holding it may do, so moving it
    is a grant of its new rank to all of them: TeamAuthority's to allow."""

    def test_a_plain_member_cannot_lift_a_role_they_created(self, team):
        """A member creates a role at a plain member's rank, and is later
        moved onto it (the owner may: it ranks no higher than theirs). As
        its creator the member could then make it extend admin, and
        administer the team again; moving it is a grant they cannot make."""
        # Only an admin may create a role (TestCreatingARole), so the member
        # creates it while an admin and is demoted onto it below.
        member = team.member(env("ADMIN_ROLE_ID"))
        helper = team.role(member.id, env("USER_ROLE_ID"), _name("helper"))
        team.memberships_as(team.owner.id).update(
            team.memberships[member.id], role_id=helper.id
        )

        _refused(
            {403},
            team.roles(member.id).update,
            helper.id,
            parent_id=env("ADMIN_ROLE_ID"),
        )
        assert team.parent_of(helper.id) == env("USER_ROLE_ID")
        assert not team.edits(member.id)

    def test_an_admin_cannot_lift_a_role_above_their_own(self, team):
        """An admin may grant ``mod`` (their own rank), but making it extend
        admin would hand its holders a rank above the admin's."""
        mod = team.role(team.owner.id, env("USER_ROLE_ID"), _name("mod"))
        moderator = team.member(mod.id, granted_by=team.owner.id)

        _refused(
            {403},
            team.roles(team.owner.id).update,
            mod.id,
            parent_id=env("ADMIN_ROLE_ID"),
        )
        assert team.parent_of(mod.id) == env("USER_ROLE_ID")
        assert not team.edits(moderator.id)

    def test_an_admin_may_move_a_role_within_their_rank(self, team):
        mod = team.role(team.owner.id, env("USER_ROLE_ID"), _name("mod"))
        assert (
            team.roles(team.owner.id).update(mod.id, parent_id=None).parent_id is None
        )
        renamed = team.roles(team.owner.id).update(mod.id, friendly_name="Moderator")
        assert renamed.friendly_name == "Moderator"

    def test_a_superadmin_may_make_a_role_extend_admin(self, team):
        mod = team.role(team.owner.id, env("USER_ROLE_ID"), _name("mod"))
        moderator = team.member(mod.id, granted_by=team.owner.id)
        superadmin = team.member(env("SUPERADMIN_ROLE_ID"))

        team.roles(superadmin.id).update(mod.id, parent_id=env("ADMIN_ROLE_ID"))
        assert team.edits(moderator.id)

    def test_a_role_cannot_extend_itself(self, team):
        lead = team.role(team.owner.id, env("USER_ROLE_ID"), _name("lead"))
        below = team.role(team.owner.id, lead.id, _name("below"))
        roles = team.roles(team.owner.id)
        _refused({422}, roles.update, lead.id, parent_id=lead.id)
        _refused({422}, roles.update, lead.id, parent_id=below.id)
        assert team.parent_of(lead.id) == env("USER_ROLE_ID")


class TestCreatingARole:
    """A team's roles are its admins' to create, and only under a parent the
    creator may grant: a role extending a parent hands that rank to whoever
    later holds it. The hole: any membership row in the team, enabled or
    not, let its holder create roles there, under any parent."""

    @staticmethod
    def _exists(team: _Team, name: str) -> bool:
        return bool(
            RoleModel.DB(team.model_registry.DB.manager.Base).list(
                requester_id=env("ROOT_ID"),
                model_registry=team.model_registry,
                name=name,
                team_id=team.team.id,
            )
        )

    def _refused_create(
        self, team: _Team, requester_id: str, parent_id: Optional[str]
    ) -> None:
        name = _name("refused")
        _refused({403}, team.role, requester_id, parent_id, name)
        assert not self._exists(team, name)

    def _created_under(
        self, team: _Team, requester_id: str, parent_id: Optional[str]
    ) -> None:
        name = _name("created")
        role = team.role(requester_id, parent_id, name)
        assert role.parent_id == parent_id
        assert self._exists(team, name)

    def _alter_membership(self, team: _Team, user_id: str, **change: Any) -> None:
        UserTeamModel.DB(team.model_registry.DB.manager.Base).update(
            requester_id=env("ROOT_ID"),
            model_registry=team.model_registry,
            id=team.memberships[user_id],
            new_properties=change,
        )

    def test_a_plain_member_cannot_create_a_role(self, team):
        member = team.member(env("USER_ROLE_ID"))
        for parent_id in (env("USER_ROLE_ID"), None):
            self._refused_create(team, member.id, parent_id)

    def test_a_plain_member_cannot_batch_create_roles(self, team):
        member = team.member(env("USER_ROLE_ID"))
        names = [_name("batch"), _name("batch")]
        _refused(
            {403},
            team.roles(member.id).create,
            entities=[
                {
                    "name": name,
                    "parent_id": env("USER_ROLE_ID"),
                    "team_id": team.team.id,
                }
                for name in names
            ],
        )
        assert not any(self._exists(team, name) for name in names)

    def test_a_disabled_admin_membership_cannot_create_a_role(self, team):
        admin = team.member(env("ADMIN_ROLE_ID"))
        self._alter_membership(team, admin.id, enabled=False)
        self._refused_create(team, admin.id, env("USER_ROLE_ID"))

    def test_an_expired_admin_membership_cannot_create_a_role(self, team):
        admin = team.member(env("ADMIN_ROLE_ID"))
        self._alter_membership(
            team, admin.id, expires_at=datetime.now(timezone.utc) - timedelta(days=1)
        )
        self._refused_create(team, admin.id, env("USER_ROLE_ID"))

    def test_the_admin_of_a_deleted_team_cannot_create_a_role(self, team):
        TeamModel.DB(team.model_registry.DB.manager.Base).delete(
            requester_id=env("ROOT_ID"),
            model_registry=team.model_registry,
            id=team.team.id,
        )
        self._refused_create(team, team.owner.id, env("USER_ROLE_ID"))

    def test_an_admin_creates_a_role_under_user_admin_or_none(self, team):
        for parent_id in (env("USER_ROLE_ID"), env("ADMIN_ROLE_ID"), None):
            self._created_under(team, team.owner.id, parent_id)

    def test_an_admin_cannot_create_a_role_above_their_own(self, team):
        """``superadmin``, and a team role extending admin, both rank above
        an admin: a role under either would outrank its creator."""
        superadmin = team.member(env("SUPERADMIN_ROLE_ID"))
        lead = team.role(superadmin.id, env("ADMIN_ROLE_ID"), _name("lead"))
        for parent_id in (env("SUPERADMIN_ROLE_ID"), lead.id):
            self._refused_create(team, team.owner.id, parent_id)

    def test_an_admin_cannot_batch_create_a_role_above_their_own(self, team):
        name = _name("batch")
        _refused(
            {403},
            team.roles(team.owner.id).create,
            entities=[
                {
                    "name": name,
                    "parent_id": env("SUPERADMIN_ROLE_ID"),
                    "team_id": team.team.id,
                }
            ],
        )
        assert not self._exists(team, name)

    def test_a_superadmin_creates_a_role_under_admin_or_superadmin(self, team):
        superadmin = team.member(env("SUPERADMIN_ROLE_ID"))
        for parent_id in (env("ADMIN_ROLE_ID"), env("SUPERADMIN_ROLE_ID")):
            self._created_under(team, superadmin.id, parent_id)

    def test_root_and_system_create_any_role_without_membership(self, team):
        for requester_id in (env("ROOT_ID"), env("SYSTEM_ID")):
            for parent_id in (env("SUPERADMIN_ROLE_ID"), None):
                self._created_under(team, requester_id, parent_id)


class TestRootAdministersTeamRoles:
    """Root administers every team without belonging to it. The hole:
    ``RoleManager.get`` asked for the requester's membership in the role's
    team and refused root (403), so root could neither read nor move a
    team's role through the manager."""

    def test_root_reads_and_moves_a_team_role(self, team):
        mod = team.role(team.owner.id, env("USER_ROLE_ID"), _name("mod"))
        roles = team.roles(env("ROOT_ID"))
        assert roles.get(id=mod.id).id == mod.id
        roles.update(mod.id, parent_id=env("SUPERADMIN_ROLE_ID"))
        assert team.parent_of(mod.id) == env("SUPERADMIN_ROLE_ID")
