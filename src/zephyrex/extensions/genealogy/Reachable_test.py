# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reaching people through the network: a tie counts when it was in effect
at any time in the window; ``reachable_accounts`` walks mutually linked
accounts outward, however each is shown, and skips a one-sided link, a tie
that ended before the window and a kind not walked."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import pytest
from fastapi import FastAPI

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.genealogy.BLL_Genealogy import PersonManager
from zephyrex.extensions.genealogy.EXT_Genealogy import EXT_Genealogy
from zephyrex.extensions.genealogy.SocialGraph import Tie
from zephyrex.testing.factories import create_user

NOW = datetime(2026, 11, 2, tzinfo=timezone.utc)


def _tie(start: Any, end: Any) -> Tie:
    return Tie("a", "b", "partnership", valid_from=start, valid_to=end)


@pytest.mark.parametrize(
    "start, end, within",
    [
        (None, None, True),
        (NOW - timedelta(days=90), None, True),
        (NOW - timedelta(days=90), NOW - timedelta(days=40), True),
        (NOW - timedelta(days=90), NOW - timedelta(days=61), False),
        (NOW + timedelta(days=1), None, False),
    ],
)
def test_a_tie_in_effect_at_any_time_in_the_window(start, end, within):
    assert _tie(start, end).in_effect_between(NOW - timedelta(days=60), NOW) is within


class TestReachableAccounts(ExtensionServerMixin):
    extension_class = EXT_Genealogy

    @staticmethod
    def _headers(user: Any) -> Dict[str, str]:
        return {"Authorization": f"Bearer {user.jwt}"}

    def _person(self, server, user, name: str, **fields: Any) -> str:
        response = server.post(
            "/v1/person",
            json={"person": {"name": name, **fields}},
            headers=self._headers(user),
        )
        assert response.status_code == 201, response.text
        return str(response.json()["person"]["id"])

    def _tie(self, server, user, a: str, b: str, **fields: Any) -> None:
        response = server.post(
            "/v1/relationship",
            json={
                "relationship": {
                    "person_id": a,
                    "target_person_id": b,
                    "kind": fields.pop("kind", "partnership"),
                    **fields,
                }
            },
            headers=self._headers(user),
        )
        assert response.status_code == 201, response.text

    @pytest.fixture(scope="class")
    def network(self, server) -> Dict[str, Any]:
        """Sam–Alex and Alex–Jo partners; Jo's tie with Pat ended long ago;
        Sam records Lee, who never linked back; Jo and Kim are friends;
        Jo is hidden from everyone."""
        users = {
            n: create_user(server) for n in ("sam", "alex", "jo", "pat", "lee", "kim")
        }
        selves = {
            n: self._person(
                server,
                u,
                n,
                linked_user_id=u.id,
                **({"visibility": "hidden"} if n == "jo" else {}),
            )
            for n, u in users.items()
        }
        ended = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()

        def both(one: str, other: str, **fields: Any) -> None:
            for me, them in ((one, other), (other, one)):
                theirs = self._person(
                    server, users[me], them, linked_user_id=users[them].id
                )
                self._tie(server, users[me], selves[me], theirs, **dict(fields))

        both("sam", "alex")
        both("alex", "jo")
        both("jo", "pat", valid_to=ended)
        both("jo", "kim", kind="friendship")
        lee = self._person(server, users["sam"], "lee", linked_user_id=users["lee"].id)
        self._tie(server, users["sam"], selves["sam"], lee)
        return users

    def _reached(self, server, user, since_days: int) -> Dict[str, int]:
        app = server.app
        assert isinstance(app, FastAPI)
        people = PersonManager(
            model_registry=app.state.model_registry, requester_id=user.id
        )
        since = datetime.now(timezone.utc) - timedelta(days=since_days)
        return people.reachable_accounts(["partnership"], since)

    def test_partners_and_their_partners_are_reached(self, server, network):
        reached = self._reached(server, network["sam"], 30)
        assert reached == {network["alex"].id: 1, network["jo"].id: 2}

    def test_a_tie_that_ended_before_the_window_is_not_walked(self, server, network):
        assert network["pat"].id not in self._reached(server, network["sam"], 30)

    def test_a_tie_in_the_window_is_walked_though_it_has_ended(self, server, network):
        assert self._reached(server, network["sam"], 400)[network["pat"].id] == 3

    def test_someone_who_never_linked_back_is_not_reached(self, server, network):
        assert network["lee"].id not in self._reached(server, network["sam"], 400)

    def test_kinds_not_walked_are_not_followed(self, server, network):
        assert network["kim"].id not in self._reached(server, network["sam"], 400)

    def test_an_account_with_no_self_person_reaches_nobody(self, server):
        assert self._reached(server, create_user(server), 30) == {}
