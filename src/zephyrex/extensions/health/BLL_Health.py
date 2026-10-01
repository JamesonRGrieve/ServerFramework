from datetime import datetime
from enum import Enum
from typing import ClassVar, Dict, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from fastapi import HTTPException

from zephyrex.pydantic2.sqlalchemy import DatabaseMixin
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    BaseMixinModel,
    DateSearchModel,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import UserModel


# Health provider type constants
class HealthProviderType:
    FITBIT = "fitbit"
    APPLE_HEALTH = "apple_health"
    GOOGLE_FIT = "google_fit"
    MYFITNESSPAL = "myfitnesspal"
    STRAVA = "strava"
    GARMIN = "garmin"
    WITHINGS = "withings"

    @classmethod
    def values(cls):
        return [
            cls.FITBIT,
            cls.APPLE_HEALTH,
            cls.GOOGLE_FIT,
            cls.MYFITNESSPAL,
            cls.STRAVA,
            cls.GARMIN,
            cls.WITHINGS,
        ]


class HealthProviderModel(
    BaseMixinModel, UpdateMixinModel, UserModel.Reference.ID, DatabaseMixin
):
    provider_name: str = Field(..., description="Name of the health provider")
    provider_type: str = Field(..., description="Type of health provider")
    enabled: bool = Field(True, description="Whether the provider is enabled")
    username: Optional[str] = Field(None, description="Username for the provider")
    credentials: Optional[Dict] = Field(None, description="Provider credentials")

    # Database metadata
    table_comment: ClassVar[str] = (
        "Health data provider configurations for users including credentials and settings"
    )

    class ReferenceID:
        healthprovider_id: str = Field(..., description="The ID of the health provider")

        class Optional:
            healthprovider_id: Optional[str] = None

        class Search:
            healthprovider_id: Optional[StringSearchModel] = None

    class Create(BaseModel, UserModel.Reference.ID):
        provider_name: str = Field(..., description="Name of the health provider")
        provider_type: str = Field(..., description="Type of health provider")
        enabled: bool = Field(True, description="Whether the provider is enabled")
        username: Optional[str] = Field(None, description="Username for the provider")
        credentials: Optional[Dict] = Field(None, description="Provider credentials")

    class Update(BaseModel):
        provider_name: Optional[str] = Field(
            None, description="Name of the health provider"
        )
        enabled: Optional[bool] = Field(
            None, description="Whether the provider is enabled"
        )
        username: Optional[str] = Field(None, description="Username for the provider")
        credentials: Optional[Dict] = Field(None, description="Provider credentials")

    class Search(
        BaseMixinModel.Search, UpdateMixinModel.Search, UserModel.Reference.ID.Search
    ):
        provider_name: Optional[StringSearchModel] = None
        provider_type: Optional[str] = None
        enabled: Optional[bool] = None
        username: Optional[StringSearchModel] = None


class HealthProviderReferenceModel(HealthProviderModel.Reference.ID):
    health_provider: Optional[HealthProviderModel] = None

    class Optional(HealthProviderModel.Reference.ID.Optional):
        health_provider: Optional[HealthProviderModel] = None


class HealthProviderNetworkModel:
    class POST(BaseModel):
        health_provider: HealthProviderModel.Create

    class PUT(BaseModel):
        health_provider: HealthProviderModel.Update

    class SEARCH(BaseModel):
        health_provider: HealthProviderModel.Search

    class ResponseSingle(BaseModel):
        health_provider: HealthProviderModel

    class ResponsePlural(BaseModel):
        health_providers: List[HealthProviderModel]


# Activity type constants
class ActivityType:
    RUNNING = "running"
    WALKING = "walking"
    CYCLING = "cycling"
    SWIMMING = "swimming"
    WEIGHTLIFTING = "weightlifting"
    YOGA = "yoga"
    HIKING = "hiking"
    OTHER = "other"


