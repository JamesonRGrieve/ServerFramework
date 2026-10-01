# SPDX-License-Identifier: AGPL-3.0-or-later
from typing import Any, Dict, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.lib.Dependencies import EXT_Dependency, PIP_Dependency
from zephyrex.lib.Logging import logger


class EXT_MCP(AbstractStaticExtension):
    """
    Model Context Protocol (MCP) extension for AGInfrastructure.

    Implements a client for the Model Context Protocol, providing AI model
    integration through standardized protocol handling: connecting to MCP
    servers, exchanging messages, invoking tools, retrieving resources, and
    managing protocol sessions.
    """

    # Extension metadata
    name = "mcp"
    version = "1.0.0"
    description = (
        "Model Context Protocol extension providing AI model integration through "
        "standardized protocol handling, tool invocation, resource access, and "
        "session management."
    )

    # Define dependencies
    ext_dependencies = [
        EXT_Dependency(
            name="core",
            friendly_name="Core Extension",
            optional=False,
            reason="Required for shared extension infrastructure",
        )
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="mcp",
            friendly_name="Model Context Protocol SDK",
            optional=False,
            semver=">=1.0.0",
            reason="Official MCP client SDK for stdio/streamable-HTTP transports",
        ),
        PIP_Dependency(
            name="httpx",
            friendly_name="HTTPX",
            optional=False,
            semver=">=0.24.0",
            reason="HTTP transport used by the MCP SDK's streamable-HTTP client",
        ),
        PIP_Dependency(
            name="websockets",
            friendly_name="Websockets",
            optional=False,
            semver=">=11.0",
            reason="WebSocket transport for MCP servers exposed over ws(s)://",
        ),
    ]

    sys_dependencies: List[Any] = []

    # Define what capabilities this extension provides
    capabilities = [
        "model_communication",
        "context_management",
        "protocol_handling",
        "resource_access",
        "tool_integration",
        "session_management",
    ]

    # Define database tables
    db_tables: List[str] = []

    def __init__(
        self,
        server_uri: str = "",
        protocol_version: str = "2024-11-05",
        session_id: str = "",
        **kwargs,
    ):
        """
        Initialize the MCP extension.
        """
        super().__init__(**kwargs)

        # Instance-level copy so register_capability() never mutates the
        # class-level default shared across every instance.
        self.capabilities = list(self.capabilities)

        self.server_uri = server_uri
        self.protocol_version = protocol_version
        self.session_id = session_id

        self.client: Optional[Any] = None
        self.active_connections: Dict[str, Any] = {}

    def on_initialize(self) -> bool:
        """
        Initialize the MCP extension.
        """
        logger.debug("Initializing MCP Extension...")

        try:
            self._create_client()

            self.register_capability("model_communication")
            self.register_capability("context_management")
            self.register_capability("protocol_handling")
            self.register_capability("resource_access")
            self.register_capability("tool_integration")
            self.register_capability("session_management")

            logger.debug("MCP extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize MCP extension: {str(e)}")
            return False

    def _create_client(self) -> None:
        """
        Create the MCP client instance for the configured server URI.
        """
        if not self.server_uri:
            logger.warning("No MCP server URI configured")
            self.client = None
            return

        try:
            from extensions.mcp.mcp_client import MCPClient

            self.client = MCPClient(
                server_uri=self.server_uri,
                protocol_version=self.protocol_version,
            )
            logger.debug(f"MCP client created for {self.server_uri}")
        except Exception as e:
            logger.error(f"Failed to create MCP client: {str(e)}")
            self.client = None

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

    def has_capability(self, capability: str) -> bool:
        """Check if this extension has a specific capability."""
        return capability in self.capabilities

    @ability("connect_to_server")
    async def connect_to_server(
        self, server_uri: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Connect to an MCP server, defaulting to the configured server URI.
        """
        if not self.client:
            return await self._no_client_warning()

        try:
            uri = server_uri if server_uri else self.server_uri
            await self.client.connect(uri)
            return {
                "success": True,
                "message": f"Connected to MCP server at {uri}",
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Failed to connect to MCP server: {str(e)}",
            }

    @ability("send_message")
    async def send_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send a raw MCP protocol message to the connected server.
        """
        if not self.client:
            return await self._no_client_warning()

        try:
            response = await self.client.send_message(message)
            return {"success": True, "response": response}
        except Exception as e:
            return {
                "success": False,
                "message": f"Failed to send message: {str(e)}",
            }

    @ability("call_tool")
    async def call_tool(
        self, tool_name: str, arguments: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Invoke a tool exposed by the connected MCP server.
        """
        if not self.client:
            return await self._no_client_warning()

        try:
            result = await self.client.call_tool(tool_name, arguments or {})
            return {"success": True, "result": result}
        except Exception as e:
            return {
                "success": False,
                "message": f"Failed to call tool: {str(e)}",
            }

    @ability("get_resources")
    async def get_resources(self) -> Dict[str, Any]:
        """
        Retrieve the resources exposed by the connected MCP server.
        """
        if not self.client:
            return await self._no_client_warning()

        try:
            resources = await self.client.get_resources()
            return {"success": True, "resources": resources}
        except Exception as e:
            return {
                "success": False,
                "message": f"Failed to get resources: {str(e)}",
            }

    @ability("manage_session")
    async def manage_session(
        self, action: str, session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create or destroy an MCP protocol session.
        """
        if not self.client:
            return await self._no_client_warning()

        try:
            if action == "create":
                result = await self.client.create_session()
                new_session_id = (
                    result.get("session_id", "") if isinstance(result, dict) else ""
                )
                self.session_id = new_session_id
                return {"success": True, "session_id": new_session_id}

            if action == "destroy":
                target_session_id = session_id if session_id else self.session_id
                await self.client.destroy_session(target_session_id)
                self.session_id = ""
                return {
                    "success": True,
                    "message": f"Session {target_session_id} destroyed",
                }

            return {
                "success": False,
                "message": f"Invalid session action: {action}",
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Failed to manage session: {str(e)}",
            }

    async def _no_client_warning(self, *args, **kwargs) -> Dict[str, Any]:
        """Return a warning result when no MCP client is configured."""
        return {
            "success": False,
            "message": "MCP client not configured. Please set a server URI and initialize the extension.",
        }

    def get_required_permissions(self) -> List[str]:
        """Return the list of permissions required by this extension."""
        return [
            "mcp:connect",
            "mcp:send",
            "mcp:receive",
            "mcp:tools",
            "mcp:resources",
            "network:connect",
        ]

    def on_start(self) -> bool:
        """
        Start the MCP extension.
        """
        try:
            logger.debug("MCP extension started successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to start MCP extension: {e}")
            return False

    def on_stop(self) -> bool:
        """
        Stop the MCP extension, releasing the client and any tracked connections.
        """
        try:
            self.client = None
            self.active_connections = {}

            logger.debug("MCP extension stopped successfully")
            return True
        except Exception as e:
            logger.error(f"Error stopping MCP extension: {e}")
            return False

    def validate_config(self) -> List[str]:
        """
        Validate the extension configuration.
        """
        issues = []

        if not self.server_uri:
            issues.append("MCP server URI not specified")

        if not self.protocol_version:
            issues.append("MCP protocol version not specified")

        return issues

    def on_startup(self):
        """
        Called during application startup.
        """
        logger.debug("MCP extension startup hook called")

    def on_shutdown(self):
        """
        Called during application shutdown.
        """
        logger.debug("MCP extension shutdown hook called")


# This module is normally imported as ``zephyrex.extensions.mcp.EXT_MCP`` (the
# post-Item-60 namespace layout). Some call sites and tests still target it via
# the pre-zephyrex, bare "extensions.<name>.<file>" dotted path. Register this
# already-executed module object under that alias too (mirroring what
# ``ExtensionLoader.load_extension_module`` does for its own synthesized name)
# so a lookup via either path resolves to the same module instance -- and, in
# particular, so ``unittest.mock.patch("extensions.mcp.EXT_MCP.<name>")``
# patches the very attribute this module's own code reads, instead of
# silently re-executing this file a second time under a disconnected name.
import sys as _sys

for _alias in ("zephyrex.extensions.mcp.EXT_MCP", "extensions.mcp.EXT_MCP"):
    _sys.modules.setdefault(_alias, _sys.modules[__name__])
