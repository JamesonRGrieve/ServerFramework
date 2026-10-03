# SPDX-License-Identifier: AGPL-3.0-or-later
"""Optimistic concurrency over the generic REST and GraphQL surfaces.

A record's ETag is ``"<updated_at>"`` (``created_at`` before any update),
exactly as its body serialises it. GET, create and update answer with it; a
PUT or DELETE naming a version that is no longer current is refused with 412
and the record as it stands, a batch with one stale record is refused whole,
and with IF_MATCH_REQUIRED a save naming no version is refused with 428.

Regression: before this, the REST PUT never handed If-Match to the manager,
DELETE and batches had no check at all, and the manager compared a hash of
the timestamp by substring, so clients could not detect a concurrent edit.
"""

import uuid
from typing import Any, Dict, Iterator, List

import pytest

from zephyrex.lib import Environment
from zephyrex.lib.Preconditions import (
    IF_MATCH_REQUIRED_SETTING,
    REQUIRED_DETAIL,
    entity_etag,
)

TEAMS = "/v1/team"
STALE = '"1970-01-01T00:00:00"'


def _headers(user: Any, **extra: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {user.jwt}", **extra}


def _create(server: Any, user: Any) -> Any:
    name = f"Versioned {uuid.uuid4().hex[:8]}"
    return server.post(
        TEAMS,
        json={"team": {"name": name, "description": "concurrency"}},
        headers=_headers(user),
    )


def _team(server: Any, user: Any) -> Dict[str, Any]:
    response = _create(server, user)
    assert response.status_code == 201, response.text
    team: Dict[str, Any] = response.json()["team"]
    return team


def _get(server: Any, user: Any, team_id: str) -> Any:
    return server.get(f"{TEAMS}/{team_id}", headers=_headers(user))


def _put(server: Any, user: Any, team_id: str, **headers: str) -> Any:
    return server.put(
        f"{TEAMS}/{team_id}",
        json={"team": {"name": f"Renamed {uuid.uuid4().hex[:8]}"}},
        headers=_headers(user, **headers),
    )


def _if_match(etag: str) -> Dict[str, str]:
    return {"If-Match": etag}


@pytest.fixture
def if_match_required(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Run the test with IF_MATCH_REQUIRED on, as ``env()`` reads it."""
    monkeypatch.setenv(IF_MATCH_REQUIRED_SETTING, "true")
    monkeypatch.setattr(Environment.settings, IF_MATCH_REQUIRED_SETTING, "true")
    yield


class TestTheETag:
    def test_create_answers_with_the_records_version(self, server, admin_a):
        response = _create(server, admin_a)
        assert response.status_code == 201, response.text
        team = response.json()["team"]
        assert response.headers["etag"] == f'"{team["updated_at"]}"'

    def test_get_answers_with_the_version_in_the_body(self, server, admin_a):
        team = _team(server, admin_a)
        response = _get(server, admin_a, team["id"])
        assert response.status_code == 200, response.text
        body = response.json()["team"]
        assert response.headers.get_list("etag") == [f'"{body["updated_at"]}"']
        assert response.headers["etag"] == entity_etag(team)

    def test_a_projection_without_timestamps_still_carries_it(self, server, admin_a):
        team = _team(server, admin_a)
        response = server.get(
            f"{TEAMS}/{team['id']}?fields=id,name", headers=_headers(admin_a)
        )
        assert response.status_code == 200, response.text
        assert "updated_at" not in response.json()["team"]
        assert response.headers["etag"] == entity_etag(team)

    def test_a_conditional_get_naming_it_is_304(self, server, admin_a):
        team = _team(server, admin_a)
        etag = entity_etag(team)
        assert etag is not None
        unchanged = server.get(
            f"{TEAMS}/{team['id']}",
            headers=_headers(admin_a, **{"If-None-Match": etag}),
        )
        assert unchanged.status_code == 304
        assert unchanged.headers["etag"] == etag
        changed = server.get(
            f"{TEAMS}/{team['id']}",
            headers=_headers(admin_a, **{"If-None-Match": STALE}),
        )
        assert changed.status_code == 200

    def test_a_list_keeps_its_body_hash_validator(self, server, admin_a):
        listed = server.get(TEAMS, headers=_headers(admin_a))
        assert listed.status_code == 200, listed.text
        etag = listed.headers["etag"]
        assert etag.startswith('W/"')
        again = server.get(TEAMS, headers=_headers(admin_a, **{"If-None-Match": etag}))
        assert again.status_code == 304


class TestSave:
    def test_the_current_version_saves_and_answers_with_the_next(self, server, admin_a):
        team = _team(server, admin_a)
        etag = entity_etag(team)
        assert etag is not None
        saved = _put(server, admin_a, team["id"], **_if_match(etag))
        assert saved.status_code == 200, saved.text
        body = saved.json()["team"]
        assert saved.headers["etag"] == f'"{body["updated_at"]}"'
        assert saved.headers["etag"] != etag
        assert (
            _get(server, admin_a, team["id"]).headers["etag"] == saved.headers["etag"]
        )

    def test_a_stale_version_is_refused_with_the_current_record(self, server, admin_a):
        team = _team(server, admin_a)
        first = entity_etag(team)
        assert first is not None
        assert _put(server, admin_a, team["id"], **_if_match(first)).status_code == 200
        current = _get(server, admin_a, team["id"]).json()["team"]

        refused = _put(server, admin_a, team["id"], **_if_match(first))
        assert refused.status_code == 412, refused.text
        body = refused.json()
        assert set(body) == {"detail", "current"}
        assert isinstance(body["detail"], str)
        assert body["current"]["id"] == team["id"]
        assert body["current"]["name"] == current["name"]
        assert body["current"]["updated_at"] == current["updated_at"]
        assert refused.headers["etag"] == entity_etag(current)
        # Nothing was written.
        assert _get(server, admin_a, team["id"]).json()["team"] == current

    @pytest.mark.parametrize(
        "spelling",
        ["weak", "bare", "listed", "any"],
    )
    def test_every_spelling_of_the_current_version_saves(
        self, server, admin_a, spelling
    ):
        team = _team(server, admin_a)
        version = team["updated_at"]
        header = {
            "weak": f'W/"{version}"',
            "bare": version,
            "listed": f'{STALE}, "{version}"',
            "any": "*",
        }[spelling]
        saved = _put(server, admin_a, team["id"], **_if_match(header))
        assert saved.status_code == 200, saved.text

    def test_no_version_saves_while_lenient(self, server, admin_a):
        team = _team(server, admin_a)
        assert _put(server, admin_a, team["id"]).status_code == 200

    def test_no_version_is_428_when_required(self, server, admin_a, if_match_required):
        team = _team(server, admin_a)
        refused = _put(server, admin_a, team["id"])
        assert refused.status_code == 428, refused.text
        assert refused.json() == {"detail": REQUIRED_DETAIL}
        etag = entity_etag(team)
        assert etag is not None
        assert _put(server, admin_a, team["id"], **_if_match(etag)).status_code == 200
        assert _put(server, admin_a, team["id"], **_if_match(etag)).status_code == 412

    def test_reads_and_creates_need_no_version_when_required(
        self, server, admin_a, if_match_required
    ):
        team = _team(server, admin_a)
        assert _get(server, admin_a, team["id"]).status_code == 200


class TestDelete:
    def _delete(self, server: Any, user: Any, team_id: str, **headers: str) -> Any:
        return server.delete(f"{TEAMS}/{team_id}", headers=_headers(user, **headers))

    def test_a_stale_version_is_refused_and_the_record_stays(self, server, admin_a):
        team = _team(server, admin_a)
        refused = self._delete(server, admin_a, team["id"], **_if_match(STALE))
        assert refused.status_code == 412, refused.text
        assert refused.json()["current"]["id"] == team["id"]
        assert _get(server, admin_a, team["id"]).status_code == 200

    def test_the_current_version_deletes(self, server, admin_a):
        team = _team(server, admin_a)
        etag = entity_etag(team)
        assert etag is not None
        deleted = self._delete(server, admin_a, team["id"], **_if_match(etag))
        assert deleted.status_code == 204, deleted.text
        assert _get(server, admin_a, team["id"]).status_code == 404

    def test_no_version_deletes_while_lenient(self, server, admin_a):
        team = _team(server, admin_a)
        assert self._delete(server, admin_a, team["id"]).status_code == 204

    def test_no_version_is_428_when_required(self, server, admin_a, if_match_required):
        team = _team(server, admin_a)
        refused = self._delete(server, admin_a, team["id"])
        assert refused.status_code == 428, refused.text
        assert refused.json() == {"detail": REQUIRED_DETAIL}
        assert _get(server, admin_a, team["id"]).status_code == 200


class TestMembership:
    """/v1/team/{team_id}/user/{user_id} names its record, the membership,
    by two ids; it is held to the membership row's version."""

    def _member(self, server, admin_a, admin_b) -> Dict[str, Any]:
        from zephyrex.lib.Environment import env
        from zephyrex.testing.factories import add_user_to_team

        team = _team(server, admin_a)
        add_user_to_team(server, admin_b.id, team["id"], env("USER_ROLE_ID"))
        listed = server.get(f"{TEAMS}/{team['id']}/user", headers=_headers(admin_a))
        assert listed.status_code == 200, listed.text
        rows = listed.json()["user_teams"]
        mine = [row for row in rows if row["user_id"] == admin_b.id]
        assert len(mine) == 1, rows
        (membership,) = mine
        return {"team": team, "membership": membership}

    def _path(self, made: Dict[str, Any]) -> str:
        return f"{TEAMS}/{made['team']['id']}/user/{made['membership']['user_id']}"

    def test_a_stale_role_change_is_refused(self, server, admin_a, admin_b):
        made = self._member(server, admin_a, admin_b)
        body = {"user_team": {"role_id": made["membership"]["role_id"]}}
        refused = server.patch(
            self._path(made), json=body, headers=_headers(admin_a, **_if_match(STALE))
        )
        assert refused.status_code == 412, refused.text
        assert refused.json()["current"]["id"] == made["membership"]["id"]
        etag = entity_etag(made["membership"])
        assert etag is not None
        saved = server.patch(
            self._path(made), json=body, headers=_headers(admin_a, **_if_match(etag))
        )
        assert saved.status_code == 200, saved.text

    def test_a_stale_removal_is_refused_and_the_member_stays(
        self, server, admin_a, admin_b
    ):
        made = self._member(server, admin_a, admin_b)
        refused = server.delete(
            self._path(made), headers=_headers(admin_a, **_if_match(STALE))
        )
        assert refused.status_code == 412, refused.text
        etag = entity_etag(made["membership"])
        assert etag is not None
        removed = server.delete(
            self._path(made), headers=_headers(admin_a, **_if_match(etag))
        )
        assert removed.status_code == 204, removed.text

    def test_a_removal_naming_no_version_is_428_when_required(
        self, server, admin_a, admin_b, if_match_required
    ):
        made = self._member(server, admin_a, admin_b)
        refused = server.delete(self._path(made), headers=_headers(admin_a))
        assert refused.status_code == 428, refused.text


class TestSelfScopedUser:
    """``PUT /v1/user`` (profile) and ``PATCH /v1/user`` (password) name no
    record in their path; both are held to the requester's own users row,
    whose ``"<updated_at ?? created_at>"`` a client sends.

    Regression: nothing bound their If-Match, so a stale profile save
    overwrote a newer one and a stale password change went through."""

    NEW_PASSWORD = "Another-passw0rd"

    @pytest.fixture
    def user(self, server: Any) -> Any:
        from zephyrex.testing.factories import create_user

        return create_user(server)

    def _me(self, server: Any, user: Any) -> Dict[str, Any]:
        response = server.get("/v1/user", headers=_headers(user))
        assert response.status_code == 200, response.text
        me: Dict[str, Any] = response.json()["user"]
        return me

    def _profile(self, server: Any, user: Any, name: str, **headers: str) -> Any:
        return server.put(
            "/v1/user",
            json={"user": {"display_name": name}},
            headers=_headers(user, **headers),
        )

    def _password(self, server: Any, user: Any, **headers: str) -> Any:
        from zephyrex.testing.factories import TEST_PASSWORD

        return server.patch(
            "/v1/user",
            json={"current_password": TEST_PASSWORD, "new_password": self.NEW_PASSWORD},
            headers=_headers(user, **headers),
        )

    def _signs_in_with(self, server: Any, user: Any, password: str) -> bool:
        import base64

        credentials = base64.b64encode(f"{user.email}:{password}".encode()).decode()
        response = server.post(
            "/v1/user/authorize", headers={"Authorization": f"Basic {credentials}"}
        )
        return response.status_code == 200 and "token" in response.json()

    def _make_stale(self, server: Any, user: Any) -> str:
        """The version the user read, then moved on by another save."""
        read = entity_etag(self._me(server, user))
        assert read is not None
        moved = self._profile(server, user, "Moved on", **_if_match(read))
        assert moved.status_code == 200, moved.text
        return read

    def test_a_stale_profile_save_is_refused_with_the_current_record(
        self, server, user
    ):
        stale = self._make_stale(server, user)
        current = self._me(server, user)

        refused = self._profile(server, user, "Stale", **_if_match(stale))
        assert refused.status_code == 412, refused.text
        body = refused.json()
        assert set(body) == {"detail", "current"}
        assert body["current"]["id"] == user.id
        assert body["current"]["updated_at"] == current["updated_at"]
        assert body["current"]["display_name"] == "Moved on"
        assert refused.headers["etag"] == entity_etag(current)
        assert self._me(server, user) == current

    def test_the_current_version_saves_the_profile(self, server, user):
        me = self._me(server, user)
        etag = entity_etag(me)
        assert etag is not None
        saved = self._profile(server, user, "Fresh", **_if_match(etag))
        assert saved.status_code == 200, saved.text
        after = self._me(server, user)
        assert after["display_name"] == "Fresh"
        assert entity_etag(after) != etag

    def test_a_profile_save_naming_no_version_saves_while_lenient(self, server, user):
        assert self._profile(server, user, "Unversioned").status_code == 200
        assert self._me(server, user)["display_name"] == "Unversioned"

    def test_a_profile_save_naming_no_version_is_428_when_required(
        self, server, user, if_match_required
    ):
        before = self._me(server, user)
        refused = self._profile(server, user, "Unversioned")
        assert refused.status_code == 428, refused.text
        assert refused.json() == {"detail": REQUIRED_DETAIL}
        assert self._me(server, user) == before

    def test_a_stale_password_change_is_refused_and_changes_nothing(self, server, user):
        from zephyrex.testing.factories import TEST_PASSWORD

        stale = self._make_stale(server, user)
        current = self._me(server, user)

        refused = self._password(server, user, **_if_match(stale))
        assert refused.status_code == 412, refused.text
        body = refused.json()
        assert body["current"]["id"] == user.id
        assert body["current"]["updated_at"] == current["updated_at"]
        assert self._signs_in_with(server, user, TEST_PASSWORD)
        assert not self._signs_in_with(server, user, self.NEW_PASSWORD)

    def test_the_current_version_changes_the_password(self, server, user):
        etag = entity_etag(self._me(server, user))
        assert etag is not None
        changed = self._password(server, user, **_if_match(etag))
        assert changed.status_code == 200, changed.text
        assert self._signs_in_with(server, user, self.NEW_PASSWORD)

    def test_a_password_change_naming_no_version_goes_through_while_lenient(
        self, server, user
    ):
        assert self._password(server, user).status_code == 200
        assert self._signs_in_with(server, user, self.NEW_PASSWORD)

    def test_a_password_change_naming_no_version_is_428_when_required(
        self, server, user, if_match_required
    ):
        from zephyrex.testing.factories import TEST_PASSWORD

        refused = self._password(server, user)
        assert refused.status_code == 428, refused.text
        assert refused.json() == {"detail": REQUIRED_DETAIL}
        assert self._signs_in_with(server, user, TEST_PASSWORD)


class TestBatch:
    def _batch_put(self, server: Any, user: Any, targets: List[Any]) -> Any:
        return server.put(
            TEAMS,
            json={"team": {"description": "batched"}, "target_ids": targets},
            headers=_headers(user),
        )

    def test_one_stale_item_refuses_the_whole_batch(self, server, admin_a):
        fresh, stale = _team(server, admin_a), _team(server, admin_a)
        refused = self._batch_put(
            server,
            admin_a,
            [
                {"id": fresh["id"], "if_match": entity_etag(fresh)},
                {"id": stale["id"], "if_match": STALE},
            ],
        )
        assert refused.status_code == 412, refused.text
        body = refused.json()
        assert body["stale_ids"] == [stale["id"]]
        assert [row["id"] for row in body["current"]] == [stale["id"]]
        for team in (fresh, stale):
            unchanged = _get(server, admin_a, team["id"]).json()["team"]
            assert unchanged["description"] == "concurrency"

    def test_current_versions_and_bare_ids_save(self, server, admin_a):
        versioned, bare = _team(server, admin_a), _team(server, admin_a)
        saved = self._batch_put(
            server,
            admin_a,
            [{"id": versioned["id"], "if_match": entity_etag(versioned)}, bare["id"]],
        )
        assert saved.status_code == 200, saved.text
        for team in (versioned, bare):
            row = _get(server, admin_a, team["id"]).json()["team"]
            assert row["description"] == "batched"

    def test_a_target_without_a_version_is_428_when_required(
        self, server, admin_a, if_match_required
    ):
        versioned, bare = _team(server, admin_a), _team(server, admin_a)
        refused = self._batch_put(
            server,
            admin_a,
            [{"id": versioned["id"], "if_match": entity_etag(versioned)}, bare["id"]],
        )
        assert refused.status_code == 428, refused.text
        assert refused.json() == {
            "detail": REQUIRED_DETAIL,
            "missing_ids": [bare["id"]],
        }

    def test_a_batch_delete_is_held_to_the_listed_versions(self, server, admin_a):
        first, second = _team(server, admin_a), _team(server, admin_a)
        ids = f"{first['id']},{second['id']}"
        only_first = server.delete(
            f"{TEAMS}?target_ids={ids}",
            headers=_headers(admin_a, **_if_match(str(entity_etag(first)))),
        )
        assert only_first.status_code == 412, only_first.text
        assert only_first.json()["stale_ids"] == [second["id"]]
        assert _get(server, admin_a, first["id"]).status_code == 200

        both = f"{entity_etag(first)}, {entity_etag(second)}"
        deleted = server.delete(
            f"{TEAMS}?target_ids={ids}", headers=_headers(admin_a, **_if_match(both))
        )
        assert deleted.status_code == 204, deleted.text


class TestGraphQL:
    def _update(self, server: Any, user: Any, team_id: str, if_match: str) -> Any:
        query = (
            'mutation { updateTeam(id: "%s", input: {name: "GQL %s"}, ifMatch: %s)'
            " { id } }" % (team_id, uuid.uuid4().hex[:8], _graphql_string(if_match))
        )
        response = server.post(
            "/graphql", json={"query": query}, headers=_headers(user)
        )
        assert response.status_code == 200, response.text
        return response.json()

    def test_a_stale_if_match_is_a_precondition_error(self, server, admin_a):
        team = _team(server, admin_a)
        refused = self._update(server, admin_a, team["id"], STALE)
        assert refused.get("data") in (None, {"updateTeam": None}), refused
        (error,) = refused["errors"]
        assert error["extensions"]["code"] == "PRECONDITION_FAILED"
        assert error["extensions"]["status"] == 412
        assert error["extensions"]["current"]["id"] == team["id"]
        assert _get(server, admin_a, team["id"]).json()["team"]["name"] == team["name"]

    def test_the_current_version_saves(self, server, admin_a):
        team = _team(server, admin_a)
        saved = self._update(server, admin_a, team["id"], str(entity_etag(team)))
        assert "errors" not in saved, saved
        assert saved["data"]["updateTeam"]["id"] == team["id"]


def _graphql_string(value: str) -> str:
    """A GraphQL string literal (the ETag holds quotes)."""
    return '"%s"' % value.replace("\\", "\\\\").replace('"', '\\"')