class ActivityRecordModel(
    BaseMixinModel,
    UserModel.Reference.ID,
    HealthProviderModel.Reference.ID,
    DatabaseMixin,
):
    date: datetime = Field(..., description="Date of the activity")
    activity_type: str = Field(..., description="Type of activity")
    duration_minutes: int = Field(..., description="Duration in minutes")
    calories_burned: Optional[float] = Field(None, description="Calories burned")
    steps: Optional[int] = Field(None, description="Steps taken")
    distance_km: Optional[float] = Field(None, description="Distance in kilometers")
    heart_rate_avg: Optional[int] = Field(None, description="Average heart rate")
    heart_rate_max: Optional[int] = Field(None, description="Maximum heart rate")
    metadata: Optional[Dict] = Field(None, description="Additional activity metadata")

    # Database metadata
    table_comment: ClassVar[str] = (
        "User activity and exercise records with metrics like calories, steps, and heart rate"
    )

    class ReferenceID:
        activityrecord_id: str = Field(..., description="The ID of the activity record")

        class Optional:
            activityrecord_id: Optional[str] = None

        class Search:
            activityrecord_id: Optional[StringSearchModel] = None

    class Create(BaseModel, UserModel.Reference.ID, HealthProviderModel.Reference.ID):
        date: datetime = Field(..., description="Date of the activity")
        activity_type: str = Field(..., description="Type of activity")
        duration_minutes: int = Field(..., description="Duration in minutes")
        calories_burned: Optional[float] = Field(None, description="Calories burned")
        steps: Optional[int] = Field(None, description="Steps taken")
        distance_km: Optional[float] = Field(None, description="Distance in kilometers")
        heart_rate_avg: Optional[int] = Field(None, description="Average heart rate")
        heart_rate_max: Optional[int] = Field(None, description="Maximum heart rate")
        metadata: Optional[Dict] = Field(
            None, description="Additional activity metadata"
        )

    class Update(BaseModel):
        date: Optional[datetime] = Field(None, description="Date of the activity")
        activity_type: Optional[str] = Field(None, description="Type of activity")
        duration_minutes: Optional[int] = Field(None, description="Duration in minutes")
        calories_burned: Optional[float] = Field(None, description="Calories burned")
        steps: Optional[int] = Field(None, description="Steps taken")
        distance_km: Optional[float] = Field(None, description="Distance in kilometers")
        heart_rate_avg: Optional[int] = Field(None, description="Average heart rate")
        heart_rate_max: Optional[int] = Field(None, description="Maximum heart rate")
        metadata: Optional[Dict] = Field(
            None, description="Additional activity metadata"
        )

    class Search(
        BaseMixinModel.Search,
        UserModel.Reference.ID.Search,
        HealthProviderModel.Reference.ID.Search,
    ):
        date: Optional[DateSearchModel] = None
        activity_type: Optional[str] = None
        duration_minutes: Optional[int] = None


class ActivityRecordReferenceModel(ActivityRecordModel.Reference.ID):
    activity_record: Optional[ActivityRecordModel] = None

    class Optional(ActivityRecordModel.Reference.ID.Optional):
        activity_record: Optional[ActivityRecordModel] = None


class ActivityRecordNetworkModel:
    class POST(BaseModel):
        activity_record: ActivityRecordModel.Create

    class PUT(BaseModel):
        activity_record: ActivityRecordModel.Update

    class SEARCH(BaseModel):
        activity_record: ActivityRecordModel.Search

    class ResponseSingle(BaseModel):
        activity_record: ActivityRecordModel

    class ResponsePlural(BaseModel):
        activity_records: List[ActivityRecordModel]


# Meal type constants
class MealType:
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"
    SNACK = "snack"


