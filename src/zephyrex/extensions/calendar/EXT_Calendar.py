# SPDX-License-Identifier: AGPL-3.0-or-later
"""Calendars: read, create, change and delete events and find free time on
Google Calendar, Microsoft 365 / Outlook (Microsoft Graph) and Calendly.

An event belongs to one calendar account, so every ability names its
``provider`` and runs on that provider's instances (one instance per
account). Times are timezone-aware; a naive time is taken as UTC.
Providers answer an event as ``{id, title, start, end, location,
description, attendees, meeting_url, url, provider}`` with ISO 8601 UTC
times.

Free time is computed here, from the busy intervals a provider reports,
within working hours in the caller's timezone.
"""

from abc import abstractmethod
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any, ClassVar, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ability,
)
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

CALENDAR_REQUEST_TIMEOUT_SECONDS = 20.0
DEFAULT_EVENT_LIMIT = 25
MAX_EVENT_LIMIT = 250
MAX_RANGE_DAYS = 366
MAX_SLOT_DAYS = 31
MAX_DURATION_MINUTES = 24 * 60

Interval = Tuple[datetime, datetime]


def aware(moment: datetime) -> datetime:
    """``moment`` in UTC; a naive time is taken as UTC."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def iso(moment: datetime) -> str:
    return aware(moment).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    """An API time (``Z``, an offset, or naive meaning UTC)."""
    return aware(datetime.fromisoformat(value))


def time_range(start: datetime, end: datetime) -> Interval:
    start, end = aware(start), aware(end)
    if end <= start:
        raise InvalidInputExternalError("the end must be after the start")
    if end - start > timedelta(days=MAX_RANGE_DAYS):
        raise InvalidInputExternalError(f"a range spans at most {MAX_RANGE_DAYS} days")
    return start, end


def event_limit(limit: int) -> int:
    if not 1 <= limit <= MAX_EVENT_LIMIT:
        raise InvalidInputExternalError(f"limit must be 1-{MAX_EVENT_LIMIT}")
    return limit


def zone(name: str) -> tzinfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidInputExternalError(f"{name!r} is not an IANA time zone") from exc


def clock(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise InvalidInputExternalError(f"{value!r} is not a HH:MM time") from exc


def free_slots(
    busy: List[Interval],
    first_day: date,
    days: int,
    day_start: time,
    day_end: time,
    duration: timedelta,
    buffer: timedelta,
    where: tzinfo,
) -> List[Interval]:
    """The ``duration``-long slots, inside working hours on each of
    ``days`` days from ``first_day`` (in ``where``), clear of every busy
    interval by ``buffer``. Slots start on the hour or after a busy
    interval ends."""
    blocked = sorted((start - buffer, end + buffer) for start, end in busy)
    slots: List[Interval] = []
    for offset in range(days):
        day = first_day + timedelta(days=offset)
        cursor = datetime.combine(day, day_start, where).astimezone(UTC)
        closing = datetime.combine(day, day_end, where).astimezone(UTC)
        for start, end in blocked:
            if end <= cursor or start >= closing:
                continue
            while cursor + duration <= min(start, closing):
                slots.append((cursor, cursor + duration))
                cursor += duration
            cursor = max(cursor, end)
        while cursor + duration <= closing:
            slots.append((cursor, cursor + duration))
            cursor += duration
    return slots


class AbstractCalendarProvider(AbstractStaticProvider):
    """A calendar service; each instance is one account (one calendar)."""

    name: ClassVar[str] = ""
    friendly_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    _abilities: ClassVar[Set[str]] = {
        "list_events",
        "create_event",
        "update_event",
        "delete_event",
        "find_free_time",
    }
    _env: ClassVar[Dict[str, Any]] = {}
    http_timeout_seconds: ClassVar[float] = CALENDAR_REQUEST_TIMEOUT_SECONDS

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    def token(cls, instance: ProviderInstanceModel) -> str:
        token = cls.setting(instance, "api_key")
        if not token:
            raise TransientExternalError(
                f"{cls.friendly_name} access token not configured", provider=cls.name
            )
        return str(token)

    @classmethod
    def event(
        cls,
        *,
        event_id: str,
        title: str,
        start: Optional[str],
        end: Optional[str],
        location: str = "",
        description: str = "",
        attendees: Optional[List[str]] = None,
        meeting_url: str = "",
        url: str = "",
    ) -> Dict[str, Any]:
        return {
            "id": event_id,
            "title": title,
            "start": start,
            "end": end,
            "location": location,
            "description": description,
            "attendees": attendees or [],
            "meeting_url": meeting_url,
            "url": url,
            "provider": cls.name,
        }

    @classmethod
    @abstractmethod
    async def list_events(
        cls, instance: ProviderInstanceModel, start: datetime, end: datetime, limit: int
    ) -> List[Dict[str, Any]]:
        """Events overlapping the range, earliest first."""

    @classmethod
    @abstractmethod
    async def create_event(
        cls, instance: ProviderInstanceModel, fields: Dict[str, Any]
    ) -> Dict[str, Any]:
        """``fields``: title, start, end, and optionally location,
        description, attendees and online_meeting."""

    @classmethod
    @abstractmethod
    async def update_event(
        cls, instance: ProviderInstanceModel, event_id: str, changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        """``changes``: any of the fields ``create_event`` takes."""

    @classmethod
    @abstractmethod
    async def delete_event(
        cls, instance: ProviderInstanceModel, event_id: str
    ) -> Dict[str, Any]: ...

    @classmethod
    @abstractmethod
    async def busy(
        cls, instance: ProviderInstanceModel, start: datetime, end: datetime
    ) -> List[Interval]:
        """The busy intervals in the range."""

    @classmethod
    def services(cls) -> List[str]:
        return ["calendar"]


def event_fields(
    title: Optional[str],
    start: Optional[datetime],
    end: Optional[datetime],
    location: Optional[str],
    description: Optional[str],
    attendees: Optional[List[str]],
    online_meeting: Optional[bool],
) -> Dict[str, Any]:
    """The fields given, checked: a title is not blank, times are aware
    and ordered, attendees are email addresses."""
    if title is not None and not title.strip():
        raise InvalidInputExternalError("an event needs a title")
    if start is not None and end is not None:
        start, end = time_range(start, end)
    for address in attendees or []:
        if "@" not in address or any(c.isspace() for c in address):
            raise InvalidInputExternalError(f"{address!r} is not an email address")
    fields = {
        "title": title,
        "start": aware(start) if start is not None else None,
        "end": aware(end) if end is not None else None,
        "location": location,
        "description": description,
        "attendees": attendees,
        "online_meeting": online_meeting,
    }
    return {key: value for key, value in fields.items() if value is not None}


class EXT_Calendar(AbstractStaticExtension):
    name: ClassVar[str] = "calendar"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Events and free time on Google Calendar, Microsoft 365 and Calendly"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[Set[str]] = set(AbstractCalendarProvider._abilities)

    @classmethod
    @ability("list_events")
    async def list_events(
        cls,
        provider: str,
        start: datetime,
        end: datetime,
        limit: int = DEFAULT_EVENT_LIMIT,
    ) -> List[Dict[str, Any]]:
        """Events between ``start`` and ``end``, earliest first."""
        start, end = time_range(start, end)
        result: List[Dict[str, Any]] = await cls.rotate_provider_for(
            provider, "list_events", start, end, event_limit(limit)
        )
        return result

    @classmethod
    @ability("create_event")
    async def create_event(
        cls,
        provider: str,
        title: str,
        start: datetime,
        end: datetime,
        location: Optional[str] = None,
        description: Optional[str] = None,
        attendees: Optional[List[str]] = None,
        online_meeting: bool = False,
    ) -> Dict[str, Any]:
        """Add an event (inviting ``attendees``; with an online meeting
        link where the calendar offers one)."""
        fields = event_fields(
            title, start, end, location, description, attendees, online_meeting
        )
        result: Dict[str, Any] = await cls.rotate_provider_for(
            provider, "create_event", fields
        )
        return result

    @classmethod
    @ability("update_event")
    async def update_event(
        cls,
        provider: str,
        event_id: str,
        title: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        location: Optional[str] = None,
        description: Optional[str] = None,
        attendees: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Change an event; ``start`` and ``end`` change together."""
        if (start is None) != (end is None):
            raise InvalidInputExternalError("start and end change together")
        changes = event_fields(
            title, start, end, location, description, attendees, None
        )
        if not changes:
            raise InvalidInputExternalError("nothing to change")
        result: Dict[str, Any] = await cls.rotate_provider_for(
            provider, "update_event", event_id, changes
        )
        return result

    @classmethod
    @ability("delete_event")
    async def delete_event(cls, provider: str, event_id: str) -> Dict[str, Any]:
        """Delete (Calendly: cancel) an event."""
        result: Dict[str, Any] = await cls.rotate_provider_for(
            provider, "delete_event", event_id
        )
        return result

    @classmethod
    @ability("find_free_time")
    async def find_free_time(
        cls,
        provider: str,
        first_day: date,
        days: int = 7,
        duration_minutes: int = 30,
        work_day_start: str = "09:00",
        work_day_end: str = "17:00",
        timezone: str = "UTC",
        buffer_minutes: int = 0,
    ) -> List[Dict[str, str]]:
        """Free slots of ``duration_minutes`` within working hours, from now
        on (a slot already begun is not offered)."""
        if not 1 <= days <= MAX_SLOT_DAYS:
            raise InvalidInputExternalError(f"days must be 1-{MAX_SLOT_DAYS}")
        if not 1 <= duration_minutes <= MAX_DURATION_MINUTES:
            raise InvalidInputExternalError("duration_minutes must be 1-1440")
        if not 0 <= buffer_minutes <= MAX_DURATION_MINUTES:
            raise InvalidInputExternalError("buffer_minutes must be 0-1440")
        where, opens, closes = (
            zone(timezone),
            clock(work_day_start),
            clock(work_day_end),
        )
        if closes <= opens:
            raise InvalidInputExternalError("the working day must end after it starts")
        start = datetime.combine(first_day, opens, where)
        end = datetime.combine(first_day + timedelta(days=days - 1), closes, where)
        busy: List[Interval] = await cls.rotate_provider_for(
            provider, "busy", aware(start), aware(end)
        )
        slots = free_slots(
            busy,
            first_day,
            days,
            opens,
            closes,
            timedelta(minutes=duration_minutes),
            timedelta(minutes=buffer_minutes),
            where,
        )
        now = datetime.now(UTC)
        return [
            {"start": iso(begin), "end": iso(finish)}
            for begin, finish in slots
            if begin >= now
        ]
