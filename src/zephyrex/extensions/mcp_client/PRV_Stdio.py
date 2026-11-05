# SPDX-License-Identifier: AGPL-3.0-or-later
"""A local MCP server the server process starts and talks to over its
standard input and output.

Starting a server runs a command on this host, so the commands are the
operator's: they are declared in the JSON file ``MCP_STDIO_SERVERS_FILE``
names, in the shape MCP clients share::

    {"mcpServers": {"files": {"command": "npx",
                              "args": ["-y", "@modelcontextprotocol/server-filesystem", "/srv/docs"],
                              "env": {"LOG_LEVEL": "warn"}}}}

An instance only picks one of them by name (its ``server`` setting); no
setting or ability argument can name a command. The command gets the
SDK's minimal environment plus the entry's own ``env``, not the server's
secrets."""

import json
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping, Tuple

from mcp.client.stdio import StdioServerParameters, stdio_client

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import TransientExternalError
from zephyrex.extensions.mcp_client.EXT_MCPClient import AbstractMCPServerProvider
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

SERVERS_FILE_ENV = "MCP_STDIO_SERVERS_FILE"


def declared_server(declared: Any, name: str) -> StdioServerParameters:
    """Entry ``name`` of a servers file's ``mcpServers``, checked."""
    servers = declared.get("mcpServers") if isinstance(declared, Mapping) else None
    if not isinstance(servers, dict):
        raise ValueError("the file holds no mcpServers object")
    if name not in servers:
        raise KeyError(name)
    entry = servers[name]
    if not isinstance(entry, dict):
        raise ValueError(f"{name}: an entry is an object")
    command, args = entry.get("command"), entry.get("args", [])
    entry_env, cwd = entry.get("env", {}), entry.get("cwd")
    if not isinstance(command, str) or not command:
        raise ValueError(f"{name}: command is a non-empty string")
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ValueError(f"{name}: args is a list of strings")
    if not isinstance(entry_env, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in entry_env.items()
    ):
        raise ValueError(f"{name}: env maps names to strings")
    if cwd is not None and not isinstance(cwd, str):
        raise ValueError(f"{name}: cwd is a path")
    return StdioServerParameters(
        command=command, args=args, env=entry_env or None, cwd=cwd
    )


class PRV_Stdio_MCPClient(AbstractMCPServerProvider):
    name: ClassVar[str] = "mcp_stdio"
    friendly_name: ClassVar[str] = "MCP server (local command)"
    description: ClassVar[str] = (
        "A local MCP server the operator has declared, run over stdio"
    )
    _env: ClassVar[Dict[str, Any]] = {SERVERS_FILE_ENV: ""}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "server", "The server's name in the operator's MCP servers file"
        ),
    )

    @classmethod
    def parameters(cls, instance: ProviderInstanceModel) -> StdioServerParameters:
        name = str(cls.setting(instance, "server") or "").strip()
        path = env(SERVERS_FILE_ENV).strip()
        if not name or not path:
            raise TransientExternalError(
                f"MCP stdio server not configured (the instance's server and "
                f"{SERVERS_FILE_ENV})",
                provider=cls.name,
            )
        try:
            declared = json.loads(Path(path).read_text())
            return declared_server(declared, name)
        except KeyError:
            raise TransientExternalError(
                f"The MCP servers file declares no server {name!r}", provider=cls.name
            ) from None
        except (OSError, ValueError) as exc:
            raise TransientExternalError(
                f"The MCP servers file cannot be used: {exc}", provider=cls.name
            ) from exc

    @classmethod
    def transport(
        cls, instance: ProviderInstanceModel, statuses: List[int]
    ) -> AbstractAsyncContextManager[Any]:
        return stdio_client(cls.parameters(instance))
