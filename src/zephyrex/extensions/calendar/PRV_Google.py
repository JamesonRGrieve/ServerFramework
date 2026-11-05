# SPDX-License-Identifier: AGPL-3.0-or-later
"""Google Calendar, through the Calendar API v3.

The instance's API key is an OAuth access token with the
``calendar.events`` and ``calendar.freebusy`` scopes (else
``GOOGLE_CALENDAR_TOKEN``); ``calendar_id`` names the calendar
(``primary`` by default). An online meeting is a Google Meet link.
"""

import uuid
from datetime import datetime
from typing import Any, ClassVar, Dict, List, Mapping, Tuple
from urllib.parse import quote

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.calendar.EXT_Calendar import (
    AbstractCalendarProvider,
    Interval,
    iso,
    parse_time,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

API_URL = "https://www.googleapis.com/calendar/v3"
DEFAULT_CALENDAR = "primary"


def when(moment: Mapping[str, Any]) -> str:
    """A Google start/end: a time, or an all-day event's date."""
    return str(moment.get("dateTime") or moment.get("date") or "")


class PRV_Google_Calendar(AbstractCalendarProvider):
    name: ClassVar[str] = "google_calendar"
    friendly_name: ClassVar[str] = "Google Calendar"
    description: ClassVar[str] = "Google Calendar"
    _env: ClassVar[Dict[str, Any]] = {
        "GOOGLE_CALENDAR_TOKEN": "",
        "GOOGLE_CALENDAR_ID": DEFAULT_CALENDAR,
    }
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "OAuth access token (calendar.events, calendar.freebusy)",
            env="GOOGLE_CALENDAR_TOKEN",
            secret=True,
            field="api_key",
        ),
        InstanceSetting(
            "calendar_id",
            "Calendar id (primary, or an address like team@group.calendar.google.com)",
            env="GOOGLE_CALENDAR_ID",
            default=DEFAULT_CALENDAR,
        ),
    )

    @classmethod
    def _calendar(cls, instance: ProviderInstanceModel) -> str:
        return str(cls.setting(instance, "calendar_id") or DEFAULT_CALENDAR)

    @classmethod
    def _events_url(cls, instance: ProviderInstanceModel) -> str:
        return f"{API_URL}/calendars/{quote(cls._calendar(instance), safe='')}/events"

    @classmethod
    async def _call(
        cls, instance: ProviderInstanceModel, method: str, url: str, **kwargs: Any
    ) -> Any:
        return await cls.http().request(
            method,
            url,
            headers={"Authorization": f"Bearer {cls.token(instance)}"},
            **kwargs,
        )

    @classmethod
    def _event(cls, found: Mapping[str, Any]) -> Dict[str, Any]:
        return cls.event(
            event_id=str(found.get("id", "")),
            title=found.get("summary", ""),
            start=when(found.get("start") or {}),
            end=when(found.get("end") or {}),
            location=found.get("location", ""),
            description=found.get("description", ""),
            attendees=[a["email"] for a in found.get("attendees", []) if "email" in a],
            meeting_url=found.get("hangoutLink", ""),
            url=found.get("htmlLink", ""),
        )

    @staticmethod
    def _body(fields: Dict[str, Any]) -> Dict[str, Any]:
        body: Dict[str, Any] = {}
        if "title" in fields:
            body["summary"] = fields["title"]
        if "start" in fields:
            body["start"] = {"dateTime": iso(fields["start"])}
            body["end"] = {"dateTime": iso(fields["end"])}
        for ours, theirs in (("location", "location"), ("description", "description")):
            if ours in fields:
                body[theirs] = fields[ours]
        if "attendees" in fields:
            body["attendees"] = [{"email": address} for address in fields["attendees"]]
        if fields.get("online_meeting"):
            body["conferenceData"] = {
                "createRequest": {
                    "requestId": uuid.uuid4().hex,
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }
        return body

    @classmethod
    async def list_events(
        cls, instance: ProviderInstanceModel, start: datetime, end: datetime, limit: int
    ) -> List[Dict[str, Any]]:
        found = await cls._call(
            instance,
            "GET",
            cls._events_url(instance),
            params={
                "timeMin": iso(start),
                "timeMax": iso(end),
                "maxResults": limit,
                "singleEvents": "true",
                "orderBy": "startTime",
            },
        )
        return [cls._event(event) for event in found.get("items", [])]

    @classmethod
    async def create_event(
        cls, instance: ProviderInstanceModel, fields: Dict[str, Any]
    ) -> Dict[str, Any]:
        return cls._event(
            await cls._call(
                instance,
                "POST",
                cls._events_url(instance),
                params={"conferenceDataVersion": 1, "sendUpdates": "all"},
                json=cls._body(fields),
            )
        )

    @classmethod
    async def update_event(
        cls, instance: ProviderInstanceModel, event_id: str, changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        return cls._event(
            await cls._call(
                instance,
                "PATCH",
                f"{cls._events_url(instance)}/{quote(event_id, safe='')}",
                params={"sendUpdates": "all"},
                json=cls._body(changes),
            )
        )

    @classmethod
    async def delete_event(
        cls, instance: ProviderInstanceModel, event_id: str
    ) -> Dict[str, Any]:
        await cls._call(
            instance,
            "DELETE",
            f"{cls._events_url(instance)}/{quote(event_id, safe='')}",
            params={"sendUpdates": "all"},
        )
        return {"id": event_id, "deleted": True, "provider": cls.name}

    @classmethod
    async def busy(
        cls, instance: ProviderInstanceModel, start: datetime, end: datetime
    ) -> List[Interval]:
        calendar = cls._calendar(instance)
        found = await cls._call(
            instance,
            "POST",
            f"{API_URL}/freeBusy",
            json={
                "timeMin": iso(start),
                "timeMax": iso(end),
                "items": [{"id": calendar}],
            },
        )
        busy = found.get("calendars", {}).get(calendar, {}).get("busy", [])
        return [(parse_time(slot["start"]), parse_time(slot["end"])) for slot in busy]
