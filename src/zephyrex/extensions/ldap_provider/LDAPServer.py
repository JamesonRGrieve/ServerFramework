# SPDX-License-Identifier: AGPL-3.0-or-later
"""The LDAPv3 protocol server in front of :class:`LDAPDirectory`.

Messages are RFC 4511 PDUs, decoded and encoded with pyasn1 against
ldap3's ASN.1 definitions of the protocol. Two listeners can run: LDAPS
(TLS from the first byte) and plain LDAP, where nothing but StartTLS is
answered until the connection is encrypted; every other request there is
refused with confidentialityRequired. Credentials never cross the wire in
the clear.

The directory is read-only: bind, search, compare, WhoAmI, StartTLS and
unbind are served; add, delete, modify, modify DN and password modify are
refused with unwillingToPerform. A connection is processed one message at
a time, so abandon has nothing to cancel. Every message is bounded
(:data:`MAX_MESSAGE_BYTES`), every connection idles out
(:data:`IDLE_TIMEOUT_SECONDS`), and connections past the configured
maximum are told the server is unavailable and closed.

The server runs on an event loop of its own in a thread
(:class:`LDAPServerThread`), so its connections never hold up the web
app's loop; the directory's blocking database and bcrypt work runs on
that loop's worker threads.
"""

from __future__ import annotations

import asyncio
import ssl
import threading
from dataclasses import dataclass
from typing import Any, List, Optional, Set, Tuple

from ldap3.protocol.rfc4511 import (
    AttributeDescription,
    AttributeValue,
    BindResponse,
    CompareResponse,
    ExtendedResponse,
    LDAPDN,
    LDAPMessage,
    LDAPString,
    MessageID,
    PartialAttribute,
    PartialAttributeList,
    ProtocolOp,
    ResponseName,
    ResponseValue,
    SearchResultDone,
    SearchResultEntry,
    Vals,
)
from ldap3.protocol.rfc4511 import AddResponse, DelResponse, ModifyDNResponse
from ldap3.protocol.rfc4511 import ModifyResponse
from ldap3.protocol.rfc4511 import ResultCode as ASN1ResultCode
from pyasn1.codec.ber import decoder, encoder
from pyasn1.error import PyAsn1Error

from zephyrex.extensions.ldap_provider.LDAPDirectory import (
    STARTTLS_OID,
    WHOAMI_OID,
    LDAPDirectory,
    Principal,
    Refused,
    ResultCode,
    SearchOutcome,
    SearchRequest,
)
from zephyrex.extensions.ldap_provider.LDAPFilter import (
    FilterMalformed,
    FilterRefused,
    parse_filter,
)
from zephyrex.extensions.ldap_provider.LDAPProtocol import RequestMessage
from zephyrex.lib.Logging import logger

MAX_MESSAGE_BYTES = 1024 * 1024
IDLE_TIMEOUT_SECONDS = 300.0
TLS_HANDSHAKE_TIMEOUT_SECONDS = 10.0
STARTUP_TIMEOUT_SECONDS = 10.0
SHUTDOWN_TIMEOUT_SECONDS = 10.0
SEQUENCE_TAG = 0x30
MAX_LENGTH_OCTETS = 4

NOTICE_OF_DISCONNECTION_OID = "1.3.6.1.4.1.1466.20036"
PASSWORD_MODIFY_OID = "1.3.6.1.4.1.4203.1.11.1"

# Each request and the response that answers it.
RESPONSES = {
    "bindRequest": ("bindResponse", BindResponse),
    "searchRequest": ("searchResDone", SearchResultDone),
    "modifyRequest": ("modifyResponse", ModifyResponse),
    "addRequest": ("addResponse", AddResponse),
    "delRequest": ("delResponse", DelResponse),
    "modDNRequest": ("modDNResponse", ModifyDNResponse),
    "compareRequest": ("compareResponse", CompareResponse),
    "extendedReq": ("extendedResp", ExtendedResponse),
}
WRITES = frozenset({"modifyRequest", "addRequest", "delRequest", "modDNRequest"})
UNANSWERED = frozenset({"unbindRequest", "abandonRequest"})


class ProtocolViolation(Exception):
    """The peer sent something that is not an LDAP message."""


@dataclass(frozen=True)
class ListenerConfig:
    """Where to listen: ``starttls_port`` (plain LDAP, StartTLS required)
    and ``ldaps_port`` on ``host``; either may be None (off) and 0 picks a
    free port. ``tls`` is the server's TLS context."""

    host: str
    starttls_port: Optional[int]
    ldaps_port: Optional[int]
    tls: ssl.SSLContext
    max_connections: int


