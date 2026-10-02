# SPDX-License-Identifier: AGPL-3.0-or-later
"""Microsoft 365 / Outlook calendars, through Microsoft Graph.

The instance's API key is a delegated access token with
``Calendars.ReadWrite`` (else ``MICROSOFT_GRAPH_TOKEN``); it works on the
signed-in user's default calendar. An online meeting is a Teams meeting.
Busy time is the calendar's events not shown as free.
"""

from datetime import datetime
from typing import Any, ClassVar, Dict, List, Mapping, Tuple
from urllib.parse import quote

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.calendar.EXT_Calendar import (
    MAX_EVENT_LIMIT,
    AbstractCalendarProvider,
    Interval,
    iso,
    parse_time,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

API_URL = "https://graph.microsoft.com/v1.0"
# Graph answers times in this zone when asked (Prefer: outlook.timezone).
PREFER_UTC = 'outlook.timezone="UTC"'
FREE = {"free", "unknown"}


def graph_time(moment: Mapping[str, Any]) -> str:
    """A Graph dateTimeTimeZone answered in UTC, as ISO 8601 UTC."""
    value = str(moment.get("dateTime", ""))
    return iso(parse_time(value)) if value else ""


class PRV_Microsoft_Calendar(AbstractCalendarProvider):
    name: ClassVar[str] = "microsoft_calendar"
    friendly_name: ClassVar[str] = "Microsoft 365 Calendar"
    description: ClassVar[str] = "Microsoft 365 / Outlook calendar"
    _env: ClassVar[Dict[str, Any]] = {"MICROSOFT_GRAPH_TOKEN": ""}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "Delegated access token (Calendars.ReadWrite)",
            env="MICROSOFT_GRAPH_TOKEN",
            secret=True,
            field="api_key",
        ),
    )

    @classmethod
    async def _call(
        cls, instance: ProviderInstanceModel, method: str, path: str, **kwargs: Any
    ) -> Any:
        return await cls.http().request(
            method,
            f"{API_URL}{path}",
            headers={
                "Authorization": f"Bearer {cls.token(instance)}",
                "Prefer": PREFER_UTC,
            },
            **kwargs,
        )

    @classmethod
    def _event(cls, found: Mapping[str, Any]) -> Dict[str, Any]:
        return cls.event(
            event_id=str(found.get("id", "")),
            title=found.get("subject", ""),
            start=graph_time(found.get("start") or {}),
            end=graph_time(found.get("end") or {}),
            location=(found.get("location") or {}).get("displayName", ""),
            description=found.get("bodyPreview", ""),
            attendees=[
                attendee["emailAddress"]["address"]
                for attendee in found.get("attendees", [])
                if attendee.get("emailAddress", {}).get("address")
            ],
            meeting_url=(found.get("onlineMeeting") or {}).get("joinUrl", ""),
            url=found.get("webLink", ""),
        )

    @staticmethod
    def _body(fields: Dict[str, Any]) -> Dict[str, Any]:
        body: Dict[str, Any] = {}
        if "title" in fields:
            body["subject"] = fields["title"]
        if "start" in fields:
            body["start"] = {"dateTime": iso(fields["start"]), "timeZone": "UTC"}
            body["end"] = {"dateTime": iso(fields["end"]), "timeZone": "UTC"}
        if "location" in fields:
            body["location"] = {"displayName": fields["location"]}
        if "description" in fields:
            body["body"] = {"contentType": "text", "content": fields["description"]}
        if "attendees" in fields:
            body["attendees"] = [
                {"emailAddress": {"address": address}, "type": "required"}
                for address in fields["attendees"]
            ]
        if fields.get("online_meeting"):
            body["isOnlineMeeting"] = True
            body["onlineMeetingProvider"] = "teamsForBusiness"
        return body

    @classmethod
    async def _view(
        cls, instance: ProviderInstanceModel, start: datetime, end: datetime, limit: int
    ) -> List[Dict[str, Any]]:
        found = await cls._call(
            instance,
            "GET",
            "/me/calendarView",
            params={
                "startDateTime": iso(start),
                "endDateTime": iso(end),
                "$top": limit,
                "$orderby": "start/dateTime",
            },
        )
        events: List[Dict[str, Any]] = found.get("value", [])
        return events

    @classmethod
    async def list_events(
        cls, instance: ProviderInstanceModel, start: datetime, end: datetime, limit: int
    ) -> List[Dict[str, Any]]:
        return [
            cls._event(event) for event in await cls._view(instance, start, end, limit)
        ]

    @classmethod
    async def create_event(
        cls, instance: ProviderInstanceModel, fields: Dict[str, Any]
    ) -> Dict[str, Any]:
        return cls._event(
            await cls._call(instance, "POST", "/me/events", json=cls._body(fields))
        )

    @classmethod
    async def update_event(
        cls, instance: ProviderInstanceModel, event_id: str, changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        return cls._event(
            await cls._call(
                instance,
                "PATCH",
                f"/me/events/{quote(event_id, safe='')}",
                json=cls._body(changes),
            )
        )

    @classmethod
    async def delete_event(
        cls, instance: ProviderInstanceModel, event_id: str
    ) -> Dict[str, Any]:
        await cls._call(instance, "DELETE", f"/me/events/{quote(event_id, safe='')}")
        return {"id": event_id, "deleted": True, "provider": cls.name}

    @classmethod
    async def busy(
        cls, instance: ProviderInstanceModel, start: datetime, end: datetime
    ) -> List[Interval]:
        events = await cls._view(instance, start, end, MAX_EVENT_LIMIT)
        return [
            (
                parse_time(event["start"]["dateTime"]),
                parse_time(event["end"]["dateTime"]),
            )
            for event in events
            if event.get("showAs", "busy") not in FREE and not event.get("isCancelled")
        ]