class NutritionRecordModel(
    BaseMixinModel,
    UserModel.Reference.ID,
    HealthProviderModel.Reference.ID,
    DatabaseMixin,
):
    date: datetime = Field(..., description="Date of the meal")
    meal_type: str = Field(..., description="Type of meal")
    food_name: str = Field(..., description="Name of the food")
    serving_size: float = Field(..., description="Size of the serving")
    serving_unit: str = Field(..., description="Unit of the serving (e.g., g, oz)")
    calories: float = Field(..., description="Calories in the serving")
    protein_g: Optional[float] = Field(None, description="Protein in grams")
    carbs_g: Optional[float] = Field(None, description="Carbohydrates in grams")
    fat_g: Optional[float] = Field(None, description="Fat in grams")
    fiber_g: Optional[float] = Field(None, description="Fiber in grams")
    sugar_g: Optional[float] = Field(None, description="Sugar in grams")
    sodium_mg: Optional[float] = Field(None, description="Sodium in milligrams")
    metadata: Optional[Dict] = Field(None, description="Additional nutrition metadata")

    # Database metadata
    table_comment: ClassVar[str] = (
        "User nutrition and food intake records with detailed macronutrient information"
    )

    class ReferenceID:
        nutritionrecord_id: str = Field(
            ..., description="The ID of the nutrition record"
        )

        class Optional:
            nutritionrecord_id: Optional[str] = None

        class Search:
            nutritionrecord_id: Optional[StringSearchModel] = None

    class Create(BaseModel, UserModel.Reference.ID, HealthProviderModel.Reference.ID):
        date: datetime = Field(..., description="Date of the meal")
        meal_type: str = Field(..., description="Type of meal")
        food_name: str = Field(..., description="Name of the food")
        serving_size: float = Field(..., description="Size of the serving")
        serving_unit: str = Field(..., description="Unit of the serving (e.g., g, oz)")
        calories: float = Field(..., description="Calories in the serving")
        protein_g: Optional[float] = Field(None, description="Protein in grams")
        carbs_g: Optional[float] = Field(None, description="Carbohydrates in grams")
        fat_g: Optional[float] = Field(None, description="Fat in grams")
        fiber_g: Optional[float] = Field(None, description="Fiber in grams")
        sugar_g: Optional[float] = Field(None, description="Sugar in grams")
        sodium_mg: Optional[float] = Field(None, description="Sodium in milligrams")
        metadata: Optional[Dict] = Field(
            None, description="Additional nutrition metadata"
        )

    class Update(BaseModel):
        date: Optional[datetime] = Field(None, description="Date of the meal")
        meal_type: Optional[str] = Field(None, description="Type of meal")
        food_name: Optional[str] = Field(None, description="Name of the food")
        serving_size: Optional[float] = Field(None, description="Size of the serving")
        serving_unit: Optional[str] = Field(None, description="Unit of the serving")
        calories: Optional[float] = Field(None, description="Calories in the serving")
        protein_g: Optional[float] = Field(None, description="Protein in grams")
        carbs_g: Optional[float] = Field(None, description="Carbohydrates in grams")
        fat_g: Optional[float] = Field(None, description="Fat in grams")
        fiber_g: Optional[float] = Field(None, description="Fiber in grams")
        sugar_g: Optional[float] = Field(None, description="Sugar in grams")
        sodium_mg: Optional[float] = Field(None, description="Sodium in milligrams")
        metadata: Optional[Dict] = Field(
            None, description="Additional nutrition metadata"
        )

    class Search(
        BaseMixinModel.Search,
        UserModel.Reference.ID.Search,
        HealthProviderModel.Reference.ID.Search,
    ):
        date: Optional[DateSearchModel] = None
        meal_type: Optional[str] = None
        food_name: Optional[StringSearchModel] = None


class NutritionRecordReferenceModel(NutritionRecordModel.Reference.ID):
    nutrition_record: Optional[NutritionRecordModel] = None

    class Optional(NutritionRecordModel.Reference.ID.Optional):
        nutrition_record: Optional[NutritionRecordModel] = None


