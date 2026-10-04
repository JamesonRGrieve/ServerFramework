# SPDX-License-Identifier: AGPL-3.0-or-later
"""What an ``include`` may load, on a real database.

The hole this closes: ``include=`` on ``get``, ``list`` and ``search``
(REST ``?include=`` too) eager-loaded the named ORM relationships with an
unfiltered ``joinedload``, so a requester who could read a record read every
record it reached: a team's sub-teams that other users made, the parent team
of a team they may no longer see into, any extension's parent or child rows.
The REST layer re-resolved only what was missing, and kept whatever the join
had loaded.

The rule now: an included record answers to the VIEW filter a direct read of
it applies, as the requester. One they could not read is ``None``, and a
collection holds only its visible members. Visible rows load as before, in
one query per relationship and level whatever the number of rows.

``user`` / ``role`` / ``team`` includes of a reference column (a
membership's ``user``) are not ORM relationships on the core models; the
REST layer and GraphQL resolve them through a permission-filtered read as
the requester. The guards below hold them to the same rule."""

import uuid
from typing import Any, Dict, Iterator, List, Optional

import pytest
from fastapi import HTTPException
from sqlalchemy import event

from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Auth import (
    RoleManager,
    TeamManager,
    TeamModel,
    UserManager,
    UserModel,
    UserTeamManager,
    UserTeamModel,
)
from zephyrex.testing.factories import (
    add_user_to_team,
    create_role,
    create_team,
    create_user,
)


def _team(server: Any, owner: UserModel, parent_id: Optional[str] = None) -> Any:
    return create_team(
        server, owner.id, name=f"include {uuid.uuid4().hex[:8]}", parent_id=parent_id
    )


def _membership_of(model_registry: Any, user: Any, team: Any) -> str:
    """The id of ``user``'s membership in ``team``."""
    return str(
        UserTeamModel.DB(model_registry.DB.manager.Base).list(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            user_id=user.id,
            team_id=team.id,
        )[0]["id"]
    )


def _disable(model_registry: Any, membership_id: str) -> None:
    UserTeamModel.DB(model_registry.DB.manager.Base).update(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        id=membership_id,
        new_properties={"enabled": False},
    )


def _ids(records: List[Dict[str, Any]]) -> List[str]:
    """The ids of included records (a nested collection serializes as
    dicts)."""
    return [record["id"] for record in records]


def _readable(manager: Any, record_id: str) -> bool:
    """Whether ``manager``'s requester reads the record directly."""
    try:
        manager.get(id=record_id)
    except HTTPException as refused:
        assert refused.status_code in (403, 404), refused.detail
        return False
    return True


def _teams(model_registry: Any, requester_id: str) -> TeamManager:
    return TeamManager(requester_id=requester_id, model_registry=model_registry)


def _bearer(user: Any) -> Dict[str, str]:
    return {"Authorization": f"Bearer {user.jwt}"}


@pytest.fixture
def sub_teams(server: Any, model_registry: Any) -> Dict[str, Any]:
    """A parent team with a sub-team its owner made and one another user
    made under it, which the owner cannot read."""
    owner, other = create_user(server), create_user(server)
    parent = _team(server, owner)
    mine = _team(server, owner, parent_id=parent.id)
    theirs = _team(server, other, parent_id=parent.id)
    teams = _teams(model_registry, owner.id)
    assert _readable(teams, mine.id)
    assert not _readable(teams, theirs.id)
    return {"owner": owner, "parent": parent, "mine": mine, "theirs": theirs}


def test_a_collection_include_lists_only_visible_members(
    model_registry: Any, sub_teams: Dict[str, Any]
):
    teams = _teams(model_registry, sub_teams["owner"].id)
    parent, mine = sub_teams["parent"], sub_teams["mine"]

    got = teams.get(id=parent.id, include="children")
    assert _ids(got.children) == [mine.id]
    listed = teams.list(id=parent.id, include=["children"])
    assert [_ids(team.children) for team in listed] == [[mine.id]]
    searched = teams.search(id=parent.id, include="children")
    assert [_ids(team.children) for team in searched] == [[mine.id]]


def test_root_includes_every_member(model_registry: Any, sub_teams: Dict[str, Any]):
    root = _teams(model_registry, env("ROOT_ID"))
    children = root.get(id=sub_teams["parent"].id, include="children").children
    assert set(_ids(children)) == {
        sub_teams["mine"].id,
        sub_teams["theirs"].id,
    }


def test_a_nested_include_filters_every_level(server, model_registry, sub_teams):
    """``children.children``: a grandchild someone else made under the
    owner's own sub-team is not the owner's to read, so it is not listed."""
    owner, mine = sub_teams["owner"], sub_teams["mine"]
    grandchild = _team(server, create_user(server), parent_id=mine.id)
    teams = _teams(model_registry, owner.id)
    assert not _readable(teams, grandchild.id)

    got = teams.get(id=sub_teams["parent"].id, include="children.children")
    assert _ids(got.children) == [mine.id]
    assert got.children[0]["children"] == []

    root = _teams(model_registry, env("ROOT_ID"))
    by_root = root.get(id=sub_teams["parent"].id, include="children.children")
    nested = {child["id"]: child["children"] for child in by_root.children}
    assert _ids(nested[mine.id]) == [grandchild.id]


