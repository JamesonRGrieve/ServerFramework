# SPDX-License-Identifier: AGPL-3.0-or-later
"""Agreements against a real server: only the people in one see it; any of
them writes it; a change to its terms asks everyone to confirm again; only
its owner adds people or deletes it, and anyone may leave."""

from types import SimpleNamespace
from typing import Any, Dict

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.agreements.BLL_Agreements import (
    DEFAULT_TERM_KINDS,
    in_effect,
    register_term_kinds,
    term_kinds,
)
from zephyrex.extensions.agreements.EXT_Agreements import EXT_Agreements
from zephyrex.testing.factories import create_user


@pytest.fixture
def house_rules():
    register_term_kinds(("clause", "rule", "rule", " "))
    yield
    register_term_kinds(DEFAULT_TERM_KINDS)


def test_an_app_names_its_kinds_of_term(house_rules):
    assert term_kinds() == ("clause", "rule")
    with pytest.raises(ValueError):
        register_term_kinds([" "])


def test_in_effect_needs_everyone_on_this_version():
    party = lambda v: SimpleNamespace(confirmed_version=v)  # noqa: E731
    assert in_effect(2, [party(2), party(2)])
    assert not in_effect(2, [party(2), party(1)])
    assert not in_effect(2, [party(2), party(None)])
    assert not in_effect(1, [])


