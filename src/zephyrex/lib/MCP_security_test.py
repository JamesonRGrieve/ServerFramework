# SPDX-License-Identifier: AGPL-3.0-or-later
"""MCP security tests — bridge authorization, tool access, output isolation.

Tests the MCP bridge at the application layer (MCPBridge functions)
and the HTTP transport where possible. Corpus sections 58–73.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("JWT_SECRET", "test-jwt-secret-32-bytes-or-more-aaaaaa")
os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("SEED_DATA", "false")
os.environ.setdefault("MCP", "true")


@pytest.fixture(scope="module")
def mcp_app(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("mcp_sec")
    os.environ["DATABASE_NAME"] = f"mcp_sec_{os.getpid()}"
    os.environ["DATABASE_PATH"] = str(tmp)
    os.environ["MCP"] = "true"

    from zephyrex.lib.Environment import refresh_settings
    from zephyrex.pydantic2.sqlalchemy import (
        clear_registry_cache,
        reset_extension_system,
    )

    refresh_settings()
    clear_registry_cache()
    reset_extension_system()
    from zephyrex.pydantic2.strawberry import reset_gql_contribution_registry

    reset_gql_contribution_registry()

    from zephyrex.app import instance

    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    app = instance(extensions="", db_prefix=f"mcp.sec.{worker}.{os.getpid()}")
    assert hasattr(app.state, "mcp"), "MCP failed to mount"
    return app


@pytest.fixture(scope="module")
def mcp_client(mcp_app):
    from starlette.testclient import TestClient

    return TestClient(mcp_app)


@pytest.fixture(scope="module")
def mcp_tools(mcp_app):
    from zephyrex.lib.MCPBridge import _build_tools_from_openapi

    return _build_tools_from_openapi(mcp_app.openapi())


def _text_contents(result):
    """Return the tool result's text payloads, asserting there is content.

    A call that produced no content -- or content with no ``.text`` -- was a
    vacuous pass for the old ``for c in result.content: if hasattr(c, 'text'):``
    loops (they asserted nothing when the loop body never ran).
    """
    assert result.content, "tool call returned no content to inspect"
    texts = [c.text for c in result.content if hasattr(c, "text")]
    assert texts, "tool result carried no text content"
    return texts


@pytest.mark.security
class TestMCPBridgeToolGeneration:
    def test_tools_generated_from_openapi(self, mcp_tools):
        assert len(mcp_tools) > 0, "MCP bridge generated no tools from OpenAPI"

    def test_tool_names_are_safe_identifiers(self, mcp_tools):
        import re

        for tool in mcp_tools:
            assert re.match(
                r"^[a-zA-Z0-9_-]+$", tool.name
            ), f"Tool name contains unsafe characters: {tool.name}"

    def test_tool_descriptions_do_not_leak_internal_paths(self, mcp_tools):
        for tool in mcp_tools:
            desc = (tool.description or "").lower()
            assert (
                "/home/" not in desc and "/usr/" not in desc and "/etc/" not in desc
            ), f"Tool description leaks filesystem path: {tool.description}"

    def test_tool_schemas_have_required_fields(self, mcp_tools):
        for tool in mcp_tools:
            schema = tool.input_schema
            assert "type" in schema
            assert schema["type"] == "object"
            assert "properties" in schema


@pytest.mark.security
class TestMCPBridgeToolExecution:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, mcp_app, mcp_tools):
        from zephyrex.lib.MCPBridge import _call_tool_via_app

        result = await _call_tool_via_app(
            mcp_app, "nonexistent_tool", {}, mcp_tools, {}
        )
        assert result.is_error

    @pytest.mark.asyncio
    async def test_tool_call_sqli_in_argument(self, mcp_app, mcp_tools):
        from zephyrex.lib.MCPBridge import _call_tool_via_app

        assert mcp_tools, "MCP bridge generated no tools (would have masked as a skip)"
        tool = mcp_tools[0]
        result = await _call_tool_via_app(
            mcp_app, tool.name, {"param_id": "' OR 1=1; DROP TABLE--"}, mcp_tools, {}
        )
        for text in _text_contents(result):
            assert "drop table" not in text.lower()

    @pytest.mark.asyncio
    async def test_tool_call_path_traversal_in_argument(self, mcp_app, mcp_tools):
        from zephyrex.lib.MCPBridge import _call_tool_via_app

        assert mcp_tools, "MCP bridge generated no tools (would have masked as a skip)"
        tool = mcp_tools[0]
        result = await _call_tool_via_app(
            mcp_app, tool.name, {"param_id": "../../../etc/passwd"}, mcp_tools, {}
        )
        for text in _text_contents(result):
            assert "root:" not in text

    @pytest.mark.asyncio
    async def test_tool_output_does_not_leak_stacktrace(self, mcp_app, mcp_tools):
        from zephyrex.lib.MCPBridge import _call_tool_via_app

        result = await _call_tool_via_app(mcp_app, "nonexistent", {}, mcp_tools, {})
        for text in _text_contents(result):
            assert "traceback" not in text.lower()
            assert 'file "' not in text.lower()

    @pytest.mark.asyncio
    async def test_tool_output_does_not_leak_credentials(self, mcp_app, mcp_tools):
        from zephyrex.lib.MCPBridge import _call_tool_via_app

        assert mcp_tools, "MCP bridge generated no tools (would have masked as a skip)"
        tool = mcp_tools[0]
        result = await _call_tool_via_app(mcp_app, tool.name, {}, mcp_tools, {})
        for text in _text_contents(result):
            low = text.lower()
            assert "jwt_secret" not in low
            assert "password_hash" not in low

    @pytest.mark.asyncio
    async def test_tool_forwards_authorization_header(self, mcp_app, mcp_tools):
        from zephyrex.lib.MCPBridge import _call_tool_via_app

        assert mcp_tools, "MCP bridge generated no tools (would have masked as a skip)"
        tool = mcp_tools[0]
        result = await _call_tool_via_app(
            mcp_app,
            tool.name,
            {},
            mcp_tools,
            {"authorization": "Bearer fake-token"},
        )
        # Forwarding the Authorization header through the app must produce a real
        # tool result (content), not crash the proxy -- stronger than the old
        # `is not None`. (Observing the exact downstream header would need an
        # echo endpoint the bare MCP app does not expose.)
        assert _text_contents(result)

    @pytest.mark.asyncio
    async def test_tool_forwards_accept_header_for_toon(self, mcp_app, mcp_tools):
        from zephyrex.lib.MCPBridge import _call_tool_via_app

        assert mcp_tools, "MCP bridge generated no tools (would have masked as a skip)"
        tool = mcp_tools[0]
        result = await _call_tool_via_app(
            mcp_app,
            tool.name,
            {},
            mcp_tools,
            {"accept": "application/toon"},
        )
        # The TOON Accept header must flow through the proxy to a real result,
        # not crash it -- stronger than the old `is not None`.
        assert _text_contents(result)


@pytest.mark.security
class TestMCPHTTPTransport:
    def test_mcp_endpoint_exists(self, mcp_app):
        routes = [r.path for r in mcp_app.routes if hasattr(r, "path")]
        mcp_routes = [r for r in routes if "mcp" in r.lower()]
        assert len(mcp_routes) > 0, "No /mcp routes mounted"

    @pytest.mark.asyncio
    async def test_mcp_internal_calls_go_through_app_middleware(
        self, mcp_app, mcp_tools
    ):
        """The bridge proxies tool calls through the full FastAPI app
        (including security middleware), not directly to handlers."""
        from zephyrex.lib.MCPBridge import _call_tool_via_app

        assert mcp_tools, "MCP bridge generated no tools"
        # Make a real call and assert it produced a proper MCP result via the app
        # path -- an `is not None` import check proved nothing about the call
        # actually traversing the app.
        result = await _call_tool_via_app(mcp_app, mcp_tools[0].name, {}, mcp_tools, {})
        assert hasattr(result, "content") and hasattr(result, "is_error")
        assert result.content, "tool call routed through the app returned no content"


@pytest.mark.security
class TestMCPToolIsolation:
    def test_tool_names_do_not_expose_admin_operations(self, mcp_tools):
        for tool in mcp_tools:
            low = tool.name.lower()
            assert (
                "debug" not in low and "internal" not in low
            ), f"Tool name exposes internal operation: {tool.name}"

    def test_tool_count_matches_openapi_operations(self, mcp_app, mcp_tools):
        schema = mcp_app.openapi()
        op_count = sum(
            1
            for methods in schema.get("paths", {}).values()
            for m in methods
            if m.lower() in ("get", "post", "put", "patch", "delete")
            and methods[m].get("operationId")
        )
        assert (
            len(mcp_tools) == op_count
        ), f"Tool count ({len(mcp_tools)}) doesn't match OpenAPI operations ({op_count})"