@pytest.fixture
def lapsed_creator(server: Any, model_registry: Any) -> Dict[str, Any]:
    """A team its creator still reads (as its creator) after their membership
    lapsed, under a parent team someone else owns, which they then no longer
    read."""
    creator, stranger = create_user(server), create_user(server)
    parent = _team(server, stranger)
    child = _team(server, creator, parent_id=parent.id)
    teams = _teams(model_registry, creator.id)
    assert teams.get(id=child.id, include="parent").parent["id"] == parent.id

    _disable(model_registry, _membership_of(model_registry, creator, child))
    assert _readable(teams, child.id)
    assert not _readable(teams, parent.id)
    return {"creator": creator, "parent": parent, "child": child}


def test_an_included_parent_the_requester_cannot_see_is_none(
    model_registry: Any, lapsed_creator: Dict[str, Any]
):
    teams = _teams(model_registry, lapsed_creator["creator"].id)
    child = lapsed_creator["child"]

    assert teams.get(id=child.id, include="parent").parent is None
    assert [team.parent for team in teams.list(id=child.id, include="parent")] == [None]
    searched = teams.search(id=child.id, include=["parent"])
    assert [team.parent for team in searched] == [None]


def test_rest_include_parent_returns_no_hidden_team(
    server: Any, lapsed_creator: Dict[str, Any]
):
    creator, child = lapsed_creator["creator"], lapsed_creator["child"]
    hidden = lapsed_creator["parent"]

    got = server.get(f"/v1/team/{child.id}?include=parent", headers=_bearer(creator))
    assert got.status_code == 200, got.text
    assert not got.json()["team"]["parent"]
    assert hidden.name not in got.text

    listed = server.get("/v1/team?include=parent", headers=_bearer(creator))
    assert listed.status_code == 200, listed.text
    rows = {team["id"]: team for team in listed.json()["teams"]}
    assert not rows[child.id]["parent"]
    assert hidden.name not in listed.text


@pytest.fixture
def statements(model_registry: Any) -> Iterator[List[str]]:
    """Every SQL statement run while the fixture is live."""
    engine = model_registry.DB.manager.engine
    seen: List[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_an_include_is_one_query_whatever_the_number_of_rows(
    server, model_registry, statements
):
    """Listing teams with ``children.children`` included costs the same
    number of statements for one team as for five."""

    def statements_for(team_count: int) -> int:
        owner = create_user(server)
        ids = []
        for _ in range(team_count):
            team = _team(server, owner)
            child = _team(server, owner, parent_id=team.id)
            _team(server, owner, parent_id=child.id)
            ids.append(team.id)
        teams = _teams(model_registry, owner.id)
        statements.clear()
        rows = teams.list(
            filters=[TeamModel.DB(model_registry.DB.manager.Base).id.in_(ids)],
            include=["children.children"],
        )
        assert len(rows) == team_count
        assert all(len(row.children[0]["children"]) == 1 for row in rows)
        return len(statements)

    assert statements_for(5) == statements_for(1)


# Guards: reference includes the core models resolve outside the ORM.


@pytest.fixture
def ended_member(server: Any, model_registry: Any) -> Dict[str, Any]:
    """A team owner and a member whose membership the owner still manages
    (and so reads) after it was disabled, when the member is no longer
    someone the owner may see."""
    owner, member = create_user(server), create_user(server)
    team = _team(server, owner)
    membership = add_user_to_team(server, member.id, team.id, env("USER_ROLE_ID"))
    users = UserManager(requester_id=owner.id, model_registry=model_registry)
    assert _readable(users, member.id)

    _disable(model_registry, membership.id)
    assert not _readable(users, member.id)
    memberships = UserTeamManager(requester_id=owner.id, model_registry=model_registry)
    assert _readable(memberships, membership.id)
    return {"owner": owner, "member": member, "team": team, "membership": membership}


def test_rest_include_user_returns_no_hidden_user(
    server: Any, ended_member: Dict[str, Any]
):
    owner, member = ended_member["owner"], ended_member["member"]
    membership = ended_member["membership"]

    got = server.get(
        f"/v1/user_team/{membership.id}?include=user", headers=_bearer(owner)
    )
    assert got.status_code == 200, got.text
    assert not got.json()["user_team"]["user"]
    assert member.email not in got.text

    listed = server.get(
        f"/v1/user_team?team_id={ended_member['team'].id}&include=user",
        headers=_bearer(owner),
    )
    assert listed.status_code == 200, listed.text
    rows = {row["id"]: row for row in listed.json()["user_teams"]}
    assert not rows[membership.id]["user"]
    own = _membership_of(server.app.state.model_registry, owner, ended_member["team"])
    assert rows[own]["user"]["id"] == owner.id
    assert member.email not in listed.text


def _graphql_membership_roles(server: Any, viewer: Any) -> Dict[str, Any]:
    response = server.post(
        "/graphql",
        json={"query": "{ userTeams { id role { id name } } }"},
        headers=_bearer(viewer),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "errors" not in body, body
    return {row["id"]: row["role"] for row in body["data"]["userTeams"]}


def test_graphql_navigation_to_a_role_resolves_as_the_requester(server, model_registry):
    """A member whose membership lapsed still reads their own membership
    row, but no longer the team's roles, so the row's ``role`` is null."""
    owner, member = create_user(server), create_user(server)
    team = _team(server, owner)
    role = create_role(server, env("ROOT_ID"), team.id, name=f"r{uuid.uuid4().hex[:8]}")
    membership = add_user_to_team(server, member.id, team.id, role.id)
    assert _graphql_membership_roles(server, member)[membership.id]["id"] == role.id

    _disable(model_registry, membership.id)
    roles = RoleManager(requester_id=member.id, model_registry=model_registry)
    assert not _readable(roles, role.id)

    assert _graphql_membership_roles(server, member)[membership.id] is None
