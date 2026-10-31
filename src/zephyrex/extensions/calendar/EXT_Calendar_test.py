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
from zephyrex.extensions.calendar.PRV_CalDAV import (
    PRV_CalDAV_Calendar,
    calendar_query,
    changed_calendar,
    event_id,
    new_calendar,
    resources,
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


# A REPORT answer as Nextcloud and iCloud give it: a timed event with an
# attendee, a recurring one expanded into an occurrence, an all-day event, a
# transparent one and a cancelled one.
CALDAV_MULTISTATUS = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">
 <d:response><d:href>/cal/dates/dinner%20one.ics</d:href><d:propstat><d:prop>
  <cal:calendar-data>BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:dinner
SUMMARY:Dinner &amp; a show
DTSTART:20261005T180000Z
DTEND:20261005T210000Z
LOCATION:Downtown
ATTENDEE;CN=Alex:mailto:alex@example.com
END:VEVENT
END:VCALENDAR
</cal:calendar-data></d:prop></d:propstat></d:response>
 <d:response><d:href>/cal/dates/weekly.ics</d:href><d:propstat><d:prop>
  <cal:calendar-data>BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:weekly
SUMMARY:Date night
DTSTART:20261005T090000Z
DURATION:PT1H
RECURRENCE-ID:20261005T090000Z
END:VEVENT
END:VCALENDAR
</cal:calendar-data></d:prop></d:propstat></d:response>
 <d:response><d:href>/cal/dates/trip.ics</d:href><d:propstat><d:prop>
  <cal:calendar-data>BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:trip
SUMMARY:Weekend away
DTSTART;VALUE=DATE:20261005
DTEND;VALUE=DATE:20261006
END:VEVENT
BEGIN:VEVENT
UID:reminder
SUMMARY:Reminder
DTSTART:20261005T120000Z
DTEND:20261005T130000Z
TRANSP:TRANSPARENT
END:VEVENT
BEGIN:VEVENT
UID:called-off
SUMMARY:Called off
DTSTART:20261005T140000Z
DTEND:20261005T150000Z
STATUS:CANCELLED
END:VEVENT
END:VCALENDAR
</cal:calendar-data></d:prop></d:propstat></d:response>
 <d:response><d:href>/cal/dates/</d:href><d:propstat><d:prop/></d:propstat></d:response>
</d:multistatus>"""


class TestCalDAV:
    def test_the_query_asks_for_the_range_with_recurrences_expanded(self):
        query = calendar_query(at(9), at(17))
        assert '<C:expand start="20261005T090000Z" end="20261005T170000Z"/>' in query
        assert (
            '<C:time-range start="20261005T090000Z" end="20261005T170000Z"/>' in query
        )

    def test_an_answer_lists_each_event_earliest_first(self):
        found = PRV_CalDAV_Calendar._events(resources(CALDAV_MULTISTATUS))
        events = [event for event, _ in found]
        assert [e["title"] for e in events] == [
            "Weekend away",
            "Date night",
            "Reminder",
            "Called off",
            "Dinner & a show",
        ]
        dinner = events[-1]
        assert dinner["id"] == "dinner one.ics"
        assert (dinner["start"], dinner["end"]) == (
            "2026-10-05T18:00:00Z",
            "2026-10-05T21:00:00Z",
        )
        assert dinner["attendees"] == ["alex@example.com"]
        assert dinner["location"] == "Downtown"
        assert events[1]["end"] == "2026-10-05T10:00:00Z"  # from its DURATION
        assert (events[0]["start"], events[0]["end"]) == ("2026-10-05", "2026-10-06")

    def test_only_time_that_blocks_is_busy(self):
        spans = {
            event["title"]: span
            for event, span in PRV_CalDAV_Calendar._events(
                resources(CALDAV_MULTISTATUS)
            )
        }
        assert spans["Reminder"] is None
        assert spans["Called off"] is None
        assert spans["Dinner & a show"] == (at(18), at(21))
        assert spans["Weekend away"] == (at(0), at(0) + timedelta(days=1))

    def test_a_new_event_round_trips(self):
        body = new_calendar(
            "uid-1",
            {
                "title": "Picnic",
                "start": at(12),
                "end": at(14),
                "attendees": ["jo@x.co"],
            },
        )
        [(event, span)] = PRV_CalDAV_Calendar._events([("uid-1.ics", body.decode())])
        assert (event["id"], event["title"], event["attendees"]) == (
            "uid-1.ics",
            "Picnic",
            ["jo@x.co"],
        )
        assert span == (at(12), at(14))

    def test_a_change_rewrites_the_series_not_its_overrides(self):
        original = new_calendar(
            "uid-2", {"title": "Old", "start": at(9), "end": at(10)}
        )
        changed = changed_calendar(
            original.decode(), {"title": "New", "start": at(11), "end": at(12)}
        )
        [(event, _)] = PRV_CalDAV_Calendar._events([("uid-2.ics", changed.decode())])
        assert (event["title"], event["start"]) == ("New", "2026-10-05T11:00:00Z")

    def test_an_online_meeting_is_refused(self):
        with pytest.raises(InvalidInputExternalError, match="online meetings"):
            new_calendar(
                "uid-3",
                {
                    "title": "Call",
                    "start": at(9),
                    "end": at(10),
                    "online_meeting": True,
                },
            )

    def test_an_id_is_the_resource_name(self):
        assert event_id("/cal/dates/dinner%20one.ics") == "dinner one.ics"

    async def test_an_id_stays_one_path_segment(self, provider_instance):
        instance = provider_instance(
            PRV_CalDAV_Calendar,
            api_key=BOGUS,
            settings={"calendar_url": "https://caldav.example.com/cal/"},
        )
        for bad in ("../other/x.ics", "..", ""):
            with pytest.raises(InvalidInputExternalError):
                await PRV_CalDAV_Calendar.delete_event(instance, bad)

    def test_a_non_xml_answer_is_a_precise_error(self):
        with pytest.raises(PermanentExternalError, match="not XML"):
            resources("<html><body>Sign in")


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

    @reachable("https://caldav.icloud.com")
    async def test_caldav(self, provider_instance):
        with pytest.raises(AuthExternalError):
            await PRV_CalDAV_Calendar.list_events(
                provider_instance(
                    PRV_CalDAV_Calendar,
                    api_key=BOGUS,
                    settings={
                        "username": "nobody@example.com",
                        "calendar_url": "https://caldav.icloud.com/",
                    },
                ),
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
