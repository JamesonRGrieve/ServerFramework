# SPDX-License-Identifier: AGPL-3.0-or-later
"""A real SCIM 2.0 service provider on loopback, for tests: it holds users
and groups, answers RFC 7644 requests over HTTP, and is strict about
them. Every request is recorded (with when it arrived), and every way a
request falls short of the protocol is answered with a SCIM error and
noted in ``violations``, so a test can assert the client spoke the
protocol correctly as well as that the result is right.

What it enforces: the bearer token; ``application/scim+json`` requests
and ``Accept``; the core schemas on resources and ``PatchOp`` on patches;
``userName``/``displayName`` uniqueness (409, case-insensitive);
``If-Match`` against the resource's version when ETags are on (412), and
no ``If-Match`` when they are off; no PATCH when PATCH is off (501);
filters in a small grammar (``eq``, ``pr``, ``and``, ``or``, with JSON
string literals), any other filter refused (400 ``invalidFilter``);
paging (``startIndex``/``count``, capped at ``page_size``).

``fail`` makes the next matching request(s) answer a given status, for the
refusals a real service provider gives (429 with ``Retry-After``, 401).
"""

import copy
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlsplit

MEDIA_TYPE = "application/scim+json"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
SCHEMAS = {
    "Users": "urn:ietf:params:scim:schemas:core:2.0:User",
    "Groups": "urn:ietf:params:scim:schemas:core:2.0:Group",
}
NAMES = {"Users": "userName", "Groups": "displayName"}
BASE_PATH = "/scim/v2"
WAIT_SECONDS = 15.0

Reply = Tuple[int, Dict[str, str], Optional[Dict[str, Any]]]
_TOKEN = re.compile(r'\s*("(?:[^"\\]|\\.)*"|\(|\)|[A-Za-z][\w.$:-]*)')
_MEMBER_PATH = re.compile(r'^members\[value eq ("(?:[^"\\]|\\.)*")\]$')


class SCIMError(Exception):
    def __init__(self, status: int, detail: str, scim_type: Optional[str] = None):
        super().__init__(detail)
        self.status, self.detail, self.scim_type = status, detail, scim_type


@dataclass(frozen=True)
class Seen:
    method: str
    path: str
    query: Dict[str, str]
    headers: Mapping[str, str]
    body: Any
    at: float


@dataclass
class Fault:
    method: str
    path: str
    status: int
    headers: Dict[str, str]
    body: Optional[Dict[str, Any]]
    times: int


def _tokens(text: str) -> List[str]:
    found, position = [], 0
    while position < len(text):
        match = _TOKEN.match(text, position)
        if match is None:
            if text[position:].strip():
                raise SCIMError(
                    400, f"bad filter near {text[position:]!r}", "invalidFilter"
                )
            break
        found.append(match.group(1))
        position = match.end()
    return found


def _value(resource: Mapping[str, Any], path: str) -> Any:
    current: Any = resource
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = next(
            (v for k, v in current.items() if k.lower() == part.lower()), None
        )
    return current


def parse_filter(text: str) -> Callable[[Mapping[str, Any]], bool]:
    """A predicate for ``text`` in ``expr := term (or term)*``, ``term :=
    factor (and factor)*``, ``factor := attr pr | attr eq "json" | (expr)``."""
    tokens = _tokens(text)
    position = 0

    def take() -> str:
        nonlocal position
        if position >= len(tokens):
            raise SCIMError(400, "the filter ends early", "invalidFilter")
        position += 1
        return tokens[position - 1]

    def peek() -> Optional[str]:
        return tokens[position].lower() if position < len(tokens) else None

    def factor() -> Callable[[Mapping[str, Any]], bool]:
        token = take()
        if token == "(":
            inner = expression()
            if take() != ")":
                raise SCIMError(400, "unclosed parenthesis", "invalidFilter")
            return inner
        attribute, operator = token, take().lower()
        if operator == "pr":
            return lambda r: _value(r, attribute) not in (None, "", [])
        if operator != "eq":
            raise SCIMError(400, f"unsupported operator {operator}", "invalidFilter")
        literal = take()
        try:
            wanted = json.loads(literal)
        except ValueError:
            raise SCIMError(400, f"bad literal {literal}", "invalidFilter")
        if not isinstance(wanted, str):
            raise SCIMError(400, f"not a string {literal}", "invalidFilter")
        return lambda r: str(_value(r, attribute) or "").lower() == wanted.lower()

    def term() -> Callable[[Mapping[str, Any]], bool]:
        parts = [factor()]
        while peek() == "and":
            take()
            parts.append(factor())
        return lambda r: all(p(r) for p in parts)

    def expression() -> Callable[[Mapping[str, Any]], bool]:
        parts = [term()]
        while peek() == "or":
            take()
            parts.append(term())
        return lambda r: any(p(r) for p in parts)

    predicate = expression()
    if position != len(tokens):
        raise SCIMError(400, f"unexpected {tokens[position]!r}", "invalidFilter")
    return predicate