def server_tls_context(cert_path: str, key_path: str) -> ssl.SSLContext:
    """A server TLS context (TLS 1.2 or later) for the certificate chain at
    ``cert_path`` and its key at ``key_path``."""
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert_path, key_path)
    return context


def _text(value: Any) -> str:
    try:
        return bytes(value).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolViolation("a string is not UTF-8") from exc


def _result(cls: Any, code: ResultCode, message: str = "", matched_dn: str = "") -> Any:
    result = cls()
    result["resultCode"] = ASN1ResultCode(int(code))
    result["matchedDN"] = LDAPDN(matched_dn)
    result["diagnosticMessage"] = LDAPString(message)
    return result


def _encode(message_id: int, op_name: str, op_value: Any) -> bytes:
    message = LDAPMessage()
    message["messageID"] = MessageID(message_id)
    message["protocolOp"] = ProtocolOp().setComponentByName(op_name, op_value)
    encoded: bytes = encoder.encode(message)
    return encoded


def _entry(dn: str, attributes: List[Tuple[str, Tuple[str, ...]]]) -> Any:
    entry = SearchResultEntry()
    entry["object"] = LDAPDN(dn)
    found = PartialAttributeList()
    for position, (name, values) in enumerate(attributes):
        attribute = PartialAttribute()
        attribute["type"] = AttributeDescription(name)
        vals = Vals()
        vals.clear()
        for index, value in enumerate(values):
            vals.setComponentByPosition(index, AttributeValue(value.encode("utf-8")))
        attribute["vals"] = vals
        found.setComponentByPosition(position, attribute)
    if not attributes:
        found.clear()
    entry["attributes"] = found
    return entry


