# SPDX-License-Identifier: AGPL-3.0-or-later
"""Any CalDAV calendar (RFC 4791): iCloud, Fastmail, Nextcloud, Radicale,
Baïkal and the rest.

An instance is one calendar collection: ``calendar_url`` is the
collection's URL, signed in to with ``username`` and the API key as the
password (an app-specific password on iCloud and Fastmail). An event's id
is its resource name in the collection (``<uid>.ics``); a recurring event
is listed once per occurrence (the server expands it), each with the
series' id, so a change or deletion applies to the whole series.

CalDAV has no online meetings, so asking for one is refused. Busy time is
the events in the range that block time (not ``TRANSP:TRANSPARENT``, not
cancelled), since not every server answers a free-busy query.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, ClassVar, Dict, List, Optional, Tuple
from icalendar import Calendar, Event, vCalAddress

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.calendar.EXT_Calendar import (
    AbstractCalendarProvider,
    Interval,
    aware,
    iso,
)
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    PermanentExternalError,
    TransientExternalError,
)
from zephyrex.lib.DAV import (
    CALDAV,
    collection_url,
    multistatus_resources,
    resource_name,
    resource_url,
)
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

# How many events a busy-time read takes in, at most: a day's worth of
# meetings for a month and more.
BUSY_EVENT_LIMIT = 1000
PRODUCT_ID = "-//Zephyrex//Calendar//EN"


def caldav_time(moment: datetime) -> str:
    """A CalDAV time-range bound: UTC, basic format (``20261005T090000Z``)."""
    return aware(moment).strftime("%Y%m%dT%H%M%SZ")


def calendar_query(start: datetime, end: datetime) -> str:
    """A calendar-query REPORT for the events overlapping the range, with
    recurring events expanded into their occurrences in it."""
    since, until = caldav_time(start), caldav_time(end)
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
        "<D:prop><D:getetag/>"
        f'<C:calendar-data><C:expand start="{since}" end="{until}"/></C:calendar-data>'
        "</D:prop>"
        '<C:filter><C:comp-filter name="VCALENDAR"><C:comp-filter name="VEVENT">'
        f'<C:time-range start="{since}" end="{until}"/>'
        "</C:comp-filter></C:comp-filter></C:filter>"
        "</C:calendar-query>"
    )


def resources(multistatus: str) -> List[Tuple[str, str]]:
    """Each ``(href, iCalendar)`` in a REPORT's multistatus answer."""
    return multistatus_resources(multistatus, f"{CALDAV}calendar-data")


def event_id(href: str) -> str:
    """The resource name an href ends in: the event's id in its collection."""
    return resource_name(href)


def _moment(value: Any) -> Optional[str]:
    """A VEVENT time as the calendar answers it: an ISO UTC time, or an
    all-day event's date."""
    if value is None:
        return None
    moment = value.dt
    if isinstance(moment, datetime):
        return iso(moment)
    if isinstance(moment, date):
        return moment.isoformat()
    return None


def _end(component: Any) -> Optional[str]:
    if component.get("DTEND") is not None:
        return _moment(component.get("DTEND"))
    start = component.get("DTSTART")
    duration = component.get("DURATION")
    if start is None or duration is None:
        return _moment(start)
    finish = start.dt + duration.dt
    return iso(finish) if isinstance(finish, datetime) else finish.isoformat()


def _attendees(component: Any) -> List[str]:
    found = component.get("ATTENDEE")
    if found is None:
        return []
    listed = found if isinstance(found, list) else [found]
    return [
        str(a)[len("mailto:") :] for a in listed if str(a).lower().startswith("mailto:")
    ]


    """When a VEVENT blocks time, as UTC; None for an all-day date-only event
    with no time, or one that is transparent or cancelled."""
    if str(component.get("TRANSP", "")).upper() == "TRANSPARENT":
        return None
    if str(component.get("STATUS", "")).upper() == "CANCELLED":
        return None
    start = component.get("DTSTART")
    if start is None:
        return None
    begin = start.dt
    if component.get("DTEND") is not None:
        finish = component.get("DTEND").dt
    elif component.get("DURATION") is not None:
        finish = begin + component.get("DURATION").dt
    else:
        finish = begin + (
            timedelta(days=1) if not isinstance(begin, datetime) else timedelta()
        )
    if not isinstance(begin, datetime):
        begin = datetime.combine(begin, datetime.min.time(), UTC)
        finish = datetime.combine(finish, datetime.min.time(), UTC)
    return aware(begin), aware(finish)


    """Write the calendar extension's event ``fields`` onto a VEVENT."""
    if fields.get("online_meeting"):
        raise InvalidInputExternalError("CalDAV calendars have no online meetings")
    for ours, theirs in (
        ("title", "SUMMARY"),
        ("location", "LOCATION"),
        ("description", "DESCRIPTION"),
    ):
        if ours in fields:
            component.pop(theirs, None)
            component.add(theirs, fields[ours])
    if "start" in fields:
        for key in ("DTSTART", "DTEND", "DURATION"):
            component.pop(key, None)
        component.add("DTSTART", aware(fields["start"]))
        component.add("DTEND", aware(fields["end"]))
    if "attendees" in fields:
        component.pop("ATTENDEE", None)
        for address in fields["attendees"]:
            component.add("ATTENDEE", vCalAddress(f"mailto:{address}"))
    component.pop("DTSTAMP", None)
    component.add("DTSTAMP", datetime.now(UTC))


def new_calendar(uid: str, fields: Dict[str, Any]) -> bytes:
    """A one-event VCALENDAR for ``fields``, with ``uid``."""
    calendar = Calendar()
    calendar.add("PRODID", PRODUCT_ID)
    calendar.add("VERSION", "2.0")
    event = Event()
    event.add("UID", uid)
    apply_fields(event, fields)
    calendar.add_component(event)
    return bytes(calendar.to_ical())