class NutritionRecordNetworkModel:
    class POST(BaseModel):
        nutrition_record: NutritionRecordModel.Create

    class PUT(BaseModel):
        nutrition_record: NutritionRecordModel.Update

    class SEARCH(BaseModel):
        nutrition_record: NutritionRecordModel.Search

    class ResponseSingle(BaseModel):
        nutrition_record: NutritionRecordModel

    class ResponsePlural(BaseModel):
        nutrition_records: List[NutritionRecordModel]


class WeightRecordModel(
    BaseMixinModel,
    UserModel.Reference.ID,
    HealthProviderModel.Reference.ID,
    DatabaseMixin,
):
    date: datetime = Field(..., description="Date of the weight measurement")
    weight_kg: float = Field(..., description="Weight in kilograms")
    bmi: Optional[float] = Field(None, description="Body Mass Index")
    body_fat_percentage: Optional[float] = Field(
        None, description="Body fat percentage"
    )
    lean_mass_kg: Optional[float] = Field(None, description="Lean mass in kilograms")
    fat_mass_kg: Optional[float] = Field(None, description="Fat mass in kilograms")
    notes: Optional[str] = Field(None, description="Additional notes")

    # Database metadata
    table_comment: ClassVar[str] = (
        "User weight measurements and body composition data with BMI and fat percentage"
    )

    class ReferenceID:
        weightrecord_id: str = Field(..., description="The ID of the weight record")

        class Optional:
            weightrecord_id: Optional[str] = None

        class Search:
            weightrecord_id: Optional[StringSearchModel] = None

    class Create(BaseModel, UserModel.Reference.ID, HealthProviderModel.Reference.ID):
        date: datetime = Field(..., description="Date of the weight measurement")
        weight_kg: float = Field(..., description="Weight in kilograms")
        bmi: Optional[float] = Field(None, description="Body Mass Index")
        body_fat_percentage: Optional[float] = Field(
            None, description="Body fat percentage"
        )
        lean_mass_kg: Optional[float] = Field(
            None, description="Lean mass in kilograms"
        )
        fat_mass_kg: Optional[float] = Field(None, description="Fat mass in kilograms")
        notes: Optional[str] = Field(None, description="Additional notes")

    class Update(BaseModel):
        date: Optional[datetime] = Field(
            None, description="Date of the weight measurement"
        )
        weight_kg: Optional[float] = Field(None, description="Weight in kilograms")
        bmi: Optional[float] = Field(None, description="Body Mass Index")
        body_fat_percentage: Optional[float] = Field(
            None, description="Body fat percentage"
        )
        lean_mass_kg: Optional[float] = Field(
            None, description="Lean mass in kilograms"
        )
        fat_mass_kg: Optional[float] = Field(None, description="Fat mass in kilograms")
        notes: Optional[str] = Field(None, description="Additional notes")

    class Search(
        BaseMixinModel.Search,
        UserModel.Reference.ID.Search,
        HealthProviderModel.Reference.ID.Search,
    ):
        date: Optional[DateSearchModel] = None
        weight_kg: Optional[float] = None
        bmi: Optional[float] = None


class WeightRecordReferenceModel(WeightRecordModel.Reference.ID):
    weight_record: Optional[WeightRecordModel] = None

    class Optional(WeightRecordModel.Reference.ID.Optional):
        weight_record: Optional[WeightRecordModel] = None


class WeightRecordNetworkModel:
    class POST(BaseModel):
        weight_record: WeightRecordModel.Create

    class PUT(BaseModel):
        weight_record: WeightRecordModel.Update

    class SEARCH(BaseModel):
        weight_record: WeightRecordModel.Search

    class ResponseSingle(BaseModel):
        weight_record: WeightRecordModel

    class ResponsePlural(BaseModel):
        weight_records: List[WeightRecordModel]


