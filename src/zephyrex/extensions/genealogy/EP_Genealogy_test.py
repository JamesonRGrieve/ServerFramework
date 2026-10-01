# SPDX-License-Identifier: AGPL-3.0-or-later
"""Endpoint tests for genealogy: CRUD on both models through the shared
suite, then the family-tree and GEDCOM routes against a real server."""

import uuid
from typing import Dict, List

import pytest

from zephyrex.AbstractTest import ParentEntity
from zephyrex.endpoints.AbstractEPTest import AbstractEPTest
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.genealogy.BLL_Genealogy import PersonModel, RelationshipModel
from zephyrex.extensions.genealogy.EXT_Genealogy import EXT_Genealogy
from zephyrex.extensions.genealogy.GEDCOM import parse


class TestPersonEP(AbstractEPTest, ExtensionServerMixin):
    extension_class = EXT_Genealogy
    base_endpoint = "person"
    entity_name = "person"
    class_under_test = PersonModel
    required_fields = ["id", "name", "created_at"]
    # The shared GraphQL create sends only this field; a person needs a name.
    string_field_to_update = "name"
    searchable_fields = ["name"]
    parent_entities: List[ParentEntity] = []
    system_entity = False
    create_fields = {"name": lambda: f"Person {uuid.uuid4()}"}
    update_fields = {"description": lambda: f"Updated {uuid.uuid4()}"}
    unique_fields: List[str] = []

    def create_payload(
        self,
        name=None,
        parent_ids=None,
        team_id=None,
        minimal=False,
        invalid_data=False,
    ):
        if invalid_data:
            return {"name": 12345, "birth_date": "not a date"}
        payload = {"name": name or f"Person {uuid.uuid4()}"}
        if not minimal:
            payload["gender"] = "female"
        return payload


class TestRelationshipEP(AbstractEPTest, ExtensionServerMixin):
    extension_class = EXT_Genealogy
    base_endpoint = "relationship"
    entity_name = "relationship"
    class_under_test = RelationshipModel
    required_fields = ["id", "created_at"]
    string_field_to_update = "notes"
    searchable_fields = ["kind"]
    parent_entities = [
        ParentEntity(
            name="person",
            foreign_key="person_id",
            test_class=TestPersonEP,
            nullable=True,
        )
    ]
    system_entity = False
    create_fields = {"kind": "ancestry"}
    update_fields = {"notes": lambda: f"Updated {uuid.uuid4()}"}
    unique_fields: List[str] = []

    def create_payload(
        self,
        name=None,
        parent_ids=None,
        team_id=None,
        minimal=False,
        invalid_data=False,
    ):
        if invalid_data:
            return {"kind": 12345, "intensity": "very"}
        payload: Dict[str, object] = {"kind": "ancestry"}
        if parent_ids and parent_ids.get("person_id"):
            payload["person_id"] = parent_ids["person_id"]
        if not minimal:
            payload["discriminator"] = "biological"
            payload["notes"] = name or "edge"
        return payload


