# SPDX-License-Identifier: AGPL-3.0-or-later
"""An MCP server over Streamable HTTP, the protocol's remote transport.

``url`` is the server's MCP endpoint (``https://example.com/mcp``); a
bearer token, when the server wants one, is the instance's API key. Every
request the client makes, the session's own included, passes the server's
SSRF guard, so a server on a private network must be named in
``EGRESS_ALLOWED_HOSTS``. The SDK follows a redirect only within the
endpoint's origin."""

import asyncio
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, AsyncIterator, ClassVar, Dict, List, Tuple

import httpx2
from mcp.client.streamable_http import streamable_http_client

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.extensions.mcp_client.EXT_MCPClient import AbstractMCPServerProvider
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.lib.ProviderHTTPClient import SSRFGuardError, validate_outbound_url
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

CONNECT_TIMEOUT_SECONDS = 30.0


class PRV_StreamableHTTP_MCPClient(AbstractMCPServerProvider):
    name: ClassVar[str] = "mcp_streamable_http"
    friendly_name: ClassVar[str] = "MCP server (Streamable HTTP)"
    description: ClassVar[str] = "A remote MCP server over Streamable HTTP"
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="httpx2",
                friendly_name="HTTPX2",
                semver=">=2.13",
                reason="The HTTP client the MCP SDK's transport takes",
            )
        ]
    )
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting("url", "The server's MCP endpoint (https://example.com/mcp)"),
        InstanceSetting(
            "api_key",
            "Bearer token, if the server wants one",
            secret=True,
            field="api_key",
        ),
    )

    @classmethod
    def endpoint(cls, instance: ProviderInstanceModel) -> str:
        url = str(cls.setting(instance, "url") or "").strip()
        if not url:
            raise TransientExternalError(
                "MCP server endpoint not configured", provider=cls.name
            )
        return url

    @classmethod
    def http_client(
        cls, instance: ProviderInstanceModel, statuses: List[int]
    ) -> httpx2.AsyncClient:
        """A client that checks each request's destination and notes each
        error status it is answered with."""

        async def guard(request: httpx2.Request) -> None:
            try:
                await asyncio.to_thread(validate_outbound_url, str(request.url))
            except SSRFGuardError as exc:
                raise InvalidInputExternalError(
                    f"Outbound URL refused by SSRF guard: {exc}", provider=cls.name
                ) from exc

        async def note(response: httpx2.Response) -> None:
            if response.status_code >= 400:
                statuses.append(response.status_code)

        headers: Dict[str, str] = {}
        token = cls.setting(instance, "api_key")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return httpx2.AsyncClient(
            headers=headers,
            timeout=httpx2.Timeout(
                CONNECT_TIMEOUT_SECONDS, read=cls.http_timeout_seconds
            ),
            event_hooks={"request": [guard], "response": [note]},
            trust_env=False,
        )

    @classmethod
    def transport(
        cls, instance: ProviderInstanceModel, statuses: List[int]
    ) -> AbstractAsyncContextManager[Any]:
        url = cls.endpoint(instance)
        try:
            validate_outbound_url(url)
        except SSRFGuardError as exc:
            raise InvalidInputExternalError(
                f"Outbound URL refused by SSRF guard: {exc}", provider=cls.name
            ) from exc
        return cls.connected(url, cls.http_client(instance, statuses))

    @staticmethod
    @asynccontextmanager
    async def connected(url: str, client: httpx2.AsyncClient) -> AsyncIterator[Any]:
        """The transport over ``client``, which the SDK leaves open when it
        is handed one, closed with it."""
        async with client:
            async with streamable_http_client(url, http_client=client) as streams:
                yield streams
