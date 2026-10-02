# SPDX-License-Identifier: AGPL-3.0-or-later
"""The calendar extension: times and ranges, event field checks, the
free-time computation (buffers, busy spans, a daylight-saving day),
each provider's request bodies and answers, Calendly's refusals, real
calls refusing a bogus token, and read-only live checks."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    PermanentExternalError,
)
from zephyrex.extensions.calendar.EXT_Calendar import (
    EXT_Calendar,
    aware,
    clock,
    event_fields,
    free_slots,
    iso,
    parse_time,
    time_range,
    zone,
)
from zephyrex.extensions.calendar.PRV_Calendly import PRV_Calendly_Calendar
from zephyrex.extensions.calendar.PRV_Google import PRV_Google_Calendar, when
from zephyrex.extensions.calendar.PRV_Microsoft import (
    PRV_Microsoft_Calendar,
    graph_time,
)

BOGUS = "not-a-real-token"
DAY = date(2026, 10, 5)  # a Monday
HALF_HOUR = timedelta(minutes=30)


def at(hour: int, minute: int = 0, day: date = DAY) -> datetime:
    return datetime.combine(day, time(hour, minute), UTC)


def _online(url: str) -> bool:
    try:
        httpx.head(url, timeout=5)
        return True
    except httpx.HTTPError:
        return False


def reachable(url: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(not _online(url), reason=f"{url} is unreachable")


class TestTimes:
    def test_naive_is_utc(self):
        assert aware(datetime(2026, 10, 5, 9)) == at(9)
        assert iso(datetime(2026, 10, 5, 9)) == "2026-10-05T09:00:00Z"

    def test_offsets_convert(self):
        assert parse_time("2026-10-05T11:00:00+02:00") == at(9)
        assert parse_time("2026-10-05T09:00:00.0000000") == at(9)

    def test_ranges(self):
        assert time_range(at(9), at(10)) == (at(9), at(10))
        with pytest.raises(InvalidInputExternalError):
            time_range(at(10), at(9))
        with pytest.raises(InvalidInputExternalError):
            time_range(at(9), at(9) + timedelta(days=400))

    def test_zones_and_clock_times(self):
        assert zone("Europe/Paris") == ZoneInfo("Europe/Paris")
        assert clock("09:30") == time(9, 30)
        for bad in ("Mars/Olympus", "../etc"):
            with pytest.raises(InvalidInputExternalError):
                zone(bad)
        with pytest.raises(InvalidInputExternalError):
            clock("9am")

    def test_event_fields(self):
        fields = event_fields("Standup", at(9), at(10), None, None, ["a@b.co"], True)
        assert fields == {
            "title": "Standup",
            "start": at(9),
            "end": at(10),
            "attendees": ["a@b.co"],
            "online_meeting": True,
        }
        with pytest.raises(InvalidInputExternalError):
            event_fields("  ", None, None, None, None, None, None)
        with pytest.raises(InvalidInputExternalError):
            event_fields("x", None, None, None, None, ["not an address"], None)

    async def test_ability_arguments_are_checked_first(self):
        with pytest.raises(InvalidInputExternalError):
            await EXT_Calendar.update_event("google_calendar", "e1", start=at(9))
        with pytest.raises(InvalidInputExternalError):
            await EXT_Calendar.update_event("google_calendar", "e1")
        with pytest.raises(InvalidInputExternalError):
            await EXT_Calendar.find_free_time("google_calendar", DAY, days=0)
        with pytest.raises(InvalidInputExternalError):
            await EXT_Calendar.find_free_time(
                "google_calendar", DAY, work_day_start="17:00", work_day_end="09:00"
            )


class TestFreeSlots:
    def slots(self, busy, days=1, buffer=timedelta(0), where=UTC, first=DAY):
        return free_slots(
            busy, first, days, time(9), time(12), HALF_HOUR, buffer, where
        )

    def test_an_empty_morning(self):
        assert [start.hour * 60 + start.minute for start, _ in self.slots([])] == [
            540,
            570,
            600,
            630,
            660,
            690,
        ]

    def test_busy_time_is_skipped_and_slots_resume_after_it(self):
        found = self.slots([(at(9, 45), at(10, 20))])
        assert found[0] == (at(9), at(9, 30))
        assert (at(10, 20), at(10, 50)) in found
        assert all(end <= at(9, 45) or start >= at(10, 20) for start, end in found)

    def test_a_buffer_keeps_slots_clear(self):
        found = self.slots([(at(10), at(10, 30))], buffer=timedelta(minutes=15))
        assert all(end <= at(9, 45) or start >= at(10, 45) for start, end in found)

    def test_a_busy_span_over_days(self):
        found = self.slots([(at(11, day=DAY), at(10, day=DAY + timedelta(days=1)))], 2)
        assert all(
            start.date() == DAY or start >= at(10, day=DAY + timedelta(days=1))
            for start, _ in found
        )
        assert (
            at(11, 30, DAY + timedelta(days=1)),
            at(12, day=DAY + timedelta(days=1)),
        ) in found

    def test_working_hours_follow_the_zone_across_daylight_saving(self):
        """Paris leaves summer time on 2026-10-25: 09:00 is 07:00 UTC on
        the 24th and 08:00 UTC on the 26th."""
        paris = ZoneInfo("Europe/Paris")
        saturday = self.slots([], where=paris, first=date(2026, 10, 24))
        monday = self.slots([], where=paris, first=date(2026, 10, 26))
        assert saturday[0][0] == datetime(2026, 10, 24, 7, tzinfo=UTC)
        assert monday[0][0] == datetime(2026, 10, 26, 8, tzinfo=UTC)


class TestProviders:
    def test_google_bodies_and_answers(self):
        body = PRV_Google_Calendar._body(
            {"title": "Demo", "start": at(9), "end": at(10), "online_meeting": True}
        )
        assert body["start"] == {"dateTime": "2026-10-05T09:00:00Z"}
        assert body["conferenceData"]["createRequest"]["conferenceSolutionKey"] == {
            "type": "hangoutsMeet"
        }
        assert when({"date": "2026-10-05"}) == "2026-10-05"
        event = PRV_Google_Calendar._event(
            {
                "id": "e1",
                "summary": "Demo",
                "start": {"dateTime": "2026-10-05T09:00:00Z"},
                "end": {"dateTime": "2026-10-05T10:00:00Z"},
                "attendees": [{"email": "a@b.co"}, {"displayName": "no address"}],
                "hangoutLink": "https://meet.google.com/abc",
            }
        )
        assert event["attendees"] == ["a@b.co"]
        assert event["meeting_url"] == "https://meet.google.com/abc"

    def test_microsoft_bodies_and_answers(self):
        body = PRV_Microsoft_Calendar._body(
            {
                "start": at(9),
                "end": at(10),
                "description": "Notes",
                "online_meeting": True,
            }
        )
        assert body["start"] == {"dateTime": "2026-10-05T09:00:00Z", "timeZone": "UTC"}
        assert body["onlineMeetingProvider"] == "teamsForBusiness"
        assert graph_time({"dateTime": "2026-10-05T09:00:00.0000000"}) == (
            "2026-10-05T09:00:00Z"
        )

    async def test_calendly_cannot_create_or_change(self, provider_instance):
        instance = provider_instance(PRV_Calendly_Calendar, api_key=BOGUS)
        with pytest.raises(PermanentExternalError):
            await PRV_Calendly_Calendar.create_event(instance, {"title": "x"})
        with pytest.raises(PermanentExternalError):
            await PRV_Calendly_Calendar.update_event(instance, "e1", {"title": "x"})

    async def test_a_calendly_id_stays_one_path_segment(self, provider_instance):
        instance = provider_instance(PRV_Calendly_Calendar, api_key=BOGUS)
        with pytest.raises(InvalidInputExternalError):
            await PRV_Calendly_Calendar.delete_event(instance, "../users/me")


class TestRefusedTokens:
    @reachable("https://www.googleapis.com")
    async def test_google(self, provider_instance):
        with pytest.raises(AuthExternalError):
            await PRV_Google_Calendar.list_events(
                provider_instance(PRV_Google_Calendar, api_key=BOGUS), at(9), at(10), 5
            )

    @reachable("https://graph.microsoft.com")
    async def test_microsoft(self, provider_instance):
        with pytest.raises(AuthExternalError):
            await PRV_Microsoft_Calendar.list_events(
                provider_instance(PRV_Microsoft_Calendar, api_key=BOGUS),
                at(9),
                at(10),
                5,
            )

    @reachable("https://api.calendly.com")
    async def test_calendly(self, provider_instance):
        with pytest.raises(AuthExternalError):
            await PRV_Calendly_Calendar.list_events(
                provider_instance(PRV_Calendly_Calendar, api_key=BOGUS),
                at(9),
                at(10),
                5,
            )


class TestLive:
    """Read-only checks with test accounts (the next week's events)."""

    @pytest.mark.external_api(provider="google_calendar_test")
    async def test_google(self, provider_instance, sandbox_credentials_for):
        token = sandbox_credentials_for("google_calendar_test")["GOOGLE_CALENDAR_TOKEN"]
        now = datetime.now(UTC)
        events = await PRV_Google_Calendar.list_events(
            provider_instance(PRV_Google_Calendar, api_key=token),
            now,
            now + timedelta(days=7),
            10,
        )
        assert isinstance(events, list)

    @pytest.mark.external_api(provider="microsoft_calendar_test")
    async def test_microsoft(self, provider_instance, sandbox_credentials_for):
        token = sandbox_credentials_for("microsoft_calendar_test")[
            "MICROSOFT_GRAPH_TOKEN"
        ]
        now = datetime.now(UTC)
        busy = await PRV_Microsoft_Calendar.busy(
            provider_instance(PRV_Microsoft_Calendar, api_key=token),
            now,
            now + timedelta(days=7),
        )
        assert all(start < end for start, end in busy)

    @pytest.mark.external_api(provider="calendly_test")
    async def test_calendly(self, provider_instance, sandbox_credentials_for):
        token = sandbox_credentials_for("calendly_test")["CALENDLY_TOKEN"]
        now = datetime.now(UTC)
        busy = await PRV_Calendly_Calendar.busy(
            provider_instance(PRV_Calendly_Calendar, api_key=token),
            now,
            now + timedelta(days=10),
        )
        assert all(start < end for start, end in busy)
