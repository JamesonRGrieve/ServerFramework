# SPDX-License-Identifier: AGPL-3.0-or-later
"""A record's own key/values against a real server (a team is the record here): its editors write
them, anyone who can view it reads them, private ones stay with its editors, and someone outside
gets the same answer as for a record that isn't there. A team is a record its members edit; a
person's own user record is one a teammate views but doesn't edit."""

import uuid
from typing import Any, Dict

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.metadata.EXT_Metadata import MetadataExtension
from zephyrex.lib.Environment import env
from zephyrex.testing.factories import add_user_to_team, create_team


def auth(user: Any) -> Dict[str, str]:
    return {"Authorization": f"Bearer {user.jwt}"}


class TestRecordMetadata(ExtensionServerMixin):
    extension_class = MetadataExtension

    @pytest.fixture
    def city(self, server: Any, admin_a: Any, user_b: Any) -> str:
        """admin_a's team (admin_a edits it), with user_b a member (views it, can't edit it)."""
        team = create_team(server, admin_a.id, name=f"Vell {uuid.uuid4().hex[:8]}")
        add_user_to_team(server, user_b.id, team.id, env("USER_ROLE_ID"))
        return str(team.id)

    @staticmethod
    def _add(
        server: Any, user: Any, city: str, kind: str = "team", **fields: Any
    ) -> Any:
        body = {"target_entity_type": kind, "target_entity_id": city, **fields}
        return server.post(
            "/v1/record_metadata", json={"record_metadata": body}, headers=auth(user)
        )

    @staticmethod
    def _of(server: Any, user: Any, city: str, kind: str = "team") -> Any:
        return server.get(
            "/v1/record_metadata/of",
            params={"target_entity_type": kind, "target_entity_id": city},
            headers=auth(user),
        )

    def test_an_editor_writes_and_a_viewer_reads_in_order(
        self, server: Any, admin_a: Any, user_b: Any, city: str
    ) -> None:
        made = self._add(
            server,
            admin_a,
            city,
            key="population",
            value="12000",
            value_type="number",
            ordinal=2,
        )
        assert made.status_code == 201, made.text
        assert (
            self._add(
                server, admin_a, city, key="ruler", value="Duke Arn", ordinal=1
            ).status_code
            == 201
        )
        read = self._of(server, user_b, city)
        assert read.status_code == 200, read.text
        assert [(row["key"], row["value"]) for row in read.json()["metadata"]] == [
            ("ruler", "Duke Arn"),
            ("population", "12000"),
        ]

    def test_private_rows_stay_with_the_editors(
        self, server: Any, admin_a: Any, user_b: Any, city: str
    ) -> None:
        # A person's own record: they edit it; a teammate (user_b, through the city) only views it.
        # (On a team, members edit the team itself, so a team can't show the difference.)
        me = str(admin_a.id)
        made = self._add(
            server, admin_a, me, "user", key="secret", value="cult", is_private=True
        )
        assert made.status_code == 201, made.text
        assert (
            self._add(
                server, admin_a, me, "user", key="motto", value="Hold fast"
            ).status_code
            == 201
        )
        seen = self._of(server, user_b, me, "user")
        assert seen.status_code == 200, seen.text
        assert [row["key"] for row in seen.json()["metadata"]] == ["motto"]
        assert sorted(
            row["key"]
            for row in self._of(server, admin_a, me, "user").json()["metadata"]
        ) == [
            "motto",
            "secret",
        ]
        # The viewer can't write there: the same 404 as for no record at all.
        assert (
            self._add(server, user_b, me, "user", key="x", value="1").status_code == 404
        )

    def test_who_may_write_and_what(
        self, server: Any, admin_a: Any, admin_b: Any, user_b: Any, city: str
    ) -> None:
        # Someone outside can't write or read: the same 404 as for no record at all.
        assert self._add(server, admin_b, city, key="x", value="1").status_code == 404
        assert (
            self._add(
                server, admin_a, str(uuid.uuid4()), key="x", value="1"
            ).status_code
            == 404
        )
        assert self._of(server, admin_b, city).status_code == 404
        # One row per key, a known value type, a loaded record type.
        assert self._add(server, admin_a, city, key="x", value="1").status_code == 201
        assert self._add(server, admin_a, city, key="x", value="2").status_code == 409
        assert (
            self._add(server, admin_a, city, key="y", value_type="colour").status_code
            == 422
        )
        unknown = server.post(
            "/v1/record_metadata",
            json={
                "record_metadata": {
                    "target_entity_type": "no_such_thing",
                    "target_entity_id": "1",
                    "key": "x",
                }
            },
            headers=auth(admin_a),
        )
        assert unknown.status_code == 422

    def test_an_editor_changes_and_removes_a_row(
        self, server: Any, admin_a: Any, admin_b: Any, city: str
    ) -> None:
        row = self._add(server, admin_a, city, key="ruler", value="Duke Arn").json()[
            "record_metadata"
        ]
        assert (
            server.patch(
                f"/v1/record_metadata/{row['id']}",
                json={"record_metadata": {"value": "x"}},
                headers=auth(admin_b),
            ).status_code
            == 404
        )
        changed = server.patch(
            f"/v1/record_metadata/{row['id']}",
            json={"record_metadata": {"value": "Duchess Ime"}},
            headers=auth(admin_a),
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["record_metadata"]["value"] == "Duchess Ime"
        assert (
            server.delete(
                f"/v1/record_metadata/{row['id']}", headers=auth(admin_b)
            ).status_code
            == 404
        )
        assert (
            server.delete(
                f"/v1/record_metadata/{row['id']}", headers=auth(admin_a)
            ).status_code
            == 204
        )
        assert self._of(server, admin_a, city).json()["metadata"] == []

    def test_a_record_row_belongs_to_the_record_alone(
        self, server: Any, admin_a: Any, city: str
    ) -> None:
        from zephyrex.extensions.metadata.BLL_Metadata import MetadataManager

        with pytest.raises(Exception) as refused:
            MetadataManager(
                requester_id=env("ROOT_ID"),
                model_registry=server.app.state.model_registry,
            ).create(
                key="k",
                user_id=admin_a.id,
                target_entity_type="team",
                target_entity_id=city,
            )
        assert getattr(refused.value, "status_code", None) == 400
