# SPDX-License-Identifier: AGPL-3.0-or-later
"""``?<field>__in=a,b,c`` on a generated LIST: the rows whose field is one
of the listed values, in one request (a page naming a dozen linked records
by id reads them at once). At most a batch's worth of values; it combines
with the other filters and never widens what the requester may read."""

import uuid
from typing import Any, Dict, List

import pytest

from zephyrex.logic.AbstractLogicManager.manager import _in_values
from zephyrex.pydantic2.fastapi.limits import MAX_BATCH_SIZE

TEAMS = "/v1/team"


def _headers(user: Any) -> Dict[str, str]:
    return {"Authorization": f"Bearer {user.jwt}"}


def _teams(server: Any, user: Any, count: int, prefix: str) -> List[Dict[str, Any]]:
    made = []
    for index in range(count):
        response = server.post(
            TEAMS,
            json={"team": {"name": f"{prefix} {index}", "description": "in"}},
            headers=_headers(user),
        )
        assert response.status_code == 201, response.text
        made.append(response.json()["team"])
    return made


def _ids(response: Any) -> List[str]:
    assert response.status_code == 200, response.text
    return sorted(team["id"] for team in response.json()["teams"])


def test_the_listed_ids_and_no_others(server, admin_a):
    first, second, third = _teams(server, admin_a, 3, f"In {uuid.uuid4().hex[:6]}")
    wanted = sorted([first["id"], third["id"]])
    response = server.get(
        TEAMS, params={"id__in": ",".join(wanted)}, headers=_headers(admin_a)
    )
    assert _ids(response) == wanted
    assert second["id"] not in _ids(response)


def test_it_combines_with_the_other_filters(server, admin_a):
    tag = uuid.uuid4().hex[:6]
    first, second = _teams(server, admin_a, 2, f"Alpha {tag}")
    (other,) = _teams(server, admin_a, 1, f"Beta {tag}")
    response = server.get(
        TEAMS,
        params={
            "id__in": f"{first['id']}, {other['id']}",
            "name__inc": "Alpha",
        },
        headers=_headers(admin_a),
    )
    assert _ids(response) == [first["id"]]


def test_it_never_widens_what_the_requester_reads(server, admin_a, admin_b):
    (mine,) = _teams(server, admin_a, 1, f"Mine {uuid.uuid4().hex[:6]}")
    (theirs,) = _teams(server, admin_b, 1, f"Theirs {uuid.uuid4().hex[:6]}")
    response = server.get(
        TEAMS,
        params={"id__in": f"{mine['id']},{theirs['id']}"},
        headers=_headers(admin_a),
    )
    assert _ids(response) == [mine["id"]]


def test_at_most_a_batch_of_values(server, admin_a):
    too_many = ",".join(str(uuid.uuid4()) for _ in range(MAX_BATCH_SIZE + 1))
    response = server.get(TEAMS, params={"id__in": too_many}, headers=_headers(admin_a))
    assert response.status_code == 422, response.text


def test_an_unknown_field_is_refused(server, admin_a):
    response = server.get(TEAMS, params={"nope__in": "a,b"}, headers=_headers(admin_a))
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "raw, numeric, boolean, expected",
    [
        ("a, b,,c", False, False, ["a", "b", "c"]),
        (["a", "b"], False, False, ["a", "b"]),
        ("1,2.5", True, False, [1, 2.5]),
        ("true,False", False, True, [True, False]),
    ],
)
def test_values_are_read_as_the_fields_type(raw, numeric, boolean, expected):
    assert _in_values(raw, numeric, boolean) == expected


def test_a_non_number_for_a_number_field_is_422():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as refused:
        _in_values("1,x", True, False)
    assert refused.value.status_code == 422
