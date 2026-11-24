# SPDX-License-Identifier: AGPL-3.0-or-later
"""Foundry VTT, through the world's foundry-mcp server: an MCP server that
keeps the world's GM session (its own account and headless browser) and
offers the world as tools. The app is its MCP client, through the
``mcp_client`` extension: the instance's ``mcp_server`` names an
``mcp_client`` server instance (its endpoint and bearer token), and every
read and write is one of the server's tools (``list_documents``,
``get_document``, ``list_embedded``, ``create_documents``,
``update_documents``, ``foundry_status``). The app never joins the world
itself, so it never signs the server's GM out.

Writes need both switches: the server's own (``FOUNDRY_ALLOW_WRITES``) and
this instance's ``allow_writes``, off by default. The instance's ``mode``
says who runs the table: ``assistant`` (the default: a human GM runs it and
the app assists, so every write waits for that GM's approval, the call's
``approved_by``) or ``campaign`` (the app's AI is the GM and Foundry its
tabletop, so writes need no approval). A world serving both is two
instances.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.external_vtt.EXT_External_VTT import (
    AbstractVTTProvider,
    Doc,
)
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    PermanentExternalError,
    TransientExternalError,
)
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

TRUE = ("1", "true", "yes", "on")
ASSISTANT = "assistant"
CAMPAIGN = "campaign"
MODES = (ASSISTANT, CAMPAIGN)
WRITE_TOOLS = frozenset({"create_documents", "update_documents"})


def tool_value(result: Dict[str, Any]) -> Any:
    """A foundry-mcp tool's JSON value from an ``mcp_client`` call result:
    its structured content (a non-object value comes wrapped as
    ``{"result": ...}``), else its first text content parsed as JSON. A tool
    that reported an error raises, with the server's own words."""
    texts = [
        item.get("text", "")
        for item in result.get("content") or []
        if isinstance(item, dict) and item.get("type") == "text"
    ]
    if result.get("is_error"):
        message = (texts[0] if texts else "the tool failed").strip()
        if "FOUNDRY_ALLOW_WRITES" in message or "read-only" in message:
            raise PermanentExternalError(
                f"the world's server refuses writes: {message}"
            )
        raise TransientExternalError(f"the world's server: {message}")
    structured = result.get("structured_content")
    if isinstance(structured, dict) and set(structured) == {"result"}:
        return structured["result"]
    if structured is not None:
        return structured
    if texts:
        try:
            return json.loads(texts[0])
        except ValueError:
            return texts[0]
    return None


def _ids(value: Any) -> List[str]:
    if not isinstance(value, list):
        raise InvalidInputExternalError(f"the world's server answered {value!r}")
    return [str(item) for item in value]


