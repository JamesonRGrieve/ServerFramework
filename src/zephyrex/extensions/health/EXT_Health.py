# SPDX-License-Identifier: AGPL-3.0-or-later
"""A personal health log: activities, meals, weigh-ins and sleep, with a
summary over any period.

The records (``BLL_Health``) belong to the user who logged them, through
the API. The abilities do the same for an agent acting for a user: each
takes that user's ``requester_id`` and works on their records only.
"""

from datetime import datetime
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Set, Tuple, Type

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.extensions.health.BLL_Health import (
    HealthActivityManager,
    HealthMealManager,
    HealthSleepManager,
    HealthWeightManager,
    moment,
)
from zephyrex.extensions.health.Summary import summarize
from zephyrex.lib.Dependencies import Dependencies

# kind -> (its manager, the field that dates a record)
KINDS: Mapping[str, Tuple[Type[Any], str]] = {
    "activity": (HealthActivityManager, "performed_at"),
    "meal": (HealthMealManager, "eaten_at"),
    "weight": (HealthWeightManager, "measured_at"),
    "sleep": (HealthSleepManager, "bedtime"),
}
MAX_RECORDS = 1_000


def _fields(**values: Any) -> Dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


class EXT_Health(AbstractStaticExtension):
    name: ClassVar[str] = "health"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "A personal health log of activities, meals, weight and sleep"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[Set[str]] = {
        "log_activity",
        "log_meal",
        "log_weight",
        "log_sleep",
        "list_health_records",
        "delete_health_record",
        "health_summary",
    }

    @classmethod
    def _manager(cls, requester_id: str, kind: str) -> Any:
        if kind not in KINDS:
            raise InvalidInputExternalError(
                f"kind must be one of {', '.join(KINDS)}, not {kind!r}"
            )
        return cls.as_requester(KINDS[kind][0], requester_id)

    @classmethod
    def _log(cls, requester_id: str, record_kind: str, **fields: Any) -> Dict[str, Any]:
        created = cls._manager(requester_id, record_kind).create(**_fields(**fields))
        return dict(created.model_dump(mode="json"))

    @classmethod
    def _between(
        cls, requester_id: str, kind: str, start: datetime, end: datetime
    ) -> List[Any]:
        if moment(end) <= moment(start):
            raise InvalidInputExternalError("the end must be after the start")
        manager = cls._manager(requester_id, kind)
        field = KINDS[kind][1]
        found: List[Any] = manager.search(
            **{field: {"after": moment(start), "before": moment(end)}},
            sort_by=field,
            limit=MAX_RECORDS,
        )
        return found

    @classmethod
    @ability("log_activity")
    async def log_activity(
        cls,
        requester_id: str,
        performed_at: datetime,
        kind: str,
        duration_minutes: int,
        calories_burned: Optional[float] = None,
        steps: Optional[int] = None,
        distance_km: Optional[float] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        return cls._log(
            requester_id,
            "activity",
            performed_at=performed_at,
            kind=kind,
            duration_minutes=duration_minutes,
            calories_burned=calories_burned,
            steps=steps,
            distance_km=distance_km,
            notes=notes,
        )

    @classmethod
    @ability("log_meal")
    async def log_meal(
        cls,
        requester_id: str,
        eaten_at: datetime,
        kind: str,
        food: str,
        calories: float,
        protein_g: Optional[float] = None,
        carbs_g: Optional[float] = None,
        fat_g: Optional[float] = None,
    ) -> Dict[str, Any]:
        return cls._log(
            requester_id,
            "meal",
            eaten_at=eaten_at,
            kind=kind,
            food=food,
            calories=calories,
            protein_g=protein_g,
            carbs_g=carbs_g,
            fat_g=fat_g,
        )

    @classmethod
    @ability("log_weight")
    async def log_weight(
        cls,
        requester_id: str,
        measured_at: datetime,
        weight_kg: float,
        body_fat_percent: Optional[float] = None,
    ) -> Dict[str, Any]:
        return cls._log(
            requester_id,
            "weight",
            measured_at=measured_at,
            weight_kg=weight_kg,
            body_fat_percent=body_fat_percent,
        )

    @classmethod
    @ability("log_sleep")
    async def log_sleep(
        cls,
        requester_id: str,
        bedtime: datetime,
        wake_time: datetime,
        quality: Optional[int] = None,
    ) -> Dict[str, Any]:
        return cls._log(
            requester_id, "sleep", bedtime=bedtime, wake_time=wake_time, quality=quality
        )

    @classmethod
    @ability("list_health_records")
    async def list_health_records(
        cls, requester_id: str, kind: str, start: datetime, end: datetime
    ) -> List[Dict[str, Any]]:
        """One kind of record (activity, meal, weight, sleep) in a period,
        earliest first."""
        return [
            record.model_dump(mode="json")
            for record in cls._between(requester_id, kind, start, end)
        ]

    @classmethod
    @ability("delete_health_record")
    async def delete_health_record(
        cls, requester_id: str, kind: str, record_id: str
    ) -> Dict[str, Any]:
        cls._manager(requester_id, kind).delete(record_id)
        return {"id": record_id, "kind": kind, "deleted": True}

    @classmethod
    @ability("health_summary")
    async def health_summary(
        cls, requester_id: str, start: datetime, end: datetime
    ) -> Dict[str, Any]:
        """Activity, nutrition, weight change and sleep over a period."""
        return summarize(
            start,
            end,
            *(cls._between(requester_id, kind, start, end) for kind in KINDS),
        )
