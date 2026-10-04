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
not deleted; the requester's side reaches up to live parent teams, as
team-scoped records do, and down to live sub-teams at any depth, never
sideways), and anyone an explicit Permission row lets them view. ROOT and
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


def test_the_team_hierarchy_is_visible_in_both_directions(server, model_registry):
    """A sub-team's members see its parents' members, and (by the operator's
    decision of 2026-10-03) a parent team's members see its sub-teams'.
    This test asserted the parent side could not, which was the rule
    before that decision."""
    parent_owner, child_owner = create_user(server), create_user(server)
    parent = _team(server, parent_owner)
    _team(server, child_owner, parent_id=parent.id)

    assert _visible(model_registry, child_owner.id, parent_owner)
    assert _visible(model_registry, parent_owner.id, child_owner)


def _hierarchy(server: Any) -> Dict[str, Any]:
    """A parent team with a sub-team, a grandchild team under that, and a
    sibling sub-team, each with a plain member besides its owner."""
    people = {
        name: create_user(server)
        for name in ("parent", "sub", "grandchild", "sibling", "stranger")
    }
    parent = _team(server, people["parent"])
    sub = _team(server, create_user(server), parent_id=parent.id)
    grandchild = _team(server, create_user(server), parent_id=sub.id)
    sibling = _team(server, create_user(server), parent_id=parent.id)
    _team(server, people["stranger"])
    memberships = {
        "sub": _join(server, people["sub"], sub),
        "grandchild": _join(server, people["grandchild"], grandchild),
        "sibling": _join(server, people["sibling"], sibling),
    }
    return {
        "people": people,
        "teams": {"parent": parent, "sub": sub, "grandchild": grandchild},
        "memberships": memberships,
    }


def test_a_parent_team_member_sees_sub_team_members_at_any_depth(
    server, model_registry
):
    tree = _hierarchy(server)
    people = tree["people"]
    parent = people["parent"].id

    assert _visible(model_registry, parent, people["sub"])
    assert _visible(model_registry, parent, people["grandchild"])
    assert _visible(model_registry, parent, people["sibling"])
    # Upward, as before.
    assert _visible(model_registry, people["grandchild"].id, people["parent"])
    assert _visible(model_registry, people["grandchild"].id, people["sub"])


def test_the_hierarchy_walk_never_goes_sideways(server, model_registry):
    """Up from a membership or down from one, never up and then down: a
    sibling sub-team's member stays invisible, and so does anyone in an
    unrelated team."""
    tree = _hierarchy(server)
    people = tree["people"]

    assert not _visible(model_registry, people["sibling"].id, people["sub"])
    assert not _visible(model_registry, people["sub"].id, people["sibling"])
    assert not _visible(model_registry, people["sibling"].id, people["grandchild"])
    for name in ("parent", "sub", "grandchild", "sibling"):
        assert not _visible(model_registry, people["stranger"].id, people[name])
        assert not _visible(model_registry, people[name].id, people["stranger"])


def test_a_deleted_sub_team_ends_the_walk_down(server, model_registry):
    """Deleting the sub-team hides its members and everyone beneath it,
    though the grandchild team itself is not deleted."""
    tree = _hierarchy(server)
    people = tree["people"]
    TeamModel.DB(model_registry.DB.manager.Base).delete(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        id=tree["teams"]["sub"].id,
    )

    assert not _visible(model_registry, people["parent"].id, people["sub"])
    assert not _visible(model_registry, people["parent"].id, people["grandchild"])
    assert not _visible(model_registry, people["grandchild"].id, people["parent"])
    assert _visible(model_registry, people["parent"].id, people["sibling"])


@pytest.mark.parametrize(
    "ended",
    [
        pytest.param({"enabled": False}, id="disabled"),
        pytest.param(
            {"expires_at": datetime.now(timezone.utc) - timedelta(days=1)},
            id="expired",
        ),
        pytest.param({"deleted_at": datetime.now(timezone.utc)}, id="soft-deleted"),
    ],
)
def test_an_ended_membership_ends_the_walk_down(
    server, model_registry, ended: Dict[str, Any]
):
    tree = _hierarchy(server)
    people = tree["people"]
    _change_membership(model_registry, tree["memberships"]["grandchild"], **ended)

    assert not _visible(model_registry, people["parent"].id, people["grandchild"])
    assert _visible(model_registry, people["parent"].id, people["sub"])


def test_the_viewers_own_ended_membership_ends_the_walk_down(server, model_registry):
    """The requester's side must be live too: a parent-team member whose
    membership ended sees no one below it."""
    tree = _hierarchy(server)
    people = tree["people"]
    viewer = create_user(server)
    membership = _join(server, viewer, tree["teams"]["parent"])
    assert _visible(model_registry, viewer.id, people["grandchild"])

    _change_membership(model_registry, membership, enabled=False)

    assert not _visible(model_registry, viewer.id, people["sub"])
    assert not _visible(model_registry, viewer.id, people["grandchild"])


