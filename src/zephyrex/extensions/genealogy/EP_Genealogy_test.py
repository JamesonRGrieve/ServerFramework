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

    @pytest.mark.parametrize("intensity", [1.5, -1.01, 5])
    def test_intensity_outside_minus_one_to_one_is_refused(
        self, server, admin_a, family, intensity
    ):
        """intensity is -1.0 (nemesis) to +1.0 (closest); 5 used to be stored."""
        response = server.post(
            "/v1/relationship",
            json={
                "relationship": {
                    "person_id": family["mum"],
                    "target_person_id": family["aunt"],
                    "kind": "sibling_of",
                    "intensity": intensity,
                }
            },
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        assert response.status_code == 422, response.text

    def test_import_refuses_what_it_cannot_read(self, server, admin_a):
        response = server.post(
            "/v1/person/gedcom",
            json={"gedcom": "0 HEAD\n1 GEDC\n2 VERS 5.5.1\n1 CHAR ANSEL\n0 TRLR\n"},
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        assert response.status_code == 422
        assert "ANSEL" in response.text


class TestSocialRoutes(ExtensionServerMixin):
    """Connections, metamours and the polycule network over a small group,
    the relationship checks on write, and that no walk reaches past what
    the requester may see."""

    extension_class = EXT_Genealogy

    @staticmethod
    def _headers(user) -> Dict[str, str]:
        return {"Authorization": f"Bearer {user.jwt}"}

    @classmethod
    def _relate(cls, server, user, subject, target, kind, discriminator=None, **extra):
        return server.post(
            "/v1/relationship",
            json={
                "relationship": {
                    "person_id": subject,
                    "target_person_id": target,
                    "kind": kind,
                    "discriminator": discriminator,
                    **extra,
                }
            },
            headers=cls._headers(user),
        )

    @pytest.fixture(scope="class")
    def group(self, server, admin_a) -> Dict[str, str]:
        ids: Dict[str, str] = {}
        for name in ("ana", "ben", "cleo", "dev", "eli", "fay"):
            response = server.post(
                "/v1/person",
                json={"person": {"name": name}},
                headers=self._headers(admin_a),
            )
            assert response.status_code == 201, response.text
            ids[name] = response.json()["person"]["id"]
        for subject, target, kind, discriminator, extra in (
            ("ana", "ben", "partnership", "nesting", {}),
            ("ben", "ana", "partnership", "nesting", {}),
            ("ben", "cleo", "partnership", "dating", {}),
            ("cleo", "dev", "partnership", "anchor", {}),
            (
                "ana",
                "eli",
                "partnership",
                "dating",
                {"valid_to": "2020-01-01T00:00:00"},
            ),
            ("ben", "fay", "friendship", "best_friend", {}),
        ):
            response = self._relate(
                server, admin_a, ids[subject], ids[target], kind, discriminator, **extra
            )
            assert response.status_code == 201, response.text
        return ids

    def _get(self, server, user, path):
        return server.get(path, headers=self._headers(user))

    def test_connections(self, server, admin_a, group):
        response = self._get(server, admin_a, f"/v1/person/{group['ben']}/connections")
        assert response.status_code == 200, response.text
        assert response.json()["connections"] == [
            {
                "person_id": group["fay"],
                "kind": "friendship",
                "discriminator": "best_friend",
            },
            *sorted(
                (
                    {
                        "person_id": group["ana"],
                        "kind": "partnership",
                        "discriminator": "nesting",
                    },
                    {
                        "person_id": group["cleo"],
                        "kind": "partnership",
                        "discriminator": "dating",
                    },
                ),
                key=lambda c: c["person_id"],
            ),
        ]

    def test_connections_limited_to_one_kind(self, server, admin_a, group):
        response = self._get(
            server, admin_a, f"/v1/person/{group['ben']}/connections?kinds=friendship"
        )
        assert [c["person_id"] for c in response.json()["connections"]] == [
            group["fay"]
        ]

    def test_an_ended_partnership_is_kept_only_when_asked(self, server, admin_a, group):
        path = f"/v1/person/{group['ana']}/connections"
        now = {
            c["person_id"]
            for c in self._get(server, admin_a, path).json()["connections"]
        }
        ever = {
            c["person_id"]
            for c in self._get(server, admin_a, f"{path}?include_ended=true").json()[
                "connections"
            ]
        }
        assert group["eli"] not in now
        assert group["eli"] in ever

    def test_metamours(self, server, admin_a, group):
        response = self._get(server, admin_a, f"/v1/person/{group['ana']}/metamours")
        assert response.status_code == 200, response.text
        assert response.json()["metamours"] == [
            {"person_id": group["cleo"], "via": [group["ben"]]}
        ]

    def test_network_walks_the_polycule(self, server, admin_a, group):
        response = self._get(
            server, admin_a, f"/v1/person/{group['ana']}/network?kinds=partnership"
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["kinds"] == ["partnership"]
        assert body["members"] == [
            {"person_id": group["ben"], "distance": 1},
            {"person_id": group["cleo"], "distance": 2},
            {"person_id": group["dev"], "distance": 3},
        ]
        pairs = {
            frozenset((t["person_id"], t["target_person_id"])): t["discriminator"]
            for t in body["ties"]
        }
        assert pairs == {
            frozenset((group["ana"], group["ben"])): "nesting",
            frozenset((group["ben"], group["cleo"])): "dating",
            frozenset((group["cleo"], group["dev"])): "anchor",
        }
        assert all(t["person_id"] < t["target_person_id"] for t in body["ties"])

    def test_an_unknown_kind_is_refused_not_ignored(self, server, admin_a, group):
        response = self._get(
            server,
            admin_a,
            f"/v1/person/{group['ana']}/network?kinds=partnership,ancestry",
        )
        assert response.status_code == 422
        assert "ancestry" in response.text

    def test_a_directed_kind_can_be_walked(self, server, admin_a, group):
        mentor = self._relate(
            server, admin_a, group["fay"], group["eli"], "mentorship", "teacher"
        )
        assert mentor.status_code == 201, mentor.text
        body = self._get(
            server, admin_a, f"/v1/person/{group['fay']}/network?kinds=mentorship"
        ).json()
        assert body["members"] == [{"person_id": group["eli"], "distance": 1}]
        [tie] = body["ties"]
        assert (tie["person_id"], tie["target_person_id"], tie["directed"]) == (
            group["fay"],
            group["eli"],
            True,
        )

    def test_another_user_cannot_walk_the_group(self, server, admin_b, group):
        response = self._get(server, admin_b, f"/v1/person/{group['ana']}/network")
        assert response.status_code == 404

    def test_a_discriminator_of_another_kind_is_refused(self, server, admin_a, group):
        response = self._relate(
            server, admin_a, group["dev"], group["eli"], "friendship", "nesting"
        )
        assert response.status_code == 422, response.text
        assert "nesting" in response.text

    def test_a_partnership_with_oneself_is_refused(self, server, admin_a, group):
        response = self._relate(
            server, admin_a, group["dev"], group["dev"], "partnership"
        )
        assert response.status_code == 422, response.text

    def test_an_update_to_a_wrong_discriminator_is_refused(
        self, server, admin_a, group
    ):
        created = self._relate(
            server, admin_a, group["eli"], group["fay"], "acquaintance", "colleague"
        )
        assert created.status_code == 201, created.text
        relationship = created.json()["relationship"]
        response = server.put(
            f"/v1/relationship/{relationship['id']}",
            json={"relationship": {"discriminator": "anchor"}},
            headers=self._headers(admin_a),
        )
        assert response.status_code == 422, response.text


class TestPersonCards(ExtensionServerMixin):
    """Self persons, private notes and per-field publishing: a card shows the
    requester's own notes and what the person published to them, and nobody
    sees another's notes about them."""

    extension_class = EXT_Genealogy

    @staticmethod
    def _headers(user) -> Dict[str, str]:
        return {"Authorization": f"Bearer {user.jwt}"}

    @classmethod
    def _person(cls, server, user, name, linked_user_id=None):
        response = server.post(
            "/v1/person",
            json={"person": {"name": name, "linked_user_id": linked_user_id}},
            headers=cls._headers(user),
        )
        assert response.status_code == 201, response.text
        return response.json()["person"]["id"]

    @classmethod
    def _attribute(cls, server, user, person_id, category, label, value, unit=None):
        return server.post(
            "/v1/person_attribute",
            json={
                "person_attribute": {
                    "person_id": person_id,
                    "category": category,
                    "label": label,
                    "value": value,
                    "unit": unit,
                }
            },
            headers=cls._headers(user),
        )

    @staticmethod
    def _grant_view(server, attribute_id, user_id) -> None:
        """Publish one field to one account, as the server does once it has
        checked the two are connected (granting runs as ROOT)."""
        from zephyrex.extensions.acl_rbac.BLL_ACL import PermissionManager
        from zephyrex.lib.Environment import env

        PermissionManager(
            model_registry=server.app.state.model_registry, requester_id=env("ROOT_ID")
        ).create(
            user_id=user_id,
            resource_type="person_attributes",
            resource_id=attribute_id,
            can_view=True,
        )

    @pytest.fixture(scope="class")
    def cards(self, server, admin_a, admin_b) -> Dict[str, str]:
        alex_self = self._person(
            server, admin_a, "Alex (me)", linked_user_id=admin_a.id
        )
        dress = self._attribute(server, admin_a, alex_self, "sizes", "dress", "M")
        allergy = self._attribute(
            server, admin_a, alex_self, "allergies", "medical", "penicillin"
        )
        assert dress.status_code == 201 and allergy.status_code == 201, allergy.text
        dress_id = dress.json()["person_attribute"]["id"]
        self._grant_view(server, dress_id, admin_b.id)
        ghost = self._person(server, admin_b, "Alex", linked_user_id=admin_a.id)
        note = self._attribute(server, admin_b, ghost, "sizes", "ring", "7", unit="US")
        assert note.status_code == 201, note.text
        return {
            "alex_self": alex_self,
            "ghost": ghost,
            "note": note.json()["person_attribute"]["id"],
        }

    def test_an_account_has_one_self_person(self, server, admin_a, cards):
        response = server.post(
            "/v1/person",
            json={"person": {"name": "Another me", "linked_user_id": admin_a.id}},
            headers=self._headers(admin_a),
        )
        assert response.status_code == 409, response.text

    def test_a_card_shows_my_notes_and_only_what_was_published_to_me(
        self, server, admin_b, cards
    ):
        response = server.get(
            f"/v1/person/{cards['ghost']}/card", headers=self._headers(admin_b)
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert [(n["label"], n["value"], n["unit"]) for n in body["notes"]] == [
            ("ring", "7", "US")
        ]
        assert [(p["category"], p["label"], p["value"]) for p in body["published"]] == [
            ("sizes", "dress", "M")
        ]

    def test_my_own_card_is_my_profile(self, server, admin_a, cards):
        body = server.get(
            f"/v1/person/{cards['alex_self']}/card", headers=self._headers(admin_a)
        ).json()
        assert {n["label"] for n in body["notes"]} == {"dress", "medical"}
        assert body["published"] == []

    def test_a_card_entry_carries_the_version_to_write_it_by(
        self, server, admin_b, cards
    ):
        body = server.get(
            f"/v1/person/{cards['ghost']}/card", headers=self._headers(admin_b)
        ).json()
        (note,) = body["notes"]
        version = note["updated_at"] or note["created_at"]
        assert version, note
        saved = server.put(
            f"/v1/person_attribute/{note['id']}",
            json={"person_attribute": {"value": note["value"]}},
            headers={**self._headers(admin_b), "If-Match": f'"{version}"'},
        )
        assert saved.status_code == 200, saved.text

    def test_nobody_sees_anothers_notes_about_them(self, server, admin_a, cards):
        listed = server.get("/v1/person_attribute", headers=self._headers(admin_a))
        assert listed.status_code == 200, listed.text
        assert cards["note"] not in {
            a["id"] for a in listed.json()["person_attributes"]
        }
        direct = server.get(
            f"/v1/person_attribute/{cards['note']}", headers=self._headers(admin_a)
        )
        assert direct.status_code == 404

    def test_a_note_cannot_be_written_on_someone_elses_record(
        self, server, admin_a, cards
    ):
        response = self._attribute(
            server, admin_a, cards["ghost"], "sizes", "shoe", "9"
        )
        assert response.status_code == 404, response.text

    def test_a_ghost_without_an_account_publishes_nothing(self, server, admin_b):
        ghost = self._person(server, admin_b, "Not on the app")
        body = server.get(
            f"/v1/person/{ghost}/card", headers=self._headers(admin_b)
        ).json()
        assert body["published"] == [] and body["linked_user_id"] is None

    def test_a_deleted_self_person_publishes_nothing_and_frees_the_slot(
        self, server, admin_b, user_b
    ):
        """ROOT lookups see deleted rows: a deleted self person must not
        still count as the account's self, nor keep publishing."""
        from zephyrex.testing.factories import current_if_match

        mine = self._person(server, user_b, "Me", linked_user_id=user_b.id)
        field = self._attribute(server, user_b, mine, "sizes", "hat", "M")
        self._grant_view(server, field.json()["person_attribute"]["id"], admin_b.id)
        ghost = self._person(server, admin_b, "User B", linked_user_id=user_b.id)
        before = server.get(f"/v1/person/{ghost}/card", headers=self._headers(admin_b))
        assert [p["label"] for p in before.json()["published"]] == ["hat"]
        url = f"/v1/person/{mine}"
        headers = self._headers(user_b)
        deleted = server.delete(
            url, headers={**headers, **current_if_match(server, url, headers)}
        )
        assert deleted.status_code in (200, 204), deleted.text
        after = server.get(f"/v1/person/{ghost}/card", headers=self._headers(admin_b))
        assert after.json()["published"] == []
        self._person(server, user_b, "Me again", linked_user_id=user_b.id)


class TestLinkedNetwork(ExtensionServerMixin):
    """Two accounts that link each other see one merged polycule, with each
    stranger shown by their visibility; a one-sided link merges nothing."""

    extension_class = EXT_Genealogy

    @staticmethod
    def _headers(user) -> Dict[str, str]:
        return {"Authorization": f"Bearer {user.jwt}"}

    @classmethod
    def _person(cls, server, user, name, **fields):
        response = server.post(
            "/v1/person",
            json={"person": {"name": name, **fields}},
            headers=cls._headers(user),
        )
        assert response.status_code == 201, response.text
        return response.json()["person"]["id"]

    @classmethod
    def _partners(cls, server, user, a, b, discriminator):
        for subject, target in ((a, b), (b, a)):
            response = server.post(
                "/v1/relationship",
                json={
                    "relationship": {
                        "person_id": subject,
                        "target_person_id": target,
                        "kind": "partnership",
                        "discriminator": discriminator,
                    }
                },
                headers=cls._headers(user),
            )
            assert response.status_code == 201, response.text

    @pytest.fixture(scope="class")
    def polycule(self, server, admin_a, admin_b, user_b) -> Dict[str, str]:
        a_me = self._person(server, admin_a, "Sam", linked_user_id=admin_a.id)
        a_alex = self._person(server, admin_a, "My Alex", linked_user_id=admin_b.id)
        a_jordan = self._person(server, admin_a, "Jordan")
        self._partners(server, admin_a, a_me, a_alex, "nesting")
        self._partners(server, admin_a, a_me, a_jordan, "dating")
        b_me = self._person(
            server, admin_b, "Alex", linked_user_id=admin_b.id, visibility="named"
        )
        b_sam = self._person(server, admin_b, "Sam R", linked_user_id=admin_a.id)
        b_priya = self._person(server, admin_b, "Priya", visibility="named")
        b_riley = self._person(server, admin_b, "Riley")
        b_hidden = self._person(server, admin_b, "Hidden one", visibility="hidden")
        self._partners(server, admin_b, b_me, b_sam, "nesting")
        self._partners(server, admin_b, b_me, b_priya, "anchor")
        self._partners(server, admin_b, b_priya, b_riley, "casual")
        self._partners(server, admin_b, b_me, b_hidden, "liaison")
        # user_b links Sam's account, but Sam never linked user_b.
        u_me = self._person(server, user_b, "Mallory", linked_user_id=user_b.id)
        u_sam = self._person(server, user_b, "Sam", linked_user_id=admin_a.id)
        self._partners(server, user_b, u_me, u_sam, "dating")
        return {
            "a_alex": a_alex,
            "a_jordan": a_jordan,
            "b_priya": b_priya,
            "b_riley": b_riley,
        }

    def _network(self, server, user):
        response = server.get(
            "/v1/person/linked-network?kinds=partnership", headers=self._headers(user)
        )
        assert response.status_code == 200, response.text
        return {node["key"]: node for node in response.json()["nodes"]}

    def test_a_mutual_link_merges_the_polycule(
        self, server, admin_a, admin_b, polycule
    ):
        nodes = self._network(server, admin_a)
        assert nodes[f"user:{admin_b.id}"]["name"] == "My Alex"
        assert nodes[f"user:{admin_b.id}"]["mine"] is True
        assert nodes[f"person:{polycule['b_priya']}"]["name"] == "Priya"
        riley = nodes[f"person:{polycule['b_riley']}"]
        assert (riley["name"], riley["visibility"], riley["distance"]) == (
            None,
            "anonymous",
            3,
        )
        assert "Hidden one" not in {node["name"] for node in nodes.values()}
        groups = {nodes[f"person:{polycule['a_jordan']}"]["group"], riley["group"]}
        assert len(groups) == 2

    def test_a_one_sided_link_merges_nothing(self, server, user_b, polycule):
        nodes = self._network(server, user_b)
        assert {node["name"] for node in nodes.values()} == {"Mallory", "Sam"}

    def test_someone_without_a_self_person_is_told_so(self, server):
        from zephyrex.testing.factories import create_user, generate_test_email

        newcomer = create_user(
            server, email=generate_test_email("newcomer"), last_name="New"
        )
        response = server.get(
            "/v1/person/linked-network", headers=self._headers(newcomer)
        )
        assert response.status_code == 404
