from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension, ability
from zephyrex.lib.Dependencies import EXT_Dependency, PIP_Dependency
from zephyrex.lib.Logging import logger


class EXT_Calendar(AbstractStaticExtension):
    """
    Calendar extension for AGInfrastructure.
    Provides scheduling and event management for various calendar platforms
    including Google Calendar and Microsoft 365 Calendar.

    Component loading (DB, BLL, EP) is handled automatically by the import system
    based on file naming conventions.
    """

    # Extension metadata
    name = "calendar"
    version = "1.0.0"
    description = "Calendar extension for scheduling and event management"

    # Define dependencies
    ext_dependencies = [
        EXT_Dependency(
            name="core",
            friendly_name="Core Extension",
            optional=False,
            reason="Required for base calendar functionality",
        ),
        EXT_Dependency(
            name="oauth",
            friendly_name="OAuth Extension",
            optional=False,
            reason="Required for calendar platform authentication",
        ),
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="google-api-python-client",
            friendly_name="Google API Client",
            optional=True,
            reason="Required for Google Calendar integration",
            semver=">=2.0.0",
        ),
        PIP_Dependency(
            name="microsoft-graph-api",
            friendly_name="Microsoft Graph API",
            optional=True,
            reason="Required for Microsoft 365 Calendar integration",
            semver=">=1.0.0",
        ),
        PIP_Dependency(
            name="requests",
            friendly_name="HTTP Requests Library",
            optional=False,
            reason="Required for API communications",
            semver=">=2.28.0",
        ),
        PIP_Dependency(
            name="pytz",
            friendly_name="Timezone Library",
            optional=False,
            reason="Required for timezone handling",
            semver=">=2021.1",
        ),
    ]

    sys_dependencies = []

    # Define database tables (none for this extension)
    db_tables = []

    # Define what capabilities this extension provides
    capabilities = [
        "event_management",
        "calendar_scheduling",
        "availability_checking",
        "meeting_creation",
        "calendar_synchronization",
        "timezone_handling",
    ]

    def __init__(
        self,
        calendar_platform: str = "google",
        access_token: str = "",
        timezone: str = "UTC",
        **kwargs,
    ):
        """
        Initialize the calendar extension.
        """
        super().__init__(**kwargs)

        self.calendar_platform = calendar_platform.lower()
        self.access_token = access_token
        self.timezone = timezone
        self.provider = None
        self.commands = {}

    def on_initialize(self) -> bool:
        """Initialize the calendar extension with the appropriate provider."""
        logger.debug("Initializing Calendar Extension...")

        try:
            self._create_provider()
            self._register_commands()

            # Register capabilities
            for capability in self.capabilities:
                self.register_capability(capability)

            logger.debug("Calendar extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Calendar extension: {str(e)}")
            return False

    def _create_provider(self):
        """Create the appropriate calendar provider based on calendar_platform."""
        try:
            if self.calendar_platform == "google":
                try:
                    from zephyrex.extensions.calendar.Google import (
                        GoogleCalendarProvider,
                    )

                    self.provider = GoogleCalendarProvider(
                        access_token=self.access_token,
                        timezone=self.timezone,
                        extension_id=self.name,
                        **getattr(self, "settings", {}),
                    )
                    logger.debug("Google Calendar provider created successfully")

                except ImportError as e:
                    logger.warning(f"Could not import Google Calendar provider: {e}")
                    self.provider = None

            elif self.calendar_platform == "microsoft":
                try:
                    from zephyrex.extensions.calendar.Microsoft import (
                        MicrosoftCalendarProvider,
                    )

                    self.provider = MicrosoftCalendarProvider(
                        access_token=self.access_token,
                        timezone=self.timezone,
                        extension_id=self.name,
                        **getattr(self, "settings", {}),
                    )
                    logger.debug("Microsoft Calendar provider created successfully")

                except ImportError as e:
                    logger.warning(f"Could not import Microsoft Calendar provider: {e}")
                    self.provider = None

            else:
                logger.error(f"Unsupported calendar platform: {self.calendar_platform}")
                self.provider = None

        except Exception as e:
            logger.error(f"Error creating calendar provider: {str(e)}")
            self.provider = None

    def _register_commands(self):
        """Register commands based on available provider."""
        if self.provider and hasattr(self.provider, "commands"):
            self.commands = self.provider.commands
        else:
            # Provide placeholder commands that warn about missing provider
            platform_name = self.calendar_platform.upper()
            self.commands = {
                f"Get {platform_name} Calendar Events": self._no_provider_warning,
                f"Create {platform_name} Calendar Event": self._no_provider_warning,
                f"Update {platform_name} Calendar Event": self._no_provider_warning,
                f"Delete {platform_name} Calendar Event": self._no_provider_warning,
                f"Find {platform_name} Available Timeslots": self._no_provider_warning,
            }

    async def _no_provider_warning(self, *args, **kwargs) -> str:
        """Warning message when a provider is not available."""
        return f"No calendar provider available for {self.calendar_platform}. Please check your configuration."

    def register_capability(self, capability: str):
        """Register a new capability."""
        if capability not in self.capabilities:
            self.capabilities.append(capability)

    def get_registered_capabilities(self) -> Set[str]:
        """Return currently registered capabilities."""
        return set(self.capabilities)

    def get_capabilities(self) -> Set[str]:
        """Return the capabilities this extension provides."""
        return set(self.capabilities)

    @ability("get_events")
    async def get_events(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        max_events: int = 10,
    ) -> Dict[str, Any]:
        """Get calendar events within a date range."""
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            events = self.provider.get_events(start_date, end_date, max_events)
            return {
                "success": True,
                "events": events,
                "count": len(events) if events else 0,
            }

        except Exception as e:
            logger.error(f"Error retrieving calendar events: {e}")
            return {
                "success": False,
                "message": f"Error retrieving calendar events: {str(e)}",
            }

    @ability("create_event")
    async def create_event(
        self,
        subject: str,
        start_time: datetime,
        end_time: datetime,
        location: Optional[str] = None,
        attendees: Optional[List[str]] = None,
        description: Optional[str] = None,
        is_online_meeting: bool = False,
    ) -> Dict[str, Any]:
        """Create a new calendar event."""
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            result = self.provider.create_event(
                subject,
                start_time,
                end_time,
                location,
                attendees,
                description,
                is_online_meeting,
            )
            return {
                "success": True,
                "result": result,
                "subject": subject,
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
            }

        except Exception as e:
            logger.error(f"Error creating calendar event: {e}")
            return {
                "success": False,
                "message": f"Error creating calendar event: {str(e)}",
            }

    @ability("update_event")
    async def update_event(
        self,
        event_id: str,
        subject: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        location: Optional[str] = None,
        attendees: Optional[List[str]] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update an existing calendar event."""
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            result = self.provider.update_event(
                event_id,
                subject,
                start_time,
                end_time,
                location,
                attendees,
                description,
            )
            return {"success": True, "result": result, "event_id": event_id}

        except Exception as e:
            logger.error(f"Error updating calendar event: {e}")
            return {
                "success": False,
                "message": f"Error updating calendar event: {str(e)}",
            }

    @ability("delete_event")
    async def delete_event(self, event_id: str) -> Dict[str, Any]:
        """Delete a calendar event."""
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            result = self.provider.delete_event(event_id)
            return {"success": True, "result": result, "event_id": event_id}

        except Exception as e:
            logger.error(f"Error deleting calendar event: {e}")
            return {
                "success": False,
                "message": f"Error deleting calendar event: {str(e)}",
            }

    @ability("get_available_timeslots")
    async def get_available_timeslots(
        self,
        start_date: datetime,
        num_days: int = 7,
        work_day_start: str = "09:00",
        work_day_end: str = "17:00",
        duration_minutes: int = 30,
        buffer_minutes: int = 0,
    ) -> Dict[str, Any]:
        """Find available time slots over a specified number of days."""
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            timeslots = self.provider.get_available_timeslots(
                start_date,
                num_days,
                work_day_start,
                work_day_end,
                duration_minutes,
                buffer_minutes,
            )
            return {
                "success": True,
                "timeslots": timeslots,
                "count": len(timeslots) if timeslots else 0,
            }

        except Exception as e:
            logger.error(f"Error finding available timeslots: {e}")
            return {
                "success": False,
                "message": f"Error finding available timeslots: {str(e)}",
            }

    def on_start(self) -> bool:
        """Start the Calendar extension."""
        try:
            logger.debug("Calendar extension started successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to start Calendar extension: {e}")
            return False

    def on_stop(self) -> bool:
        """Stop the Calendar extension."""
        try:
            if self.provider:
                # Clean up provider resources if needed
                self.provider = None

            logger.debug("Calendar extension stopped successfully")
            return True
        except Exception as e:
            logger.error(f"Error stopping Calendar extension: {e}")
            return False

    def validate_config(self) -> List[str]:
        """Validate the extension configuration."""
        issues = []

        # Check for required Python packages
        try:
            import requests
        except ImportError:
            issues.append(
                "Requests library not installed - API communications will not work"
            )

        try:
            import pytz
        except ImportError:
            issues.append(
                "Pytz library not installed - timezone handling will not work"
            )

        # Platform-specific validation
        if not self.calendar_platform:
            issues.append("Calendar platform not specified")
        elif self.calendar_platform not in ["google", "microsoft"]:
            issues.append(f"Unsupported calendar platform: {self.calendar_platform}")

        # Check required credentials
        if not self.access_token:
            issues.append(
                f"{self.calendar_platform.title()} Calendar requires access token"
            )

        # Platform-specific library checks
        if self.calendar_platform == "google":
            try:
                import googleapiclient
            except ImportError:
                issues.append(
                    "Google API client not installed - Google Calendar will not work"
                )

        elif self.calendar_platform == "microsoft":
            try:
                import msal
            except ImportError:
                issues.append(
                    "MSAL library not installed - Microsoft Calendar will not work"
                )

        return issues

    def get_required_permissions(self) -> List[str]:
        """Return the list of permissions required by this extension."""
        return [
            "calendar:read",
            "calendar:write",
            "calendar:events:create",
            "calendar:events:update",
            "calendar:events:delete",
            "calendar:availability:read",
        ]

    def on_startup(self):
        """Called during application startup."""
        logger.debug("Calendar extension startup hook called")

    def on_shutdown(self):
        """Called during application shutdown."""
        logger.debug("Calendar extension shutdown hook called")

    def has_capability(self, capability: str) -> bool:
        """Check if this extension has a specific capability."""
        return capability in self.capabilities