@dataclass
class SCIMServiceProvider:
    token: str
    patch: bool = True
    etag: bool = True
    page_size: int = 100
    config: bool = True
    patch_answers_no_content: bool = False
    base_url: str = ""
    host: str = ""
    resources: Dict[str, Dict[str, Dict[str, Any]]] = field(
        default_factory=lambda: {"Users": {}, "Groups": {}}
    )
    requests: List[Seen] = field(default_factory=list)
    violations: List[str] = field(default_factory=list)
    faults: List[Fault] = field(default_factory=list)
    _version: int = 0
    _lock: threading.Condition = field(default_factory=threading.Condition)

    # ----- what a test sets up and reads -----------------------------------

    def seed(self, endpoint: str, **attributes: Any) -> Dict[str, Any]:
        """A resource made out-of-band (by an admin, or another IdP)."""
        with self._lock:
            return self._store(endpoint, str(uuid.uuid4()), dict(attributes))

    def edit(self, endpoint: str, remote_id: str, **attributes: Any) -> None:
        """Change a resource out-of-band; its version moves on."""
        with self._lock:
            current = self.resources[endpoint][remote_id]
            current.update(attributes)
            self._store(endpoint, remote_id, current)

    def drop(self, endpoint: str, remote_id: str) -> None:
        with self._lock:
            del self.resources[endpoint][remote_id]

    def fail(
        self,
        method: str,
        path: str,
        status: int,
        headers: Optional[Dict[str, str]] = None,
        body: Optional[Dict[str, Any]] = None,
        times: int = 1,
    ) -> None:
        with self._lock:
            self.faults.append(Fault(method, path, status, headers or {}, body, times))

    def by_name(self, endpoint: str, name: str) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                r
                for r in self.resources[endpoint].values()
                if str(r.get(NAMES[endpoint], "")).lower() == name.lower()
            ]

    def writes(self) -> List[Seen]:
        with self._lock:
            return [r for r in self.requests if r.method != "GET"]

    def wait_for(
        self, check: Callable[[], bool], timeout: float = WAIT_SECONDS
    ) -> bool:
        """Block until ``check()`` holds after some request, or ``timeout``."""
        deadline = time.monotonic() + timeout
        with self._lock:
            while not check():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._lock.wait(remaining)
            return True

    # ----- the protocol ----------------------------------------------------

    def _error(self, error: SCIMError) -> Reply:
        body: Dict[str, Any] = {
            "schemas": [ERROR_SCHEMA],
            "status": str(error.status),
            "detail": error.detail,
        }
        if error.scim_type:
            body["scimType"] = error.scim_type
        return error.status, {}, body

    def _violation(
        self, status: int, detail: str, scim_type: Optional[str] = None
    ) -> SCIMError:
        self.violations.append(detail)
        return SCIMError(status, detail, scim_type)

    def _store(
        self, endpoint: str, remote_id: str, resource: Dict[str, Any]
    ) -> Dict[str, Any]:
        self._version += 1
        resource = {k: v for k, v in resource.items() if k not in ("id", "meta")}
        resource.update(
            id=remote_id,
            schemas=[SCHEMAS[endpoint]],
            meta={
                "resourceType": endpoint[:-1],
                "version": f'W/"{self._version}"',
                "location": f"{self.base_url}/{endpoint}/{remote_id}",
            },
        )
        self.resources[endpoint][remote_id] = resource
        return resource

    def _answer(self, status: int, resource: Dict[str, Any]) -> Reply:
        headers = {"ETag": resource["meta"]["version"]} if self.etag else {}
        if status == 201:
            headers["Location"] = resource["meta"]["location"]
        return status, headers, resource

    def _unique(
        self, endpoint: str, resource: Mapping[str, Any], own_id: Optional[str]
    ) -> None:
        name = str(resource.get(NAMES[endpoint]) or "")
        if not name:
            raise self._violation(400, f"{NAMES[endpoint]} is required", "invalidValue")
        for other in self.resources[endpoint].values():
            if (
                other["id"] != own_id
                and str(other.get(NAMES[endpoint], "")).lower() == name.lower()
            ):
                raise SCIMError(409, f"{name} exists", "uniqueness")

    def _members(self, resource: Mapping[str, Any]) -> None:
        for member in resource.get("members") or []:
            if member.get("value") not in self.resources["Users"]:
                raise self._violation(
                    400, f"no user {member.get('value')}", "invalidValue"
                )

    def _precondition(
        self, current: Mapping[str, Any], headers: Mapping[str, str]
    ) -> None:
        sent = headers.get("if-match")
        if not self.etag:
            if sent:
                raise self._violation(400, "If-Match sent to a server without ETags")
            return
        if not sent:
            self.violations.append("a write without If-Match")
            return
        if sent != current["meta"]["version"]:
            raise SCIMError(412, "version mismatch", "invalidVers")

    def _resource_body(self, endpoint: str, body: Any) -> Dict[str, Any]:
        if not isinstance(body, dict):
            raise self._violation(400, "the body is not a JSON object", "invalidSyntax")
        if SCHEMAS[endpoint] not in (body.get("schemas") or []):
            raise self._violation(
                400, f"missing schema {SCHEMAS[endpoint]}", "invalidSyntax"
            )
        return body

    def _patched(self, current: Dict[str, Any], body: Any) -> Dict[str, Any]:
        if not isinstance(body, dict) or body.get("schemas") != [PATCH_SCHEMA]:
            raise self._violation(
                400, "a PATCH without the PatchOp schema", "invalidSyntax"
            )
        operations = body.get("Operations")
        if not isinstance(operations, list) or not operations:
            raise self._violation(400, "a PATCH without Operations", "invalidSyntax")
        resource = copy.deepcopy(current)
        for operation in operations:
            op = str(operation.get("op", "")).lower()
            path = operation.get("path")
            value = operation.get("value")
            if op not in ("add", "remove", "replace"):
                raise self._violation(400, f"bad op {op!r}", "invalidSyntax")
            if op == "remove":
                member = _MEMBER_PATH.match(path or "")
                if member is None:
                    raise self._violation(
                        400, f"bad remove path {path!r}", "invalidPath"
                    )
                gone = json.loads(member.group(1))
                resource["members"] = [
                    m for m in resource.get("members") or [] if m.get("value") != gone
                ]
            elif path is None:
                if not isinstance(value, dict):
                    raise self._violation(
                        400, "a path-less op needs an object", "invalidValue"
                    )
                resource.update(value)
            elif op == "add" and path == "members":
                have = {m["value"] for m in resource.get("members") or []}
                resource["members"] = list(resource.get("members") or []) + [
                    m for m in value if m["value"] not in have
                ]
            elif "." in path or "[" in path:
                raise self._violation(400, f"unsupported path {path!r}", "invalidPath")
            else:
                resource[path] = value
        return resource

    def _list(self, endpoint: str, query: Mapping[str, str]) -> Reply:
        predicate: Callable[[Mapping[str, Any]], bool] = lambda r: True
        if "filter" in query:
            try:
                predicate = parse_filter(query["filter"])
            except SCIMError as exc:
                self.violations.append(f"filter {query['filter']!r}: {exc.detail}")
                raise
        try:
            start = max(1, int(query.get("startIndex", "1")))
            count = max(
                0, min(self.page_size, int(query.get("count", str(self.page_size))))
            )
        except ValueError:
            raise self._violation(400, "bad paging", "invalidValue")
        matched = [r for r in self.resources[endpoint].values() if predicate(r)]
        page = matched[start - 1 : start - 1 + count]
        return (
            200,
            {},
            {
                "schemas": [LIST_SCHEMA],
                "totalResults": len(matched),
                "startIndex": start,
                "itemsPerPage": len(page),
                "Resources": page,
            },
        )

    def handle(
        self, method: str, raw_path: str, headers: Mapping[str, str], raw_body: bytes
    ) -> Reply:
        parts = urlsplit(raw_path)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        body: Any = None
        if raw_body:
            try:
                body = json.loads(raw_body)
            except ValueError:
                body = raw_body.decode("utf-8", "replace")
        with self._lock:
            self.requests.append(
                Seen(method, parts.path, query, headers, body, time.monotonic())
            )
            try:
                return self._handle(method, parts.path, query, headers, body)
            except SCIMError as exc:
                return self._error(exc)
            finally:
                self._lock.notify_all()

    def _fault(self, method: str, path: str) -> Optional[Reply]:
        for fault in self.faults:
            if fault.method == method and path.startswith(fault.path) and fault.times:
                fault.times -= 1
                return (
                    fault.status,
                    fault.headers,
                    fault.body
                    or {
                        "schemas": [ERROR_SCHEMA],
                        "status": str(fault.status),
                    },
                )
        return None

    def _handle(
        self,
        method: str,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        body: Any,
    ) -> Reply:
        if not path.startswith(BASE_PATH + "/"):
            raise SCIMError(404, "not a SCIM path")
        path = path[len(BASE_PATH) + 1 :]
        if headers.get("authorization") != f"Bearer {self.token}":
            raise SCIMError(401, "bad token")
        faulted = self._fault(method, path)
        if faulted is not None:
            return faulted
        if MEDIA_TYPE not in (headers.get("accept") or ""):
            self.violations.append(f"{method} {path} without Accept {MEDIA_TYPE}")
        if body is not None and headers.get("content-type") != MEDIA_TYPE:
            raise self._violation(
                400, f"{method} {path} not {MEDIA_TYPE}", "invalidSyntax"
            )
        if path == "ServiceProviderConfig":
            if not self.config:
                raise SCIMError(404, "no ServiceProviderConfig")
            return (
                200,
                {},
                {
                    "schemas": [
                        "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"
                    ],
                    "patch": {"supported": self.patch},
                    "bulk": {
                        "supported": False,
                        "maxOperations": 0,
                        "maxPayloadSize": 0,
                    },
                    "filter": {"supported": True, "maxResults": self.page_size},
                    "changePassword": {"supported": False},
                    "sort": {"supported": False},
                    "etag": {"supported": self.etag},
                    "authenticationSchemes": [
                        {
                            "type": "oauthbearertoken",
                            "name": "Bearer",
                            "description": "Bearer",
                        }
                    ],
                },
            )
        endpoint, _, remote_id = path.partition("/")
        if endpoint not in SCHEMAS:
            raise SCIMError(404, f"no endpoint {endpoint}")
        remote_id = unquote(remote_id)
        store = self.resources[endpoint]
        if not remote_id:
            if method == "GET":
                return self._list(endpoint, query)
            if method != "POST":
                raise self._violation(405, f"{method} on {endpoint}")
            resource = self._resource_body(endpoint, body)
            self._unique(endpoint, resource, None)
            self._members(resource)
            return self._answer(201, self._store(endpoint, str(uuid.uuid4()), resource))
        current = store.get(remote_id)
        if current is None:
            raise SCIMError(404, f"no {endpoint} {remote_id}")
        if method == "GET":
            return self._answer(200, current)
        if method == "DELETE":
            self._precondition(current, headers)
            del store[remote_id]
            return 204, {}, None
        if method == "PUT":
            self._precondition(current, headers)
            resource = self._resource_body(endpoint, body)
            if resource.get("id") not in (None, remote_id):
                raise self._violation(400, "a PUT naming another id", "mutability")
            self._unique(endpoint, resource, remote_id)
            self._members(resource)
            return self._answer(200, self._store(endpoint, remote_id, resource))
        if method == "PATCH":
            if not self.patch:
                raise self._violation(501, "PATCH to a server without PATCH")
            self._precondition(current, headers)
            resource = self._patched(current, body)
            self._unique(endpoint, resource, remote_id)
            self._members(resource)
            stored = self._store(endpoint, remote_id, resource)
            if self.patch_answers_no_content:
                return (
                    204,
                    {"ETag": stored["meta"]["version"]} if self.etag else {},
                    None,
                )
            return self._answer(200, stored)
        raise self._violation(405, f"{method} on a resource")

    # ----- serving ---------------------------------------------------------

    def serve(self) -> ThreadingHTTPServer:
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def _any(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                status, headers, body = provider.handle(
                    self.command,
                    self.path,
                    {k.lower(): v for k, v in self.headers.items()},
                    self.rfile.read(length) if length else b"",
                )
                payload = json.dumps(body).encode() if body is not None else b""
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                if payload:
                    self.send_header("Content-Type", MEDIA_TYPE)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _any

            def log_message(self, *args: object) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.host = f"127.0.0.1:{server.server_address[1]}"
        self.base_url = f"http://{self.host}{BASE_PATH}"
        return server