class SleepRecordModel(
    BaseMixinModel,
    UserModel.Reference.ID,
    HealthProviderModel.Reference.ID,
    DatabaseMixin,
):
    sleep_date: datetime = Field(..., description="Date of the sleep (day it started)")
    bedtime: datetime = Field(..., description="Time went to bed")
    wake_time: datetime = Field(..., description="Time woke up")
    sleep_duration_minutes: int = Field(
        ..., description="Total sleep duration in minutes"
    )
    deep_sleep_minutes: Optional[int] = Field(None, description="Deep sleep in minutes")
    light_sleep_minutes: Optional[int] = Field(
        None, description="Light sleep in minutes"
    )
    rem_sleep_minutes: Optional[int] = Field(None, description="REM sleep in minutes")
    awake_minutes: Optional[int] = Field(
        None, description="Time awake during sleep in minutes"
    )
    sleep_quality_score: Optional[int] = Field(None, description="Sleep quality score")
    metadata: Optional[Dict] = Field(None, description="Additional sleep metadata")

    # Database metadata
    table_comment: ClassVar[str] = (
        "User sleep records with detailed sleep stage breakdown and quality metrics"
    )

    class ReferenceID:
        sleeprecord_id: str = Field(..., description="The ID of the sleep record")

        class Optional:
            sleeprecord_id: Optional[str] = None

        class Search:
            sleeprecord_id: Optional[StringSearchModel] = None

    class Create(BaseModel, UserModel.Reference.ID, HealthProviderModel.Reference.ID):
        sleep_date: datetime = Field(
            ..., description="Date of the sleep (day it started)"
        )
        bedtime: datetime = Field(..., description="Time went to bed")
        wake_time: datetime = Field(..., description="Time woke up")
        sleep_duration_minutes: int = Field(
            ..., description="Total sleep duration in minutes"
        )
        deep_sleep_minutes: Optional[int] = Field(
            None, description="Deep sleep in minutes"
        )
        light_sleep_minutes: Optional[int] = Field(
            None, description="Light sleep in minutes"
        )
        rem_sleep_minutes: Optional[int] = Field(
            None, description="REM sleep in minutes"
        )
        awake_minutes: Optional[int] = Field(
            None, description="Time awake during sleep in minutes"
        )
        sleep_quality_score: Optional[int] = Field(
            None, description="Sleep quality score"
        )
        metadata: Optional[Dict] = Field(None, description="Additional sleep metadata")

    class Update(BaseModel):
        sleep_date: Optional[datetime] = Field(None, description="Date of the sleep")
        bedtime: Optional[datetime] = Field(None, description="Time went to bed")
        wake_time: Optional[datetime] = Field(None, description="Time woke up")
        sleep_duration_minutes: Optional[int] = Field(
            None, description="Sleep duration in minutes"
        )
        deep_sleep_minutes: Optional[int] = Field(
            None, description="Deep sleep in minutes"
        )
        light_sleep_minutes: Optional[int] = Field(
            None, description="Light sleep in minutes"
        )
        rem_sleep_minutes: Optional[int] = Field(
            None, description="REM sleep in minutes"
        )
        awake_minutes: Optional[int] = Field(
            None, description="Time awake during sleep"
        )
        sleep_quality_score: Optional[int] = Field(
            None, description="Sleep quality score"
        )
        metadata: Optional[Dict] = Field(None, description="Additional sleep metadata")

    class Search(
        BaseMixinModel.Search,
        UserModel.Reference.ID.Search,
        HealthProviderModel.Reference.ID.Search,
    ):
        sleep_date: Optional[DateSearchModel] = None
        bedtime: Optional[DateSearchModel] = None
        wake_time: Optional[DateSearchModel] = None
        sleep_duration_minutes: Optional[int] = None


class SleepRecordReferenceModel(SleepRecordModel.Reference.ID):
    sleep_record: Optional[SleepRecordModel] = None

    class Optional(SleepRecordModel.Reference.ID.Optional):
        sleep_record: Optional[SleepRecordModel] = None


