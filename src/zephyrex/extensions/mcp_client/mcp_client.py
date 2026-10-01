# SPDX-License-Identifier: AGPL-3.0-or-later
"""Model Context Protocol (MCP) client.

Thin, transport-agnostic client used by :class:`~extensions.mcp.EXT_MCP.EXT_MCP`
to talk to a real MCP server. Two transports are supported:

* ``ws://`` / ``wss://`` - a minimal JSON-RPC 2.0 client built directly on
  the ``websockets`` library, since the official ``mcp`` SDK does not ship
  a WebSocket transport.
* Anything else (``http://`` / ``https://`` URLs, or a bare stdio command)
  - the streamable-HTTP or stdio transports from the official ``mcp`` SDK.

Only the operations :class:`EXT_MCP` needs are exposed: connecting, a
generic message send, tool invocation, resource listing, and lightweight
session bookkeeping.
"""

import itertools
import json
import uuid
from contextlib import AsyncExitStack
from typing import Any, Dict, List, Optional


class MCPClient:
    """Client for communicating with a Model Context Protocol server."""

    def __init__(self, server_uri: str = "", protocol_version: str = "2024-11-05"):
        self.server_uri = server_uri
        self.protocol_version = protocol_version

        self._session: Optional[Any] = None
        self._exit_stack: Optional[AsyncExitStack] = None
        self._websocket: Optional[Any] = None
        self._request_ids = itertools.count(1)
        self._sessions: Dict[str, bool] = {}

    async def connect(self, server_uri: Optional[str] = None) -> bool:
        """Connect to the configured (or given) MCP server."""
        uri = server_uri or self.server_uri
        if not uri:
            raise ValueError("No MCP server URI configured")
        self.server_uri = uri

        if uri.startswith(("ws://", "wss://")):
            import websockets

            self._websocket = await websockets.connect(uri)
            await self._rpc_request(
                "initialize",
                {
                    "protocolVersion": self.protocol_version,
                    "capabilities": {},
                    "clientInfo": {"name": "zephyrex-mcp", "version": "1.0.0"},
                },
            )
            return True

        from mcp import ClientSession

        self._exit_stack = AsyncExitStack()

        if uri.startswith(("http://", "https://")):
            from mcp.client.streamable_http import streamable_http_client

            read_stream, write_stream = await self._exit_stack.enter_async_context(
                streamable_http_client(uri)
            )
        else:
            from mcp.client.stdio import StdioServerParameters, stdio_client

            read_stream, write_stream = await self._exit_stack.enter_async_context(
                stdio_client(StdioServerParameters(command=uri))
            )

        session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await session.initialize()
        self._session = session
        return True

    async def disconnect(self) -> None:
        """Tear down the active transport, if any."""
        if self._websocket is not None:
            await self._websocket.close()
            self._websocket = None
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
            self._exit_stack = None
        self._session = None

    async def _rpc_request(
        self, method: str, params: Optional[Dict[str, Any]] = None
    ) -> Any:
        """Send a JSON-RPC 2.0 request over the WebSocket transport and await its reply."""
        if self._websocket is None:
            raise RuntimeError("WebSocket transport not connected")

        request_id = next(self._request_ids)
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        await self._websocket.send(json.dumps(payload))

        while True:
            raw = await self._websocket.recv()
            message = json.loads(raw)
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(f"MCP server error: {message['error']}")
                return message.get("result")

    async def send_message(self, message: Dict[str, Any]) -> Any:
        """Send a raw MCP JSON-RPC style message (``{"method": ..., "params": ...}``)."""
        method = message.get("method", "")
        params = message.get("params", {}) or {}

        if self._websocket is not None:
            return await self._rpc_request(method, params)

        if self._session is None:
            raise RuntimeError("Not connected to an MCP server")

        if method in ("tools/call", "call_tool"):
            return await self.call_tool(
                params.get("name", ""), params.get("arguments", {})
            )
        if method in ("resources/list", "list_resources"):
            return await self.get_resources()
        if method in ("tools/list", "list_tools"):
            result = await self._session.list_tools()
            return result.model_dump()
        if method == "ping":
            await self._session.send_ping()
            return {"status": "pong"}

        raise NotImplementedError(
            f"Unsupported MCP method for this transport: {method}"
        )

    async def call_tool(
        self, tool_name: str, arguments: Optional[Dict[str, Any]] = None
    ) -> Any:
        """Invoke a tool exposed by the connected MCP server."""
        arguments = arguments or {}

        if self._websocket is not None:
            return await self._rpc_request(
                "tools/call", {"name": tool_name, "arguments": arguments}
            )

        if self._session is None:
            raise RuntimeError("Not connected to an MCP server")

        result = await self._session.call_tool(tool_name, arguments)
        return result.model_dump() if hasattr(result, "model_dump") else result

    async def get_resources(self) -> List[Any]:
        """List resources exposed by the connected MCP server."""
        if self._websocket is not None:
            result = await self._rpc_request("resources/list")
            return result.get("resources", []) if isinstance(result, dict) else result

        if self._session is None:
            raise RuntimeError("Not connected to an MCP server")

        result = await self._session.list_resources()
        return result.resources if hasattr(result, "resources") else result

    async def create_session(self) -> Dict[str, Any]:
        """Register a new logical session against the active connection."""
        session_id = str(uuid.uuid4())
        self._sessions[session_id] = True
        return {"session_id": session_id}

    async def destroy_session(self, session_id: str) -> bool:
        """Forget a logical session previously created with ``create_session``."""
        return self._sessions.pop(session_id, None) is not None


# See the matching block at the bottom of EXT_MCP.py: register this
# already-executed module under both the ``zephyrex.extensions.mcp.*`` and the
# pre-zephyrex bare ``extensions.mcp.*`` dotted paths, so a lookup (or an
# ``unittest.mock.patch``) via either name resolves to this same module
# instance instead of triggering a disconnected second import.
import sys as _sys

for _alias in ("zephyrex.extensions.mcp.mcp_client", "extensions.mcp.mcp_client"):
    _sys.modules.setdefault(_alias, _sys.modules[__name__])
