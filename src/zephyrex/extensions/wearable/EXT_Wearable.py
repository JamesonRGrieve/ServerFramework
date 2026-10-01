from typing import Any, Dict, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension, ability
from zephyrex.lib.Dependencies import EXT_Dependency, PIP_Dependency
from zephyrex.lib.Logging import logger


class EXT_Wearable(AbstractStaticExtension):
    """
    Wearable devices extension for AGInfrastructure.

    Provides capabilities for collecting health data, fitness metrics, and
    device management across various wearable platforms (currently Apple
    Health and FitBit, selected via the ``device_platform`` setting).

    Component loading (DB, BLL, EP) is handled automatically by the import
    system based on file naming conventions.
    """

    # Extension metadata
    name = "wearable"
    version = "1.0.0"
    description = "Integrates with various wearable health devices"

    # Define dependencies
    ext_dependencies = [
        EXT_Dependency(
            name="oauth",
            friendly_name="OAuth Extension",
            optional=True,
            reason="OAuth authentication for wearable device APIs",
        ),
        EXT_Dependency(
            name="labels",
            friendly_name="Labels Extension",
            optional=True,
            reason="Optional labels for wearable data categorization",
        ),
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="requests",
            friendly_name="HTTP Requests Library",
            optional=False,
            semver=">=2.28.0",
            reason="HTTP requests for wearable device APIs",
        ),
    ]

    sys_dependencies: List[Any] = []

    # Define what capabilities this extension provides
    capabilities = [
        "health_data_collection",
        "fitness_tracking",
        "device_management",
        "data_synchronization",
        "wearable_analytics",
    ]

    # Define database tables
    db_tables: List[Any] = []

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()

        # Free-form settings bag (device_platform, api_key, per-provider
        # config, ...). Stored per-instance so constructing several
        # extensions with different settings never cross-contaminates.
        self.settings: Dict[str, Any] = dict(kwargs)

        # Provider instance reference
        self.provider: Optional[Any] = None

        # Give each instance its own copy of the mutable class-level
        # capability list so register_capability() on one instance can
        # never leak into the class default (and therefore into sibling
        # instances) — mirrors EXT_Automotive/EXT_SMS.
        self.capabilities = list(type(self).capabilities)

    def on_initialize(self) -> bool:
        """
        Initialize the Wearable extension.
        """
        logger.debug("Initializing Wearable Extension...")

        try:
            # Create provider instance
            self._create_provider()

            # Register capabilities
            for capability in (
                "health_data_collection",
                "fitness_tracking",
                "device_management",
                "data_synchronization",
                "wearable_analytics",
            ):
                self.register_capability(capability)

            logger.debug("Wearable extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Wearable extension: {str(e)}")
            return False

    def _create_provider(self) -> None:
        """
        Create the appropriate wearable provider instance based on the
        configured ``device_platform`` setting.
        """
        device_platform = (
            self.settings.get("device_platform", "") if self.settings else ""
        )

        # Pull out the settings the provider ABC consumes explicitly so the
        # remainder can be forwarded as **kwargs without a duplicate-keyword
        # collision.
        provider_kwargs = dict(self.settings)
        provider_kwargs.pop("device_platform", None)
        api_key = provider_kwargs.pop("api_key", "")
        conversation_directory = provider_kwargs.pop("conversation_id", "")

        try:
            if device_platform == "apple":
                from zephyrex.extensions.wearable.PRV_Apple import AppleHealthProvider

                self.provider = AppleHealthProvider(
                    api_key=api_key,
                    extension_id=self.name,
                    conversation_directory=conversation_directory,
                    **provider_kwargs,
                )
                logger.debug("Wearable provider for apple created successfully")
            elif device_platform == "fitbit":
                from zephyrex.extensions.wearable.PRV_FitBit import FitBitProvider

                self.provider = FitBitProvider(
                    api_key=api_key,
                    extension_id=self.name,
                    conversation_directory=conversation_directory,
                    **provider_kwargs,
                )
                logger.debug("Wearable provider for fitbit created successfully")
            else:
                logger.warning(
                    f"No wearable provider configured for platform: {device_platform!r}"
                )
                self.provider = None

        except ImportError as e:
            logger.warning(
                f"Could not import wearable provider for {device_platform}: {e}"
            )
            self.provider = None
        except Exception as e:
            logger.error(f"Failed to initialize wearable provider: {str(e)}")
            self.provider = None

    def get_capabilities(self) -> Set[str]:
        """Return the capabilities this extension provides."""
        return set(self.capabilities)

    def register_capability(self, capability: str) -> None:
        """Register a new capability."""
        if capability not in self.capabilities:
            self.capabilities.append(capability)

    def get_registered_capabilities(self) -> Set[str]:
        """Return currently registered capabilities."""
        return set(self.capabilities)

    @ability("get_health_data")
    async def get_health_data(
        self, device_type: str = "all", data_type: str = "steps", period: str = "today"
    ) -> Dict[str, Any]:
        """
        Get health data from wearable devices.
        """
        if not self.provider:
            return {"error": await self._no_provider_warning()}

        try:
            if hasattr(self.provider, "get_health_data"):
                # Provider methods are synchronous (see PRV_Wearable) — no await.
                return self.provider.get_health_data(device_type, data_type, period)
            else:
                return {"error": "Health data retrieval not supported by this provider"}
        except Exception as e:
            return {"error": f"Failed to get health data: {str(e)}"}

    @ability("sync_devices")
    async def sync_devices(self) -> str:
        """
        Synchronize data from all connected wearable devices.
        """
        if not self.provider:
            return await self._no_provider_warning()

        try:
            if hasattr(self.provider, "sync_devices"):
                return self.provider.sync_devices()
            else:
                return "Device synchronization not supported by this provider"
        except Exception as e:
            return f"Failed to sync devices: {str(e)}"

    @ability("get_device_status")
    async def get_device_status(self, device_id: str = "") -> Dict[str, Any]:
        """
        Get status and information about connected wearable devices.
        """
        if not self.provider:
            return {"error": await self._no_provider_warning()}

        try:
            if hasattr(self.provider, "get_device_status"):
                return self.provider.get_device_status(device_id)
            else:
                return {"error": "Device status not supported by this provider"}
        except Exception as e:
            return {"error": f"Failed to get device status: {str(e)}"}

    @ability("analyze_fitness_trends")
    async def analyze_fitness_trends(
        self, metric: str = "steps", period: str = "week"
    ) -> Dict[str, Any]:
        """
        Analyze fitness trends from wearable data.
        """
        if not self.provider:
            return {"error": await self._no_provider_warning()}

        try:
            if hasattr(self.provider, "analyze_trends"):
                return self.provider.analyze_trends(metric, period)
            else:
                return {
                    "error": "Fitness trend analysis not supported by this provider"
                }
        except Exception as e:
            return {"error": f"Failed to analyze fitness trends: {str(e)}"}

    async def _no_provider_warning(self, *args: Any, **kwargs: Any) -> str:
        """Return a warning message when no provider is configured."""
        return "Wearable provider not configured. Please check your configuration."

    def get_required_permissions(self) -> List[str]:
        """Return the list of permissions required by this extension."""
        return [
            "wearable:read",
            "wearable:sync",
            "health:data",
            "fitness:track",
        ]

    def on_start(self) -> bool:
        """
        Start the Wearable extension.
        """
        try:
            logger.debug("Wearable extension started successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to start Wearable extension: {e}")
            return False

    def on_stop(self) -> bool:
        """
        Stop the Wearable extension.
        """
        try:
            if self.provider:
                # Clean up provider resources if needed
                self.provider = None

            logger.debug("Wearable extension stopped successfully")
            return True
        except Exception as e:
            logger.error(f"Error stopping Wearable extension: {e}")
            return False

    def validate_config(self) -> List[str]:
        """
        Validate the extension configuration.
        """
        issues = []

        device_platform = (
            self.settings.get("device_platform", "") if self.settings else ""
        )
        if not device_platform:
            issues.append("Wearable device platform not specified")

        return issues

    def on_startup(self) -> None:
        """
        Called during application startup.
        """
        logger.debug("Wearable extension startup hook called")

    def on_shutdown(self) -> None:
        """
        Called during application shutdown.
        """
        logger.debug("Wearable extension shutdown hook called")

    def has_capability(self, capability: str) -> bool:
        """Check if this extension has a specific capability."""
        return capability in self.capabilities