class Connection:
    """One client connection: its messages, in order, until it unbinds,
    idles out, violates the protocol or the server stops."""

    def __init__(
        self,
        server: "LDAPServer",
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        encrypted: bool,
    ) -> None:
        self.server = server
        self.directory = server.directory
        self.reader = reader
        self.writer = writer
        self.encrypted = encrypted
        self.principal: Optional[Principal] = None
        peer = writer.get_extra_info("peername")
        self.address = str(peer[0]) if peer else "unknown"

    async def send(self, data: bytes) -> None:
        self.writer.write(data)
        await self.writer.drain()

    async def disconnect(self, code: ResultCode, message: str) -> None:
        """Send a Notice of Disconnection (RFC 4511 4.4.1) and close."""
        notice = _result(ExtendedResponse, code, message)
        notice["responseName"] = ResponseName(NOTICE_OF_DISCONNECTION_OID)
        try:
            await self.send(_encode(0, "extendedResp", notice))
        except (ConnectionError, OSError):
            pass

    async def read_message(self) -> Optional[Any]:
        """The next message, or None when the peer has gone."""
        try:
            header = await asyncio.wait_for(
                self.reader.readexactly(2), IDLE_TIMEOUT_SECONDS
            )
        except (asyncio.IncompleteReadError, ConnectionError):
            return None
        if header[0] != SEQUENCE_TAG:
            raise ProtocolViolation("not an LDAP message")
        length = header[1]
        length_octets = b""
        if length & 0x80:
            count = length & 0x7F
            if not 1 <= count <= MAX_LENGTH_OCTETS:
                raise ProtocolViolation("unsupported message length")
            length_octets = await self.reader.readexactly(count)
            length = int.from_bytes(length_octets, "big")
        if length > MAX_MESSAGE_BYTES:
            raise ProtocolViolation(f"messages are at most {MAX_MESSAGE_BYTES} bytes")
        body = await asyncio.wait_for(
            self.reader.readexactly(length), IDLE_TIMEOUT_SECONDS
        )
        try:
            message, rest = decoder.decode(
                header + length_octets + body, asn1Spec=RequestMessage()
            )
        except PyAsn1Error as exc:
            raise ProtocolViolation("malformed LDAP message") from exc
        if rest:
            raise ProtocolViolation("trailing bytes after an LDAP message")
        return message

    async def run(self) -> None:
        try:
            while True:
                message = await self.read_message()
                if message is None:
                    return
                if not await self.handle(message):
                    return
        except asyncio.TimeoutError:
            await self.disconnect(ResultCode.UNAVAILABLE, "idle for too long")
        except (ProtocolViolation, asyncio.IncompleteReadError) as exc:
            await self.disconnect(ResultCode.PROTOCOL_ERROR, str(exc))
        except (ConnectionError, OSError):
            return

    @staticmethod
    def _critical_control(message: Any) -> bool:
        controls = message["controls"]
        if not controls.isValue:
            return False
        return any(bool(control["criticality"]) for control in controls)

    async def handle(self, message: Any) -> bool:
        """Answer ``message``; False when the connection is to close."""
        message_id = int(message["messageID"])
        op = message["protocolOp"]
        name = op.getName()
        if name == "unbindRequest":
            return False
        if name in UNANSWERED:
            return True
        if name not in RESPONSES:
            raise ProtocolViolation(f"{name} is not a request")
        response_name, response_cls = RESPONSES[name]
        request = op.getComponent()
        try:
            if self._critical_control(message):
                raise Refused(
                    ResultCode.UNAVAILABLE_CRITICAL_EXTENSION,
                    "no controls are supported",
                )
            if name == "extendedReq":
                return await self.extended(message_id, request)
            if not self.encrypted:
                raise Refused(
                    ResultCode.CONFIDENTIALITY_REQUIRED,
                    "use StartTLS before any other operation",
                )
            if name in WRITES:
                raise Refused(
                    ResultCode.UNWILLING_TO_PERFORM, "the directory is read-only"
                )
            if name == "bindRequest":
                await self.bind(message_id, request)
            elif name == "searchRequest":
                await self.search(message_id, request)
            else:
                await self.compare(message_id, request)
        except Refused as refusal:
            await self.send(
                _encode(
                    message_id,
                    response_name,
                    _result(
                        response_cls,
                        refusal.code,
                        refusal.message,
                        refusal.matched_dn,
                    ),
                )
            )
        except (ProtocolViolation, ConnectionError, OSError):
            raise
        except Exception:
            logger.exception("ldap_provider: %s failed", name)
            await self.send(
                _encode(
                    message_id,
                    response_name,
                    _result(
                        response_cls, ResultCode.OPERATIONS_ERROR, "internal error"
                    ),
                )
            )
        return True

    async def bind(self, message_id: int, request: Any) -> None:
        self.principal = None
        if int(request["version"]) != 3:
            raise Refused(ResultCode.PROTOCOL_ERROR, "only LDAPv3 is served")
        authentication = request["authentication"]
        if authentication.getName() != "simple":
            raise Refused(
                ResultCode.AUTH_METHOD_NOT_SUPPORTED, "only simple binds are served"
            )
        dn = _text(request["name"])
        password = _text(authentication.getComponent())
        self.principal = await asyncio.to_thread(
            self.directory.bind, dn, password, self.address
        )
        await self.send(
            _encode(
                message_id, "bindResponse", _result(BindResponse, ResultCode.SUCCESS)
            )
        )

    async def search(self, message_id: int, request: Any) -> None:
        try:
            node = parse_filter(request["filter"])
        except FilterMalformed as exc:
            raise Refused(ResultCode.PROTOCOL_ERROR, str(exc)) from exc
        except FilterRefused as exc:
            raise Refused(ResultCode.ADMIN_LIMIT_EXCEEDED, str(exc)) from exc
        search = SearchRequest(
            base=_text(request["baseObject"]),
            scope=int(request["scope"]),
            filter=node,
            size_limit=int(request["sizeLimit"]),
            time_limit=int(request["timeLimit"]),
            attributes=tuple(_text(name) for name in request["attributes"]),
            types_only=bool(request["typesOnly"]),
        )
        outcome: SearchOutcome = await asyncio.to_thread(
            self.directory.search, self.principal, search
        )
        for dn, attributes in outcome.entries:
            await self.send(
                _encode(message_id, "searchResEntry", _entry(dn, attributes))
            )
        await self.send(
            _encode(
                message_id,
                "searchResDone",
                _result(
                    SearchResultDone, outcome.code, outcome.message, outcome.matched_dn
                ),
            )
        )

    async def compare(self, message_id: int, request: Any) -> None:
        ava = request["ava"]
        code = await asyncio.to_thread(
            self.directory.compare,
            self.principal,
            _text(request["entry"]),
            _text(ava["attributeDesc"]),
            _text(ava["assertionValue"]),
        )
        await self.send(
            _encode(message_id, "compareResponse", _result(CompareResponse, code))
        )

    async def extended(self, message_id: int, request: Any) -> bool:
        oid = _text(request["requestName"])
        if oid == STARTTLS_OID:
            if self.encrypted:
                raise Refused(ResultCode.OPERATIONS_ERROR, "TLS is already in use")
            response = _result(ExtendedResponse, ResultCode.SUCCESS)
            response["responseName"] = ResponseName(STARTTLS_OID)
            await self.send(_encode(message_id, "extendedResp", response))
            await self.writer.start_tls(
                self.server.config.tls,
                ssl_handshake_timeout=TLS_HANDSHAKE_TIMEOUT_SECONDS,
            )
            self.encrypted = True
            return True
        if not self.encrypted:
            raise Refused(
                ResultCode.CONFIDENTIALITY_REQUIRED,
                "use StartTLS before any other operation",
            )
        if oid == WHOAMI_OID:
            response = _result(ExtendedResponse, ResultCode.SUCCESS)
            identity = f"dn:{self.principal.dn}" if self.principal else ""
            response["responseValue"] = ResponseValue(identity.encode("utf-8"))
            await self.send(_encode(message_id, "extendedResp", response))
            return True
        if oid == PASSWORD_MODIFY_OID:
            raise Refused(ResultCode.UNWILLING_TO_PERFORM, "the directory is read-only")
        raise Refused(
            ResultCode.PROTOCOL_ERROR, f"unsupported extended operation {oid}"
        )


