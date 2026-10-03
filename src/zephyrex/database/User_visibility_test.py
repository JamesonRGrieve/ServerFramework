# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who may read a user record, on a real database.

The hole this closes: the users rule in ``generate_permission_filter`` OR'd
in "every account that is not ROOT, SYSTEM or the template user", so any
authenticated user read any user: ``UserManager.get/list/search``, a team's
member list (``GET /v1/team/{id}/user``) and GraphQL's navigation from a
membership to its ``user`` all answered for strangers, and a user-owned SCIM
target could export the whole directory.

The rule now: a requester sees themselves, the users they share a live team
with (both memberships enabled, unexpired and not deleted, in a team that is
not deleted; the requester's side reaches up to parent teams, as team-scoped
records do), and anyone an explicit Permission row lets them view. ROOT and
SYSTEM see everyone. Anyone else is a 404 and absent from list and search."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import pytest
from fastapi import HTTPException

from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Auth import (
    TeamModel,
    UserManager,
    UserModel,
    UserTeamModel,
)
from zephyrex.testing.factories import add_user_to_team, create_team, create_user


def _visible(model_registry: Any, viewer_id: str, target: UserModel) -> bool:
    """Whether ``viewer_id`` can read ``target``: GET, LIST and SEARCH must
    agree, and a refused GET is a 404 (existence does not leak)."""
    users = UserManager(requester_id=viewer_id, model_registry=model_registry)
    try:
        got = users.get(id=target.id).id == target.id
    except HTTPException as refused:
        assert refused.status_code == 404, refused.detail
        got = False
    expected = [target.id] if got else []
    assert [u.id for u in users.list(id=target.id)] == expected
    assert [u.id for u in users.search(email={"eq": target.email})] == expected
    assert (target.id in {u.id for u in users.list()}) is got
    return got


def _team(server: Any, owner: UserModel, parent_id: Optional[str] = None) -> Any:
    return create_team(
        server, owner.id, name=f"visibility {uuid.uuid4().hex[:8]}", parent_id=parent_id
    )


def _join(server: Any, user: UserModel, team: Any) -> Any:
    return add_user_to_team(server, user.id, team.id, env("USER_ROLE_ID"))


def _membership_db(model_registry: Any) -> Any:
    return UserTeamModel.DB(model_registry.DB.manager.Base)


def _change_membership(
    model_registry: Any, membership: Any, **new_properties: Any
) -> None:
    _membership_db(model_registry).update(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        id=membership.id,
        new_properties=new_properties,
    )


def test_an_unrelated_user_is_invisible(server, model_registry):
    viewer, stranger = create_user(server), create_user(server)
    _team(server, viewer)
    _team(server, stranger)

    assert not _visible(model_registry, viewer.id, stranger)
    assert not _visible(model_registry, stranger.id, viewer)
    assert _visible(model_registry, viewer.id, viewer)


def test_a_teammate_is_visible(server, model_registry):
    owner, member = create_user(server), create_user(server)
    _join(server, member, _team(server, owner))

    assert _visible(model_registry, owner.id, member)
    assert _visible(model_registry, member.id, owner)


def test_parent_team_members_are_visible_from_a_sub_team(server, model_registry):
    """Team-scoped records reach a sub-team's members from its parents, not
    the other way round; user records follow the same hierarchy."""
    parent_owner, child_owner = create_user(server), create_user(server)
    parent = _team(server, parent_owner)
    _team(server, child_owner, parent_id=parent.id)

    assert _visible(model_registry, child_owner.id, parent_owner)
    assert not _visible(model_registry, parent_owner.id, child_owner)


@pytest.mark.parametrize(
    "ended",
    [
        pytest.param({"enabled": False}, id="disabled"),
        pytest.param(
            {"expires_at": datetime.now(timezone.utc) - timedelta(days=1)},
            id="expired",
        ),
        pytest.param(
            {"deleted_at": datetime.now(timezone.utc)},
            id="soft-deleted",
        ),
    ],
)
def test_an_ended_membership_stops_visibility(
    server, model_registry, ended: Dict[str, Any]
):
    owner, member = create_user(server), create_user(server)
    membership = _join(server, member, _team(server, owner))
    assert _visible(model_registry, owner.id, member)

    _change_membership(model_registry, membership, **ended)

    assert not _visible(model_registry, owner.id, member)
    assert not _visible(model_registry, member.id, owner)


