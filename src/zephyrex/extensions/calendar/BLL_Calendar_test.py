# SPDX-License-Identifier: AGPL-3.0-or-later
"""A user's own calendars, shared availability and booking, against a real
server. The calendar services themselves are the providers' own tests' to
call; here each account's answer stands in for its service (``on_account``),
so what is tested is whose calendars are read and written, and the time
worked out from them."""

from datetime import UTC, date, datetime, timedelta
from typing import Any, Dict, List

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.calendar import BLL_Calendar
from zephyrex.extensions.calendar.BLL_Calendar import CalendarAccountModel, merged
from zephyrex.extensions.calendar.EXT_Calendar import EXT_Calendar, connected_account
from zephyrex.extensions.calendar.PRV_CalDAV import PRV_CalDAV_Calendar
from zephyrex.extensions.calendar.PRV_Google import PRV_Google_Calendar
from zephyrex.extensions.ExternalErrors import AuthExternalError, TransientExternalError
from zephyrex.testing.factories import create_user

TOMORROW = date.today() + timedelta(days=1)
EVENING = {
    "day_start": "18:00",
    "day_end": "22:00",
    "timezone": "UTC",
    "duration_minutes": 120,
    "days": 1,
}


def at(hour: int) -> datetime:
    return datetime.combine(TOMORROW, datetime.min.time(), UTC) + timedelta(hours=hour)


class TestConnectedAccount:
    def test_the_provider_reads_the_account_while_the_call_runs(self):
        with connected_account(
            {
                "calendar_url": "https://dav.example.org/cal/",
                "username": "sam",
                "api_key": "app-password",
            }
        ) as instance:
            assert (
                PRV_CalDAV_Calendar.setting(instance, "calendar_url")
                == "https://dav.example.org/cal/"
            )
            assert PRV_CalDAV_Calendar.token(instance) == "app-password"
            assert instance.scope == "user"
        assert PRV_CalDAV_Calendar.setting(None, "username") != "sam"

    def test_an_unset_setting_falls_back_to_its_default_not_the_environment(
        self, monkeypatch
    ):
        monkeypatch.setenv("GOOGLE_CALENDAR_ID", "operator@example.org")
        with connected_account({"api_key": "token"}) as instance:
            assert PRV_Google_Calendar.setting(instance, "calendar_id") == "primary"


def test_busy_time_is_merged():
    assert merged(
        [(at(19), at(20)), (at(18), at(19)), (at(21), at(22)), (at(19), at(19))]
    ) == [
        (at(18), at(20)),
        (at(21), at(22)),
    ]