class TestAgreements(ExtensionServerMixin):
    extension_class = EXT_Agreements

    @staticmethod
    def _headers(user: Any) -> Dict[str, str]:
        return {"Authorization": f"Bearer {user.jwt}"}

    def _start(self, server, owner, *others) -> Dict[str, Any]:
        response = server.post(
            "/v1/agreements",
            json={
                "title": "Us",
                "revisit_on": "2027-01-01",
                "party_user_ids": [o.id for o in others],
            },
            headers=self._headers(owner),
        )
        assert response.status_code in (200, 201), response.text
        made: Dict[str, Any] = response.json()
        return made

    @pytest.fixture(scope="class")
    def people(self, server) -> Dict[str, Any]:
        owner, alex, sam, stranger = (create_user(server) for _ in range(4))
        return {"owner": owner, "alex": alex, "sam": sam, "stranger": stranger}

    def test_only_the_people_in_it_see_it(self, server, people):
        made = self._start(server, people["owner"], people["alex"])
        assert [p["user_id"] for p in made["parties"]] == [
            people["owner"].id,
            people["alex"].id,
        ]
        assert made["owner_id"] == people["owner"].id
        for who, sees in (("owner", True), ("alex", True), ("stranger", False)):
            mine = server.get("/v1/agreements/mine", headers=self._headers(people[who]))
            assert mine.status_code == 200, mine.text
            ids = [a["id"] for a in mine.json()["agreements"]]
            assert (made["id"] in ids) is sees
        sneak = server.post(
            f"/v1/agreements/{made['id']}/terms",
            json={"text": "Mine now"},
            headers=self._headers(people["stranger"]),
        )
        assert sneak.status_code == 404, sneak.text

    def test_a_term_is_of_a_kind_the_app_names(self, server, people, house_rules):
        made = self._start(server, people["owner"])
        plain = server.post(
            f"/v1/agreements/{made['id']}/terms",
            json={"text": "Quiet after ten"},
            headers=self._headers(people["owner"]),
        )
        assert plain.status_code == 200, plain.text
        assert plain.json()["terms"][0]["kind"] == "clause"
        stray = server.post(
            f"/v1/agreements/{made['id']}/terms",
            json={"kind": "limit", "text": "Quiet after ten"},
            headers=self._headers(people["owner"]),
        )
        assert stray.status_code == 422, stray.text

    def test_a_change_asks_everyone_to_confirm_again(self, server, people, house_rules):
        made = self._start(server, people["owner"], people["alex"])
        added = server.post(
            f"/v1/agreements/{made['id']}/terms",
            json={"kind": "rule", "text": "Dishes done before bed"},
            headers=self._headers(people["alex"]),
        )
        assert added.status_code == 200, added.text
        term = added.json()["terms"][0]
        assert (term["kind"], term["written_by"]) == ("rule", people["alex"].id)
        version = added.json()["version"]
        for who in ("owner", "alex"):
            confirmed = server.post(
                f"/v1/agreements/{made['id']}/confirm",
                json={},
                headers=self._headers(people[who]),
            )
            assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["in_effect"] is True
        reworded = server.post(
            f"/v1/agreements/terms/{term['id']}",
            json={"text": "Dishes done by morning"},
            headers=self._headers(people["owner"]),
        )
        assert reworded.status_code == 200, reworded.text
        body = reworded.json()
        assert body["version"] == version + 1
        assert body["in_effect"] is False
        assert body["terms"][0]["text"] == "Dishes done by morning"
        assert body["terms"][0]["written_by"] == people["owner"].id
        assert [p["confirmed"] for p in body["parties"]] == [False, False]
        same = server.post(
            f"/v1/agreements/terms/{term['id']}",
            json={"text": "Dishes done by morning"},
            headers=self._headers(people["owner"]),
        )
        assert same.json()["version"] == version + 1
        gone = server.post(
            f"/v1/agreements/terms/{term['id']}/remove",
            json={},
            headers=self._headers(people["alex"]),
        )
        assert gone.status_code == 200, gone.text
        assert gone.json()["terms"] == []
        assert gone.json()["version"] == version + 2

    def test_renaming_and_revisiting_leave_confirmations_alone(self, server, people):
        made = self._start(server, people["owner"], people["alex"])
        for who in ("owner", "alex"):
            server.post(
                f"/v1/agreements/{made['id']}/confirm",
                json={},
                headers=self._headers(people[who]),
            )
        changed = server.post(
            f"/v1/agreements/{made['id']}/details",
            json={"title": "Our agreement", "clear_revisit": True},
            headers=self._headers(people["alex"]),
        )
        assert changed.status_code == 200, changed.text
        body = changed.json()
        assert (body["title"], body["revisit_on"], body["in_effect"]) == (
            "Our agreement",
            None,
            True,
        )

    def test_only_the_owner_adds_people_and_deletes_it(self, server, people):
        made = self._start(server, people["owner"], people["alex"])
        refused = server.post(
            f"/v1/agreements/{made['id']}/parties",
            json={"user_id": people["sam"].id},
            headers=self._headers(people["alex"]),
        )
        assert refused.status_code == 403, refused.text
        added = server.post(
            f"/v1/agreements/{made['id']}/parties",
            json={"user_id": people["sam"].id},
            headers=self._headers(people["owner"]),
        )
        assert added.status_code == 200, added.text
        assert len(added.json()["parties"]) == 3
        twice = server.post(
            f"/v1/agreements/{made['id']}/parties",
            json={"user_id": people["sam"].id},
            headers=self._headers(people["owner"]),
        )
        assert twice.status_code == 409, twice.text
        not_theirs = server.post(
            f"/v1/agreements/{made['id']}/remove",
            json={},
            headers=self._headers(people["alex"]),
        )
        assert not_theirs.status_code == 403, not_theirs.text
        deleted = server.post(
            f"/v1/agreements/{made['id']}/remove",
            json={},
            headers=self._headers(people["owner"]),
        )
        assert deleted.status_code == 200, deleted.text
        assert made["id"] not in [a["id"] for a in deleted.json()["agreements"]]
        after = server.get("/v1/agreements/mine", headers=self._headers(people["sam"]))
        assert made["id"] not in [a["id"] for a in after.json()["agreements"]]

    def test_anyone_may_leave_but_the_owner_deletes_instead(self, server, people):
        made = self._start(server, people["owner"], people["alex"], people["sam"])
        others = server.post(
            f"/v1/agreements/{made['id']}/parties/remove",
            json={"user_id": people["sam"].id},
            headers=self._headers(people["alex"]),
        )
        assert others.status_code == 403, others.text
        left = server.post(
            f"/v1/agreements/{made['id']}/parties/remove",
            json={"user_id": people["alex"].id},
            headers=self._headers(people["alex"]),
        )
        assert left.status_code == 200, left.text
        assert made["id"] not in [a["id"] for a in left.json()["agreements"]]
        owner_leaves = server.post(
            f"/v1/agreements/{made['id']}/parties/remove",
            json={"user_id": people["owner"].id},
            headers=self._headers(people["owner"]),
        )
        assert owner_leaves.status_code == 409, owner_leaves.text
