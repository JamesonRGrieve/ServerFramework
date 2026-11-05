# SPDX-License-Identifier: AGPL-3.0-or-later
"""MCP client: names, arguments and the stdio servers file checked; real
MCP servers (the SDK's own, over Streamable HTTP and over stdio) listed,
called and read; a tool's failure reported in its result; refusals,
missing servers and refused tokens typed; loopback refused until allowed
egress; and a server reached through its instance by name and no other."""

import socket
import sys
import threading
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, Iterator, Optional

import pytest
import uvicorn
from fastapi import HTTPException
from mcp.server.mcpserver import MCPServer

from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    PermanentExternalError,
    TransientExternalError,
)
from zephyrex.extensions.mcp_client.EXT_MCPClient import (
    EXT_MCPClient,
    arguments_object,
    leaves,
    plain_name,
    prompt_arguments,
)
from zephyrex.extensions.mcp_client.PRV_Stdio import (
    SERVERS_FILE_ENV,
    PRV_Stdio_MCPClient,
    declared_server,
)
from zephyrex.extensions.mcp_client.PRV_StreamableHTTP import (
    PRV_StreamableHTTP_MCPClient,
)
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Auth import UserModel
from zephyrex.logic.BLL_Providers import ProviderInstanceManager
from zephyrex.testing.factories import provider_instance_as
from zephyrex.extensions.conftest import build_extension_app


@pytest.fixture(scope="module")
def extension_app(request: pytest.FixtureRequest) -> Any:
    """This extension's app with the auth companions: tests here look provider
    instances up as a user, which the permission query needs them for."""
    return build_extension_app(request, with_companions=True)


TOKEN = "test-bearer-token"
SERVER_SOURCE = '''
from mcp.server.mcpserver import MCPServer

server = MCPServer("stdio-test")


@server.tool()
def echo(text: str) -> str:
    """Say it back."""
    return text


server.run("stdio")
'''


def build_server() -> MCPServer:
    server = MCPServer("test")

    @server.tool()
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    @server.tool()
    def fail() -> str:
        """Always fails."""
        raise ValueError("this tool fails")

    @server.resource("note://greeting")
    def greeting() -> str:
        return "hello"

    @server.prompt()
    def review(language: str) -> str:
        """Ask for a review."""
        return f"Review this {language} code."

    return server