class LDAPServer:
    """The listeners and their connections, on the running event loop."""

    def __init__(self, directory: LDAPDirectory, config: ListenerConfig) -> None:
        self.directory = directory
        self.config = config
        self._servers: List[asyncio.Server] = []
        self._connections: Set["asyncio.Task[Any]"] = set()
        self.ports: dict[str, int] = {}

    async def start(self) -> None:
        listeners = (
            ("starttls", self.config.starttls_port, None),
            ("ldaps", self.config.ldaps_port, self.config.tls),
        )
        for label, port, tls in listeners:
            if port is None:
                continue
            encrypted = tls is not None

            async def accept(
                reader: asyncio.StreamReader,
                writer: asyncio.StreamWriter,
                encrypted: bool = encrypted,
            ) -> None:
                await self._serve(reader, writer, encrypted)

            server = await asyncio.start_server(
                accept,
                self.config.host,
                port,
                ssl=tls,
                ssl_handshake_timeout=TLS_HANDSHAKE_TIMEOUT_SECONDS if tls else None,
                reuse_port=True,
            )
            self._servers.append(server)
            self.ports[label] = int(server.sockets[0].getsockname()[1])

    async def _serve(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        encrypted: bool,
    ) -> None:
        connection = Connection(self, reader, writer, encrypted)
        task = asyncio.current_task()
        try:
            if len(self._connections) >= self.config.max_connections:
                await connection.disconnect(
                    ResultCode.UNAVAILABLE, "too many connections"
                )
                return
            if task is not None:
                self._connections.add(task)
            await connection.run()
        finally:
            if task is not None:
                self._connections.discard(task)
            writer.close()

    async def close(self) -> None:
        """Stop listening, end every connection and wait for them to go."""
        for server in self._servers:
            server.close()
        connections = list(self._connections)
        for task in connections:
            task.cancel()
        await asyncio.gather(*connections, return_exceptions=True)
        for server in self._servers:
            await server.wait_closed()
        self._servers.clear()


class LDAPServerThread:
    """An :class:`LDAPServer` on an event loop of its own, in a thread."""

    def __init__(self, directory: LDAPDirectory, config: ListenerConfig) -> None:
        self.server = LDAPServer(directory, config)
        self._ready = threading.Event()
        self._error: Optional[Exception] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stopping: Optional[asyncio.Event] = None
        self._thread = threading.Thread(
            target=self._run, name="ldap_provider", daemon=True
        )

    @property
    def ports(self) -> dict[str, int]:
        return self.server.ports

    def start(self) -> None:
        """Listen; returns once listening, raising what stopped it if not."""
        self._thread.start()
        if not self._ready.wait(STARTUP_TIMEOUT_SECONDS):
            raise TimeoutError("the LDAP server did not start listening")
        if self._error is not None:
            self._thread.join(SHUTDOWN_TIMEOUT_SECONDS)
            raise self._error

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        try:
            loop.run_until_complete(self._serve())
        finally:
            loop.run_until_complete(loop.shutdown_default_executor())
            loop.close()

    async def _serve(self) -> None:
        self._stopping = asyncio.Event()
        try:
            await self.server.start()
        except Exception as exc:
            self._error = exc
            self._ready.set()
            await self.server.close()
            return
        self._ready.set()
        await self._stopping.wait()
        await self.server.close()

    def stop(self) -> None:
        """Stop listening, close every connection and end the thread."""
        loop, stopping = self._loop, self._stopping
        if loop is not None and stopping is not None and not loop.is_closed():
            loop.call_soon_threadsafe(stopping.set)
        if self._thread.is_alive():
            self._thread.join(SHUTDOWN_TIMEOUT_SECONDS)