class TestFamilyTreeRoutes(ExtensionServerMixin):
    """The tree, kinship and GEDCOM routes over a small family, and that a
    tree never reaches past what the requester may see."""

    extension_class = EXT_Genealogy

    @pytest.fixture(scope="class")
    def family(self, server, admin_a) -> Dict[str, str]:
        headers = {"Authorization": f"Bearer {admin_a.jwt}"}
        ids: Dict[str, str] = {}
        for name in ("grandma", "mum", "aunt", "ann", "cousin"):
            response = server.post(
                "/v1/person", json={"person": {"name": name}}, headers=headers
            )
            assert response.status_code == 201, response.text
            ids[name] = response.json()["person"]["id"]
        for parent, child in (
            ("grandma", "mum"),
            ("grandma", "aunt"),
            ("mum", "ann"),
            ("aunt", "cousin"),
        ):
            response = server.post(
                "/v1/relationship",
                json={
                    "relationship": {
                        "person_id": ids[parent],
                        "target_person_id": ids[child],
                        "kind": "ancestry",
                        "discriminator": "biological",
                    }
                },
                headers=headers,
            )
            assert response.status_code == 201, response.text
        return ids

    @staticmethod
    def _get(server, user, path):
        return server.get(path, headers={"Authorization": f"Bearer {user.jwt}"})

    def test_ancestors(self, server, admin_a, family):
        response = self._get(server, admin_a, f"/v1/person/{family['ann']}/ancestors")
        assert response.status_code == 200, response.text
        assert response.json()["relatives"] == [
            {"person_id": family["mum"], "generation": 1},
            {"person_id": family["grandma"], "generation": 2},
        ]

    def test_descendants_limited(self, server, admin_a, family):
        response = self._get(
            server, admin_a, f"/v1/person/{family['grandma']}/descendants?generations=1"
        )
        assert response.status_code == 200, response.text
        assert {r["person_id"] for r in response.json()["relatives"]} == {
            family["mum"],
            family["aunt"],
        }

    def test_kinship_first_cousins(self, server, admin_a, family):
        response = self._get(
            server, admin_a, f"/v1/person/{family['ann']}/kinship/{family['cousin']}"
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["related"] is True
        assert body["common_ancestors"] == [family["grandma"]]
        assert (body["degree"], body["cousin"], body["removed"]) == (4, 1, 0)

    def test_an_unknown_person_is_404(self, server, admin_a):
        response = self._get(server, admin_a, f"/v1/person/{uuid.uuid4()}/ancestors")
        assert response.status_code == 404

    def test_another_user_cannot_walk_the_tree(self, server, admin_b, family):
        response = self._get(server, admin_b, f"/v1/person/{family['ann']}/ancestors")
        assert response.status_code == 404

    def test_export_gedcom(self, server, admin_a, family):
        response = self._get(server, admin_a, "/v1/person/gedcom")
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/plain")
        exported = parse(response.text)
        assert exported.version == "5.5.1"
        names = {p.name for p in exported.people.values()}
        assert {"grandma", "mum", "aunt", "ann", "cousin"} <= names

    def test_another_users_export_holds_none_of_them(self, server, admin_b, family):
        response = self._get(server, admin_b, "/v1/person/gedcom")
        assert response.status_code == 200, response.text
        names = {p.name for p in parse(response.text).people.values()}
        assert not names & {"grandma", "mum", "aunt", "ann", "cousin"}

    def test_import_gedcom(self, server, admin_a):
        text = (
            "0 HEAD\n1 GEDC\n2 VERS 7.0\n"
            "0 @I1@ INDI\n1 NAME Pat /Parent/\n1 SEX F\n"
            "0 @I2@ INDI\n1 NAME Kit /Child/\n1 FAMC @F1@\n2 PEDI ADOPTED\n"
            "0 @F1@ FAM\n1 WIFE @I1@\n1 CHIL @I2@\n0 TRLR\n"
        )
        headers = {"Authorization": f"Bearer {admin_a.jwt}"}
        response = server.post(
            "/v1/person/gedcom", json={"gedcom": text}, headers=headers
        )
        assert response.status_code == 200, response.text  # custom routes answer 200
        body = response.json()
        assert body["version"] == "7.0"
        assert body["relationships"] == 1
        child = body["people"]["@I2@"]
        ancestors = self._get(server, admin_a, f"/v1/person/{child}/ancestors").json()
        assert ancestors["relatives"] == [
            {"person_id": body["people"]["@I1@"], "generation": 1}
        ]
        adopted_only = self._get(
            server, admin_a, f"/v1/person/{child}/ancestors?roles=biological"
        ).json()
        assert adopted_only["relatives"] == []

    def test_import_refuses_what_it_cannot_read(self, server, admin_a):
        response = server.post(
            "/v1/person/gedcom",
            json={"gedcom": "0 HEAD\n1 GEDC\n2 VERS 5.5.1\n1 CHAR ANSEL\n0 TRLR\n"},
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        assert response.status_code == 422
        assert "ANSEL" in response.text
