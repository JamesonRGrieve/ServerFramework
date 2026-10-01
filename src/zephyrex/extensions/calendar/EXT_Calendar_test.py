from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from zephyrex.extensions.calendar.EXT_Calendar import EXT_Calendar


class TestCalendarExtension:
    """Test cases for Calendar Extension."""

    @pytest.fixture
    def extension(self):
        """Create a CalendarExtension instance for testing."""
        return EXT_Calendar()

    def test_extension_metadata(self, extension):
        """Test extension metadata is correctly set."""
        assert extension.name == "calendar"
        assert extension.version == "1.0.0"
        assert "calendar" in extension.description.lower()
        assert "scheduling" in extension.description.lower()

    def test_dependencies(self, extension):
        """Test extension dependencies are properly defined."""
        # Check extension dependencies
        ext_deps = {dep.name for dep in extension.ext_dependencies}
        assert "core" in ext_deps
        assert "oauth" in ext_deps

        # Check pip dependencies
        pip_deps = {dep.name for dep in extension.pip_dependencies}
        assert "google-api-python-client" in pip_deps
        assert "microsoft-graph-api" in pip_deps
        assert "requests" in pip_deps
        assert "pytz" in pip_deps

        # Check sys dependencies
        assert isinstance(extension.sys_dependencies, list)

    def test_capabilities(self, extension):
        """Test extension capabilities are properly defined."""
        expected_capabilities = {
            "event_management",
            "calendar_scheduling",
            "availability_checking",
            "meeting_creation",
            "calendar_synchronization",
            "timezone_handling",
        }
        assert set(extension.capabilities) == expected_capabilities

    def test_initialization(self, extension):
        """Test extension initialization."""
        assert hasattr(extension, "calendar_platform")
        assert hasattr(extension, "access_token")
        assert hasattr(extension, "timezone")
        assert hasattr(extension, "provider")
        assert hasattr(extension, "commands")

    def test_default_configuration(self, extension):
        """Test default configuration values."""
        assert extension.calendar_platform == "google"
        assert extension.access_token == ""
        assert extension.timezone == "UTC"

    @patch("zephyrex.extensions.calendar.EXT_Calendar.logger")
    def test_on_initialize_success(self, mock_logger, extension):
        """Test successful extension initialization."""
        with patch.object(extension, "_create_provider"), patch.object(
            extension, "_register_commands"
        ), patch.object(extension, "register_capability"):

            result = extension.on_initialize()
            assert result is True
            mock_logger.debug.assert_called()

    @patch("zephyrex.extensions.calendar.EXT_Calendar.logger")
    def test_on_initialize_failure(self, mock_logger, extension):
        """Test extension initialization failure."""
        with patch.object(
            extension, "_create_provider", side_effect=Exception("Test error")
        ):
            result = extension.on_initialize()
            assert result is False
            mock_logger.error.assert_called()

    def test_create_provider_google(self, extension):
        """Test Google Calendar provider creation."""
        extension.calendar_platform = "google"

        with patch(
            "zephyrex.extensions.calendar.Google.GoogleCalendarProvider"
        ) as mock_provider:
            mock_instance = MagicMock()
            mock_provider.return_value = mock_instance

            extension._create_provider()

            assert extension.provider == mock_instance
            mock_provider.assert_called_once()

    def test_create_provider_microsoft(self, extension):
        """Test Microsoft Calendar provider creation."""
        extension.calendar_platform = "microsoft"

        with patch(
            "zephyrex.extensions.calendar.Microsoft.MicrosoftCalendarProvider"
        ) as mock_provider:
            mock_instance = MagicMock()
            mock_provider.return_value = mock_instance

            extension._create_provider()

            assert extension.provider == mock_instance
            mock_provider.assert_called_once()

    def test_create_provider_unsupported_platform(self, extension):
        """Test provider creation with unsupported platform."""
        extension.calendar_platform = "unsupported_platform"

        with patch("zephyrex.extensions.calendar.EXT_Calendar.logger") as mock_logger:
            extension._create_provider()

            assert extension.provider is None
            mock_logger.error.assert_called_with(
                "Unsupported calendar platform: unsupported_platform"
            )

    def test_create_provider_import_error(self, extension):
        """Test provider creation with import error."""
        extension.calendar_platform = "google"

        with patch(
            "zephyrex.extensions.calendar.Google.GoogleCalendarProvider",
            side_effect=ImportError("Module not found"),
        ), patch("zephyrex.extensions.calendar.EXT_Calendar.logger") as mock_logger:

            extension._create_provider()

            assert extension.provider is None
            mock_logger.warning.assert_called()

    def test_register_commands_with_provider(self, extension):
        """Test command registration when provider is available."""
        mock_provider = MagicMock()
        mock_provider.commands = {"test_command": MagicMock()}
        extension.provider = mock_provider

        extension._register_commands()

        assert extension.commands == mock_provider.commands

    def test_register_commands_without_provider(self, extension):
        """Test command registration when provider is not available."""
        extension.provider = None
        extension.calendar_platform = "google"

        extension._register_commands()

        assert len(extension.commands) == 5
        assert "Get GOOGLE Calendar Events" in extension.commands
        assert "Create GOOGLE Calendar Event" in extension.commands
        assert "Update GOOGLE Calendar Event" in extension.commands
        assert "Delete GOOGLE Calendar Event" in extension.commands
        assert "Find GOOGLE Available Timeslots" in extension.commands

    @pytest.mark.asyncio
    async def test_no_provider_warning(self, extension):
        """Test warning message when no provider is available."""
        extension.calendar_platform = "google"

        result = await extension._no_provider_warning()

        assert "No calendar provider available for google" in result

    def test_capability_management(self, extension):
        """Test capability management methods."""
        # Test register_capability
        extension.register_capability("test_capability")
        assert "test_capability" in extension.capabilities

        # Test get_registered_capabilities
        capabilities = extension.get_registered_capabilities()
        assert isinstance(capabilities, set)
        assert "test_capability" in capabilities

        # Test get_capabilities
        capabilities = extension.get_capabilities()
        assert isinstance(capabilities, set)

        # Test has_capability
        assert extension.has_capability("test_capability") is True
        assert extension.has_capability("nonexistent_capability") is False

    @pytest.mark.asyncio
    async def test_get_events_success(self, extension):
        """Test successful event retrieval."""
        mock_provider = MagicMock()
        mock_events = [
            {"id": "1", "title": "Meeting 1", "start_time": "2024-01-01T10:00:00Z"},
            {"id": "2", "title": "Meeting 2", "start_time": "2024-01-01T14:00:00Z"},
        ]
        mock_provider.get_events = MagicMock(return_value=mock_events)
        extension.provider = mock_provider

        start_date = datetime(2024, 1, 1)
        end_date = datetime(2024, 1, 2)

        result = await extension.get_events(start_date, end_date, max_events=10)

        assert result["success"] is True
        assert result["events"] == mock_events
        assert result["count"] == 2
        mock_provider.get_events.assert_called_once_with(start_date, end_date, 10)

    @pytest.mark.asyncio
    async def test_get_events_no_provider(self, extension):
        """Test event retrieval without provider."""
        extension.provider = None

        result = await extension.get_events()

        assert result["success"] is False
        assert "No calendar provider available" in result["message"]

    @pytest.mark.asyncio
    async def test_create_event_success(self, extension):
        """Test successful event creation."""
        mock_provider = MagicMock()
        mock_provider.create_event = MagicMock(
            return_value="event_created_successfully"
        )
        extension.provider = mock_provider

        start_time = datetime(2024, 1, 1, 10, 0)
        end_time = datetime(2024, 1, 1, 11, 0)

        result = await extension.create_event(
            subject="Test Meeting",
            start_time=start_time,
            end_time=end_time,
            location="Conference Room A",
            attendees=["user1@example.com", "user2@example.com"],
            description="Test meeting description",
            is_online_meeting=True,
        )

        assert result["success"] is True
        assert result["result"] == "event_created_successfully"
        assert result["subject"] == "Test Meeting"
        assert result["start_time"] == start_time.isoformat()
        assert result["end_time"] == end_time.isoformat()

        mock_provider.create_event.assert_called_once_with(
            "Test Meeting",
            start_time,
            end_time,
            "Conference Room A",
            ["user1@example.com", "user2@example.com"],
            "Test meeting description",
            True,
        )

    @pytest.mark.asyncio
    async def test_create_event_no_provider(self, extension):
        """Test event creation without provider."""
        extension.provider = None

        start_time = datetime(2024, 1, 1, 10, 0)
        end_time = datetime(2024, 1, 1, 11, 0)

        result = await extension.create_event("Test Meeting", start_time, end_time)

        assert result["success"] is False
        assert "No calendar provider available" in result["message"]

    @pytest.mark.asyncio
    async def test_update_event_success(self, extension):
        """Test successful event update."""
        mock_provider = MagicMock()
        mock_provider.update_event = MagicMock(
            return_value="event_updated_successfully"
        )
        extension.provider = mock_provider

        start_time = datetime(2024, 1, 1, 10, 0)
        end_time = datetime(2024, 1, 1, 11, 0)

        result = await extension.update_event(
            event_id="event_123",
            subject="Updated Meeting",
            start_time=start_time,
            end_time=end_time,
            location="Conference Room B",
            attendees=["user3@example.com"],
            description="Updated description",
        )

        assert result["success"] is True
        assert result["result"] == "event_updated_successfully"
        assert result["event_id"] == "event_123"

        mock_provider.update_event.assert_called_once_with(
            "event_123",
            "Updated Meeting",
            start_time,
            end_time,
            "Conference Room B",
            ["user3@example.com"],
            "Updated description",
        )

    @pytest.mark.asyncio
    async def test_update_event_no_provider(self, extension):
        """Test event update without provider."""
        extension.provider = None

        result = await extension.update_event("event_123", subject="Updated Meeting")

        assert result["success"] is False
        assert "No calendar provider available" in result["message"]

    @pytest.mark.asyncio
    async def test_delete_event_success(self, extension):
        """Test successful event deletion."""
        mock_provider = MagicMock()
        mock_provider.delete_event = MagicMock(
            return_value="event_deleted_successfully"
        )
        extension.provider = mock_provider

        result = await extension.delete_event("event_123")

        assert result["success"] is True
        assert result["result"] == "event_deleted_successfully"
        assert result["event_id"] == "event_123"
        mock_provider.delete_event.assert_called_once_with("event_123")

    @pytest.mark.asyncio
    async def test_delete_event_no_provider(self, extension):
        """Test event deletion without provider."""
        extension.provider = None

        result = await extension.delete_event("event_123")

        assert result["success"] is False
        assert "No calendar provider available" in result["message"]

    @pytest.mark.asyncio
    async def test_get_available_timeslots_success(self, extension):
        """Test successful timeslot retrieval."""
        mock_provider = MagicMock()
        mock_timeslots = [
            {"start": "2024-01-01T09:00:00Z", "end": "2024-01-01T09:30:00Z"},
            {"start": "2024-01-01T10:00:00Z", "end": "2024-01-01T10:30:00Z"},
        ]
        mock_provider.get_available_timeslots = MagicMock(return_value=mock_timeslots)
        extension.provider = mock_provider

        start_date = datetime(2024, 1, 1)

        result = await extension.get_available_timeslots(
            start_date=start_date,
            num_days=7,
            work_day_start="09:00",
            work_day_end="17:00",
            duration_minutes=30,
            buffer_minutes=15,
        )

        assert result["success"] is True
        assert result["timeslots"] == mock_timeslots
        assert result["count"] == 2

        mock_provider.get_available_timeslots.assert_called_once_with(
            start_date, 7, "09:00", "17:00", 30, 15
        )

    @pytest.mark.asyncio
    async def test_get_available_timeslots_no_provider(self, extension):
        """Test timeslot retrieval without provider."""
        extension.provider = None

        start_date = datetime(2024, 1, 1)
        result = await extension.get_available_timeslots(start_date)

        assert result["success"] is False
        assert "No calendar provider available" in result["message"]

    @pytest.mark.asyncio
    async def test_ability_error_handling(self, extension):
        """Test error handling in abilities."""
        mock_provider = MagicMock()
        mock_provider.get_events = MagicMock(side_effect=Exception("Provider error"))
        extension.provider = mock_provider

        result = await extension.get_events()

        assert result["success"] is False
        assert "Error retrieving calendar events" in result["message"]

    def test_lifecycle_methods(self, extension):
        """Test extension lifecycle methods."""
        # Test on_start
        assert extension.on_start() is True

        # Test on_stop
        extension.provider = MagicMock()
        assert extension.on_stop() is True
        assert extension.provider is None

        # Test on_startup and on_shutdown
        extension.on_startup()  # Should not raise exception
        extension.on_shutdown()  # Should not raise exception

    def test_validate_config_success(self, extension):
        """Test successful configuration validation."""
        with patch("builtins.__import__"):
            issues = extension.validate_config()
            assert isinstance(issues, list)

    def test_validate_config_missing_requests(self, extension):
        """Test configuration validation with missing requests library."""
        with patch("builtins.__import__", side_effect=ImportError("Module not found")):
            issues = extension.validate_config()

            assert len(issues) > 0
            assert any("Requests library not installed" in issue for issue in issues)

    def test_validate_config_missing_pytz(self, extension):
        """Test configuration validation with missing pytz library."""

        def mock_import(name, *args, **kwargs):
            if name == "pytz":
                raise ImportError("Module not found")
            return MagicMock()

        with patch("builtins.__import__", side_effect=mock_import):
            issues = extension.validate_config()

            assert any("Pytz library not installed" in issue for issue in issues)

    def test_validate_config_no_platform(self, extension):
        """Test configuration validation with no platform."""
        extension.calendar_platform = ""

        issues = extension.validate_config()

        assert any("Calendar platform not specified" in issue for issue in issues)

    def test_validate_config_unsupported_platform(self, extension):
        """Test configuration validation with unsupported platform."""
        extension.calendar_platform = "unsupported"

        issues = extension.validate_config()

        assert any(
            "Unsupported calendar platform: unsupported" in issue for issue in issues
        )

    def test_validate_config_no_access_token(self, extension):
        """Test configuration validation with no access token."""
        extension.access_token = ""

        issues = extension.validate_config()

        assert any("Google Calendar requires access token" in issue for issue in issues)

    def test_validate_config_google_missing_library(self, extension):
        """Test configuration validation when Google API library is missing."""
        extension.calendar_platform = "google"

        def mock_import(name, *args, **kwargs):
            if name == "googleapiclient":
                raise ImportError("Module not found")
            return MagicMock()

        with patch("builtins.__import__", side_effect=mock_import):
            issues = extension.validate_config()

            assert any("Google API client not installed" in issue for issue in issues)

    def test_validate_config_microsoft_missing_library(self, extension):
        """Test configuration validation when Microsoft library is missing."""
        extension.calendar_platform = "microsoft"

        def mock_import(name, *args, **kwargs):
            if name == "msal":
                raise ImportError("Module not found")
            return MagicMock()

        with patch("builtins.__import__", side_effect=mock_import):
            issues = extension.validate_config()

            assert any("MSAL library not installed" in issue for issue in issues)

    def test_get_required_permissions(self, extension):
        """Test required permissions."""
        permissions = extension.get_required_permissions()
        assert isinstance(permissions, list)
        assert len(permissions) > 0

        expected_permissions = [
            "calendar:read",
            "calendar:write",
            "calendar:events:create",
            "calendar:events:update",
            "calendar:events:delete",
            "calendar:availability:read",
        ]
        assert set(permissions) == set(expected_permissions)

    def test_custom_configuration(self):
        """Test extension with custom configuration."""
        extension = EXT_Calendar(
            calendar_platform="microsoft",
            access_token="test_token_123",
            timezone="America/New_York",
        )

        assert extension.calendar_platform == "microsoft"
        assert extension.access_token == "test_token_123"
        assert extension.timezone == "America/New_York"

    def test_provider_with_commands(self, extension):
        """Test provider that has commands attribute."""
        mock_provider = MagicMock()
        mock_provider.commands = {
            "create_event": MagicMock(),
            "get_events": MagicMock(),
        }
        extension.provider = mock_provider

        extension._register_commands()

        assert extension.commands == mock_provider.commands

    def test_provider_without_commands(self, extension):
        """Test provider that doesn't have commands attribute."""
        mock_provider = MagicMock()
        del mock_provider.commands  # Remove commands attribute
        extension.provider = mock_provider
        extension.calendar_platform = "microsoft"

        extension._register_commands()

        # Should fall back to placeholder commands
        assert "Get MICROSOFT Calendar Events" in extension.commands

    @pytest.mark.asyncio
    async def test_all_abilities_with_different_platforms(self, extension):
        """Test all abilities work with different calendar platforms."""
        for platform in ["google", "microsoft"]:
            extension.calendar_platform = platform
            extension.provider = None

            # All abilities should return provider not available error
            result = await extension.get_events()
            assert result["success"] is False
            assert f"No calendar provider available for {platform}" in result["message"]

            start_time = datetime(2024, 1, 1, 10, 0)
            end_time = datetime(2024, 1, 1, 11, 0)

            result = await extension.create_event("Test", start_time, end_time)
            assert result["success"] is False

            result = await extension.update_event("event_123")
            assert result["success"] is False

            result = await extension.delete_event("event_123")
            assert result["success"] is False

            result = await extension.get_available_timeslots(start_time)
            assert result["success"] is False

    def test_provider_settings_passed(self, extension):
        """Test that settings are passed to provider."""
        extension.settings = {"custom_setting": "value"}
        extension.calendar_platform = "google"

        with patch(
            "zephyrex.extensions.calendar.Google.GoogleCalendarProvider"
        ) as mock_provider:
            extension._create_provider()

            # Check that settings were passed to provider
            mock_provider.assert_called_once()
            call_kwargs = mock_provider.call_args[1]
            assert "custom_setting" in call_kwargs
            assert call_kwargs["custom_setting"] == "value"

    def test_provider_with_extension_id(self, extension):
        """Test that extension ID is passed to provider."""
        extension.calendar_platform = "google"

        with patch(
            "zephyrex.extensions.calendar.Google.GoogleCalendarProvider"
        ) as mock_provider:
            extension._create_provider()

            mock_provider.assert_called_once()
            call_kwargs = mock_provider.call_args[1]
            assert call_kwargs["extension_id"] == "calendar"

    def test_provider_with_access_token_and_timezone(self, extension):
        """Test that access token and timezone are passed to provider."""
        extension.calendar_platform = "google"
        extension.access_token = "test_token"
        extension.timezone = "America/New_York"

        with patch(
            "zephyrex.extensions.calendar.Google.GoogleCalendarProvider"
        ) as mock_provider:
            extension._create_provider()

            mock_provider.assert_called_once()
            call_kwargs = mock_provider.call_args[1]
            assert call_kwargs["access_token"] == "test_token"
            assert call_kwargs["timezone"] == "America/New_York"

    @pytest.mark.asyncio
    async def test_event_creation_with_all_parameters(self, extension):
        """Test event creation with all optional parameters."""
        mock_provider = MagicMock()
        mock_provider.create_event = MagicMock(return_value="event_created")
        extension.provider = mock_provider

        start_time = datetime(2024, 1, 1, 10, 0)
        end_time = datetime(2024, 1, 1, 11, 0)

        result = await extension.create_event(
            subject="All Parameters Meeting",
            start_time=start_time,
            end_time=end_time,
            location="Virtual",
            attendees=["user1@example.com"],
            description="Comprehensive test",
            is_online_meeting=True,
        )

        assert result["success"] is True
        mock_provider.create_event.assert_called_once_with(
            "All Parameters Meeting",
            start_time,
            end_time,
            "Virtual",
            ["user1@example.com"],
            "Comprehensive test",
            True,
        )

    @pytest.mark.asyncio
    async def test_event_update_with_partial_parameters(self, extension):
        """Test event update with only some parameters."""
        mock_provider = MagicMock()
        mock_provider.update_event = MagicMock(return_value="event_updated")
        extension.provider = mock_provider

        result = await extension.update_event(
            event_id="event_123", subject="New Subject Only"
        )

        assert result["success"] is True
        mock_provider.update_event.assert_called_once_with(
            "event_123",
            "New Subject Only",
            None,  # start_time
            None,  # end_time
            None,  # location
            None,  # attendees
            None,  # description
        )


if __name__ == "__main__":
    pytest.main([__file__])