def test_a_deleted_team_stops_visibility(server, model_registry):
    owner, member = create_user(server), create_user(server)
    team = _team(server, owner)
    _join(server, member, team)
    assert _visible(model_registry, member.id, owner)

    TeamModel.DB(model_registry.DB.manager.Base).delete(
        requester_id=env("ROOT_ID"), model_registry=model_registry, id=team.id
    )

    assert not _visible(model_registry, owner.id, member)
    assert not _visible(model_registry, member.id, owner)


def test_a_pending_invitation_is_not_a_shared_team(server, model_registry):
    """An invitation lets its invitee see the team, not read its members."""
    from zephyrex.extensions.auth_invitations.BLL_Invitations import InvitationModel

    owner, invitee = create_user(server), create_user(server)
    team = _team(server, owner)
    InvitationModel.DB(model_registry.DB.manager.Base).create(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        team_id=team.id,
        role_id=env("USER_ROLE_ID"),
        user_id=invitee.id,
        code=uuid.uuid4().hex,
        max_uses=1,
    )

    assert not _visible(model_registry, invitee.id, owner)


def test_root_and_system_see_everyone(server, model_registry):
    stranger = create_user(server)

    assert _visible(model_registry, env("ROOT_ID"), stranger)
    assert _visible(model_registry, env("SYSTEM_ID"), stranger)


def test_an_explicit_grant_on_a_user_is_honoured(server, model_registry):
    from zephyrex.extensions.acl_rbac.BLL_ACL import PermissionManager

    viewer, stranger = create_user(server), create_user(server)
    grants = PermissionManager(
        requester_id=env("ROOT_ID"), model_registry=model_registry
    )
    grant = grants.create(
        resource_type="users",
        resource_id=stranger.id,
        user_id=viewer.id,
        can_view=True,
    )
    assert _visible(model_registry, viewer.id, stranger)

    grants.delete(id=grant.id)
    assert not _visible(model_registry, viewer.id, stranger)


def _member_list(server: Any, viewer: Any, team: Any) -> Dict[str, Any]:
    response = server.get(
        f"/v1/team/{team.id}/user", headers={"Authorization": f"Bearer {viewer.jwt}"}
    )
    assert response.status_code == 200, response.text
    return {row["user_id"]: row.get("user") for row in response.json()["user_teams"]}


def test_a_team_member_list_embeds_live_members_only(server, model_registry):
    """REST: the member list shows a disabled membership's row (the admin
    manages it), but no longer the user behind it."""
    owner, member = create_user(server), create_user(server)
    team = _team(server, owner)
    membership = _join(server, member, team)
    assert _member_list(server, owner, team)[member.id]["email"] == member.email

    _change_membership(model_registry, membership, enabled=False)

    assert _member_list(server, owner, team)[member.id] is None


def _membership_users(server: Any, viewer: Any) -> Dict[str, Any]:
    response = server.post(
        "/graphql",
        json={"query": "{ userTeams { userId user { id email } } }"},
        headers={"Authorization": f"Bearer {viewer.jwt}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "errors" not in body, body
    return {row["userId"]: row["user"] for row in body["data"]["userTeams"]}


def test_graphql_navigation_reaches_live_teammates_only(server, model_registry):
    """GraphQL: a membership's ``user`` resolves as the requester, so it is
    null once that membership has ended."""
    owner, member = create_user(server), create_user(server)
    membership = _join(server, member, _team(server, owner))
    assert _membership_users(server, owner)[member.id]["email"] == member.email

    _change_membership(model_registry, membership, enabled=False)

    assert _membership_users(server, owner)[member.id] is None
