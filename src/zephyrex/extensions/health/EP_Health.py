from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from zephyrex.database.StaticDatabaseManager import DatabaseManager
from zephyrex.endpoints.AbstractEndpointRouter import AbstractEPRouter
from zephyrex.endpoints.security import get_current_user
from zephyrex.extensions.BLL_Health import (
    ActivityManager,
    HealthProviderManager,
    NutritionManager,
    SleepManager,
    WeightManager,
)


class HealthRouter(AbstractEPRouter):
    """
    Router for health tracking endpoints.
    """

    def __init__(self):
        self.router = APIRouter(prefix="/health", tags=["health"])
        self._register_routes()

    def _register_routes(self):
        """
        Register all health tracking endpoints.
        """
        # Provider routes
        self.router.get("/providers", response_model=List[Dict[str, Any]])
        self.router.post("/providers", response_model=Dict[str, Any])
        self.router.put("/providers/{provider_id}", response_model=Dict[str, Any])
        self.router.delete("/providers/{provider_id}", response_model=bool)

        # Activity routes
        self.router.get("/activities", response_model=List[Dict[str, Any]])
        self.router.post("/activities", response_model=Dict[str, Any])

        # Nutrition routes
        self.router.get("/nutrition/summary", response_model=Dict[str, Any])
        self.router.post("/nutrition", response_model=Dict[str, Any])

        # Weight routes
        self.router.get("/weight", response_model=List[Dict[str, Any]])
        self.router.post("/weight", response_model=Dict[str, Any])

        # Sleep routes
        self.router.get("/sleep", response_model=List[Dict[str, Any]])
        self.router.post("/sleep", response_model=Dict[str, Any])

    @staticmethod
    async def get_providers(
        db: Session = Depends(DatabaseManager.get_db),
        current_user: Dict[str, Any] = Depends(get_current_user),
    ) -> List[Dict[str, Any]]:
        """
        Get all health providers for the current user.
        """
        manager = HealthProviderManager(db)
        return await manager.get_providers(current_user["id"])

    @staticmethod
    async def add_provider(
        provider_data: Dict[str, Any],
        db: Session = Depends(DatabaseManager.get_db),
        current_user: Dict[str, Any] = Depends(get_current_user),
    ) -> Dict[str, Any]:
        """
        Add a new health provider for the current user.
        """
        manager = HealthProviderManager(db)
        provider_data["user_id"] = current_user["id"]

        try:
            return await manager.add_provider(**provider_data)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @staticmethod
    async def update_provider(
        provider_id: int,
        provider_data: Dict[str, Any],
        db: Session = Depends(DatabaseManager.get_db),
        current_user: Dict[str, Any] = Depends(get_current_user),
    ) -> Dict[str, Any]:
        """
        Update an existing health provider.
        """
        manager = HealthProviderManager(db)

        # Verify provider belongs to user
        providers = await manager.get_providers(current_user["id"])
        if not any(p["id"] == provider_id for p in providers):
            raise HTTPException(status_code=404, detail="Provider not found")

        try:
            return await manager.update_provider(provider_id, **provider_data)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @staticmethod
    async def delete_provider(
        provider_id: int,
        db: Session = Depends(DatabaseManager.get_db),
        current_user: Dict[str, Any] = Depends(get_current_user),
    ) -> bool:
        """
        Delete a health provider.
        """
        manager = HealthProviderManager(db)

        # Verify provider belongs to user
        providers = await manager.get_providers(current_user["id"])
        if not any(p["id"] == provider_id for p in providers):
            raise HTTPException(status_code=404, detail="Provider not found")

        return await manager.delete_provider(provider_id)

    @staticmethod
    async def get_activities(
        start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
        end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
        activity_type: Optional[str] = Query(None, description="Activity type"),
        provider_id: Optional[int] = Query(None, description="Provider ID"),
        db: Session = Depends(DatabaseManager.get_db),
        current_user: Dict[str, Any] = Depends(get_current_user),
    ) -> List[Dict[str, Any]]:
        """
        Get activity records for the current user.
        """
        manager = ActivityManager(db)
        return await manager.get_activities(
            user_id=current_user["id"],
            start_date=start_date,
            end_date=end_date,
            activity_type=activity_type,
            provider_id=provider_id,
        )

    @staticmethod
    async def log_activity(
        activity_data: Dict[str, Any],
        db: Session = Depends(DatabaseManager.get_db),
        current_user: Dict[str, Any] = Depends(get_current_user),
    ) -> Dict[str, Any]:
        """
        Log a new activity record for the current user.
        """
        manager = ActivityManager(db)
        activity_data["user_id"] = current_user["id"]

        try:
            return await manager.log_activity(**activity_data)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @staticmethod
    async def get_nutrition_summary(
        start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
        end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
        meal_type: Optional[str] = Query(None, description="Meal type"),
        provider_id: Optional[int] = Query(None, description="Provider ID"),
        db: Session = Depends(DatabaseManager.get_db),
        current_user: Dict[str, Any] = Depends(get_current_user),
    ) -> Dict[str, Any]:
        """
        Get nutrition summary for the current user.
        """
        manager = NutritionManager(db)
        return await manager.get_nutrition_summary(
            user_id=current_user["id"],
            start_date=start_date,
            end_date=end_date,
            meal_type=meal_type,
            provider_id=provider_id,
        )

    @staticmethod
    async def log_nutrition(
        nutrition_data: Dict[str, Any],
        db: Session = Depends(DatabaseManager.get_db),
        current_user: Dict[str, Any] = Depends(get_current_user),
    ) -> Dict[str, Any]:
        """
        Log a new nutrition record for the current user.
        """
        manager = NutritionManager(db)
        nutrition_data["user_id"] = current_user["id"]

        try:
            return await manager.log_nutrition(**nutrition_data)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @staticmethod
    async def get_weight_history(
        start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
        end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
        provider_id: Optional[int] = Query(None, description="Provider ID"),
        db: Session = Depends(DatabaseManager.get_db),
        current_user: Dict[str, Any] = Depends(get_current_user),
    ) -> List[Dict[str, Any]]:
        """
        Get weight history for the current user.
        """
        manager = WeightManager(db)
        return await manager.get_weight_history(
            user_id=current_user["id"],
            start_date=start_date,
            end_date=end_date,
            provider_id=provider_id,
        )

    @staticmethod
    async def log_weight(
        weight_data: Dict[str, Any],
        db: Session = Depends(DatabaseManager.get_db),
        current_user: Dict[str, Any] = Depends(get_current_user),
    ) -> Dict[str, Any]:
        """
        Log a new weight record for the current user.
        """
        manager = WeightManager(db)
        weight_data["user_id"] = current_user["id"]

        try:
            return await manager.log_weight(**weight_data)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @staticmethod
    async def get_sleep_data(
        start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
        end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
        provider_id: Optional[int] = Query(None, description="Provider ID"),
        db: Session = Depends(DatabaseManager.get_db),
        current_user: Dict[str, Any] = Depends(get_current_user),
    ) -> List[Dict[str, Any]]:
        """
        Get sleep data for the current user.
        """
        manager = SleepManager(db)
        return await manager.get_sleep_data(
            user_id=current_user["id"],
            start_date=start_date,
            end_date=end_date,
            provider_id=provider_id,
        )

    @staticmethod
    async def log_sleep(
        sleep_data: Dict[str, Any],
        db: Session = Depends(DatabaseManager.get_db),
        current_user: Dict[str, Any] = Depends(get_current_user),
    ) -> Dict[str, Any]:
        """
        Log a new sleep record for the current user.
        """
        manager = SleepManager(db)
        sleep_data["user_id"] = current_user["id"]

        try:
            return await manager.log_sleep(**sleep_data)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