def requiring(token: str, app: Any) -> Any:
    """``app`` answering 401 to a request without ``token``."""

    async def guarded(scope: Dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            if headers.get(b"authorization") != f"Bearer {token}".encode():
                await send(
                    {"type": "http.response.start", "status": 401, "headers": []}
                )
                await send({"type": "http.response.body", "body": b""})
                return
        await app(scope, receive, send)

    return guarded


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture(scope="module")
def mcp_hosts() -> Iterator[Dict[str, str]]:
    """Two real MCP servers on loopback: one open, one wanting ``TOKEN``."""
    servers, hosts = [], {}
    for label, wrap in (("open", None), ("token", TOKEN)):
        app = build_server().streamable_http_app()
        port = free_port()
        server = uvicorn.Server(
            uvicorn.Config(
                requiring(wrap, app) if wrap else app,
                host="127.0.0.1",
                port=port,
                log_level="warning",
                lifespan="on",
            )
        )
        threading.Thread(target=server.run, daemon=True).start()
        servers.append(server)
        hosts[label] = f"127.0.0.1:{port}"
    deadline = time.monotonic() + 10
    while not all(s.started for s in servers) and time.monotonic() < deadline:
        time.sleep(0.05)
    yield hosts
    for server in servers:
        server.should_exit = True


@pytest.fixture
def allowed(mcp_hosts, monkeypatch) -> Dict[str, str]:
    monkeypatch.setenv("EGRESS_ALLOWED_HOSTS", ",".join(mcp_hosts.values()))
    return {label: f"http://{host}/mcp" for label, host in mcp_hosts.items()}


@pytest.fixture
def http_server(provider_instance) -> Callable[..., Any]:
    def _make(url: str, token: Optional[str] = None) -> Any:
        return provider_instance(
            PRV_StreamableHTTP_MCPClient, api_key=token, settings={"url": url}
        )

    return _make


async def raises(kind: type, call: Awaitable[Any]) -> BaseException:
    with pytest.raises(kind) as raised:
        await call
    return raised.value


class TestChecks:
    def test_names(self):
        assert plain_name("add", "tool") == "add"
        for bad in ("", "   ", "x" * 257):
            with pytest.raises(InvalidInputExternalError):
                plain_name(bad, "tool")

    def test_arguments(self):
        assert arguments_object(None) == {}
        assert arguments_object({"a": 1}) == {"a": 1}
        with pytest.raises(InvalidInputExternalError):
            arguments_object([1, 2])
        assert prompt_arguments({"language": "Python"}) == {"language": "Python"}
        with pytest.raises(InvalidInputExternalError):
            prompt_arguments({"count": 3})

    def test_leaves_of_nested_groups(self):
        inner = ValueError("x")
        group = ExceptionGroup("a", [ExceptionGroup("b", [inner]), KeyError("y")])
        assert [type(e) for e in leaves(group)] == [ValueError, KeyError]

    def test_a_declared_stdio_server(self):
        found = declared_server(
            {"mcpServers": {"files": {"command": "npx", "args": ["-y", "pkg"]}}},
            "files",
        )
        assert found.command == "npx" and found.args == ["-y", "pkg"]
        assert found.env is None

    @pytest.mark.parametrize(
        "declared",
        [
            [],
            {"servers": {}},
            {"mcpServers": {"files": "npx"}},
            {"mcpServers": {"files": {"command": ""}}},
            {"mcpServers": {"files": {"command": "npx", "args": "-y"}}},
            {"mcpServers": {"files": {"command": "npx", "env": {"A": 1}}}},
        ],
    )
    def test_a_malformed_servers_file(self, declared):
        with pytest.raises(ValueError):
            declared_server(declared, "files")

    def test_an_undeclared_stdio_server(self):
        with pytest.raises(KeyError):
            declared_server({"mcpServers": {}}, "files")


class TestStreamableHTTP:
    async def test_tools(self, allowed, http_server):
        server = http_server(allowed["open"])
        tools = {t["name"]: t for t in await PRV_StreamableHTTP_MCPClient.tools(server)}
        assert tools["add"]["description"] == "Add two numbers."
        assert tools["add"]["input_schema"]["required"] == ["a", "b"]

    async def test_a_call(self, allowed, http_server):
        server = http_server(allowed["open"])
        result = await PRV_StreamableHTTP_MCPClient.call(
            server, "add", {"a": 2, "b": 3}
        )
        assert result["is_error"] is False
        assert result["structured_content"] == {"result": 5}
        assert result["content"][0] == {"type": "text", "text": "5"}

    async def test_a_failing_tool_reports_it_in_its_result(self, allowed, http_server):
        server = http_server(allowed["open"])
        failed = await PRV_StreamableHTTP_MCPClient.call(server, "fail", {})
        unknown = await PRV_StreamableHTTP_MCPClient.call(server, "nope", {})
        assert failed["is_error"] and unknown["is_error"]

    async def test_resources(self, allowed, http_server):
        server = http_server(allowed["open"])
        listed = await PRV_StreamableHTTP_MCPClient.resources(server)
        assert [r["uri"] for r in listed] == ["note://greeting"]
        read = await PRV_StreamableHTTP_MCPClient.read(server, "note://greeting")
        assert read["contents"][0]["text"] == "hello"

    async def test_an_unknown_resource_is_the_callers_error(self, allowed, http_server):
        server = http_server(allowed["open"])
        await raises(
            InvalidInputExternalError,
            PRV_StreamableHTTP_MCPClient.read(server, "note://missing"),
        )

    async def test_prompts(self, allowed, http_server):
        server = http_server(allowed["open"])
        listed = await PRV_StreamableHTTP_MCPClient.prompts(server)
        assert [p["name"] for p in listed] == ["review"]
        prompt = await PRV_StreamableHTTP_MCPClient.prompt(
            server, "review", {"language": "Rust"}
        )
        assert prompt["messages"][0]["content"]["text"] == "Review this Rust code."

    async def test_a_token(self, allowed, http_server):
        refused = http_server(allowed["token"])
        await raises(AuthExternalError, PRV_StreamableHTTP_MCPClient.tools(refused))
        wrong = http_server(allowed["token"], token="wrong")
        await raises(AuthExternalError, PRV_StreamableHTTP_MCPClient.tools(wrong))
        right = http_server(allowed["token"], token=TOKEN)
        assert await PRV_StreamableHTTP_MCPClient.tools(right)

    async def test_a_path_with_no_server(self, allowed, http_server):
        server = http_server(allowed["open"].replace("/mcp", "/elsewhere"))
        await raises(PermanentExternalError, PRV_StreamableHTTP_MCPClient.tools(server))

    async def test_nothing_listening(self, http_server, monkeypatch):
        host = f"127.0.0.1:{free_port()}"
        monkeypatch.setenv("EGRESS_ALLOWED_HOSTS", host)
        server = http_server(f"http://{host}/mcp")
        await raises(TransientExternalError, PRV_StreamableHTTP_MCPClient.tools(server))

    async def test_loopback_needs_an_egress_allowance(self, mcp_hosts, http_server):
        server = http_server(f"http://{mcp_hosts['open']}/mcp")
        error = await raises(
            InvalidInputExternalError, PRV_StreamableHTTP_MCPClient.tools(server)
        )
        assert "SSRF" in str(error)

    async def test_no_endpoint(self, http_server):
        await raises(
            TransientExternalError, PRV_StreamableHTTP_MCPClient.tools(http_server(""))
        )


class TestStdio:
    @pytest.fixture
    def servers_file(self, tmp_path, set_env):
        script = tmp_path / "server.py"
        script.write_text(SERVER_SOURCE)
        declared = tmp_path / "mcp.json"
        declared.write_text(
            '{"mcpServers": {"echo": {"command": "%s", "args": ["%s"]}}}'
            % (sys.executable, script)
        )
        set_env(SERVERS_FILE_ENV, str(declared))
        return declared

    async def test_a_declared_server(self, servers_file, provider_instance):
        instance = provider_instance(PRV_Stdio_MCPClient, settings={"server": "echo"})
        result = await PRV_Stdio_MCPClient.call(instance, "echo", {"text": "hi"})
        assert result["structured_content"] == {"result": "hi"}

    async def test_an_undeclared_server(self, servers_file, provider_instance):
        instance = provider_instance(PRV_Stdio_MCPClient, settings={"server": "rm"})
        error = await raises(
            TransientExternalError, PRV_Stdio_MCPClient.tools(instance)
        )
        assert "declares no server 'rm'" in str(error)

    async def test_no_servers_file(self, provider_instance, set_env):
        set_env(SERVERS_FILE_ENV, "")
        instance = provider_instance(PRV_Stdio_MCPClient, settings={"server": "echo"})
        await raises(TransientExternalError, PRV_Stdio_MCPClient.tools(instance))


ROOT = env("ROOT_ID")


def a_user(registry: Any) -> str:
    """A new user's id."""
    users = UserModel.DB(registry.DB.manager.Base)
    email = f"mcp-{uuid.uuid4().hex}@example.com"
    user: Any = users.create(
        requester_id=env("SYSTEM_ID"),
        model_registry=registry,
        return_type="dto",
        override_dto=UserModel,
        email=email,
        username=email.split("@")[0],
        first_name="Test",
        last_name="User",
        display_name="Test User",
    )
    return str(user.id)


class TestAbilities:
    @pytest.fixture
    def servers(self, allowed, http_server, rotation_over, monkeypatch):
        working = http_server(allowed["open"])
        unset = http_server("")
        monkeypatch.setattr(
            EXT_MCPClient, "_root_rotation_cache", rotation_over(unset, working)
        )
        return unset, working

    @pytest.fixture
    def owned(self, allowed, extension_app, rotation_over, monkeypatch):
        """A server one user added for themselves, with their token, and a
        user who did not."""
        registry = extension_app.state.model_registry
        owner, stranger = a_user(registry), a_user(registry)
        server = provider_instance_as(
            registry,
            PRV_StreamableHTTP_MCPClient.name,
            {"url": allowed["token"]},
            requester_id=owner,
            scope="user",
            api_key=TOKEN,
        )
        monkeypatch.setattr(
            EXT_MCPClient, "_root_rotation_cache", rotation_over(server)
        )
        return owner, stranger, server

    async def test_list_servers(self, servers):
        unset, working = servers
        listed = {
            s["name"]: s["provider"] for s in await EXT_MCPClient.list_mcp_servers(ROOT)
        }
        assert listed[working.name] == "mcp_streamable_http"
        assert unset.name in listed

    async def test_a_server_by_name(self, servers):
        _, working = servers
        result = await EXT_MCPClient.call_tool(
            ROOT, working.name, "add", {"a": 1, "b": 1}
        )
        assert result["structured_content"] == {"result": 2}

    async def test_a_server_and_no_other(self, servers):
        """The unconfigured server must not fall over to the working one
        behind it in the rotation."""
        unset, _ = servers
        error = await raises(HTTPException, EXT_MCPClient.list_tools(ROOT, unset.name))
        attempted = error.detail["attempted_providers"]
        assert {a["provider_instance_id"] for a in attempted} == {str(unset.id)}

    async def test_an_unknown_server(self, servers):
        error = await raises(
            HTTPException, EXT_MCPClient.list_tools(ROOT, "no-such-server")
        )
        assert error.status_code == 404

    async def test_arguments_are_checked_before_the_server(self, servers):
        _, working = servers
        await raises(
            InvalidInputExternalError,
            EXT_MCPClient.call_tool(ROOT, working.name, "", {}),
        )
        await raises(
            InvalidInputExternalError,
            EXT_MCPClient.get_prompt(ROOT, working.name, "review", {"language": 3}),
        )

    async def test_a_deleted_server_is_never_reached(self, servers, extension_app):
        """ROOT's reads include deleted rows: a deleted instance must be
        neither listed nor reached, even when a live one took its name."""
        unset, working = servers
        instances = ProviderInstanceManager(
            model_registry=extension_app.state.model_registry,
            requester_id=ROOT,
        )
        instances.delete(str(unset.id))
        instances.update(str(working.id), name=unset.name)

        listed = await EXT_MCPClient.list_mcp_servers(ROOT)
        assert str(unset.id) not in [s["id"] for s in listed]
        assert str(working.id) in [s["id"] for s in listed]
        error = await raises(
            HTTPException, EXT_MCPClient.list_tools(ROOT, str(unset.id))
        )
        assert error.status_code == 404
        result = await EXT_MCPClient.call_tool(
            ROOT, unset.name, "add", {"a": 2, "b": 3}
        )
        assert result["structured_content"] == {"result": 5}

    async def test_a_users_server_is_reached_with_their_token(self, owned):
        owner, _, server = owned
        listed = await EXT_MCPClient.list_mcp_servers(owner)
        assert str(server.id) in [s["id"] for s in listed]
        result = await EXT_MCPClient.call_tool(
            owner, server.name, "add", {"a": 1, "b": 2}
        )
        assert result["structured_content"] == {"result": 3}

    async def test_another_users_server_is_not_theirs_to_use(self, owned):
        """A user cannot list, or reach with its stored token, a server
        they cannot see, by its name or its id."""
        _, stranger, server = owned
        listed = await EXT_MCPClient.list_mcp_servers(stranger)
        assert str(server.id) not in [s["id"] for s in listed]
        for ref in (server.name, str(server.id)):
            for call in (
                EXT_MCPClient.call_tool(stranger, ref, "add", {"a": 1, "b": 2}),
                EXT_MCPClient.list_tools(stranger, ref),
                EXT_MCPClient.list_resources(stranger, ref),
                EXT_MCPClient.read_resource(stranger, ref, "note://greeting"),
                EXT_MCPClient.list_prompts(stranger, ref),
                EXT_MCPClient.get_prompt(stranger, ref, "review", {"language": "Go"}),
            ):
                error = await raises(HTTPException, call)
                assert error.status_code == 404

    async def test_an_ability_names_its_user(self, owned):
        _, _, server = owned
        for call in (
            EXT_MCPClient.list_mcp_servers(""),
            EXT_MCPClient.list_tools(" ", server.name),
        ):
            error = await raises(HTTPException, call)
            assert error.status_code == 400