class SleepRecordNetworkModel:
    class POST(BaseModel):
        sleep_record: SleepRecordModel.Create

    class PUT(BaseModel):
        sleep_record: SleepRecordModel.Update

    class SEARCH(BaseModel):
        sleep_record: SleepRecordModel.Search

    class ResponseSingle(BaseModel):
        sleep_record: SleepRecordModel

    class ResponsePlural(BaseModel):
        sleep_records: List[SleepRecordModel]


class HealthProviderManager(AbstractBLLManager):
    Model = HealthProviderModel
    ReferenceModel = HealthProviderReferenceModel
    NetworkModel = HealthProviderNetworkModel

    def __init__(
        self,
        requester_id: str,
        target_user_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        db: Optional[Session] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_id=target_user_id,
            target_team_id=target_team_id,
            db=db,
        )
        self._activities = None
        self._nutrition = None
        self._weight = None
        self._sleep = None

    def create(self, create_model: Model.Create) -> Model:
        # Validate provider type
        if create_model.provider_type not in HealthProviderType.values():
            raise HTTPException(
                status_code=400,
                detail=f"Invalid provider type: {create_model.provider_type}. Must be one of: {', '.join(HealthProviderType.values())}",
            )
        return super().create(create_model)

    def update(self, update_model: Model.Update, id: str) -> Model:
        # Validate provider type if provided
        if (
            hasattr(update_model, "provider_type")
            and update_model.provider_type is not None
        ):
            if update_model.provider_type not in HealthProviderType.values():
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid provider type: {update_model.provider_type}. Must be one of: {', '.join(HealthProviderType.values())}",
                )
        return super().update(update_model, id)

    @property
    def activities(self):
        if self._activities is None:
            self._activities = ActivityRecordManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._activities

    @property
    def nutrition(self):
        if self._nutrition is None:
            self._nutrition = NutritionRecordManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._nutrition

    @property
    def weight(self):
        if self._weight is None:
            self._weight = WeightRecordManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._weight

    @property
    def sleep(self):
        if self._sleep is None:
            self._sleep = SleepRecordManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._sleep


class ActivityRecordManager(AbstractBLLManager):
    Model = ActivityRecordModel
    ReferenceModel = ActivityRecordReferenceModel
    NetworkModel = ActivityRecordNetworkModel

    def __init__(
        self,
        requester_id: str,
        target_user_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        db: Optional[Session] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_id=target_user_id,
            target_team_id=target_team_id,
            db=db,
        )
        self._providers = None

    @property
    def providers(self):
        if self._providers is None:
            self._providers = HealthProviderManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._providers


class NutritionRecordManager(AbstractBLLManager):
    Model = NutritionRecordModel
    ReferenceModel = NutritionRecordReferenceModel
    NetworkModel = NutritionRecordNetworkModel

    def __init__(
        self,
        requester_id: str,
        target_user_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        db: Optional[Session] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_id=target_user_id,
            target_team_id=target_team_id,
            db=db,
        )
        self._providers = None

    @property
    def providers(self):
        if self._providers is None:
            self._providers = HealthProviderManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._providers


class WeightRecordManager(AbstractBLLManager):
    Model = WeightRecordModel
    ReferenceModel = WeightRecordReferenceModel
    NetworkModel = WeightRecordNetworkModel

    def __init__(
        self,
        requester_id: str,
        target_user_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        db: Optional[Session] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_id=target_user_id,
            target_team_id=target_team_id,
            db=db,
        )
        self._providers = None

    @property
    def providers(self):
        if self._providers is None:
            self._providers = HealthProviderManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._providers


class SleepRecordManager(AbstractBLLManager):
    Model = SleepRecordModel
    ReferenceModel = SleepRecordReferenceModel
    NetworkModel = SleepRecordNetworkModel

    def __init__(
        self,
        requester_id: str,
        target_user_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        db: Optional[Session] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_id=target_user_id,
            target_team_id=target_team_id,
            db=db,
        )
        self._providers = None

    @property
    def providers(self):
        if self._providers is None:
            self._providers = HealthProviderManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._providers
