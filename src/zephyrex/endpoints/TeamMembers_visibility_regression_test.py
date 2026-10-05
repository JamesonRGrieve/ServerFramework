# SPDX-License-Identifier: AGPL-3.0-or-later
"""A team's memberships answer to the team alone.

The server writes memberships as ROOT (creating a team gives its creator an
admin membership; accepting an invitation grants one), and ROOT-written rows
were hidden from everyone else: an admin saw only their own membership and a
member only theirs (found by the client's smoke test against 0.0.1a2). A
SYSTEM-written membership, conversely, was shown to every user.
"""

import uuid
from typing import Any, Dict, Set

from zephyrex.lib.Environment import env


def _headers(user: Any) -> Dict[str, str]:
    return {"Authorization": f"Bearer {user.jwt}"}


def _members(server: Any, viewer: Any, team_id: str) -> Set[str]:
    response = server.get(f"/v1/team/{team_id}/user", headers=_headers(viewer))
    assert response.status_code in (200, 404), response.text
    if response.status_code == 404:
        return set()
    return {row["user_id"] for row in response.json()["user_teams"]}


def _team_over_rest(server: Any, owner: Any) -> str:
    made = server.post(
        "/v1/team",
        json={"team": {"name": f"Members {uuid.uuid4().hex[:8]}"}},
        headers=_headers(owner),
    )
    assert made.status_code == 201, made.text
    return str(made.json()["team"]["id"])


def _join_by_invitation(server: Any, admin: Any, team_id: str, user: Any) -> None:
    invited = server.post(
        "/v1/invitation",
        json={
            "invitation": {
                "team_id": team_id,
                "role_id": env("USER_ROLE_ID"),
                "email": user.email,
            }
        },
        headers=_headers(admin),
    )
    assert invited.status_code == 201, invited.text
    invitation = invited.json()["invitation"]
    accepted = server.patch(
        f"/v1/invitation/{invitation['id']}",
        json={"invitation": {"invitation_code": invitation["code"]}},
        headers=_headers(user),
    )
    assert accepted.status_code == 200, accepted.text


def test_members_the_server_wrote_see_each_other(server):
    from conftest import create_user

    ada, ben, carl = create_user(server), create_user(server), create_user(server)
    team_id = _team_over_rest(server, ada)
    _join_by_invitation(server, ada, team_id, ben)

    both = {ada.id, ben.id}
    assert _members(server, ada, team_id) == both
    assert _members(server, ben, team_id) == both
    assert _members(server, carl, team_id) == set()


def test_a_system_written_membership_is_not_shown_to_everyone(server):
    from conftest import create_user
    from zephyrex.logic.BLL_Auth import UserTeamModel

    ada, dan, carl = create_user(server), create_user(server), create_user(server)
    team_id = _team_over_rest(server, ada)
    registry = server.app.state.model_registry
    UserTeamModel.DB(registry.DB.manager.Base).create(
        requester_id=env("SYSTEM_ID"),
        model_registry=registry,
        user_id=dan.id,
        team_id=team_id,
        role_id=env("USER_ROLE_ID"),
    )

    assert dan.id in _members(server, ada, team_id)
    assert _members(server, carl, team_id) == set()
