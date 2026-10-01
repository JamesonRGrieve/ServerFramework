"""
Health tracking extension for AGInfrastructure.

Provides health and fitness data tracking abilities including step counts,
heart rate, weight, sleep, and other biometric/wellness data, delegated to
whichever health provider is configured for this extension instance.
"""

from typing import Any, List, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.lib.Dependencies import EXT_Dependency, PIP_Dependency
from zephyrex.lib.Logging import logger


class EXT_Health(AbstractStaticExtension):
    """
    Health tracking extension for AGInfrastructure.

    Provides comprehensive health tracking abilities via Provider Rotation System,
    including fitness data collection, wellness monitoring, health analysis and
    biometric data tracking.
    """

    # Extension metadata
    name = "health"
    friendly_name = "Health & Fitness Tracking"
    version = "1.0.0"
    description = (
        "Health extension providing comprehensive health tracking and "
        "fitness data abilities via Provider Rotation System"
    )

    # Extension dependencies
    ext_dependencies = [
        EXT_Dependency(
            name="labels",
            friendly_name="Labels Extension",
            optional=True,
            reason="Optional labels for health data categorization",
        ),
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="aiohttp",
            friendly_name="Async HTTP Client",
            optional=False,
            semver=">=3.8.0",
            reason="HTTP requests for health data APIs",
        ),
    ]

    sys_dependencies: List[Any] = []

    # Capabilities this extension provides
    capabilities = [
        "health_tracking",
        "fitness_data",
        "wellness_monitoring",
        "health_analysis",
        "biometric_data",
    ]

    # Database tables owned by this extension
    db_tables: List[Any] = []

    def __init__(self, **kwargs):
        # AbstractStaticExtension does not define its own __init__, so avoid
        # forwarding arbitrary kwargs up the MRO to object.__init__ (which
        # rejects them). Configuration is exposed via instance attributes
        # instead, mirroring the other AGInfrastructure extensions.
        super().__init__()

        for key, value in kwargs.items():
            setattr(self, key, value)

        # Give each instance its own copy of the mutable class-level
        # capability list so register_capability() on one instance can never
        # leak into the class default (and therefore into sibling instances).
        self.capabilities = list(type(self).capabilities)

        # Provider instance reference
        self.provider = None

    def on_initialize(self) -> bool:
        """
        Initialize the Health extension.
        """
        logger.debug("Initializing Health Extension...")

        try:
            # Create provider instance
            self._create_provider()

            # Register capabilities
            self.register_capability("health_tracking")
            self.register_capability("fitness_data")
            self.register_capability("wellness_monitoring")
            self.register_capability("health_analysis")
            self.register_capability("biometric_data")

            logger.debug("Health extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Health extension: {str(e)}")
            return False

    def _create_provider(self):
        """
        Create the appropriate health provider instance.
        """
        try:
            provider_class = self.load_provider()
            if provider_class:
                settings = getattr(self, "settings", {}) or {}
                self.provider = provider_class(
                    api_key=getattr(self, "api_key", ""),
                    agent_name=getattr(self, "agent_name", ""),
                    conversation_id=getattr(self, "conversation_id", ""),
                    conversation_name=getattr(self, "conversation_name", ""),
                    user=getattr(self, "user", ""),
                    **settings,
                )
                logger.debug("Health provider created successfully")
            else:
                logger.warning("No health provider class found")
                self.provider = None

        except Exception as e:
            logger.error(f"Failed to initialize health provider: {str(e)}")
            self.provider = None

    def load_provider(self):
        """
        Load the appropriate provider class for this extension.
        This is a placeholder method that should be implemented based on your provider loading logic.
        """
        # This should be implemented based on your specific provider loading mechanism
        return None

    def get_capabilities(self) -> Set[str]:
        """Return the capabilities this extension provides."""
        return set(self.capabilities)

    def register_capability(self, capability: str):
        """Register a new capability."""
        if capability not in self.capabilities:
            self.capabilities.append(capability)

    def get_registered_capabilities(self) -> Set[str]:
        """Return currently registered capabilities."""
        return set(self.capabilities)

    def has_capability(self, capability: str) -> bool:
        """Check if this extension has a specific capability."""
        return capability in self.capabilities

    @ability("track_health_data")
    async def track_health_data(
        self, data_type: str = "", value: str = "", timestamp: str = ""
    ) -> str:
        """
        Track a single health data point (e.g. steps, weight, heart rate).

        Args:
            data_type: The kind of health metric being recorded (e.g. "steps").
            value: The value to record for the metric.
            timestamp: ISO-8601 timestamp for the reading. Defaults to "" and
                is interpreted by the provider as "now".
        """
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.track_health_data(data_type, value, timestamp)
        except Exception as e:
            return f"Failed to track health data: {str(e)}"

    @ability("get_health_summary")
    async def get_health_summary(self, user: str = "", period: str = "week") -> str:
        """
        Get a summary of tracked health data for a user over a period.

        Args:
            user: The user identifier to summarize health data for.
            period: The time window to summarize (e.g. "day", "week", "month", "year").
        """
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.get_health_summary(user, period)
        except Exception as e:
            return f"Failed to get health summary: {str(e)}"

    @ability("analyze_fitness_trends")
    async def analyze_fitness_trends(
        self, user: str = "", metric: str = "steps"
    ) -> str:
        """
        Analyze fitness trends for a user across a given metric.

        Args:
            user: The user identifier to analyze trends for.
            metric: The fitness metric to analyze (e.g. "steps", "calories").
        """
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.analyze_fitness_trends(user, metric)
        except Exception as e:
            return f"Failed to analyze fitness trends: {str(e)}"

    async def _no_provider_warning(self, *args, **kwargs) -> str:
        """Return a warning message when no provider is configured."""
        return "Health provider not configured. Please check your configuration."

    def get_required_permissions(self) -> List[str]:
        """Return the list of permissions required by this extension."""
        return [
            "health:read",
            "health:write",
            "health:analyze",
            "fitness:track",
        ]

    def on_start(self) -> bool:
        """
        Start the Health extension.
        """
        try:
            logger.debug("Health extension started successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to start Health extension: {e}")
            return False

    def on_stop(self) -> bool:
        """
        Stop the Health extension.
        """
        try:
            if self.provider:
                # Clean up provider resources if needed
                self.provider = None

            logger.debug("Health extension stopped successfully")
            return True
        except Exception as e:
            logger.error(f"Error stopping Health extension: {e}")
            return False

    def on_startup(self):
        """
        Called during application startup.
        """
        logger.debug("Health extension startup hook called")

    def on_shutdown(self):
        """
        Called during application shutdown.
        """
        logger.debug("Health extension shutdown hook called")

    def validate_config(self) -> List[str]:
        """
        Validate the extension configuration.
        """
        issues = []

        settings = getattr(self, "settings", None)
        if not settings:
            issues.append("Extension settings not provided")

        return issues
