# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who may shape a team's membership (logic/BLL_Auth/team_authority.py).

Regression tests for escalations that were open:
- a member promoted themselves to superadmin through their own membership
  (PUT /v1/user/{me}/user_team/{id});
- a member joined any team, as admin, through their own membership
  (POST /v1/user/{me}/user_team);
- a team admin granted a role above their own (PATCH /v1/team/{t}/user/{u}),
  including superadmin;
- a custom role that merely extends ``user`` ranked as an admin.
Plus the member-removal route and its guards."""

import uuid
from typing import Any, Dict, Optional

from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Auth import RoleModel


def _headers(user: Any) -> Dict[str, str]:
    return {"Authorization": f"Bearer {user.jwt}"}


def _team(server: Any, owner: Any) -> Any:
    from zephyrex.testing.factories import create_team

    return create_team(server, owner.id, name=f"Authority {uuid.uuid4().hex[:8]}")


def _member(server: Any, team: Any, role_id: str) -> Any:
    from conftest import create_user
    from zephyrex.testing.factories import add_user_to_team

    user = create_user(server)
    add_user_to_team(server, user.id, team.id, role_id)
    return user


def _custom_role(server: Any, team: Any, parent_id: str) -> str:
    model_registry = server.app.state.model_registry
    role = RoleModel.DB(model_registry.DB.manager.Base).create(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        name=f"custom_{uuid.uuid4().hex[:8]}",
        friendly_name="Custom",
        team_id=team.id,
        parent_id=parent_id,
        return_type="dto",
        override_dto=RoleModel,
    )
    return str(role.id)


def _role_in(server: Any, owner: Any, team: Any, user: Any) -> Optional[str]:
    rows = server.get(f"/v1/team/{team.id}/user", headers=_headers(owner))
    assert rows.status_code == 200, rows.text
    return next(
        (r["role_id"] for r in rows.json()["user_teams"] if r["user_id"] == user.id),
        None,
    )


def _saving_membership(server: Any, actor: Any, team: Any, user: Any) -> Dict[str, str]:
    """``actor``'s headers for a save of ``user``'s membership, naming its
    version as ``actor`` reads it (none when they cannot read it, so a
    refusal is for who they are)."""
    from zephyrex.testing.factories import if_match_of

    rows = server.get(f"/v1/team/{team.id}/user", headers=_headers(actor))
    if rows.status_code != 200:
        return _headers(actor)
    mine = [r for r in rows.json()["user_teams"] if r["user_id"] == user.id]
    return {**_headers(actor), **(if_match_of(mine[0]) if mine else {})}


def _set_role(server: Any, actor: Any, team: Any, user: Any, role_id: str) -> Any:
    return server.patch(
        f"/v1/team/{team.id}/user/{user.id}",
        json={"user_team": {"role_id": role_id}},
        headers=_saving_membership(server, actor, team, user),
    )


def _remove(server: Any, actor: Any, team: Any, user: Any) -> Any:
    return server.delete(
        f"/v1/team/{team.id}/user/{user.id}",
        headers=_saving_membership(server, actor, team, user),
    )


# -- the member's own membership is read-only -------------------------------


def test_a_member_cannot_promote_themselves_through_their_membership(server):
    from conftest import create_user

    owner = create_user(server)
    team = _team(server, owner)
    member = _member(server, team, env("USER_ROLE_ID"))
    mine = server.get(f"/v1/user/{member.id}/user_team", headers=_headers(member))
    assert mine.status_code == 200, mine.text
    (membership,) = [r for r in mine.json()["user_teams"] if r["team_id"] == team.id]

    attempt = server.put(
        f"/v1/user/{member.id}/user_team/{membership['id']}",
        json={"user_team": {"role_id": env("SUPERADMIN_ROLE_ID")}},
        headers=_headers(member),
    )
    assert attempt.status_code in (404, 405), attempt.text
    assert _role_in(server, owner, team, member) == env("USER_ROLE_ID")


def test_a_member_cannot_join_a_team_through_their_membership(server):
    from conftest import create_user

    owner = create_user(server)
    other = _team(server, owner)
    outsider = create_user(server)

    attempt = server.post(
        f"/v1/user/{outsider.id}/user_team",
        json={
            "user_team": {
                "user_id": outsider.id,
                "team_id": other.id,
                "role_id": env("ADMIN_ROLE_ID"),
            }
        },
        headers=_headers(outsider),
    )
    assert attempt.status_code in (404, 405), attempt.text
    assert _role_in(server, owner, other, outsider) is None


def test_no_route_lets_a_non_admin_create_a_membership(server):
    """The rule lives in UserTeamManager, not only in the routes."""
    import pytest
    from fastapi import HTTPException

    from conftest import create_user
    from zephyrex.logic.BLL_Auth import UserTeamManager

    owner = create_user(server)
    team = _team(server, owner)
    outsider = create_user(server)
    with pytest.raises(HTTPException) as refused:
        UserTeamManager(
            requester_id=outsider.id, model_registry=server.app.state.model_registry
        ).create(user_id=outsider.id, team_id=team.id, role_id=env("ADMIN_ROLE_ID"))
    assert refused.value.status_code == 403


# -- role changes ------------------------------------------------------------


def test_an_admin_cannot_grant_a_role_above_their_own(server):
    from conftest import create_user

    owner = create_user(server)
    team = _team(server, owner)
    member = _member(server, team, env("USER_ROLE_ID"))

    for target in (member, owner):
        refused = _set_role(server, owner, team, target, env("SUPERADMIN_ROLE_ID"))
        assert refused.status_code == 403, refused.text
    assert _role_in(server, owner, team, member) == env("USER_ROLE_ID")

    allowed = _set_role(server, owner, team, member, env("ADMIN_ROLE_ID"))
    assert allowed.status_code == 200, allowed.text
    assert _role_in(server, owner, team, member) == env("ADMIN_ROLE_ID")


def test_an_admin_cannot_change_a_member_above_their_role(server):
    from conftest import create_user

    owner = create_user(server)
    team = _team(server, owner)
    superadmin = _member(server, team, env("SUPERADMIN_ROLE_ID"))

    refused = _set_role(server, owner, team, superadmin, env("USER_ROLE_ID"))
    assert refused.status_code == 403, refused.text
    assert _role_in(server, owner, team, superadmin) == env("SUPERADMIN_ROLE_ID")


def test_a_plain_member_cannot_change_roles(server):
    from conftest import create_user

    owner = create_user(server)
    team = _team(server, owner)
    member = _member(server, team, env("USER_ROLE_ID"))

    refused = _set_role(server, member, team, member, env("ADMIN_ROLE_ID"))
    assert refused.status_code == 403, refused.text


# -- the admin subtree -------------------------------------------------------


def test_a_role_extending_user_does_not_administer_the_team(server):
    from conftest import create_user

    owner = create_user(server)
    team = _team(server, owner)
    moderator = _member(server, team, _custom_role(server, team, env("USER_ROLE_ID")))

    invite = server.post(
        "/v1/invitation",
        json={"invitation": {"team_id": team.id, "role_id": env("USER_ROLE_ID")}},
        headers=_headers(moderator),
    )
    assert invite.status_code == 403, invite.text


def test_a_role_extending_admin_administers_the_team(server):
    from conftest import create_user

    owner = create_user(server)
    team = _team(server, owner)
    lead = _member(server, team, _custom_role(server, team, env("ADMIN_ROLE_ID")))

    invite = server.post(
        "/v1/invitation",
        json={"invitation": {"team_id": team.id, "role_id": env("USER_ROLE_ID")}},
        headers=_headers(lead),
    )
    assert invite.status_code == 201, invite.text


# -- removing members --------------------------------------------------------


def test_an_admin_removes_a_member(server):
    from conftest import create_user

    owner = create_user(server)
    team = _team(server, owner)
    member = _member(server, team, env("USER_ROLE_ID"))

    removed = _remove(server, owner, team, member)
    assert removed.status_code == 204, removed.text
    assert _role_in(server, owner, team, member) is None


def test_a_member_may_leave(server):
    from conftest import create_user

    owner = create_user(server)
    team = _team(server, owner)
    member = _member(server, team, env("USER_ROLE_ID"))

    left = _remove(server, member, team, member)
    assert left.status_code == 204, left.text
    assert _role_in(server, owner, team, member) is None


def test_removal_is_refused_to_outsiders_members_and_lower_ranks(server):
    from conftest import create_user

    owner = create_user(server)
    team = _team(server, owner)
    member = _member(server, team, env("USER_ROLE_ID"))
    superadmin = _member(server, team, env("SUPERADMIN_ROLE_ID"))
    outsider = create_user(server)

    for actor, target in ((outsider, member), (member, owner), (owner, superadmin)):
        refused = _remove(server, actor, team, target)
        assert refused.status_code == 403, (actor.id, target.id, refused.text)
    assert _role_in(server, owner, team, superadmin) == env("SUPERADMIN_ROLE_ID")


def test_an_admin_cannot_change_their_own_membership(server):
    """A demotion is another admin's call, so a lone admin cannot strand
    the team by demoting themselves."""
    from conftest import create_user

    owner = create_user(server)
    team = _team(server, owner)
    refused = _set_role(server, owner, team, owner, env("USER_ROLE_ID"))
    assert refused.status_code == 403, refused.text
    assert _role_in(server, owner, team, owner) == env("ADMIN_ROLE_ID")


def test_the_last_admin_cannot_be_demoted_even_by_root(server):
    import pytest
    from fastapi import HTTPException

    from conftest import create_user
    from zephyrex.logic.BLL_Auth import UserTeamManager, UserTeamModel

    owner = create_user(server)
    team = _team(server, owner)
    registry = server.app.state.model_registry
    (membership,) = UserTeamModel.DB(registry.DB.manager.Base).list(
        requester_id=env("ROOT_ID"),
        model_registry=registry,
        team_id=team.id,
        user_id=owner.id,
        return_type="dto",
        override_dto=UserTeamModel,
    )
    root = UserTeamManager(requester_id=env("ROOT_ID"), model_registry=registry)
    for change in ({"role_id": env("USER_ROLE_ID")}, {"enabled": False}):
        with pytest.raises(HTTPException) as refused:
            root.update(membership.id, **change)
        assert refused.value.status_code == 409


def test_the_last_admin_cannot_leave(server):
    from conftest import create_user

    owner = create_user(server)
    team = _team(server, owner)

    refused = _remove(server, owner, team, owner)
    assert refused.status_code == 409, refused.text