class PRV_Foundry(AbstractVTTProvider):
    name: ClassVar[str] = "foundry"
    friendly_name: ClassVar[str] = "Foundry VTT"
    description: ClassVar[str] = "A Foundry VTT world, through its foundry-mcp server"
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "mcp_server",
            "The mcp_client server instance (name or id) of the world's "
            "foundry-mcp server",
        ),
        InstanceSetting(
            "allow_writes", "true to let the app write to the world", default="false"
        ),
        InstanceSetting(
            "mode",
            "assistant (a human GM runs the table; every write needs their "
            "approval) or campaign (the app's AI is the GM)",
            default=ASSISTANT,
        ),
        InstanceSetting(
            "webhook_secret",
            "The secret the world's foundry-webhooks module signs its events with",
            secret=True,
        ),
    )

    @classmethod
    def _server(cls, instance: ProviderInstanceModel) -> str:
        server = (cls.setting(instance, "mcp_server") or "").strip()
        if not server:
            raise TransientExternalError(
                "Foundry world not configured: mcp_server", provider=cls.name
            )
        return server

    @classmethod
    def writes_allowed(cls, instance: ProviderInstanceModel) -> bool:
        return (cls.setting(instance, "allow_writes") or "").strip().lower() in TRUE

    @classmethod
    def mode(cls, instance: ProviderInstanceModel) -> str:
        value = (cls.setting(instance, "mode") or ASSISTANT).strip().lower()
        return value if value in MODES else ASSISTANT

    @classmethod
    def check_write(cls, instance: ProviderInstanceModel, approved: bool) -> None:
        """A write this world takes now: its writes on, and in assistant mode
        approved by its GM."""
        if not cls.writes_allowed(instance):
            raise PermanentExternalError(
                "writes are off for this world (allow_writes)", provider=cls.name
            )
        if cls.mode(instance) == ASSISTANT and not approved:
            raise PermanentExternalError(
                "this world is in assistant mode: a write needs its GM's "
                "approval (approved_by)",
                provider=cls.name,
            )

    @classmethod
    async def tool(
        cls,
        instance: ProviderInstanceModel,
        name: str,
        arguments: Dict[str, Any],
        approved: bool = False,
    ) -> Any:
        """Call the world's server's tool ``name``; its JSON value."""
        server = cls._server(instance)
        if name in WRITE_TOOLS:
            cls.check_write(instance, approved)
        try:
            from zephyrex.extensions.mcp_client.EXT_MCPClient import EXT_MCPClient
        except ImportError:
            raise TransientExternalError(
                "the mcp_client extension is not loaded", provider=cls.name
            ) from None
        # The world's server is the operator's: it is reached as the server
        # (ROOT), whoever may use this world's instance.
        result: Dict[str, Any] = await EXT_MCPClient.call_tool(
            env("ROOT_ID"), server, name, arguments
        )
        return tool_value(result)

    @classmethod
    async def status(cls, instance: ProviderInstanceModel) -> Doc:
        status = await cls.tool(instance, "foundry_status", {})
        return {
            **(status if isinstance(status, dict) else {}),
            "writes": cls.writes_allowed(instance),
            "mode": cls.mode(instance),
        }

    @classmethod
    async def list_documents(
        cls,
        instance: ProviderInstanceModel,
        document_type: str,
        name_filter: str,
        fields: List[str],
    ) -> List[Doc]:
        docs: List[Doc] = await cls.tool(
            instance,
            "list_documents",
            {
                "document_type": document_type,
                "name_filter": name_filter,
                "fields": fields,
            },
        )
        return docs

    @classmethod
    async def get_document(
        cls, instance: ProviderInstanceModel, uuid: str, embedded: bool
    ) -> Doc:
        doc: Doc = await cls.tool(
            instance, "get_document", {"uuid": uuid, "embedded": embedded}
        )
        return doc

    @classmethod
    async def list_embedded(
        cls,
        instance: ProviderInstanceModel,
        parent_uuid: str,
        embedded_name: str,
        fields: List[str],
    ) -> List[Doc]:
        docs: List[Doc] = await cls.tool(
            instance,
            "list_embedded",
            {
                "parent_uuid": parent_uuid,
                "embedded_name": embedded_name,
                "fields": fields,
            },
        )
        return docs

    @classmethod
    async def create_documents(
        cls,
        instance: ProviderInstanceModel,
        document_type: str,
        docs: List[Doc],
        parent_uuid: Optional[str],
        approved: bool = False,
    ) -> List[str]:
        return _ids(
            await cls.tool(
                instance,
                "create_documents",
                {
                    "document_type": document_type,
                    "docs": docs,
                    "parent_uuid": parent_uuid,
                },
                approved,
            )
        )

    @classmethod
    async def update_documents(
        cls,
        instance: ProviderInstanceModel,
        document_type: str,
        updates: List[Doc],
        parent_uuid: Optional[str],
        approved: bool = False,
    ) -> List[str]:
        return _ids(
            await cls.tool(
                instance,
                "update_documents",
                {
                    "document_type": document_type,
                    "updates": updates,
                    "parent_uuid": parent_uuid,
                },
                approved,
            )
        )
