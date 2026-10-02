# SPDX-License-Identifier: AGPL-3.0-or-later
"""Calendly, through its API v2.

The instance's API key is a personal access token (else
``CALENDLY_TOKEN``). Calendly events are booked by invitees through
scheduling pages, so its API can list and cancel them but not create or
change them; those are refused. Deleting an event cancels it, which
notifies the invitee.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, Dict, List, Mapping, NoReturn, Optional, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import PermanentExternalError
from zephyrex.extensions.calendar.EXT_Calendar import (
    AbstractCalendarProvider,
    Interval,
    iso,
    parse_time,
)
from zephyrex.lib.ProviderHTTPClient import path_segment
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

API_URL = "https://api.calendly.com"
MAX_PAGE = 100
# Calendly's busy-time query spans at most a week.
BUSY_WINDOW = timedelta(days=7)
CANCEL_REASON = "Cancelled by the host"


class PRV_Calendly_Calendar(AbstractCalendarProvider):
    name: ClassVar[str] = "calendly"
    friendly_name: ClassVar[str] = "Calendly"
    description: ClassVar[str] = "Calendly scheduled events (list and cancel)"
    _env: ClassVar[Dict[str, Any]] = {"CALENDLY_TOKEN": ""}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "Personal access token",
            env="CALENDLY_TOKEN",
            secret=True,
            field="api_key",
        ),
    )

    @classmethod
    async def _call(
        cls,
        instance: ProviderInstanceModel,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        return await cls.http().request(
            method,
            f"{API_URL}{path}",
            params=params,
            json=json_body,
            headers={"Authorization": f"Bearer {cls.token(instance)}"},
        )

    @classmethod
    async def _user(cls, instance: ProviderInstanceModel) -> str:
        found = await cls._call(instance, "GET", "/users/me")
        return str(found["resource"]["uri"])

    @classmethod
    def _event(cls, found: Mapping[str, Any]) -> Dict[str, Any]:
        location = found.get("location") or {}
        return cls.event(
            event_id=str(found.get("uri", "")).rsplit("/", 1)[-1],
            title=found.get("name", ""),
            start=(
                iso(parse_time(found["start_time"]))
                if found.get("start_time")
                else None
            ),
            end=iso(parse_time(found["end_time"])) if found.get("end_time") else None,
            location=str(location.get("location") or ""),
            meeting_url=str(location.get("join_url") or ""),
        )

    @classmethod
    def _cannot(cls, what: str) -> NoReturn:
        raise PermanentExternalError(
            f"Calendly events are booked by invitees; its API cannot {what} them",
            provider=cls.name,
        )

    @classmethod
    async def list_events(
        cls, instance: ProviderInstanceModel, start: datetime, end: datetime, limit: int
    ) -> List[Dict[str, Any]]:
        found = await cls._call(
            instance,
            "GET",
            "/scheduled_events",
            params={
                "user": await cls._user(instance),
                "min_start_time": iso(start),
                "max_start_time": iso(end),
                "count": min(limit, MAX_PAGE),
                "sort": "start_time:asc",
                "status": "active",
            },
        )
        return [cls._event(event) for event in found.get("collection", [])][:limit]

    @classmethod
    async def create_event(
        cls, instance: ProviderInstanceModel, fields: Dict[str, Any]
    ) -> Dict[str, Any]:
        cls._cannot("create")

    @classmethod
    async def update_event(
        cls, instance: ProviderInstanceModel, event_id: str, changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        cls._cannot("change")

    @classmethod
    async def delete_event(
        cls, instance: ProviderInstanceModel, event_id: str
    ) -> Dict[str, Any]:
        await cls._call(
            instance,
            "POST",
            f"/scheduled_events/{path_segment(event_id, 'Calendly event id')}/cancellation",
            json_body={"reason": CANCEL_REASON},
        )
        return {"id": event_id, "deleted": True, "provider": cls.name}

    @classmethod
    async def busy(
        cls, instance: ProviderInstanceModel, start: datetime, end: datetime
    ) -> List[Interval]:
        user = await cls._user(instance)
        # Calendly reports busy time from now on (past slots are dropped
        # by the extension anyway).
        cursor = max(start, datetime.now(UTC))
        intervals: List[Interval] = []
        while cursor < end:
            window_end = min(cursor + BUSY_WINDOW, end)
            found = await cls._call(
                instance,
                "GET",
                "/user_busy_times",
                params={
                    "user": user,
                    "start_time": iso(cursor),
                    "end_time": iso(window_end),
                },
            )
            intervals.extend(
                (parse_time(slot["start_time"]), parse_time(slot["end_time"]))
                for slot in found.get("collection", [])
            )
            cursor = window_end
        return intervals
