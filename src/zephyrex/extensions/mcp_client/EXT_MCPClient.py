# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tools, resources and prompts on other Model Context Protocol servers.

Each provider instance is one MCP server: a Streamable HTTP endpoint (its
URL and an optional bearer token), or a local command the operator has
declared in the stdio servers file. Every ability names its server (the
instance's name or id) and runs against that server only.

A connection lasts one ability call: the client negotiates the protocol,
makes its request and closes the session. Results come back as the
protocol's own JSON shapes. A tool that fails reports ``is_error`` in its
result, as the protocol does; a refused request (an unknown resource, a
bad prompt argument) is an invalid-input error.
"""

from abc import abstractmethod
from contextlib import AbstractAsyncContextManager
from typing import Any, Awaitable, Callable, ClassVar, Dict, List, Optional, Set, Tuple

from mcp import Client
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ability,
)
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    BaseExternalError,
    InvalidInputExternalError,
    PermanentExternalError,
    TransientExternalError,
)
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

MCP_REQUEST_TIMEOUT_SECONDS = 60.0
MAX_LIST_PAGES = 20
MAX_NAME_LENGTH = 256

Session = Callable[[Client], Awaitable[Any]]


def leaves(error: BaseException) -> List[BaseException]:
    """The errors inside ``error``'s (possibly nested) exception groups."""
    if isinstance(error, BaseExceptionGroup):
        return [leaf for inner in error.exceptions for leaf in leaves(inner)]
    return [error]


def plain_name(value: str, what: str) -> str:
    if not value or not value.strip() or len(value) > MAX_NAME_LENGTH:
        raise InvalidInputExternalError(
            f"{what} is 1-{MAX_NAME_LENGTH} characters, not {value!r}"
        )
    return value


def arguments_object(arguments: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if arguments is None:
        return {}
    if not isinstance(arguments, dict):
        raise InvalidInputExternalError("arguments is a JSON object")
    return arguments


def prompt_arguments(arguments: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """A prompt's arguments: the protocol carries them as strings."""
    found = arguments_object(arguments)
    if not all(isinstance(value, str) for value in found.values()):
        raise InvalidInputExternalError("a prompt's arguments are strings")
    return found


def as_json(result: Any) -> Any:
    return result.model_dump(mode="json", exclude_none=True)


def tool_entry(tool: Any) -> Dict[str, Any]:
    return {
        "name": tool.name,
        "title": tool.title,
        "description": tool.description,
        "input_schema": tool.input_schema,
    }


def call_result(result: Any) -> Dict[str, Any]:
    return {
        "is_error": bool(result.is_error),
        "content": [as_json(item) for item in result.content],
        "structured_content": result.structured_content,
    }


class AbstractMCPServerProvider(AbstractStaticProvider):
    """A way to reach an MCP server; each instance is one server."""

    name: ClassVar[str] = ""
    friendly_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    _abilities: ClassVar[Set[str]] = {
        "list_tools",
        "call_tool",
        "list_resources",
        "read_resource",
        "list_prompts",
        "get_prompt",
    }
    _env: ClassVar[Dict[str, Any]] = {}
    http_timeout_seconds: ClassVar[float] = MCP_REQUEST_TIMEOUT_SECONDS

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    @abstractmethod
    def transport(
        cls, instance: ProviderInstanceModel, statuses: List[int]
    ) -> AbstractAsyncContextManager[Any]:
        """The transport to ``instance``'s server. An HTTP transport appends
        each error status it is answered with to ``statuses``, which the
        protocol's own error does not carry."""

    @classmethod
    def failure(cls, error: BaseException, statuses: List[int]) -> BaseExternalError:
        """``error`` from a session as a typed error. Auth refusals fail
        over; an unreachable or failing server is transient; a request the
        server refuses is the caller's."""
        if any(code in (401, 403) for code in statuses):
            return AuthExternalError(
                f"{cls.friendly_name}: the MCP server refused the credentials",
                provider=cls.name,
                upstream_status=next(c for c in statuses if c in (401, 403)),
            )
        for leaf in leaves(error):
            if isinstance(leaf, BaseExternalError):
                return leaf
            if isinstance(leaf, MCPError):
                if leaf.error.code == INVALID_PARAMS:
                    return InvalidInputExternalError(
                        f"MCP server: {leaf.error.message}", provider=cls.name
                    )
                if any(code >= 500 for code in statuses):
                    break
                return PermanentExternalError(
                    f"MCP server: {leaf.error.message}", provider=cls.name
                )
        return TransientExternalError(
            f"{cls.friendly_name}: the MCP server could not be reached or "
            f"failed ({'; '.join(type(leaf).__name__ for leaf in leaves(error))})",
            provider=cls.name,
        )

    @classmethod
    async def session(cls, instance: ProviderInstanceModel, use: Session) -> Any:
        """Open a session with ``instance``'s server, run ``use`` on it and
        close it."""
        statuses: List[int] = []
        try:
            async with Client(
                cls.transport(instance, statuses),
                read_timeout_seconds=cls.http_timeout_seconds,
                cache=None,
            ) as client:
                return await use(client)
        except BaseExternalError:
            raise
        except Exception as exc:
            raise cls.failure(exc, statuses) from exc

    @classmethod
    async def pages(
        cls, instance: ProviderInstanceModel, method: str, field: str
    ) -> List[Any]:
        """Every page of a list method's ``field``, up to ``MAX_LIST_PAGES``."""

        async def collect(client: Client) -> List[Any]:
            found: List[Any] = []
            cursor: Optional[str] = None
            for _ in range(MAX_LIST_PAGES):
                page = await getattr(client, method)(cursor=cursor)
                found.extend(getattr(page, field))
                cursor = page.next_cursor
                if not cursor:
                    break
            return found

        result: List[Any] = await cls.session(instance, collect)
        return result

    @classmethod
    async def tools(cls, instance: ProviderInstanceModel) -> List[Dict[str, Any]]:
        return [tool_entry(t) for t in await cls.pages(instance, "list_tools", "tools")]

    @classmethod
    async def call(
        cls, instance: ProviderInstanceModel, tool: str, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        async def run(client: Client) -> Dict[str, Any]:
            return call_result(await client.call_tool(tool, arguments))

        result: Dict[str, Any] = await cls.session(instance, run)
        return result

    @classmethod
    async def resources(cls, instance: ProviderInstanceModel) -> List[Dict[str, Any]]:
        found = await cls.pages(instance, "list_resources", "resources")
        return [as_json(resource) for resource in found]

    @classmethod
    async def read(cls, instance: ProviderInstanceModel, uri: str) -> Dict[str, Any]:
        async def run(client: Client) -> Dict[str, Any]:
            result = await client.read_resource(uri)
            return {"contents": [as_json(item) for item in result.contents]}

        result: Dict[str, Any] = await cls.session(instance, run)
        return result

    @classmethod
    async def prompts(cls, instance: ProviderInstanceModel) -> List[Dict[str, Any]]:
        found = await cls.pages(instance, "list_prompts", "prompts")
        return [as_json(prompt) for prompt in found]

    @classmethod
    async def prompt(
        cls, instance: ProviderInstanceModel, name: str, arguments: Dict[str, str]
    ) -> Dict[str, Any]:
        async def run(client: Client) -> Dict[str, Any]:
            result = await client.get_prompt(name, arguments)
            return {
                "description": result.description,
                "messages": [as_json(message) for message in result.messages],
            }

        result: Dict[str, Any] = await cls.session(instance, run)
        return result

    @classmethod
    def services(cls) -> List[str]:
        return ["mcp_client"]


class EXT_MCPClient(AbstractStaticExtension):
    name: ClassVar[str] = "mcp_client"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Tools, resources and prompts on other Model Context Protocol servers"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="mcp",
                friendly_name="MCP SDK",
                semver=">=2.2",
                reason="The Model Context Protocol client",
            ),
            PIP_Dependency(
                name="mcp-types",
                friendly_name="MCP types",
                semver=">=2.2",
                reason="The protocol's error codes",
            ),
        ]
    )
    _abilities: ClassVar[Set[str]] = {
        "list_mcp_servers",
        *AbstractMCPServerProvider._abilities,
    }

    @classmethod
    @ability("list_mcp_servers")
    async def list_mcp_servers(cls) -> List[Dict[str, Any]]:
        """The configured MCP servers: id, name, and how each is reached."""
        return cls.instances_of()

    @classmethod
    @ability("list_tools")
    async def list_tools(cls, server: str) -> List[Dict[str, Any]]:
        """The server's tools: name, title, description and input schema."""
        result: List[Dict[str, Any]] = await cls.rotate_on_instance(server, "tools")
        return result

    @classmethod
    @ability("call_tool")
    async def call_tool(
        cls, server: str, tool: str, arguments: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Call ``tool`` with ``arguments``: its content, structured content,
        and whether the tool reported an error."""
        result: Dict[str, Any] = await cls.rotate_on_instance(
            server, "call", plain_name(tool, "tool"), arguments_object(arguments)
        )
        return result

    @classmethod
    @ability("list_resources")
    async def list_resources(cls, server: str) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = await cls.rotate_on_instance(server, "resources")
        return result

    @classmethod
    @ability("read_resource")
    async def read_resource(cls, server: str, uri: str) -> Dict[str, Any]:
        result: Dict[str, Any] = await cls.rotate_on_instance(
            server, "read", plain_name(uri, "uri")
        )
        return result

    @classmethod
    @ability("list_prompts")
    async def list_prompts(cls, server: str) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = await cls.rotate_on_instance(server, "prompts")
        return result

    @classmethod
    @ability("get_prompt")
    async def get_prompt(
        cls, server: str, name: str, arguments: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """The prompt's messages, filled with ``arguments`` (strings)."""
        result: Dict[str, Any] = await cls.rotate_on_instance(
            server, "prompt", plain_name(name, "prompt"), prompt_arguments(arguments)
        )
        return result
