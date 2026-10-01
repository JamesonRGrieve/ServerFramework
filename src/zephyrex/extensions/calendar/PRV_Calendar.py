from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


class AbstractCalendarProvider(ABC):
    """
    Abstract base class for all calendar service providers used by the
    Calendar extension (Google Calendar, Microsoft 365 Calendar, GitHub
    Projects, Calendly).

    Concrete providers are lightweight, directly-instantiated clients that
    hold their own credentials/config (access token, timezone, API URI) and
    expose synchronous scheduling operations. EXT_Calendar's async abilities
    call into these methods directly (no await) so a provider's return
    values can be plain values rather than awaitables.
    """

    def __init__(
        self,
        api_key: str = "",
        api_uri: str = "",
        access_token: str = "",
        timezone: str = "UTC",
        extension_id: Optional[str] = None,
        agent_name: str = "",
        ApiClient: Optional[Any] = None,
        conversation_name: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        """
        Initialize the calendar provider with common configuration.
        """
        self.api_key = api_key
        self.api_uri = api_uri
        self.access_token = access_token
        self.timezone = timezone
        self.extension_id = extension_id
        self.agent_name = agent_name
        self.ApiClient = ApiClient
        self.conversation_name = conversation_name
        self.settings: Dict[str, Any] = kwargs

        # Set up common calendar commands that all providers need to implement
        self.commands = {
            f"Get {self.get_platform_name()} Calendar Events": self.get_events,
            f"Create {self.get_platform_name()} Calendar Event": self.create_event,
            f"Update {self.get_platform_name()} Calendar Event": self.update_event,
            f"Delete {self.get_platform_name()} Calendar Event": self.delete_event,
            f"Find {self.get_platform_name()} Available Timeslots": self.get_available_timeslots,
        }

    @abstractmethod
    def get_events(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        max_events: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Get calendar events within a date range.
        """

    @abstractmethod
    def create_event(
        self,
        subject: str,
        start_time: datetime,
        end_time: datetime,
        location: Optional[str] = None,
        attendees: Optional[List[str]] = None,
        description: Optional[str] = None,
        is_online_meeting: bool = False,
    ) -> Dict[str, Any]:
        """
        Create a new calendar event.
        """

    @abstractmethod
    def update_event(
        self,
        event_id: str,
        subject: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        location: Optional[str] = None,
        attendees: Optional[List[str]] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update an existing calendar event.
        """

    @abstractmethod
    def delete_event(self, event_id: str) -> str:
        """
        Delete a calendar event.
        """

    @abstractmethod
    def get_available_timeslots(
        self,
        start_date: datetime,
        num_days: int = 7,
        work_day_start: str = "09:00",
        work_day_end: str = "17:00",
        duration_minutes: int = 30,
        buffer_minutes: int = 0,
    ) -> List[Dict[str, str]]:
        """
        Find available time slots over a specified number of days.
        """

    @abstractmethod
    def check_time_availability(
        self, start_time: datetime, end_time: datetime
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Check if a specific time slot is available.
        """

    @abstractmethod
    def get_platform_name(self) -> str:
        """
        Get the name of the calendar platform this provider interacts with.
        """

    @staticmethod
    def services() -> List[str]:
        """
        Return a list of services provided by this provider.
        """
        return ["calendar", "scheduling", "events"]

    def get_extension_info(self) -> Dict[str, Any]:
        """
        Get information about the calendar extension.
        """
        return {
            "name": "Calendar",
            "platform": self.get_platform_name(),
            "description": f"Calendar extension for {self.get_platform_name()}",
        }
