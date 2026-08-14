# SPDX-License-Identifier: AGPL-3.0-or-later
"""MCP 2.0 bridge — exposes every FastAPI route as an MCP tool.

Replaces fastapi-mcp (incompatible with mcp>=2.0) with a direct bridge
that introspects the OpenAPI schema and proxies tool calls through
the internal ASGI transport. Supports TOON content negotiation via the
Accept header forwarded from the MCP request.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from fastapi import FastAPI
from mcp import types
from mcp.server import Server
from starlette.applications import Starlette

from zephyrex.lib.Logging import logger


def _operation_id_to_tool_name(op_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", op_id)[:64]


def _build_tools_from_openapi(schema: dict) -> list[types.Tool]:
    tools = []
    paths = schema.get("paths", {})
    for path, methods in paths.items():
        for method, spec in methods.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete"):
                continue
            op_id = spec.get("operationId")
            if not op_id:
                continue
            summary = spec.get("summary") or f"{method.upper()} {path}"
            desc_parts = [summary]
            if spec.get("description") and spec["description"] != summary:
                desc_parts.append(spec["description"])
            desc_parts.append(f"Endpoint: {method.upper()} {path}")
            desc_parts.append("Requires: Authorization: Bearer <JWT>")
            description = "\n".join(desc_parts)

            input_props: dict[str, Any] = {
                "method": {"type": "string", "const": method.upper(), "description": "HTTP method"},
                "path": {"type": "string", "const": path, "description": "API path"},
            }
            required = ["method", "path"]

            params = spec.get("parameters", [])
            for param in params:
                pname = param.get("name", "")
                pschema = param.get("schema", {"type": "string"})
                pschema["description"] = param.get("description", f"Parameter: {pname}")
                input_props[f"param_{pname}"] = pschema
                if param.get("required"):
                    required.append(f"param_{pname}")

            body = spec.get("requestBody", {})
            if body:
                body_content = body.get("content", {})
                json_schema = body_content.get("application/json", {}).get("schema", {"type": "object"})
                input_props["body"] = json_schema

            tools.append(
                types.Tool(
                    name=_operation_id_to_tool_name(op_id),
                    description=description[:1024],
                    input_schema={
                        "type": "object",
                        "properties": input_props,
                        "required": required,
                    },
                )
            )
    return tools


async def _call_tool_via_app(
    app: FastAPI,
    name: str,
    arguments: dict[str, Any] | None,
    tools: list[types.Tool],
    forwarded_headers: dict[str, str],
) -> types.CallToolResult:
    tool = next((t for t in tools if t.name == name), None)
    if not tool:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Unknown tool: {name}")],
            is_error=True,
        )

    props = tool.input_schema.get("properties", {})
    method = props.get("method", {}).get("const", "GET")
    path = props.get("path", {}).get("const", "/")

    args = arguments or {}
    body = args.get("body")
    query_params = {}
    path_params = {}

    for key, val in args.items():
        if key.startswith("param_"):
            param_name = key[6:]
            if f"{{{param_name}}}" in path:
                path_params[param_name] = str(val)
            else:
                query_params[param_name] = str(val)

    for pname, pval in path_params.items():
        path = path.replace(f"{{{pname}}}", pval)

    headers = {**forwarded_headers}
    if "accept" not in {k.lower() for k in headers}:
        headers["Accept"] = "application/toon"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://mcp-internal",
    ) as client:
        response = await client.request(
            method=method,
            url=path,
            params=query_params or None,
            json=body if body else None,
            headers=headers,
        )

    content_type = response.headers.get("content-type", "")
    text = response.text

    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        is_error=response.status_code >= 400,
    )


def mount_mcp(app: FastAPI, *, name: str, mount_path: str = "/mcp") -> Server:
    """Build an MCP 2.0 server from *app*'s OpenAPI schema and mount it."""

    schema = app.openapi()
    tools = _build_tools_from_openapi(schema)
    logger.info("MCP bridge: %d tools from %d OpenAPI paths", len(tools), len(schema.get("paths", {})))

    async def on_list_tools(ctx, params):
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(ctx, params):
        forwarded = {}
        return await _call_tool_via_app(
            app, params.name, params.arguments, tools, forwarded
        )

    server = Server(
        name,
        description=f"{name} MCP Server — REST/GQL bridge with TOON support",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )

    mcp_app = server.streamable_http_app(streamable_http_path=mount_path)

    for route in mcp_app.routes:
        app.routes.append(route)

    return server
