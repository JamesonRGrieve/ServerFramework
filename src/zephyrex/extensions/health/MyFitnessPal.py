import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import aiohttp

from zephyrex.extensions.PRV_Health import HealthProvider


class MyFitnessPalProvider(HealthProvider):
    """
    MyFitnessPal implementation of the Health provider.
    Connects to MyFitnessPal API to track nutrition, exercise, and weight data.
    """

    def __init__(
        self,
        api_key: str = "",
        api_uri: str = "https://api.myfitnesspal.com/v2",
        extension_id: Optional[str] = None,
        wait_between_requests: int = 1,
        wait_after_failure: int = 3,
        **kwargs,
    ):
        """
        Initialize the MyFitnessPal provider.
        """
        super().__init__(
            api_key=api_key,
            api_uri=api_uri,
            extension_id=extension_id,
            wait_between_requests=wait_between_requests,
            wait_after_failure=wait_after_failure,
            **kwargs,
        )

        # MyFitnessPal-specific capabilities
        self.register_capability("food_database_search")
        self.register_capability("recipe_creation")
        self.register_capability("meal_planning")

        # Add MyFitnessPal-specific commands
        self.commands.update(
            {
                "search_food": self.search_food,
                "log_food_item": self.log_food_item,
                "get_daily_goal": self.get_daily_goal,
            }
        )

        self.friendly_name = "MyFitnessPal"

    def _configure_provider(self, **kwargs) -> None:
        """
        Configure MyFitnessPal-specific settings.
        """
        super()._configure_provider(**kwargs)

        # Create MyFitnessPal data directory
        self.mfp_data_dir = self.safe_join(self.health_data_dir, "myfitnesspal")
        os.makedirs(self.mfp_data_dir, exist_ok=True)

        # MyFitnessPal credentials
        self.username = kwargs.get("username", "")
        self.password = kwargs.get("password", "")
        self.session_token = kwargs.get("session_token", "")

        # Create the API client session
        self.client_session = None

    @staticmethod
    def services() -> List[str]:
        """
        Return list of services provided by MyFitnessPal.

        Returns:
            List of service identifiers
        """
        return ["health", "fitness", "nutrition", "weight", "recipes", "meal_planning"]

    async def _ensure_client_session(self):
        """
        Ensure an API client session exists and is authenticated.
        """
        if self.client_session is None:
            self.client_session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
            )

            # Check if we need to authenticate
            if not self.session_token and self.username and self.password:
                try:
                    # Authenticate with MyFitnessPal
                    async with self.client_session.post(
                        f"{self.api_uri}/auth/login",
                        json={"username": self.username, "password": self.password},
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            self.session_token = data.get("access_token", "")
                            # Update headers with token
                            self.client_session.headers.update(
                                {"Authorization": f"Bearer {self.session_token}"}
                            )
                        else:
                            logging.error(
                                f"MyFitnessPal authentication failed: {response.status}"
                            )
                except Exception as e:
                    self._handle_failure(e)

    async def get_activity_summary(
        self, start_date: str = "", end_date: str = ""
    ) -> Dict[str, Any]:
        """
        Get a summary of user activity from MyFitnessPal.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format

        Returns:
            Dictionary containing activity summary
        """
        await self._ensure_client_session()

        # Use today if no date provided
        if not start_date:
            start_date = datetime.now().strftime("%Y-%m-%d")
        if not end_date:
            end_date = start_date

        try:
            async with self.client_session.get(
                f"{self.api_uri}/diary/exercise",
                params={"start_date": start_date, "end_date": end_date},
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        "date_range": f"{start_date} to {end_date}",
                        "activities": data.get("exercises", []),
                        "total_calories_burned": sum(
                            activity.get("calories_burned", 0)
                            for activity in data.get("exercises", [])
                        ),
                    }
                else:
                    logging.error(f"Failed to get activity data: {response.status}")
                    return {"error": f"Failed to get activity data: {response.status}"}
        except Exception as e:
            self._handle_failure(e)
            return {"error": str(e)}

    async def get_nutrition_summary(
        self, start_date: str = "", end_date: str = ""
    ) -> Dict[str, Any]:
        """
        Get a summary of user nutrition from MyFitnessPal.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format

        Returns:
            Dictionary containing nutrition summary
        """
        await self._ensure_client_session()

        # Use today if no date provided
        if not start_date:
            start_date = datetime.now().strftime("%Y-%m-%d")
        if not end_date:
            end_date = start_date

        try:
            async with self.client_session.get(
                f"{self.api_uri}/diary/meals",
                params={"start_date": start_date, "end_date": end_date},
            ) as response:
                if response.status == 200:
                    data = await response.json()

                    # Calculate totals across all days
                    total_calories = 0
                    total_protein = 0
                    total_carbs = 0
                    total_fat = 0

                    for day_data in data.get("days", []):
                        totals = day_data.get("totals", {})
                        total_calories += totals.get("calories", 0)
                        total_protein += totals.get("protein", 0)
                        total_carbs += totals.get("carbohydrates", 0)
                        total_fat += totals.get("fat", 0)

                    return {
                        "date_range": f"{start_date} to {end_date}",
                        "days": data.get("days", []),
                        "summary": {
                            "total_calories": total_calories,
                            "total_protein_g": total_protein,
                            "total_carbs_g": total_carbs,
                            "total_fat_g": total_fat,
                            "daily_average_calories": total_calories
                            / max(1, len(data.get("days", []))),
                        },
                    }
                else:
                    logging.error(f"Failed to get nutrition data: {response.status}")
                    return {"error": f"Failed to get nutrition data: {response.status}"}
        except Exception as e:
            self._handle_failure(e)
            return {"error": str(e)}

    async def get_weight_history(
        self, start_date: str = "", end_date: str = ""
    ) -> List[Dict[str, Any]]:
        """
        Get weight history from MyFitnessPal.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format

        Returns:
            List of weight measurements
        """
        await self._ensure_client_session()

        # Default to last 30 days if not specified
        if not start_date:
            start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        if not end_date:
            end_date = datetime.now().strftime("%Y-%m-%d")

        try:
            async with self.client_session.get(
                f"{self.api_uri}/measurements/weight",
                params={"start_date": start_date, "end_date": end_date},
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("weight_entries", [])
                else:
                    logging.error(f"Failed to get weight history: {response.status}")
                    return []
        except Exception as e:
            self._handle_failure(e)
            return []

    async def get_sleep_data(
        self, start_date: str = "", end_date: str = ""
    ) -> List[Dict[str, Any]]:
        """
        Get sleep data from MyFitnessPal.

        Note: MyFitnessPal has limited sleep tracking functionality,
        this method might return minimal data or connect to a partner service.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format

        Returns:
            List of sleep records
        """
        await self._ensure_client_session()

        # Default to last 7 days if not specified
        if not start_date:
            start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        if not end_date:
            end_date = datetime.now().strftime("%Y-%m-%d")

        try:
            # MyFitnessPal may integrate with sleep trackers rather than track directly
            async with self.client_session.get(
                f"{self.api_uri}/integrations/sleep",
                params={"start_date": start_date, "end_date": end_date},
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("sleep_records", [])
                else:
                    logging.error(f"Failed to get sleep data: {response.status}")
                    return []
        except Exception as e:
            self._handle_failure(e)
            return []

    async def search_food(
        self, query: str = "", limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Search the MyFitnessPal food database.

        Args:
            query: Food search query
            limit: Maximum number of results to return

        Returns:
            List of food items matching the query
        """
        await self._ensure_client_session()

        try:
            async with self.client_session.get(
                f"{self.api_uri}/foods/search", params={"q": query, "limit": limit}
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("items", [])
                else:
                    logging.error(f"Failed to search foods: {response.status}")
                    return []
        except Exception as e:
            self._handle_failure(e)
            return []

    async def log_food_item(
        self,
        food_id: str = "",
        meal_type: str = "breakfast",
        servings: float = 1.0,
        date: str = "",
    ) -> Dict[str, Any]:
        """
        Log a food item to the user's diary.

        Args:
            food_id: ID of the food item in MyFitnessPal database
            meal_type: Meal type (breakfast, lunch, dinner, snack)
            servings: Number of servings
            date: Date to log the food (YYYY-MM-DD format)

        Returns:
            Result of the operation
        """
        await self._ensure_client_session()

        # Use today if no date provided
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")

        try:
            payload = {
                "food_id": food_id,
                "meal_type": meal_type,
                "servings": servings,
                "date": date,
            }

            async with self.client_session.post(
                f"{self.api_uri}/diary/log_food", json=payload
            ) as response:
                if response.status in (200, 201):
                    data = await response.json()
                    return {"success": True, "entry_id": data.get("entry_id")}
                else:
                    error_msg = f"Failed to log food: {response.status}"
                    logging.error(error_msg)
                    return {"success": False, "error": error_msg}
        except Exception as e:
            self._handle_failure(e)
            return {"success": False, "error": str(e)}

    async def get_daily_goal(self, date: str = "") -> Dict[str, Any]:
        """
        Get the user's daily nutritional goals.

        Args:
            date: Date for which to get goals (YYYY-MM-DD format)

        Returns:
            Dictionary containing daily nutritional goals
        """
        await self._ensure_client_session()

        # Use today if no date provided
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")

        try:
            async with self.client_session.get(
                f"{self.api_uri}/goals/daily", params={"date": date}
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("goals", {})
                else:
                    logging.error(f"Failed to get daily goals: {response.status}")
                    return {}
        except Exception as e:
            self._handle_failure(e)
            return {}