class TestCalendars(ExtensionServerMixin):
    extension_class = EXT_Calendar

    @staticmethod
    def _headers(user: Any) -> Dict[str, str]:
        return {"Authorization": f"Bearer {user.jwt}"}

    @pytest.fixture
    def services(self, monkeypatch) -> Dict[str, Any]:
        """Each account's calendar, by its label: busy times, events written,
        and labels whose service refuses or fails."""
        state: Dict[str, Any] = {
            "busy": {},
            "written": [],
            "refuse": set(),
            "down": set(),
        }

        async def answer(
            account: Any, model_registry: Any, method: str, *args: Any
        ) -> Any:
            if account.label in state["refuse"]:
                raise AuthExternalError("refused", provider=account.provider)
            if account.label in state["down"]:
                raise TransientExternalError("down", provider=account.provider)
            if method == "busy":
                return state["busy"].get(account.label, [])
            state["written"].append((account.label, args[0]["title"]))
            return {"id": "e1"}

        monkeypatch.setattr(BLL_Calendar, "on_account", answer)
        return state

    def _connect(self, server, user, label: str) -> Any:
        return server.post(
            "/v1/calendars/accounts",
            json={
                "provider": "caldav",
                "label": label,
                "calendar_url": "https://dav.example.org/cal/",
                "username": "someone",
                "password": "app-password",
            },
            headers=self._headers(user),
        )

    def test_a_calendar_connects_and_its_password_stays_secret(self, server, services):
        sam = create_user(server)
        made = self._connect(server, sam, "Sam's")
        assert made.status_code == 200, made.text
        assert "secret" not in made.json() and "password" not in made.json()
        listed = server.get(
            "/v1/calendars/accounts", headers=self._headers(sam)
        ).json()["accounts"]
        assert [a["label"] for a in listed] == ["Sam's"]
        registry = server.app.state.model_registry
        db = CalendarAccountModel.DB(registry.DB.manager.Base)
        stored = db.list(
            requester_id=sam.id,
            model_registry=registry,
            return_type="dto",
            override_dto=CalendarAccountModel,
        )
        assert stored[0].secret and "app-password" not in stored[0].secret

    def test_a_calendar_that_refuses_is_not_kept(self, server, services):
        sam = create_user(server)
        services["refuse"].add("Wrong password")
        refused = self._connect(server, sam, "Wrong password")
        assert refused.status_code == 422
        assert (
            server.get("/v1/calendars/accounts", headers=self._headers(sam)).json()[
                "accounts"
            ]
            == []
        )

    def test_caldav_needs_its_address_and_sign_in(self, server, services):
        response = server.post(
            "/v1/calendars/accounts",
            json={
                "provider": "caldav",
                "label": "x",
                "calendar_url": "https://dav.example.org/",
            },
            headers=self._headers(create_user(server)),
        )
        assert response.status_code == 422

    def test_google_without_a_linked_google_account(self, server):
        response = server.post(
            "/v1/calendars/accounts",
            json={"provider": "google_calendar", "label": "Work"},
            headers=self._headers(create_user(server)),
        )
        assert response.status_code == 409

    def test_free_time_is_when_everyone_is_free_and_only_with_those_who_share(
        self, server, services
    ):
        sam, alex, stranger = (create_user(server) for _ in range(3))
        self._connect(server, sam, "Sam's")
        self._connect(server, alex, "Alex's")
        services["busy"] = {"Sam's": [(at(18), at(19))], "Alex's": [(at(20), at(21))]}
        request = {
            "user_ids": [alex.id],
            "first_day": TOMORROW.isoformat(),
            **EVENING,
            "duration_minutes": 60,
        }
        refused = server.post(
            "/v1/calendars/free", json=request, headers=self._headers(sam)
        )
        assert refused.status_code == 403
        server.post(
            "/v1/calendars/shares",
            json={"viewer_user_id": sam.id},
            headers=self._headers(alex),
        )
        free = server.post(
            "/v1/calendars/free", json=request, headers=self._headers(sam)
        )
        assert free.status_code == 200, free.text
        starts = [slot["start"] for slot in free.json()["slots"]]
        assert starts == [
            at(19).isoformat().replace("+00:00", "Z"),
            at(21).isoformat().replace("+00:00", "Z"),
        ]
        assert {p["user_id"]: p["calendars"] for p in free.json()["people"]} == {
            sam.id: 1,
            alex.id: 1,
        }
        assert (
            server.post(
                "/v1/calendars/free",
                json={**request, "user_ids": [stranger.id]},
                headers=self._headers(sam),
            ).status_code
            == 403
        )

    def test_a_calendar_that_does_not_answer_is_counted_not_fatal(
        self, server, services
    ):
        sam = create_user(server)
        self._connect(server, sam, "Flaky")
        services["down"].add("Flaky")
        free = server.post(
            "/v1/calendars/free",
            json={"first_day": TOMORROW.isoformat(), **EVENING},
            headers=self._headers(sam),
        )
        assert free.status_code == 200, free.text
        assert free.json()["people"] == [
            {"user_id": sam.id, "calendars": 0, "unreadable": 1}
        ]

    def test_booking_goes_where_it_is_allowed(self, server, services):
        sam, alex, jo = (create_user(server) for _ in range(3))
        for user, label in ((sam, "Sam's"), (alex, "Alex's"), (jo, "Jo's")):
            self._connect(server, user, label)
        server.post(
            "/v1/calendars/shares",
            json={"viewer_user_id": sam.id, "can_book": True},
            headers=self._headers(alex),
        )
        server.post(
            "/v1/calendars/shares",
            json={"viewer_user_id": sam.id},
            headers=self._headers(jo),
        )
        booked = server.post(
            "/v1/calendars/book",
            json={
                "title": "Dinner",
                "start": at(19).isoformat(),
                "end": at(21).isoformat(),
                "user_ids": [alex.id, jo.id],
            },
            headers=self._headers(sam),
        )
        assert booked.status_code == 200, booked.text
        assert sorted(booked.json()["booked"]) == sorted([sam.id, alex.id])
        assert booked.json()["not_booked"] == [jo.id]
        assert sorted(services["written"]) == [
            ("Alex's", "Dinner"),
            ("Sam's", "Dinner"),
        ]

    def test_sharing_changes_and_stops(self, server, services):
        sam, alex = create_user(server), create_user(server)
        headers = self._headers(alex)
        server.post(
            "/v1/calendars/shares", json={"viewer_user_id": sam.id}, headers=headers
        )
        changed = server.post(
            "/v1/calendars/shares",
            json={"viewer_user_id": sam.id, "can_book": True},
            headers=headers,
        )
        assert changed.json()["shares"] == [{"user_id": sam.id, "can_book": True}]
        assert server.get(
            "/v1/calendars/shared-with-me", headers=self._headers(sam)
        ).json()["shares"] == [{"user_id": alex.id, "can_book": True}]
        stopped = server.post(
            f"/v1/calendars/shares/{sam.id}/remove", json={}, headers=headers
        )
        assert stopped.json()["shares"] == []

    def test_a_calendar_disconnects(self, server, services):
        sam = create_user(server)
        made = self._connect(server, sam, "Sam's").json()
        left = server.post(
            f"/v1/calendars/accounts/{made['id']}/remove",
            json={},
            headers=self._headers(sam),
        )
        assert left.json()["accounts"] == []
        other = server.post(
            f"/v1/calendars/accounts/{made['id']}/remove",
            json={},
            headers=self._headers(create_user(server)),
        )
        assert other.status_code == 404
