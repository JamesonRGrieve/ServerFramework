# SPDX-License-Identifier: AGPL-3.0-or-later
"""Real HTTP mail APIs for the tests, in process, on loopback ports.

No SendGrid or SMTP2go account or Proxmox Mail Gateway is reachable where the
tests run, so these speak the parts of each API the providers call, so the
providers run unchanged against them (their ``api_url`` setting pointed here,
the loopback host let through the SSRF guard with ``EGRESS_ALLOWED_HOSTS``):

- :class:`SendGridTestServer`, SendGrid's v3 mail send: ``POST
  /v3/mail/send`` with ``Authorization: Bearer <the key>`` takes the JSON
  body and answers 202 with no body; any other key is refused with 401.
- :class:`SMTP2goTestServer`, SMTP2go's v3 API: ``POST /v3/email/send``
  takes the JSON body (``api_key``, ``to``, ``sender``, ``subject``,
  ``text_body``/``html_body``, ``attachments``) and answers ``{"data":
  {"succeeded": 1, "failed": 0, "email_id"}}``; ``POST
  /v3/stats/email_summary`` answers the account's counters. A body whose
  ``api_key`` is not the account's is refused with 401.
- :class:`PMGTestServer`, PMG's REST API under ``/api2/json``: ``version``,
  ``statistics/mail``, ``nodes/<node>/tracker``, ``quarantine/<kind>`` and
  ``POST quarantine/content``, each answer in PMG's ``{"data": ...}``
  envelope. A request whose ``Authorization`` is not ``PMGAPIToken=<the
  token>`` is refused with 401.

Every request is recorded, so a test can prove what was (or was not) sent.
"""

from __future__ import annotations

import hmac
import json
import threading
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType
from typing import Any, Dict, List, Optional, Tuple, Type, TypeVar
from urllib.parse import parse_qs, unquote, urlsplit

LOOPBACK = "127.0.0.1"
Answer = Tuple[int, Any]
Server = TypeVar("Server", bound="LoopbackAPIServer")


@dataclass(frozen=True)
class RecordedRequest:
    method: str
    path: str
    query: Dict[str, str]
    headers: Dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body) if self.body else {}

    def form(self) -> Dict[str, str]:
        return {k: v[0] for k, v in parse_qs(self.body.decode()).items()}


class LoopbackAPIServer:
    """Serves :meth:`answer` on a loopback port while open, recording every
    request."""

    def __init__(self) -> None:
        self.requests: List[RecordedRequest] = []
        self._arrived = threading.Condition()
        self._server = ThreadingHTTPServer((LOOPBACK, 0), self._handler())
        self.port = int(self._server.server_address[1])
        self.host = f"{LOOPBACK}:{self.port}"
        self.base_url = f"http://{self.host}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self: Server) -> Server:
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        self._server.shutdown()
        self._server.server_close()

    def answer(self, request: RecordedRequest) -> Answer:
        raise NotImplementedError

    def wait_for(self, count: int, timeout: float) -> bool:
        """Whether ``count`` requests arrived within ``timeout`` seconds."""
        with self._arrived:
            return self._arrived.wait_for(
                lambda: len(self.requests) >= count, timeout=timeout
            )

    def _handler(self) -> Type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                parts = urlsplit(self.path)
                request = RecordedRequest(
                    method=self.command,
                    path=unquote(parts.path),
                    query={k: v[0] for k, v in parse_qs(parts.query).items()},
                    headers={k.lower(): v for k, v in self.headers.items()},
                    body=self.rfile.read(length) if length else b"",
                )
                status, answer = server.answer(request)
                with server._arrived:
                    server.requests.append(request)
                    server._arrived.notify_all()
                body = b"" if answer is None else json.dumps(answer).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _serve

            def log_message(self, *args: Any) -> None:
                pass

        return Handler


class SMTP2goTestServer(LoopbackAPIServer):
    """An SMTP2go account whose key is ``api_key``."""

    def __init__(self, api_key: str) -> None:
        super().__init__()
        self.api_key = api_key
        self.sent: List[Dict[str, Any]] = []

    @property
    def api_url(self) -> str:
        return f"{self.base_url}/v3"

    def answer(self, request: RecordedRequest) -> Answer:
        body = request.json()
        if not hmac.compare_digest(str(body.get("api_key", "")), self.api_key):
            return 401, {"data": {"error": "Invalid API key"}}
        if request.method == "POST" and request.path == "/v3/email/send":
            self.sent.append(body)
            return 200, {
                "request_id": uuid.uuid4().hex,
                "data": {"succeeded": 1, "failed": 0, "email_id": uuid.uuid4().hex},
            }
        if request.method == "POST" and request.path == "/v3/stats/email_summary":
            return 200, {"data": {"emails": len(self.sent)}}
        return 404, {"data": {"error": "Not found"}}


class SendGridTestServer(LoopbackAPIServer):
    """A SendGrid account whose API key is ``api_key``."""

    def __init__(self, api_key: str) -> None:
        super().__init__()
        self.api_key = api_key
        self.sent: List[Dict[str, Any]] = []

    @property
    def api_url(self) -> str:
        return self.base_url

    def answer(self, request: RecordedRequest) -> Answer:
        presented = request.headers.get("authorization", "")
        if not hmac.compare_digest(presented, f"Bearer {self.api_key}"):
            return 401, {
                "errors": [
                    {
                        "field": None,
                        "message": "The provided authorization grant is invalid, "
                        "expired, or revoked",
                    }
                ]
            }
        if request.method == "POST" and request.path == "/v3/mail/send":
            self.sent.append(request.json())
            return 202, None
        return 404, {"errors": [{"message": "not found"}]}


class PMGTestServer(LoopbackAPIServer):
    """A Proxmox Mail Gateway whose API token is ``token``, with ``data``
    answered for each GET path under ``/api2/json``."""

    PREFIX = "/api2/json"

    def __init__(self, token: str, data: Dict[str, Any]) -> None:
        super().__init__()
        self.token = token
        self.data = data

    @property
    def api_url(self) -> str:
        return f"{self.base_url}{self.PREFIX}"

    def answer(self, request: RecordedRequest) -> Answer:
        presented = request.headers.get("authorization", "")
        if not hmac.compare_digest(presented, f"PMGAPIToken={self.token}"):
            return 401, {"data": None, "message": "authentication failure"}
        path = request.path[len(self.PREFIX) :].strip("/")
        if request.method == "POST" and path == "quarantine/content":
            return 200, {"data": None}
        if request.method == "GET" and path in self.data:
            return 200, {"data": self.data[path]}
        return 404, {"data": None, "message": "no such path"}
