# SPDX-License-Identifier: AGPL-3.0-or-later
"""A deleted team grants nothing, on a real database.

The hole this closes: the team CTE behind every team-scoped record
(``_get_admin_accessible_team_ids_cte`` without ``memberships_only``)
counted memberships in, invitations to and parents of soft-deleted teams,
and the EDIT rule's team-admin check did not look at the team at all. So a
team's members kept reading its records, and its admins kept editing them,
after the team was deleted."""

import uuid
from typing import Any

from zephyrex.database.StaticPermissions import user_can_edit
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Auth import RoleModel, TeamModel
from zephyrex.testing.factories import (
    add_user_to_team,
    create_role,
    create_team,
    create_user,
)


def _team(server: Any, owner: Any, parent_id: Any = None) -> Any:
    return create_team(
        server, owner.id, name=f"deleted {uuid.uuid4().hex[:8]}", parent_id=parent_id
    )


def _role_in(server: Any, owner: Any, team: Any) -> Any:
    return create_role(server, owner.id, team.id, name=f"role_{uuid.uuid4().hex[:8]}")


def _sees(model_registry: Any, viewer: Any, role: Any) -> bool:
    roles = RoleModel.DB(model_registry.DB.manager.Base)
    listed = roles.list(
        requester_id=viewer.id, model_registry=model_registry, id=role.id
    )
    found = roles.exists(
        requester_id=viewer.id, model_registry=model_registry, id=role.id
    )
    assert bool(listed) is found
    return found


def _edits(model_registry: Any, editor: Any, role: Any) -> bool:
    Base = model_registry.DB.manager.Base
    with model_registry.DB.manager._get_db_session() as session:
        return bool(
            user_can_edit(editor.id, RoleModel.DB(Base), role.id, session, Base)
        )


def _delete(model_registry: Any, team: Any) -> None:
    TeamModel.DB(model_registry.DB.manager.Base).delete(
        requester_id=env("ROOT_ID"), model_registry=model_registry, id=team.id
    )


def test_a_deleted_teams_members_lose_its_records(server, model_registry):
    owner, member = create_user(server), create_user(server)
    team = _team(server, owner)
    add_user_to_team(server, member.id, team.id, env("USER_ROLE_ID"))
    role = _role_in(server, owner, team)
    assert _sees(model_registry, member, role)

    _delete(model_registry, team)

    assert not _sees(model_registry, member, role)


def test_a_deleted_teams_admins_lose_its_records(server, model_registry):
    owner, admin = create_user(server), create_user(server)
    team = _team(server, owner)
    add_user_to_team(server, admin.id, team.id, env("ADMIN_ROLE_ID"))
    role = _role_in(server, owner, team)
    assert _edits(model_registry, admin, role)

    _delete(model_registry, team)

    assert not _edits(model_registry, admin, role)
    assert not _sees(model_registry, admin, role)


def test_a_deleted_parent_reaches_nothing_from_its_sub_teams(server, model_registry):
    """Team-scoped records reach a sub-team's members from its parents; a
    deleted parent's records are reached by no one."""
    parent_owner, child_owner = create_user(server), create_user(server)
    parent = _team(server, parent_owner)
    _team(server, child_owner, parent_id=parent.id)
    role = _role_in(server, parent_owner, parent)
    assert _sees(model_registry, child_owner, role)

    _delete(model_registry, parent)

    assert not _sees(model_registry, child_owner, role)


def test_a_live_team_keeps_its_records(server, model_registry):
    owner, member = create_user(server), create_user(server)
    team, other = _team(server, owner), _team(server, owner)
    add_user_to_team(server, member.id, team.id, env("USER_ROLE_ID"))
    role = _role_in(server, owner, team)

    _delete(model_registry, other)

    assert _sees(model_registry, member, role)
