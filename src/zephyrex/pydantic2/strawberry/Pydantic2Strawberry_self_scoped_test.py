# SPDX-License-Identifier: AGPL-3.0-or-later
"""Only the User entity gets the self-scoped GraphQL resolvers.

Users read, update and delete only themselves, so those fields take no
``id``. Other entities whose manager name merely contains "User" (UserTeam,
UserCredential, ...) act on the row the caller names.
"""

_FIELD_ARGS = """
{
  __schema {
    queryType { fields { name args { name } } }
    mutationType { fields { name args { name } } }
  }
}
"""


def _field_args(server, admin_a):
    response = server.post(
        "/graphql",
        json={"query": _FIELD_ARGS},
        headers={"Authorization": f"Bearer {admin_a.jwt}"},
    )
    assert response.status_code == 200, response.text
    schema = response.json()["data"]["__schema"]
    fields = schema["queryType"]["fields"] + schema["mutationType"]["fields"]
    return {f["name"]: {a["name"] for a in f["args"]} for f in fields}


def test_user_fields_act_on_the_requester(server, admin_a):
    args = _field_args(server, admin_a)

    assert "id" not in args["user"]
    assert "id" not in args["updateUser"]
    assert "id" not in args["deleteUser"]


def test_user_team_fields_take_the_target_id(server, admin_a):
    args = _field_args(server, admin_a)

    assert "id" in args["userTeam"]
    assert "filter" in args["userTeams"]
    assert "id" in args["updateUserTeam"]
    assert "id" in args["deleteUserTeam"]