def changed_calendar(ics: str, changes: Dict[str, Any]) -> bytes:
    """``ics`` with ``changes`` written onto its event (a series' master, not
    its overridden occurrences)."""
    calendar = Calendar.from_ical(ics)
    masters = [c for c in calendar.walk("VEVENT") if c.get("RECURRENCE-ID") is None]
    if not masters:
        raise PermanentExternalError("the resource holds no event")
    apply_fields(masters[0], changes)
    return bytes(calendar.to_ical())


    name: ClassVar[str] = "caldav"
    friendly_name: ClassVar[str] = "CalDAV"
    description: ClassVar[str] = (
        "Any CalDAV calendar: iCloud, Fastmail, Nextcloud, Radicale"
    )
    _env: ClassVar[Dict[str, Any]] = {
        "CALDAV_CALENDAR_URL": "",
        "CALDAV_USERNAME": "",
        "CALDAV_PASSWORD": "",
    }
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "calendar_url",
            "The calendar collection's URL (ends in /)",
            env="CALDAV_CALENDAR_URL",
        ),
        InstanceSetting("username", "Sign-in name", env="CALDAV_USERNAME"),
        InstanceSetting(
            "api_key",
            "Password (an app-specific password on iCloud and Fastmail)",
            env="CALDAV_PASSWORD",
            secret=True,
            field="api_key",
        ),
    )
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="icalendar",
                friendly_name="icalendar",
                semver=">=7.3",
                reason="Reading and writing CalDAV events (iCalendar)",
            ),
        ]
    )

    @classmethod
    def _collection(cls, instance: ProviderInstanceModel) -> str:
        url = str(cls.setting(instance, "calendar_url") or "")
        if not url:
            raise TransientExternalError(
                "CalDAV calendar URL not configured", provider=cls.name
            )
        return collection_url(url)

    @classmethod
    def _resource(cls, instance: ProviderInstanceModel, event: str) -> str:
        return resource_url(cls._collection(instance), event)

    @classmethod
    async def _call(
        cls,
        instance: ProviderInstanceModel,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        content: Optional[bytes] = None,
    ) -> Any:
        username = str(cls.setting(instance, "username") or "")
        return await cls.http().request(
            method,
            url,
            raw=True,
            headers=headers or {},
            content=content,
            auth=(username, cls.token(instance)),
        )

    @classmethod
    def _events(
        cls, found_resources: List[Tuple[str, str]]
    ) -> List[Tuple[Dict[str, Any], Optional[Interval]]]:
        """Each event in ``found_resources`` (``(href, iCalendar)``), earliest
        first, with the time it blocks."""
        found: List[Tuple[Dict[str, Any], Optional[Interval]]] = []
        for href, data in found_resources:
            for component in Calendar.from_ical(data).walk("VEVENT"):
                event = cls.event(
                    event_id=event_id(href),
                    title=str(component.get("SUMMARY", "")),
                    start=_moment(component.get("DTSTART")),
                    end=_end(component),
                    location=str(component.get("LOCATION", "")),
                    description=str(component.get("DESCRIPTION", "")),
                    attendees=_attendees(component),
                    url=str(component.get("URL", "")),
                )
                found.append((event, _span(component)))
        return sorted(found, key=lambda pair: str(pair[0]["start"] or ""))

    @classmethod
    async def _report(
        cls, instance: ProviderInstanceModel, start: datetime, end: datetime
    ) -> List[Tuple[Dict[str, Any], Optional[Interval]]]:
        response = await cls._call(
            instance,
            "REPORT",
            cls._collection(instance),
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            content=calendar_query(start, end).encode(),
        )
        return cls._events(resources(response.text))

    @classmethod
    async def list_events(
        cls, instance: ProviderInstanceModel, start: datetime, end: datetime, limit: int
    ) -> List[Dict[str, Any]]:
        return [event for event, _ in (await cls._report(instance, start, end))[:limit]]

    @classmethod
    async def create_event(
        cls, instance: ProviderInstanceModel, fields: Dict[str, Any]
    ) -> Dict[str, Any]:
        uid = str(uuid.uuid4())
        name = f"{uid}.ics"
        body = new_calendar(uid, fields)
        await cls._call(
            instance,
            "PUT",
            cls._resource(instance, name),
            headers={
                "Content-Type": "text/calendar; charset=utf-8",
                "If-None-Match": "*",
            },
            content=body,
        )
        return cls._events([(name, body.decode())])[0][0]

    @classmethod
    async def update_event(
        cls, instance: ProviderInstanceModel, event_id: str, changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        url = cls._resource(instance, event_id)
        current = await cls._call(instance, "GET", url)
        body = changed_calendar(current.text, changes)
        etag = current.headers.get("etag")
        await cls._call(
            instance,
            "PUT",
            url,
            # Guarded by the version read, so a change made meanwhile is not overwritten.
            headers={
                "Content-Type": "text/calendar; charset=utf-8",
                **({"If-Match": etag} if etag else {}),
            },
            content=body,
        )
        return cls._events([(event_id, body.decode())])[0][0]

    @classmethod
    async def delete_event(
        cls, instance: ProviderInstanceModel, event_id: str
    ) -> Dict[str, Any]:
        await cls._call(instance, "DELETE", cls._resource(instance, event_id))
        return {"id": event_id, "deleted": True, "provider": cls.name}

    @classmethod
    async def busy(
        cls, instance: ProviderInstanceModel, start: datetime, end: datetime
    ) -> List[Interval]:
        found = await cls._report(instance, start, end)
        return [span for _, span in found[:BUSY_EVENT_LIMIT] if span is not None]
