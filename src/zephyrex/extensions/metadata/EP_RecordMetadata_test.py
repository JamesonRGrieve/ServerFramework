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
        from zephyrex.lib.Preconditions import entity_etag

        # A save names the row's version (IF_MATCH_REQUIRED, on by default).
        assert (
            server.patch(
                f"/v1/record_metadata/{row['id']}",
                json={"record_metadata": {"value": "x"}},
                headers=auth(admin_a),
            ).status_code
            == 428
        )
        changed = server.patch(
            f"/v1/record_metadata/{row['id']}",
            json={"record_metadata": {"value": "Duchess Ime"}},
            headers={**auth(admin_a), "If-Match": entity_etag(row)},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["record_metadata"]["value"] == "Duchess Ime"
        row = changed.json()["record_metadata"]
        assert (
            server.delete(
                f"/v1/record_metadata/{row['id']}", headers=auth(admin_b)
            ).status_code
            == 404
        )
        assert (
            server.delete(
                f"/v1/record_metadata/{row['id']}",
                headers={**auth(admin_a), "If-Match": entity_etag(row)},
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

    def _batch(self, server: Any, user: Any, rows: Any) -> Any:
        return server.post(
            "/v1/record_metadata", json={"record_metadatas": rows}, headers=auth(user)
        )

    def test_a_batch_adds_every_row(
        self, server: Any, admin_a: Any, user_b: Any, city: str
    ) -> None:
        row = {"target_entity_type": "team", "target_entity_id": city}
        made = self._batch(
            server,
            admin_a,
            [{**row, "key": "ruler", "value": "Arn"}, {**row, "key": "port"}],
        )
        assert made.status_code == 201, made.text
        assert [r["key"] for r in made.json()["record_metadatas"]] == ["ruler", "port"]
        assert sorted(
            r["key"] for r in self._of(server, user_b, city).json()["metadata"]
        ) == ["port", "ruler"]

    def test_a_batch_reports_each_row_and_keeps_the_good_ones(
        self, server: Any, admin_a: Any, admin_b: Any, city: str
    ) -> None:
        row = {"target_entity_type": "team", "target_entity_id": city}
        answered = self._batch(
            server,
            admin_a,
            [
                {**row, "key": "ruler"},
                {**row, "key": "ruler"},  # the record already has it
                {**row, "key": "x", "value_type": "colour"},
                {
                    "target_entity_type": "team",
                    "target_entity_id": str(uuid.uuid4()),
                    "key": "y",
                },
                "not-an-object",
                {**row, "key": "port"},
            ],
        )
        assert answered.status_code == 207, answered.text
        body = answered.json()
        assert [r["outcome"] for r in body["results"]] == [
            "created",
            "error",
            "error",
            "error",
            "error",
            "created",
        ]
        assert [r.get("status_code") for r in body["results"][1:5]] == [
            409,
            422,
            404,
            422,
        ]
        assert [r["key"] for r in body["record_metadatas"]] == ["ruler", "port"]
        assert (body["successful_creates"], body["failed_creates"]) == (2, 4)

    def test_a_batch_in_which_every_row_fails_is_the_first_rows_error(
        self, server: Any, admin_b: Any, city: str
    ) -> None:
        row = {"target_entity_type": "team", "target_entity_id": city, "key": "x"}
        assert self._batch(server, admin_b, [row, row]).status_code == 404

    def test_one_shape_per_request(self, server: Any, admin_a: Any, city: str) -> None:
        row = {"target_entity_type": "team", "target_entity_id": city, "key": "x"}
        both = server.post(
            "/v1/record_metadata",
            json={"record_metadata": row, "record_metadatas": [row]},
            headers=auth(admin_a),
        )
        assert both.status_code == 422, both.text
        assert (
            server.post(
                "/v1/record_metadata", json={}, headers=auth(admin_a)
            ).status_code
            == 422
        )

    def _change_batch(self, server: Any, user: Any, rows: Any) -> Any:
        return server.put(
            "/v1/record_metadata", json={"record_metadatas": rows}, headers=auth(user)
        )

    def _rows(self, server: Any, user: Any, city: str, *keys: str) -> Any:
        row = {"target_entity_type": "team", "target_entity_id": city}
        made = self._batch(server, user, [{**row, "key": key} for key in keys])
        assert made.status_code == 201, made.text
        return made.json()["record_metadatas"]

    def test_a_batch_change_gives_each_row_its_own_change(
        self, server: Any, admin_a: Any, user_b: Any, city: str
    ) -> None:
        from zephyrex.lib.Preconditions import entity_etag

        ruler, port = self._rows(server, admin_a, city, "ruler", "port")
        changed = self._change_batch(
            server,
            admin_a,
            [
                {"id": ruler["id"], "if_match": entity_etag(ruler), "value": "Arn"},
                {
                    "id": port["id"],
                    "if_match": (port["updated_at"] or port["created_at"]),
                    "value": "Vell",
                    "ordinal": 3,
                },
            ],
        )
        assert changed.status_code == 200, changed.text
        assert [r["value"] for r in changed.json()["record_metadatas"]] == [
            "Arn",
            "Vell",
        ]
        read = {r["key"]: r for r in self._of(server, user_b, city).json()["metadata"]}
        assert (read["ruler"]["value"], read["port"]["ordinal"]) == ("Arn", 3)

    def test_a_batch_change_reports_each_row_and_keeps_the_good_ones(
        self, server: Any, admin_a: Any, admin_b: Any, city: str
    ) -> None:
        from zephyrex.lib.Preconditions import entity_etag

        ruler, port, gate = self._rows(server, admin_a, city, "ruler", "port", "gate")
        stale = '"1970-01-01T00:00:00"'
        answered = self._change_batch(
            server,
            admin_a,
            [
                {"id": ruler["id"], "if_match": entity_etag(ruler), "value": "Arn"},
                {"id": port["id"], "if_match": stale, "value": "lost"},
                {"id": gate["id"], "if_match": entity_etag(gate), "key": "ruler"},
                {"id": str(uuid.uuid4()), "value": "x"},
                {"value": "no id"},
            ],
        )
        assert answered.status_code == 207, answered.text
        body = answered.json()
        assert [r["outcome"] for r in body["results"]] == [
            "updated",
            "error",
            "error",
            "error",
            "error",
        ]
        assert [r["status_code"] for r in body["results"][1:]] == [412, 409, 404, 422]
        assert [r["value"] for r in body["record_metadatas"]] == ["Arn"]
        assert (body["successful_updates"], body["failed_updates"]) == (1, 4)
        read = {r["key"]: r for r in self._of(server, admin_a, city).json()["metadata"]}
        assert (read["port"]["value"], "gate" in read) == (None, True)

    def test_a_batch_change_by_someone_outside_is_the_first_rows_error(
        self, server: Any, admin_a: Any, admin_b: Any, city: str
    ) -> None:
        (ruler,) = self._rows(server, admin_a, city, "ruler")
        refused = self._change_batch(
            server, admin_b, [{"id": ruler["id"], "value": "x"}]
        )
        assert refused.status_code == 404, refused.text

    def test_a_change_or_removal_is_held_to_the_rows_version(
        self, server: Any, admin_a: Any, city: str
    ) -> None:
        from zephyrex.lib.Preconditions import entity_etag

        stale = '"1970-01-01T00:00:00"'
        (ruler,) = self._rows(server, admin_a, city, "ruler")
        path = f"/v1/record_metadata/{ruler['id']}"
        refused = server.patch(
            path,
            json={"record_metadata": {"value": "lost"}},
            headers={**auth(admin_a), "If-Match": stale},
        )
        assert refused.status_code == 412, refused.text
        assert refused.json()["current"]["id"] == ruler["id"]
        saved = server.patch(
            path,
            json={"record_metadata": {"value": "Arn"}},
            headers={**auth(admin_a), "If-Match": entity_etag(ruler)},
        )
        assert saved.status_code == 200, saved.text
        assert (
            server.delete(
                path, headers={**auth(admin_a), "If-Match": stale}
            ).status_code
            == 412
        )
        current = saved.json()["record_metadata"]
        removed = server.delete(
            path, headers={**auth(admin_a), "If-Match": entity_etag(current)}
        )
        assert removed.status_code == 204, removed.text
