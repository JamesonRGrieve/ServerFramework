from typing import Any, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension, ability
from zephyrex.lib.Dependencies import EXT_Dependency, PIP_Dependency
from zephyrex.lib.Logging import logger


class EXT_Automotive(AbstractStaticExtension):
    """
    Automotive extension for AGInfrastructure.
    Provides smart car integration functionality for various vehicle platforms,
    currently supporting Tesla vehicles with plans for additional manufacturers.

    Component loading (DB, BLL, EP) is handled automatically by the import system
    based on file naming conventions.
    """

    # Extension metadata
    name = "automotive"
    version = "1.0.0"
    description = "Automotive extension for smart car integrations"

    # Define dependencies
    ext_dependencies = [
        EXT_Dependency(
            name="core",
            friendly_name="Core Extension",
            optional=False,
            reason="Required for base automotive functionality",
        ),
        EXT_Dependency(
            name="oauth",
            friendly_name="OAuth Extension",
            optional=True,
            reason="Optional OAuth integration for authenticated vehicle platforms",
        ),
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="requests",
            friendly_name="HTTP Requests Library",
            optional=False,
            reason="Required for API communications with vehicle platforms",
            semver=">=2.28.0",
        ),
        PIP_Dependency(
            name="pydantic",
            friendly_name="Pydantic Data Validation",
            optional=False,
            reason="Required for vehicle data validation",
            semver=">=2.0.0",
        ),
        PIP_Dependency(
            name="cryptography",
            friendly_name="Cryptography Library",
            optional=True,
            reason="Required for secure vehicle communications",
            semver=">=3.0.0",
        ),
    ]

    sys_dependencies: List[Any] = []

    # Define database tables (none for this extension)
    db_tables: List[Any] = []

    # Define what capabilities this extension provides
    capabilities = [
        "vehicle_info",
        "door_control",
        "climate_control",
        "charging_control",
        "navigation",
        "vehicle_monitoring",
    ]

    def __init__(
        self,
        vehicle_platform: str = "tesla",
        vehicle_id: str = "",
        access_token: str = "",
        **kwargs: Any,
    ):
        """
        Initialize the automotive extension.
        """
        super().__init__(**kwargs)

        self.vehicle_platform = vehicle_platform.lower()
        self.vehicle_id = vehicle_id
        self.access_token = access_token
        self.provider = None
        self.commands: Dict[str, Any] = {}

        # Give each instance its own copy of the mutable class-level
        # capability list so register_capability() on one instance can never
        # leak into the class default (and therefore into sibling instances).
        self.capabilities = list(type(self).capabilities)

    def on_initialize(self) -> bool:
        """Initialize the automotive extension with the appropriate provider."""
        logger.debug("Initializing Automotive Extension...")

        try:
            self._create_provider()
            self._register_commands()

            # Register capabilities
            for capability in self.capabilities:
                self.register_capability(capability)

            logger.debug("Automotive extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Automotive extension: {str(e)}")
            return False

    def _create_provider(self) -> None:
        """Create the appropriate automotive provider based on vehicle_platform."""
        try:
            if self.vehicle_platform == "tesla":
                from zephyrex.extensions.automotive.PRV_Tesla import TeslaProvider

                self.provider = TeslaProvider(
                    api_key=getattr(self, "api_key", ""),
                    vehicle_id=self.vehicle_id,
                    access_token=self.access_token,
                    extension_id=self.name,
                    conversation_directory=getattr(self, "conversation_id", ""),
                )
                logger.debug(
                    f"Automotive provider for {self.vehicle_platform} created successfully"
                )
            else:
                logger.error(f"Unsupported vehicle platform: {self.vehicle_platform}")
                self.provider = None

        except ImportError as e:
            logger.warning(
                f"Could not import automotive provider for {self.vehicle_platform}: {e}"
            )
            self.provider = None
        except Exception as e:
            logger.error(f"Error creating automotive provider: {str(e)}")
            self.provider = None

    def _register_commands(self) -> None:
        """Register commands based on available provider."""
        if self.provider and hasattr(self.provider, "commands"):
            self.commands = self.provider.commands
        else:
            # Provide placeholder commands that warn about missing provider
            platform_name = self.vehicle_platform.upper()
            self.commands = {
                f"Get {platform_name} Vehicle Info": self._no_provider_warning,
                f"Control {platform_name} Vehicle": self._no_provider_warning,
            }

    async def _no_provider_warning(self, *args: Any, **kwargs: Any) -> str:
        """Warning message when a provider is not available."""
        return f"No automotive provider available for {self.vehicle_platform}. Please check your configuration."

    def register_capability(self, capability: str) -> None:
        """Register a new capability."""
        if capability not in self.capabilities:
            self.capabilities.append(capability)

    def get_registered_capabilities(self) -> Set[str]:
        """Return currently registered capabilities."""
        return set(self.capabilities)

    def get_capabilities(self) -> Set[str]:
        """Return the capabilities this extension provides."""
        return set(self.capabilities)

    @ability("get_vehicle_info")
    async def get_vehicle_info(self) -> str:
        """Get information about the vehicle."""
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.get_vehicle_info()
        except Exception as e:
            logger.error(f"Error getting vehicle info: {e}")
            return f"Error getting vehicle info: {str(e)}"

    @ability("lock_doors")
    async def lock_doors(self) -> str:
        """Lock the vehicle doors."""
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.lock_doors()
        except Exception as e:
            logger.error(f"Error locking doors: {e}")
            return f"Error locking doors: {str(e)}"

    @ability("unlock_doors")
    async def unlock_doors(self) -> str:
        """Unlock the vehicle doors."""
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.unlock_doors()
        except Exception as e:
            logger.error(f"Error unlocking doors: {e}")
            return f"Error unlocking doors: {str(e)}"

    @ability("set_climate")
    async def set_climate(self, temperature: float, enabled: bool = True) -> str:
        """Set the climate temperature and state."""
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.set_climate(temperature, enabled)
        except Exception as e:
            logger.error(f"Error setting climate: {e}")
            return f"Error setting climate: {str(e)}"

    @ability("start_charging")
    async def start_charging(self) -> str:
        """Start vehicle charging."""
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.start_charging()
        except Exception as e:
            logger.error(f"Error starting charging: {e}")
            return f"Error starting charging: {str(e)}"

    @ability("stop_charging")
    async def stop_charging(self) -> str:
        """Stop vehicle charging."""
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.stop_charging()
        except Exception as e:
            logger.error(f"Error stopping charging: {e}")
            return f"Error stopping charging: {str(e)}"

    @ability("navigate_to")
    async def navigate_to(self, address: str) -> str:
        """Navigate to an address."""
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.navigate_to(address)
        except Exception as e:
            logger.error(f"Error navigating to address: {e}")
            return f"Error navigating to address: {str(e)}"

    def on_start(self) -> bool:
        """Start the Automotive extension."""
        try:
            logger.debug("Automotive extension started successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to start Automotive extension: {e}")
            return False

    def on_stop(self) -> bool:
        """Stop the Automotive extension."""
        try:
            if self.provider:
                # Clean up provider resources if needed
                self.provider = None

            logger.debug("Automotive extension stopped successfully")
            return True
        except Exception as e:
            logger.error(f"Error stopping Automotive extension: {e}")
            return False

    def validate_config(self) -> List[str]:
        """Validate the extension configuration."""
        issues = []

        # Check for required Python packages
        try:
            import requests  # noqa: F401
        except ImportError:
            issues.append(
                "Requests library not installed - API communications will not work"
            )

        try:
            import pydantic  # noqa: F401
        except ImportError:
            issues.append(
                "Pydantic library not installed - data validation will not work"
            )

        # Platform-specific validation
        if not self.vehicle_platform:
            issues.append("Vehicle platform not specified")
        elif self.vehicle_platform not in ["tesla"]:
            issues.append(f"Unsupported vehicle platform: {self.vehicle_platform}")

        # Check required credentials based on platform
        if self.vehicle_platform == "tesla":
            if not self.access_token:
                issues.append("Tesla integration requires access token")
            if not self.vehicle_id:
                issues.append("Tesla integration requires vehicle ID")

        return issues

    def get_required_permissions(self) -> List[str]:
        """Return the list of permissions required by this extension."""
        return [
            "vehicle:read",
            "vehicle:control",
            "vehicle:climate",
            "vehicle:charging",
            "vehicle:navigation",
        ]

    def on_startup(self) -> None:
        """Called during application startup."""
        logger.debug("Automotive extension startup hook called")

    def on_shutdown(self) -> None:
        """Called during application shutdown."""
        logger.debug("Automotive extension shutdown hook called")

    def has_capability(self, capability: str) -> bool:
        """Check if this extension has a specific capability."""
        return capability in self.capabilities