def test_a_sub_teams_members_are_listed_to_a_parent_team_member(server, model_registry):
    """Listing a team's users follows the users rule's reach, down included,
    so a parent-team member lists a sub-team's members and a sibling
    sub-team's member does not."""
    tree = _hierarchy(server)
    people = tree["people"]
    sub = tree["teams"]["sub"]

    lister = UserManager(
        requester_id=people["parent"].id, model_registry=model_registry
    )
    assert people["sub"].id in {user.id for user in lister.list(team_id=sub.id)}
    assert people["sub"].id in _graphql_team_members(server, people["parent"], sub)

    sideways = UserManager(
        requester_id=people["sibling"].id, model_registry=model_registry
    )
    assert sideways.list(team_id=sub.id) == []
    assert _graphql_team_members(server, people["sibling"], sub) == set()


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


@pytest.mark.parametrize("internal", ["ROOT_ID", "SYSTEM_ID", "TEMPLATE_ID"])
def test_the_internal_accounts_do_not_exist_for_a_user(
    server, model_registry, internal: str
):
    """``check_permission`` granted VIEW on any SYSTEM- or TEMPLATE-created
    row before the users rule ran, so ``exists()`` on the seeded internal
    accounts answered true for anyone. The users rule alone decides now
    (it excludes the users table from those grants, as the filter does)."""
    viewer = create_user(server)
    users = UserModel.DB(model_registry.DB.manager.Base)

    assert not users.exists(
        requester_id=viewer.id, model_registry=model_registry, id=env(internal)
    )
    assert users.exists(
        requester_id=viewer.id, model_registry=model_registry, id=viewer.id
    )
    assert users.exists(
        requester_id=env("ROOT_ID"), model_registry=model_registry, id=env(internal)
    )


def test_a_system_provisioned_user_is_no_more_visible_than_any(server, model_registry):
    """A user row recorded as SYSTEM's (a create stamps each user as its own
    creator, but rows written past it, such as by an import, may name
    SYSTEM) is visible to its teammates, not to everyone."""
    stranger, teammate = create_user(server), create_user(server)
    users = UserModel.DB(model_registry.DB.manager.Base)
    provisioned = users.create(
        requester_id=env("SYSTEM_ID"),
        model_registry=model_registry,
        return_type="dto",
        override_dto=UserModel,
        email=f"provisioned_{uuid.uuid4().hex[:8]}@example.com",
    )
    users.update(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        id=provisioned.id,
        new_properties={"created_by_user_id": env("SYSTEM_ID")},
    )
    _join(server, provisioned, _team(server, teammate))

    assert not users.exists(
        requester_id=stranger.id, model_registry=model_registry, id=provisioned.id
    )
    assert not _visible(model_registry, stranger.id, provisioned)
    assert users.exists(
        requester_id=teammate.id, model_registry=model_registry, id=provisioned.id
    )


def test_get_by_team_is_an_unknown_filter_like_any_other(server, model_registry):
    """``UserManager.get(team_id=…)`` called ``user_has_read_access`` with a
    signature it does not have (and a team id as the user id), so it raised
    TypeError. Users have no team column: ``team_id`` is refused as any
    unknown filter is."""
    viewer = create_user(server)
    team = _team(server, viewer)
    users = UserManager(requester_id=viewer.id, model_registry=model_registry)
    assert users.get(id=viewer.id).id == viewer.id

    with pytest.raises(Exception) as unknown:
        users.get(id=viewer.id, no_such_column="x")
    with pytest.raises(Exception) as by_team:
        users.get(id=viewer.id, team_id=team.id)
    assert type(by_team.value) is type(unknown.value)
    assert not isinstance(by_team.value, TypeError)


def _graphql_team_members(server: Any, viewer: Any, team: Any) -> Any:
    response = server.post(
        "/graphql",
        json={"query": '{ users(teamId: "%s") { id } }' % team.id},
        headers={"Authorization": f"Bearer {viewer.jwt}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "errors" not in body, body
    return {row["id"] for row in body["data"]["users"]}


def test_graphql_lists_a_teams_live_members(server, model_registry):
    """``users(teamId:)`` filtered users on a ``team_id`` column they do not
    have, so every such query errored. It lists the team's live members, to
    those who could see them through that team."""
    owner, member, departed = (
        create_user(server),
        create_user(server),
        create_user(server),
    )
    team = _team(server, owner)
    _join(server, member, team)
    ended = _join(server, departed, team)
    _change_membership(model_registry, ended, enabled=False)
    elsewhere = _team(server, create_user(server))

    assert _graphql_team_members(server, owner, team) == {owner.id, member.id}
    assert _graphql_team_members(server, member, team) == {owner.id, member.id}
    assert _graphql_team_members(server, member, elsewhere) == set()


def test_a_teams_members_are_not_listed_to_an_outsider(server, model_registry):
    """Sharing another team with a member does not reveal what else they
    belong to."""
    outsider, member = create_user(server), create_user(server)
    team = _team(server, create_user(server))
    _join(server, member, team)
    _join(server, member, _team(server, outsider))
    assert _visible(model_registry, outsider.id, member)

    users = UserManager(requester_id=outsider.id, model_registry=model_registry)
    assert users.list(team_id=team.id) == []
    assert _graphql_team_members(server, outsider, team) == set()

    root = UserManager(requester_id=env("ROOT_ID"), model_registry=model_registry)
    assert member.id in {user.id for user in root.list(team_id=team.id)}
