# SPDX-License-Identifier: AGPL-3.0-or-later
"""A real local HTTP server for tests that call over the network: each
path answers a fixed status, headers and body, or a function of the request
received (an API whose answer depends on what is sent), whatever the
method, and every request it receives is recorded. It listens on loopback,
which the SSRF guard refuses, so the fixture also allows its host in
``EGRESS_ALLOWED_HOSTS`` for the test."""

import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, Iterator, List, Mapping, Tuple, Union

import pytest

Answer = Tuple[int, Mapping[str, str], bytes]


@dataclass(frozen=True)
class ReceivedRequest:
    method: str
    path: str
    headers: Mapping[str, str]
    body: bytes


Route = Union[Answer, Callable[[ReceivedRequest], Answer]]


@dataclass(frozen=True)
class LocalServer:
    base_url: str
    host: str
    requests: List[ReceivedRequest] = field(default_factory=list)


def _handler(routes: Mapping[str, Route], received: List[ReceivedRequest]) -> type:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.do_GET()

        def do_GET(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            request = ReceivedRequest(
                self.command,
                self.path,
                {k.lower(): v for k, v in self.headers.items()},
                self.rfile.read(length) if length else b"",
            )
            received.append(request)
            route = routes.get(self.path, (404, {}, b""))
            status, headers, body = route(request) if callable(route) else route
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

    return Handler


@pytest.fixture
def local_http_server(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[..., LocalServer]]:
    """Start a server answering ``routes``; its host is allowed egress for
    this test. Every server started is stopped afterwards."""
    servers = []

    def _start(routes: Dict[str, Route], allow: bool = True) -> LocalServer:
        received: List[ReceivedRequest] = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(routes, received))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        host = f"127.0.0.1:{server.server_address[1]}"
        if allow:
            monkeypatch.setenv("EGRESS_ALLOWED_HOSTS", host)
        return LocalServer(f"http://{host}", host, received)

    yield _start
    for server in servers:
        server.shutdown()
        server.server_close()
