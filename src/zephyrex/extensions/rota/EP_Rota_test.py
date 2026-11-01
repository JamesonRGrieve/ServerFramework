# SPDX-License-Identifier: AGPL-3.0-or-later
"""The rota against a real server: the owner sets the nights; members see the
rota and its schedule and strangers do not; a swap needs both people, and
moves the night only once the other one accepts."""

from datetime import date, timedelta
from typing import Any, Dict

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.rota.BLL_Rota import scheduled
from zephyrex.extensions.rota.EXT_Rota import EXT_Rota
from zephyrex.testing.factories import create_user

MONDAY = date(2026, 6, 1)


class Night:
    def __init__(self, day_index: int, user_id: str) -> None:
        self.day_index, self.user_id = day_index, user_id


class Swap:
    def __init__(self, night: date, frm: str, to: str, status: str) -> None:
        self.night, self.from_user_id, self.to_user_id, self.status = (
            night,
            frm,
            to,
            status,
        )
        self.created_at = self.updated_at = None


def test_the_schedule_repeats_and_applies_accepted_swaps():
    nights = [Night(0, "a"), Night(1, "b")]
    swaps = [
        Swap(MONDAY + timedelta(days=2), "a", "b", "accepted"),
        Swap(MONDAY + timedelta(days=3), "b", "a", "pending"),
    ]
    found = scheduled(MONDAY, 2, nights, swaps, MONDAY - timedelta(days=1), 5)
    assert [(n.night.isoformat(), n.user_id, n.swapped) for n in found] == [
        ("2026-05-31", None, False),
        ("2026-06-01", "a", False),
        ("2026-06-02", "b", False),
        ("2026-06-03", "b", True),
        ("2026-06-04", "b", False),
    ]


class TestRota(ExtensionServerMixin):
    extension_class = EXT_Rota

    @staticmethod
    def _headers(user: Any) -> Dict[str, str]:
        return {"Authorization": f"Bearer {user.jwt}"}

    @pytest.fixture(scope="class")
    def household(self, server) -> Dict[str, Any]:
        owner, alex, sam, stranger = (create_user(server) for _ in range(4))
        rota = server.post(
            "/v1/rota",
            json={
                "rota": {
                    "name": "Nights",
                    "cycle_days": 2,
                    "starts_on": MONDAY.isoformat(),
                }
            },
            headers=self._headers(owner),
        )
        assert rota.status_code == 201, rota.text
        rota_id = rota.json()["rota"]["id"]
        for index, who in ((0, alex), (1, sam)):
            night = server.post(
                "/v1/rota_night",
                json={
                    "rota_night": {
                        "rota_id": rota_id,
                        "day_index": index,
                        "user_id": who.id,
                    }
                },
                headers=self._headers(owner),
            )
            assert night.status_code == 201, night.text
        return {
            "owner": owner,
            "alex": alex,
            "sam": sam,
            "stranger": stranger,
            "rota_id": rota_id,
        }

    def test_only_the_owner_sets_nights(self, server, household):
        response = server.post(
            "/v1/rota_night",
            json={
                "rota_night": {
                    "rota_id": household["rota_id"],
                    "day_index": 0,
                    "user_id": household["sam"].id,
                }
            },
            headers=self._headers(household["alex"]),
        )
        assert response.status_code == 404, response.text
        twice = server.post(
            "/v1/rota_night",
            json={
                "rota_night": {
                    "rota_id": household["rota_id"],
                    "day_index": 0,
                    "user_id": household["sam"].id,
                }
            },
            headers=self._headers(household["owner"]),
        )
        assert twice.status_code == 409, twice.text

    def test_members_see_the_rota_and_strangers_do_not(self, server, household):
        mine = server.get(
            "/v1/rotas/mine", headers=self._headers(household["alex"])
        ).json()["rotas"]
        assert [rota["name"] for rota in mine] == ["Nights"]
        theirs = server.get(
            "/v1/rotas/mine", headers=self._headers(household["stranger"])
        ).json()["rotas"]
        assert theirs == []
        refused = server.get(
            f"/v1/rotas/{household['rota_id']}/schedule",
            headers=self._headers(household["stranger"]),
        )
        assert refused.status_code == 404

    def test_a_swap_needs_the_other_person(self, server, household):
        rota_id, alex, sam = household["rota_id"], household["alex"], household["sam"]
        night = (MONDAY + timedelta(days=4)).isoformat()  # an Alex night
        asked = server.post(
            f"/v1/rotas/{rota_id}/swaps",
            json={"night": night, "to_user_id": sam.id},
            headers=self._headers(alex),
        )
        assert asked.status_code == 200, asked.text
        swap = asked.json()
        assert swap["status"] == "pending"

        def with_whom() -> str:
            schedule = server.get(
                f"/v1/rotas/{rota_id}/schedule",
                params={"start": night, "days": 1},
                headers=self._headers(alex),
            ).json()
            return str(schedule["nights"][0]["user_id"])

        assert with_whom() == alex.id
        refused = server.post(
            f"/v1/rotas/swaps/{swap['id']}/answer",
            json={"accept": True},
            headers=self._headers(alex),
        )
        assert refused.status_code == 403, refused.text
        outsider = server.post(
            f"/v1/rotas/swaps/{swap['id']}/answer",
            json={"accept": True},
            headers=self._headers(household["owner"]),
        )
        assert outsider.status_code == 404, outsider.text
        accepted = server.post(
            f"/v1/rotas/swaps/{swap['id']}/answer",
            json={"accept": True},
            headers=self._headers(sam),
        )
        assert accepted.status_code == 200, accepted.text
        assert with_whom() == sam.id

    def test_only_the_two_people_can_ask(self, server, household):
        night = (MONDAY + timedelta(days=6)).isoformat()  # an Alex night
        response = server.post(
            f"/v1/rotas/{household['rota_id']}/swaps",
            json={"night": night, "to_user_id": household["alex"].id},
            headers=self._headers(household["sam"]),
        )
        assert (
            response.status_code == 422
        ), response.text  # handed to whoever has it already

    def test_erasing_an_account_takes_its_rotas_and_leaves_its_nights_open(
        self, server, household
    ):
        from zephyrex.extensions.rota.BLL_Rota import erase_rota

        registry = server.app.state.model_registry
        erase_rota(household["sam"].id, registry)
        erase_rota(household["sam"].id, registry)
        mine = server.get(
            "/v1/rotas/mine", headers=self._headers(household["owner"])
        ).json()["rotas"]
        assert [night["user_id"] for night in mine[0]["nights"]] == [
            household["alex"].id
        ]
        # Each night carries its id and version, for the owner to change it.
        assert all(night["id"] and night["created_at"] for night in mine[0]["nights"])
        erase_rota(household["owner"].id, registry)
        assert (
            server.get(
                "/v1/rotas/mine", headers=self._headers(household["alex"])
            ).json()["rotas"]
            == []
        )
