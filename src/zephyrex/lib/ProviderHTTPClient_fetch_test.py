# SPDX-License-Identifier: AGPL-3.0-or-later
"""ProviderHTTPClient.fetch against a local server: the body cap, redirects
followed and each one checked by the SSRF guard (a public page cannot
bounce the server into a private address), redirect loops, and typed
errors."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator, Tuple

import pytest

from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.lib.ProviderHTTPClient import ClientPolicy, ProviderHTTPClient

BIG_BYTES = 2 * 1024 * 1024
CAP = 1000
ROUTES = {
    "/small": (200, {"Content-Type": "text/plain"}, b"hello"),
    "/big": (200, {"Content-Type": "text/plain"}, b"x" * BIG_BYTES),
    "/hop": (302, {"Location": "/small"}, b""),
    "/to-metadata": (
        302,
        {"Location": "http://169.254.169.254/latest/meta-data/"},
        b"",
    ),
    "/loop": (302, {"Location": "/loop"}, b""),
    "/missing": (404, {"Content-Type": "text/plain"}, b"no such page"),
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        status, headers, body = ROUTES.get(self.path, (404, {}, b""))
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # The client stopped reading at its cap.

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture(scope="module")
def local_server() -> Iterator[Tuple[str, str]]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host = f"127.0.0.1:{server.server_address[1]}"
    yield f"http://{host}", host
    server.shutdown()
    server.server_close()


@pytest.fixture
def base(local_server: Tuple[str, str], monkeypatch: pytest.MonkeyPatch) -> str:
    """The local server, which only an explicit egress allowance reaches."""
    url, host = local_server
    monkeypatch.setenv("EGRESS_ALLOWED_HOSTS", host)
    return url


@pytest.fixture
def client() -> ProviderHTTPClient:
    return ProviderHTTPClient(policy=ClientPolicy(timeout=10), provider_name="fetch")


async def test_a_body(client, base):
    fetched = await client.fetch(f"{base}/small", max_bytes=CAP)
    assert fetched.body == b"hello" and not fetched.truncated
    assert fetched.content_type == "text/plain" and fetched.status == 200


async def test_the_cap_stops_reading(client, base):
    fetched = await client.fetch(f"{base}/big", max_bytes=CAP)
    assert len(fetched.body) == CAP and fetched.truncated


async def test_a_redirect_is_followed(client, base):
    fetched = await client.fetch(f"{base}/hop", max_bytes=CAP)
    assert fetched.url == f"{base}/small" and fetched.body == b"hello"


async def test_a_redirect_into_a_private_address_is_refused(client, base):
    with pytest.raises(InvalidInputExternalError, match="SSRF"):
        await client.fetch(f"{base}/to-metadata", max_bytes=CAP)


async def test_a_redirect_loop_ends(client, base):
    with pytest.raises(InvalidInputExternalError, match="redirected more than"):
        await client.fetch(f"{base}/loop", max_bytes=CAP, max_redirects=3)


async def test_an_error_status_is_typed(client, base):
    with pytest.raises(InvalidInputExternalError) as raised:
        await client.fetch(f"{base}/missing", max_bytes=CAP)
    assert raised.value.upstream_status == 404


async def test_a_private_address_is_refused_without_an_allowance(client, local_server):
    url, _ = local_server
    with pytest.raises(InvalidInputExternalError, match="SSRF"):
        await client.fetch(f"{url}/small", max_bytes=CAP)
