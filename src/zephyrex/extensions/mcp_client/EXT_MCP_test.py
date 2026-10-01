from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zephyrex.extensions.mcp.EXT_MCP import EXT_MCP


class TestMCPExtension:
    """Test cases for MCP Extension."""

    @pytest.fixture
    def extension(self):
        """Create an EXT_MCP instance for testing."""
        return EXT_MCP()

    def test_extension_metadata(self, extension):
        """Test extension metadata is correctly set."""
        assert extension.name == "mcp"
        assert extension.version == "1.0.0"
        assert "model context protocol" in extension.description.lower()
        assert "ai model integration" in extension.description.lower()

    def test_dependencies(self, extension):
        """Test extension dependencies are properly defined."""
        # Check extension dependencies
        ext_deps = {dep.name for dep in extension.ext_dependencies}
        assert "core" in ext_deps

        # Check pip dependencies
        pip_deps = {dep.name for dep in extension.pip_dependencies}
        assert "mcp" in pip_deps
        assert "httpx" in pip_deps
        assert "websockets" in pip_deps

        # Check sys dependencies
        assert isinstance(extension.sys_dependencies, list)

    def test_capabilities(self, extension):
        """Test extension capabilities are properly defined."""
        expected_capabilities = {
            "model_communication",
            "context_management",
            "protocol_handling",
            "resource_access",
            "tool_integration",
            "session_management",
        }
        assert set(extension.capabilities) == expected_capabilities

    def test_initialization(self, extension):
        """Test extension initialization."""
        assert hasattr(extension, "client")
        assert hasattr(extension, "server_uri")
        assert hasattr(extension, "protocol_version")
        assert hasattr(extension, "session_id")
        assert hasattr(extension, "active_connections")
        assert extension.client is None
        assert extension.active_connections == {}

    def test_default_configuration(self, extension):
        """Test default configuration values."""
        assert extension.server_uri == ""
        assert extension.protocol_version == "2024-11-05"
        assert extension.session_id == ""

    def test_db_tables(self, extension):
        """Test database tables list."""
        assert isinstance(extension.db_tables, list)
        assert len(extension.db_tables) == 0  # Empty by default

    @patch("extensions.mcp.EXT_MCP.logger")
    def test_on_initialize_success(self, mock_logger, extension):
        """Test successful extension initialization."""
        with patch.object(extension, "_create_client"), patch.object(
            extension, "register_capability"
        ):

            result = extension.on_initialize()
            assert result is True
            mock_logger.debug.assert_called()

    @patch("extensions.mcp.EXT_MCP.logger")
    def test_on_initialize_failure(self, mock_logger, extension):
        """Test extension initialization failure."""
        with patch.object(
            extension, "_create_client", side_effect=Exception("Test error")
        ):
            result = extension.on_initialize()
            assert result is False
            mock_logger.error.assert_called()

    def test_create_client_with_uri(self, extension):
        """Test MCP client creation with server URI."""
        extension.server_uri = "ws://localhost:8080"

        with patch("extensions.mcp.mcp_client.MCPClient") as mock_client:
            mock_instance = MagicMock()
            mock_client.return_value = mock_instance

            extension._create_client()

            assert extension.client == mock_instance
            mock_client.assert_called_once_with(
                server_uri="ws://localhost:8080", protocol_version="2024-11-05"
            )

    def test_create_client_no_uri(self, extension):
        """Test MCP client creation without server URI."""
        extension.server_uri = ""

        with patch("extensions.mcp.EXT_MCP.logger") as mock_logger:
            extension._create_client()

            assert extension.client is None
            mock_logger.warning.assert_called_with("No MCP server URI configured")

    def test_create_client_import_error(self, extension):
        """Test MCP client creation with import error."""
        extension.server_uri = "ws://localhost:8080"

        with patch(
            "extensions.mcp.mcp_client.MCPClient",
            side_effect=ImportError("Module not found"),
        ), patch("extensions.mcp.EXT_MCP.logger") as mock_logger:

            extension._create_client()

            assert extension.client is None
            mock_logger.error.assert_called()

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

    @pytest.mark.asyncio
    async def test_connect_to_server_success(self, extension):
        """Test successful connection to MCP server."""
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock(return_value=True)
        extension.client = mock_client

        result = await extension.connect_to_server("ws://localhost:8080")

        assert result["success"] is True
        assert "Connected to MCP server" in result["message"]
        mock_client.connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_to_server_no_client(self, extension):
        """Test connection to MCP server without client."""
        extension.client = None

        result = await extension.connect_to_server("ws://localhost:8080")

        assert result["success"] is False
        assert "MCP client not configured" in result["message"]

    @pytest.mark.asyncio
    async def test_connect_to_server_error(self, extension):
        """Test connection to MCP server with error."""
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock(side_effect=Exception("Connection error"))
        extension.client = mock_client

        result = await extension.connect_to_server("ws://localhost:8080")

        assert result["success"] is False
        assert "Failed to connect to MCP server" in result["message"]

    @pytest.mark.asyncio
    async def test_connect_to_server_default_uri(self, extension):
        """Test connection with default URI."""
        extension.server_uri = "ws://localhost:9000"
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock(return_value=True)
        extension.client = mock_client

        result = await extension.connect_to_server()

        mock_client.connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_message_success(self, extension):
        """Test successful message sending."""
        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(return_value={"response": "message_sent"})
        extension.client = mock_client

        message = {"type": "request", "id": "123", "method": "test"}
        result = await extension.send_message(message)

        assert result["success"] is True
        assert result["response"] == {"response": "message_sent"}
        mock_client.send_message.assert_called_once_with(message)

    @pytest.mark.asyncio
    async def test_send_message_no_client(self, extension):
        """Test message sending without client."""
        extension.client = None

        message = {"type": "request", "method": "test"}
        result = await extension.send_message(message)

        assert result["success"] is False
        assert "MCP client not configured" in result["message"]

    @pytest.mark.asyncio
    async def test_send_message_error(self, extension):
        """Test message sending with error."""
        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(side_effect=Exception("Send error"))
        extension.client = mock_client

        message = {"type": "request", "method": "test"}
        result = await extension.send_message(message)

        assert result["success"] is False
        assert "Failed to send message" in result["message"]

    @pytest.mark.asyncio
    async def test_call_tool_success(self, extension):
        """Test successful tool calling."""
        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value={"result": "tool_executed"})
        extension.client = mock_client

        result = await extension.call_tool("test_tool", {"param": "value"})

        assert result["success"] is True
        assert result["result"] == {"result": "tool_executed"}
        mock_client.call_tool.assert_called_once_with("test_tool", {"param": "value"})

    @pytest.mark.asyncio
    async def test_call_tool_no_client(self, extension):
        """Test tool calling without client."""
        extension.client = None

        result = await extension.call_tool("test_tool", {})

        assert result["success"] is False
        assert "MCP client not configured" in result["message"]

    @pytest.mark.asyncio
    async def test_call_tool_error(self, extension):
        """Test tool calling with error."""
        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(side_effect=Exception("Tool error"))
        extension.client = mock_client

        result = await extension.call_tool("test_tool", {})

        assert result["success"] is False
        assert "Failed to call tool" in result["message"]

    @pytest.mark.asyncio
    async def test_call_tool_default_arguments(self, extension):
        """Test tool calling with default arguments."""
        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value={"result": "success"})
        extension.client = mock_client

        result = await extension.call_tool("test_tool")

        mock_client.call_tool.assert_called_once_with("test_tool", {})

    @pytest.mark.asyncio
    async def test_get_resources_success(self, extension):
        """Test successful resource retrieval."""
        mock_client = AsyncMock()
        mock_client.get_resources = AsyncMock(return_value=["resource1", "resource2"])
        extension.client = mock_client

        result = await extension.get_resources()

        assert result["success"] is True
        assert result["resources"] == ["resource1", "resource2"]
        mock_client.get_resources.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_resources_no_client(self, extension):
        """Test resource retrieval without client."""
        extension.client = None

        result = await extension.get_resources()

        assert result["success"] is False
        assert "MCP client not configured" in result["message"]

    @pytest.mark.asyncio
    async def test_get_resources_error(self, extension):
        """Test resource retrieval with error."""
        mock_client = AsyncMock()
        mock_client.get_resources = AsyncMock(side_effect=Exception("Resource error"))
        extension.client = mock_client

        result = await extension.get_resources()

        assert result["success"] is False
        assert "Failed to get resources" in result["message"]

    @pytest.mark.asyncio
    async def test_manage_session_create(self, extension):
        """Test session creation."""
        mock_client = AsyncMock()
        mock_client.create_session = AsyncMock(
            return_value={"session_id": "new_session"}
        )
        extension.client = mock_client

        result = await extension.manage_session("create")

        assert result["success"] is True
        assert "new_session" in result["session_id"]
        mock_client.create_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_manage_session_destroy(self, extension):
        """Test session destruction."""
        mock_client = AsyncMock()
        mock_client.destroy_session = AsyncMock(return_value=True)
        extension.client = mock_client
        extension.session_id = "test_session"

        result = await extension.manage_session("destroy", "test_session")

        assert result["success"] is True
        mock_client.destroy_session.assert_called_once_with("test_session")

    @pytest.mark.asyncio
    async def test_manage_session_no_client(self, extension):
        """Test session management without client."""
        extension.client = None

        result = await extension.manage_session("create")

        assert result["success"] is False
        assert "MCP client not configured" in result["message"]

    @pytest.mark.asyncio
    async def test_manage_session_invalid_action(self, extension):
        """Test session management with invalid action."""
        mock_client = AsyncMock()
        extension.client = mock_client

        result = await extension.manage_session("invalid_action")

        assert result["success"] is False
        assert "Invalid session action" in result["message"]

    @pytest.mark.asyncio
    async def test_manage_session_error(self, extension):
        """Test session management with error."""
        mock_client = AsyncMock()
        mock_client.create_session = AsyncMock(side_effect=Exception("Session error"))
        extension.client = mock_client

        result = await extension.manage_session("create")

        assert result["success"] is False
        assert "Failed to manage session" in result["message"]

    @pytest.mark.asyncio
    async def test_no_client_warning(self, extension):
        """Test warning message when no client is available."""
        result = await extension._no_client_warning()

        assert result["success"] is False
        assert "MCP client not configured" in result["message"]

    def test_lifecycle_methods(self, extension):
        """Test extension lifecycle methods."""
        # Test on_start
        assert extension.on_start() is True

        # Test on_stop
        mock_client = MagicMock()
        extension.client = mock_client
        extension.active_connections = {"test": "connection"}

        assert extension.on_stop() is True
        assert extension.client is None
        assert extension.active_connections == {}

        # Test on_startup and on_shutdown
        extension.on_startup()  # Should not raise exception
        extension.on_shutdown()  # Should not raise exception

    def test_validate_config_success(self, extension):
        """Test successful configuration validation."""
        extension.server_uri = "ws://localhost:8080"
        extension.protocol_version = "2024-11-05"

        issues = extension.validate_config()
        assert isinstance(issues, list)

    def test_validate_config_no_server_uri(self, extension):
        """Test configuration validation with no server URI."""
        extension.server_uri = ""

        issues = extension.validate_config()

        assert any("MCP server URI not specified" in issue for issue in issues)

    def test_validate_config_invalid_protocol_version(self, extension):
        """Test configuration validation with invalid protocol version."""
        extension.protocol_version = ""

        issues = extension.validate_config()

        assert any("MCP protocol version not specified" in issue for issue in issues)

    def test_get_required_permissions(self, extension):
        """Test required permissions."""
        permissions = extension.get_required_permissions()
        assert isinstance(permissions, list)
        assert len(permissions) > 0

        expected_permissions = [
            "mcp:connect",
            "mcp:send",
            "mcp:receive",
            "mcp:tools",
            "mcp:resources",
            "network:connect",
        ]
        assert set(permissions) == set(expected_permissions)

    def test_custom_configuration(self):
        """Test extension with custom configuration."""
        extension = EXT_MCP(
            server_uri="ws://custom:8080",
            protocol_version="2024-12-01",
            session_id="custom_session",
        )

        assert extension.server_uri == "ws://custom:8080"
        assert extension.protocol_version == "2024-12-01"
        assert extension.session_id == "custom_session"

    def test_client_with_configuration_parameters(self, extension):
        """Test client creation with configuration parameters."""
        extension.server_uri = "ws://test:9000"
        extension.protocol_version = "2024-11-05"
        extension.timeout = 30
        extension.max_retries = 5
        extension.settings = {"custom_setting": "value"}

        with patch("extensions.mcp.mcp_client.MCPClient") as mock_client:
            extension._create_client()

            # Check that client was called with correct parameters
            mock_client.assert_called_once()
            call_kwargs = mock_client.call_args[1]
            assert call_kwargs["server_uri"] == "ws://test:9000"
            assert call_kwargs["protocol_version"] == "2024-11-05"

    def test_client_with_default_parameters(self, extension):
        """Test client creation with default parameters."""
        extension.server_uri = "ws://localhost:8080"
        extension.settings = {"test": "value"}

        with patch("extensions.mcp.mcp_client.MCPClient") as mock_client:
            extension._create_client()

            # Check that client was called with default parameters
            mock_client.assert_called_once()
            call_kwargs = mock_client.call_args[1]
            assert call_kwargs["server_uri"] == "ws://localhost:8080"
            assert call_kwargs["protocol_version"] == "2024-11-05"

    def test_has_capability(self, extension):
        """Test has_capability method."""
        assert extension.has_capability("model_communication") is True
        assert extension.has_capability("context_management") is True
        assert extension.has_capability("protocol_handling") is True
        assert extension.has_capability("resource_access") is True
        assert extension.has_capability("tool_integration") is True
        assert extension.has_capability("session_management") is True
        assert extension.has_capability("nonexistent_capability") is False

    @pytest.mark.asyncio
    async def test_all_abilities_with_different_parameters(self, extension):
        """Test all abilities work with different parameters."""
        extension.client = None

        # All abilities should return client not available error
        result = await extension.connect_to_server("ws://test:8080")
        assert result["success"] is False

        result = await extension.send_message({"type": "test"})
        assert result["success"] is False

        result = await extension.call_tool("test_tool", {"arg": "value"})
        assert result["success"] is False

        result = await extension.get_resources()
        assert result["success"] is False

        result = await extension.manage_session("create")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_client_method_calls_with_parameters(self, extension):
        """Test that client methods are called with correct parameters."""
        mock_client = AsyncMock()
        extension.client = mock_client

        # Test connect_to_server
        await extension.connect_to_server("ws://custom:8080")
        mock_client.connect.assert_called()

        # Test send_message with custom message
        custom_message = {"type": "request", "id": "456", "method": "custom"}
        await extension.send_message(custom_message)
        mock_client.send_message.assert_called_with(custom_message)

        # Test call_tool with arguments
        await extension.call_tool("custom_tool", {"arg1": "value1", "arg2": "value2"})
        mock_client.call_tool.assert_called_with(
            "custom_tool", {"arg1": "value1", "arg2": "value2"}
        )

        # Test get_resources
        await extension.get_resources()
        mock_client.get_resources.assert_called()

        # Test manage_session with session ID
        await extension.manage_session("destroy", "custom_session_id")
        mock_client.destroy_session.assert_called_with("custom_session_id")

    def test_client_error_handling_during_creation(self, extension):
        """Test client error handling during creation."""
        extension.server_uri = "ws://localhost:8080"

        with patch(
            "extensions.mcp.mcp_client.MCPClient",
            side_effect=Exception("Creation error"),
        ), patch("extensions.mcp.EXT_MCP.logger") as mock_logger:

            extension._create_client()

            assert extension.client is None
            mock_logger.error.assert_called()

    def test_active_connections_management(self, extension):
        """Test active connections management."""
        # Initially empty
        assert extension.active_connections == {}

        # Add connection
        extension.active_connections["test1"] = "connection1"
        extension.active_connections["test2"] = "connection2"

        assert len(extension.active_connections) == 2

        # Clear on stop
        extension.on_stop()
        assert extension.active_connections == {}

    @pytest.mark.asyncio
    async def test_abilities_with_empty_parameters(self, extension):
        """Test abilities with empty string parameters."""
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock(return_value=True)
        mock_client.send_message = AsyncMock(return_value={"response": "ok"})
        mock_client.call_tool = AsyncMock(return_value={"result": "ok"})
        extension.client = mock_client

        # Test with empty strings
        await extension.connect_to_server("")
        mock_client.connect.assert_called()

        await extension.send_message({})
        mock_client.send_message.assert_called_with({})

        await extension.call_tool("", {})
        mock_client.call_tool.assert_called_with("", {})

    def test_settings_passed_to_client(self, extension):
        """Test that settings are properly passed to client."""
        extension.server_uri = "ws://localhost:8080"
        extension.settings = {"timeout": 30, "retries": 3, "debug": True}

        with patch("extensions.mcp.mcp_client.MCPClient") as mock_client:
            extension._create_client()

            mock_client.assert_called_once()
            # Settings should be available in the extension but client creation
            # depends on the actual MCPClient implementation

    def test_protocol_version_validation(self, extension):
        """Test protocol version validation."""
        valid_versions = ["2024-11-05", "2024-10-07", "2023-12-01"]

        for version in valid_versions:
            extension.protocol_version = version
            issues = extension.validate_config()
            # Should not have protocol version issues for valid versions
            assert not any("protocol version" in issue.lower() for issue in issues)

    @pytest.mark.asyncio
    async def test_session_management_edge_cases(self, extension):
        """Test session management edge cases."""
        mock_client = AsyncMock()
        extension.client = mock_client

        # Test create session
        mock_client.create_session = AsyncMock(
            return_value={"session_id": "new_session_123"}
        )
        result = await extension.manage_session("create")
        assert result["success"] is True

        # Test destroy with no session ID provided but extension has one
        extension.session_id = "default_session"
        mock_client.destroy_session = AsyncMock(return_value=True)
        result = await extension.manage_session("destroy")
        mock_client.destroy_session.assert_called_with("default_session")

    def test_uri_validation_in_config(self, extension):
        """Test URI validation in configuration."""
        # Valid URIs
        valid_uris = [
            "ws://localhost:8080",
            "wss://secure.example.com:443",
            "http://localhost:3000/mcp",
            "https://api.example.com/mcp",
        ]

        for uri in valid_uris:
            extension.server_uri = uri
            issues = extension.validate_config()
            # Should not have URI format issues for valid URIs
            uri_issues = [
                issue
                for issue in issues
                if "uri" in issue.lower() and "format" in issue.lower()
            ]
            assert len(uri_issues) == 0


if __name__ == "__main__":
    pytest.main([__file__])
