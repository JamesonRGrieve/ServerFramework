import os
from abc import abstractmethod
from typing import Any, Dict, List, Optional

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtensionProvider


class HealthProvider(AbstractStaticExtensionProvider):
    """
    Abstract base class for health service extensions.
    Implements the common interface and functionality for health tracking services.
    """

    def __init__(
        self,
        api_key: str = "",
        api_uri: str = "",
        extension_id: Optional[str] = None,
        wait_between_requests: int = 1,
        wait_after_failure: int = 3,
        **kwargs,
    ):
        """
        Initialize the health provider with common configuration.
        """
        super().__init__(
            api_key=api_key,
            api_uri=api_uri,
            extension_id=extension_id,
            wait_between_requests=wait_between_requests,
            wait_after_failure=wait_after_failure,
            **kwargs,
        )

        # Register standard health capabilities
        self.register_capability("track_activity")
        self.register_capability("track_nutrition")
        self.register_capability("track_weight")
        self.register_capability("track_sleep")

    def _configure_provider(self, **kwargs) -> None:
        """
        Configure provider-specific settings.
        """
        # Set up working directory for health data
        base_dir = self.WORKING_DIRECTORY or os.getcwd()
        self.health_data_dir = self.safe_join(base_dir, "health_data")
        os.makedirs(self.health_data_dir, exist_ok=True)

        # Initialize common commands
        self.commands = {
            "get_activity_summary": self.get_activity_summary,
            "get_nutrition_summary": self.get_nutrition_summary,
            "get_weight_history": self.get_weight_history,
            "get_sleep_data": self.get_sleep_data,
        }

    @staticmethod
    @abstractmethod
    def services() -> List[str]:
        """
        Return a list of services provided by this health provider.

        Returns:
            List of service identifiers (e.g., "nutrition", "activity", "sleep", etc.)
        """
        return ["health", "fitness", "nutrition", "sleep", "activity"]

    @abstractmethod
    async def get_activity_summary(
        self, start_date: str = "", end_date: str = ""
    ) -> Dict[str, Any]:
        """
        Get a summary of user activity for a specific date range.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format

        Returns:
            Dictionary containing activity summary
        """
        pass

    @abstractmethod
    async def get_nutrition_summary(
        self, start_date: str = "", end_date: str = ""
    ) -> Dict[str, Any]:
        """
        Get a summary of user nutrition for a specific date range.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format

        Returns:
            Dictionary containing nutrition summary
        """
        pass

    @abstractmethod
    async def get_weight_history(
        self, start_date: str = "", end_date: str = ""
    ) -> List[Dict[str, Any]]:
        """
        Get weight history for a specific date range.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format

        Returns:
            List of weight measurements
        """
        pass

    @abstractmethod
    async def get_sleep_data(
        self, start_date: str = "", end_date: str = ""
    ) -> List[Dict[str, Any]]:
        """
        Get sleep data for a specific date range.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format

        Returns:
            List of sleep records
        """
        pass

    def get_commands(self) -> Dict[str, Any]:
        """
        Get the available commands for this provider.

        Returns:
            Dict of command names to command handler functions
        """
        return self.commands

    def get_extension_info(self) -> Dict[str, Any]:
        """
        Get information about the health extension this provider is associated with.

        Returns:
            Dict containing extension metadata
        """
        return {
            "name": "Health",
            "description": "Health tracking extension for fitness and wellness data",
            "capabilities": list(self.capabilities),
        }
