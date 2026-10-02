# SPDX-License-Identifier: AGPL-3.0-or-later
"""A person's own health log: activities, meals, weigh-ins and sleep.

Each record belongs to the user who logged it and nobody else (no team
sharing: this is health data). Values are range-checked on the way in,
and a night's sleep duration is computed from its bed and wake times.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, Dict, Literal, Optional, Type

from fastapi import HTTPException
from pydantic import Field

from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import UserModel
from zephyrex.pydantic2.fastapi import RouterMixin
from zephyrex.pydantic2.registry import BaseModel

ActivityKind = Literal[
    "running",
    "walking",
    "cycling",
    "swimming",
    "strength",
    "yoga",
    "hiking",
    "other",
]
MealKind = Literal["breakfast", "lunch", "dinner", "snack"]
MAX_MINUTES = 24 * 60
MAX_SLEEP = timedelta(hours=24)


class HealthActivityModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["HealthActivityManager"]]
    performed_at: datetime = Field(..., description="When the activity started")
    kind: ActivityKind = Field(..., description="What was done")
    duration_minutes: int = Field(..., description="How long it lasted")
    calories_burned: Optional[float] = Field(None, description="kcal")
    steps: Optional[int] = Field(None, description="Steps taken")
    distance_km: Optional[float] = Field(None, description="Distance covered")
    heart_rate_avg: Optional[int] = Field(None, description="Average beats per minute")
    heart_rate_max: Optional[int] = Field(None, description="Peak beats per minute")
    notes: Optional[str] = Field(None, description="Free-form notes")

    table_comment: ClassVar[str] = "A user's logged physical activities"

    class Create(BaseModel):
        performed_at: datetime
        kind: ActivityKind
        duration_minutes: int = Field(..., ge=1, le=MAX_MINUTES)
        calories_burned: Optional[float] = Field(None, ge=0, le=20_000)
        steps: Optional[int] = Field(None, ge=0, le=200_000)
        distance_km: Optional[float] = Field(None, ge=0, le=1_000)
        heart_rate_avg: Optional[int] = Field(None, ge=20, le=260)
        heart_rate_max: Optional[int] = Field(None, ge=20, le=260)
        notes: Optional[str] = Field(None, max_length=2_000)

    class Update(BaseModel):
        performed_at: Optional[datetime] = None
        kind: Optional[ActivityKind] = None
        duration_minutes: Optional[int] = Field(None, ge=1, le=MAX_MINUTES)
        calories_burned: Optional[float] = Field(None, ge=0, le=20_000)
        steps: Optional[int] = Field(None, ge=0, le=200_000)
        distance_km: Optional[float] = Field(None, ge=0, le=1_000)
        heart_rate_avg: Optional[int] = Field(None, ge=20, le=260)
        heart_rate_max: Optional[int] = Field(None, ge=20, le=260)
        notes: Optional[str] = Field(None, max_length=2_000)

    class Search(ApplicationModel.Search):
        performed_at: Optional[DateSearchModel] = None
        kind: Optional[StringSearchModel] = None


class HealthMealModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["HealthMealManager"]]
    eaten_at: datetime = Field(..., description="When it was eaten")
    kind: MealKind = Field(..., description="Which meal")
    food: str = Field(..., description="What was eaten")
    serving_size: Optional[float] = Field(None, description="How much")
    serving_unit: Optional[str] = Field(None, description="g, ml, cup, …")
    calories: float = Field(..., description="kcal")
    protein_g: Optional[float] = Field(None, description="Protein, grams")
    carbs_g: Optional[float] = Field(None, description="Carbohydrate, grams")
    fat_g: Optional[float] = Field(None, description="Fat, grams")
    fiber_g: Optional[float] = Field(None, description="Fibre, grams")
    sugar_g: Optional[float] = Field(None, description="Sugar, grams")
    sodium_mg: Optional[float] = Field(None, description="Sodium, milligrams")

    table_comment: ClassVar[str] = "A user's logged meals and their nutrients"

    class Create(BaseModel):
        eaten_at: datetime
        kind: MealKind
        food: str = Field(..., min_length=1, max_length=300)
        serving_size: Optional[float] = Field(None, gt=0)
        serving_unit: Optional[str] = Field(None, max_length=30)
        calories: float = Field(..., ge=0, le=20_000)
        protein_g: Optional[float] = Field(None, ge=0, le=2_000)
        carbs_g: Optional[float] = Field(None, ge=0, le=2_000)
        fat_g: Optional[float] = Field(None, ge=0, le=2_000)
        fiber_g: Optional[float] = Field(None, ge=0, le=2_000)
        sugar_g: Optional[float] = Field(None, ge=0, le=2_000)
        sodium_mg: Optional[float] = Field(None, ge=0, le=100_000)

    class Update(BaseModel):
        eaten_at: Optional[datetime] = None
        kind: Optional[MealKind] = None
        food: Optional[str] = Field(None, min_length=1, max_length=300)
        serving_size: Optional[float] = Field(None, gt=0)
        serving_unit: Optional[str] = Field(None, max_length=30)
        calories: Optional[float] = Field(None, ge=0, le=20_000)
        protein_g: Optional[float] = Field(None, ge=0, le=2_000)
        carbs_g: Optional[float] = Field(None, ge=0, le=2_000)
        fat_g: Optional[float] = Field(None, ge=0, le=2_000)
        fiber_g: Optional[float] = Field(None, ge=0, le=2_000)
        sugar_g: Optional[float] = Field(None, ge=0, le=2_000)
        sodium_mg: Optional[float] = Field(None, ge=0, le=100_000)

    class Search(ApplicationModel.Search):
        eaten_at: Optional[DateSearchModel] = None
        kind: Optional[StringSearchModel] = None
        food: Optional[StringSearchModel] = None


class HealthWeightModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["HealthWeightManager"]]
    measured_at: datetime = Field(..., description="When it was measured")
    weight_kg: float = Field(..., description="Body weight, kilograms")
    body_fat_percent: Optional[float] = Field(None, description="Body fat, percent")
    notes: Optional[str] = Field(None, description="Free-form notes")

    table_comment: ClassVar[str] = "A user's weigh-ins"

    class Create(BaseModel):
        measured_at: datetime
        weight_kg: float = Field(..., gt=0, le=700)
        body_fat_percent: Optional[float] = Field(None, ge=0, le=100)
        notes: Optional[str] = Field(None, max_length=2_000)

    class Update(BaseModel):
        measured_at: Optional[datetime] = None
        weight_kg: Optional[float] = Field(None, gt=0, le=700)
        body_fat_percent: Optional[float] = Field(None, ge=0, le=100)
        notes: Optional[str] = Field(None, max_length=2_000)

    class Search(ApplicationModel.Search):
        measured_at: Optional[DateSearchModel] = None


class HealthSleepModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["HealthSleepManager"]]
    bedtime: datetime = Field(..., description="When the night's sleep began")
    wake_time: datetime = Field(..., description="When it ended")
    duration_minutes: int = Field(0, description="Bed to wake (kept by the server)")
    deep_minutes: Optional[int] = Field(None, description="Deep sleep")
    light_minutes: Optional[int] = Field(None, description="Light sleep")
    rem_minutes: Optional[int] = Field(None, description="REM sleep")
    awake_minutes: Optional[int] = Field(None, description="Awake in bed")
    quality: Optional[int] = Field(None, description="Quality score, 0-100")

    table_comment: ClassVar[str] = "A user's nights of sleep"

    # duration_minutes is set by the manager from bedtime and wake_time.
    class Create(BaseModel):
        bedtime: datetime
        wake_time: datetime
        duration_minutes: int = 0
        deep_minutes: Optional[int] = Field(None, ge=0, le=MAX_MINUTES)
        light_minutes: Optional[int] = Field(None, ge=0, le=MAX_MINUTES)
        rem_minutes: Optional[int] = Field(None, ge=0, le=MAX_MINUTES)
        awake_minutes: Optional[int] = Field(None, ge=0, le=MAX_MINUTES)
        quality: Optional[int] = Field(None, ge=0, le=100)

    class Update(BaseModel):
        bedtime: Optional[datetime] = None
        wake_time: Optional[datetime] = None
        duration_minutes: Optional[int] = None
        deep_minutes: Optional[int] = Field(None, ge=0, le=MAX_MINUTES)
        light_minutes: Optional[int] = Field(None, ge=0, le=MAX_MINUTES)
        rem_minutes: Optional[int] = Field(None, ge=0, le=MAX_MINUTES)
        awake_minutes: Optional[int] = Field(None, ge=0, le=MAX_MINUTES)
        quality: Optional[int] = Field(None, ge=0, le=100)

    class Search(ApplicationModel.Search):
        bedtime: Optional[DateSearchModel] = None


def moment(value: Any) -> datetime:
    """A datetime (or its ISO text) in UTC; a naive one is taken as UTC."""
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def sleep_minutes(bedtime: Any, wake_time: Any) -> int:
    """Minutes from bed to waking: after bedtime, within a day."""
    asleep = moment(wake_time) - moment(bedtime)
    if asleep <= timedelta(0) or asleep > MAX_SLEEP:
        raise HTTPException(
            status_code=422,
            detail="wake_time must come after bedtime, within 24 hours",
        )
    return int(asleep.total_seconds() // 60)


class HealthActivityManager(AbstractBLLManager, RouterMixin):
    _model = HealthActivityModel


class HealthMealManager(AbstractBLLManager, RouterMixin):
    _model = HealthMealModel


class HealthWeightManager(AbstractBLLManager, RouterMixin):
    _model = HealthWeightModel


class HealthSleepManager(AbstractBLLManager, RouterMixin):
    _model = HealthSleepModel

    def _timed(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        return {
            **fields,
            "duration_minutes": sleep_minutes(fields["bedtime"], fields["wake_time"]),
        }

    def create(self, **kwargs: Any) -> Any:
        if isinstance(kwargs.get("entities"), list):
            kwargs["entities"] = [self._timed(entity) for entity in kwargs["entities"]]
            return super().create(**kwargs)
        return super().create(**self._timed(kwargs))

    def update(self, id: str, **kwargs: Any) -> Any:
        kwargs.pop("duration_minutes", None)
        if kwargs.get("bedtime") is not None or kwargs.get("wake_time") is not None:
            existing = self.get(id=id)
            bedtime = kwargs.get("bedtime") or existing.bedtime
            wake_time = kwargs.get("wake_time") or existing.wake_time
            kwargs["duration_minutes"] = sleep_minutes(bedtime, wake_time)
        return super().update(id, **kwargs)
