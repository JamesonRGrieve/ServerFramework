# SPDX-License-Identifier: AGPL-3.0-or-later
"""A RADIUS client for signing users in: one Access-Request, PAP.

The request hides User-Password as RFC 2865 section 5.2 says and always
carries a Message-Authenticator (RFC 3579 section 3.2), placed first, as the
2024 Blast-RADIUS advisories (CVE-2024-3596) recommend. A reply is accepted
only when

- it answers this request (its Identifier) with Access-Accept, -Reject or
  -Challenge;
- its Response Authenticator is the MD5 RFC 2865 section 3 defines over the
  request's authenticator and the shared secret; and
- it carries exactly one Message-Authenticator, and that HMAC-MD5 verifies.

Anything else is discarded as if it never arrived, so a forged or damaged
datagram cannot end the wait for the real reply.

Two transports. RADIUS over TLS (RadSec, RFC 6614) is TCP to port 2083 with
mutual certificate authentication and the fixed shared secret ``radsec``;
the server's certificate is checked against the configured CA and name.
Plain RADIUS over UDP (port 1812) relies on the shared secret alone; the
request is retransmitted unchanged on timeout (RFC 5080 section 2.2.1).
Over TLS nothing is retransmitted: TCP already is reliable (RFC 6613
section 2.6).

A service names one or more servers that share its users and secret. They
are tried in order: one that times out, cannot be reached, or only sends
replies that fail verification is passed over for the next. A challenge's
answer goes to the server that issued it, since its State means nothing to
the others.

The client is synchronous (sockets with timeouts); every wait is bounded by
the service's timeout.

pyrad encodes and decodes the attributes and hides the password; the
header, the authenticators and the verification are this module's own, so
every comparison is constant-time and the reply is checked byte for byte as
it arrived.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import os
import secrets
import socket
import ssl
import struct
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Literal, Optional, Sequence, Tuple

from pyrad.dictionary import Dictionary
from pyrad.packet import AuthPacket, Packet, PacketError

from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    TransientExternalError,
)

PROVIDER_NAME = "radius_consumer"

ACCESS_REQUEST = 1
ACCESS_ACCEPT = 2
ACCESS_REJECT = 3
ACCESS_CHALLENGE = 11

Transport = Literal["radsec", "udp"]
TRANSPORTS: Tuple[str, ...] = ("radsec", "udp")
Outcome = Literal["accept", "reject", "challenge"]
REPLY_CODES: Dict[int, Outcome] = {
    ACCESS_ACCEPT: "accept",
    ACCESS_REJECT: "reject",
    ACCESS_CHALLENGE: "challenge",
}

HEADER_LENGTH = 20
AUTHENTICATOR_LENGTH = 16
ATTRIBUTE_HEADER_LENGTH = 2
# RFC 2865 section 3: a packet is at most 4096 octets.
MAX_PACKET_LENGTH = 4096
MESSAGE_AUTHENTICATOR = 80
MESSAGE_AUTHENTICATOR_LENGTH = ATTRIBUTE_HEADER_LENGTH + AUTHENTICATOR_LENGTH
# RFC 2865 section 5.1/5.2: User-Name and an attribute value are at most
# 253 octets; a hidden password at most 128.
MAX_ATTRIBUTE_VALUE_LENGTH = 253
MAX_PASSWORD_LENGTH = 128
PASSWORD_BLOCK = 16

RADSEC_PORT = 2083
RADIUS_PORT = 1812
# RFC 6614 section 2.3: over TLS the shared secret is this fixed string.
RADSEC_SECRET = "radsec"
TLS_MINIMUM_VERSION = ssl.TLSVersion.TLSv1_2

# The attributes this client sends or reads (RFC 2865, RFC 3579).
# User-Password is octets: the client hides it before adding it.
DICTIONARY_TEXT = """\
ATTRIBUTE	User-Name		1	string
ATTRIBUTE	User-Password		2	octets
ATTRIBUTE	Reply-Message		18	string
ATTRIBUTE	State			24	octets
ATTRIBUTE	Class			25	octets
ATTRIBUTE	Session-Timeout		27	integer
ATTRIBUTE	NAS-Identifier		32	string
ATTRIBUTE	Message-Authenticator	80	octets
"""


def radius_dictionary() -> Dictionary:
    return Dictionary(io.StringIO(DICTIONARY_TEXT))


DICTIONARY = radius_dictionary()


@dataclass(frozen=True)
class Endpoint:
    """One RADIUS server: a host name or address, and a port."""

    host: str
    port: int

    @classmethod
    def parse(cls, entry: str, default_port: int) -> "Endpoint":
        """``host``, ``host:port``, ``[v6]:port`` or a bare IPv6 address."""
        text = entry.strip()
        if not text:
            raise InvalidInputExternalError("A RADIUS server entry is empty")
        host, port_text = text, ""
        if text.startswith("["):
            closing = text.find("]")
            if closing < 0:
                raise InvalidInputExternalError(f"Unclosed [ in {entry!r}")
            host, rest = text[1:closing], text[closing + 1 :]
            if rest and not rest.startswith(":"):
                raise InvalidInputExternalError(f"Unreadable server {entry!r}")
            port_text = rest[1:]
        elif text.count(":") == 1:
            host, port_text = text.split(":")
        if not host or any(c.isspace() for c in host):
            raise InvalidInputExternalError(f"Unreadable server {entry!r}")
        if port_text:
            if not port_text.isdigit() or not 0 < int(port_text) < 65536:
                raise InvalidInputExternalError(f"Bad port in {entry!r}")
            return cls(host, int(port_text))
        return cls(host, default_port)

    def label(self) -> str:
        if ":" in self.host:
            return f"[{self.host}]:{self.port}"
        return f"{self.host}:{self.port}"


@dataclass(frozen=True)
class TLSSettings:
    """RadSec credentials: the CA that signs the servers' certificates, this
    client's certificate and key (PEM), and the name the servers'
    certificates must carry when it is not the host they are reached by."""

    ca_pem: str
    client_cert_pem: str
    client_key_pem: str
    server_name: Optional[str] = None

    def context(self, workdir: Path) -> ssl.SSLContext:
        """A verifying client context. ``load_cert_chain`` reads files, so
        the certificate and key are written to ``workdir`` (private to
        this process, removed by the caller)."""
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = TLS_MINIMUM_VERSION
        context.verify_mode = ssl.CERT_REQUIRED
        context.check_hostname = True
        context.load_verify_locations(cadata=self.ca_pem)
        cert_file, key_file = workdir / "client.pem", workdir / "client.key"
        for path, pem in (
            (cert_file, self.client_cert_pem),
            (key_file, self.client_key_pem),
        ):
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as handle:
                handle.write(pem)
        context.load_cert_chain(str(cert_file), str(key_file))
        return context

    def check(self) -> None:
        """Refuse PEM material a TLS context will not load."""
        with tempfile.TemporaryDirectory(prefix="radsec-") as workdir:
            try:
                self.context(Path(workdir))
            except (ssl.SSLError, ValueError) as exc:
                raise InvalidInputExternalError(
                    f"The RadSec certificates do not load: {exc}",
                    provider=PROVIDER_NAME,
                ) from exc


@dataclass(frozen=True)
class ServiceSettings:
    """A RADIUS service: its servers in fail-over order and how to reach
    them."""

    endpoints: Tuple[Endpoint, ...]
    transport: Transport
    secret: bytes
    timeout_seconds: float
    retries: int
    nas_identifier: Optional[str] = None
    tls: Optional[TLSSettings] = None


@dataclass(frozen=True)
class Reply:
    """A verified reply: what it says, which server sent it (its index in
    the service), and the attributes a sign-in uses."""

    outcome: Outcome
    endpoint_index: int
    reply_message: Optional[str] = None
    state: Optional[bytes] = None
    session_timeout: Optional[int] = None


class DiscardedReply(Exception):
    """A datagram that is not a verified reply to the request."""


@dataclass
class _Unanswered(Exception):
    """No verified reply from one server: why, and whether anything that
    arrived failed verification (a wrong secret, a forged reply)."""

    reason: str
    discarded: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class Request:
    """An encoded Access-Request: its bytes, and its Identifier and Request
    Authenticator, which the reply must match."""

    raw: bytes

    @property
    def identifier(self) -> int:
        return self.raw[1]

    @property
    def authenticator(self) -> bytes:
        return self.raw[4:HEADER_LENGTH]


def _bounded_text(value: str, what: str, limit: int) -> bytes:
    encoded = value.encode("utf-8")
    if not encoded or len(encoded) > limit or "\x00" in value:
        raise InvalidInputExternalError(
            f"{what} is 1-{limit} octets with no NUL", provider=PROVIDER_NAME
        )
    return encoded


def build_access_request(
    secret: bytes,
    username: str,
    password: str,
    *,
    nas_identifier: Optional[str] = None,
    state: Optional[bytes] = None,
) -> Request:
    """An Access-Request with a fresh Identifier and Request Authenticator,
    Message-Authenticator first, and the password hidden."""
    _bounded_text(username, "User-Name", MAX_ATTRIBUTE_VALUE_LENGTH)
    _bounded_text(password, "User-Password", MAX_PASSWORD_LENGTH)
    packet = AuthPacket(
        code=ACCESS_REQUEST,
        id=secrets.randbelow(256),
        secret=secret,
        authenticator=secrets.token_bytes(AUTHENTICATOR_LENGTH),
        dict=DICTIONARY,
    )
    packet.add_message_authenticator()
    packet["User-Name"] = username
    packet["User-Password"] = packet.PwCrypt(password)
    if nas_identifier:
        packet["NAS-Identifier"] = nas_identifier
    if state is not None:
        packet["State"] = state
    raw: bytes = packet.RequestPacket()
    if len(raw) > MAX_PACKET_LENGTH:
        raise InvalidInputExternalError(
            "The request is too long", provider=PROVIDER_NAME
        )
    return Request(raw)


def _attributes(body: bytes) -> Iterator[Tuple[int, int, bytes]]:
    """Each attribute of a packet body: its offset, type and value."""
    offset = 0
    while offset < len(body):
        if len(body) - offset < ATTRIBUTE_HEADER_LENGTH:
            raise DiscardedReply("a truncated attribute")
        kind, length = body[offset], body[offset + 1]
        if length < ATTRIBUTE_HEADER_LENGTH or offset + length > len(body):
            raise DiscardedReply("an attribute with a bad length")
        yield offset, kind, body[offset + ATTRIBUTE_HEADER_LENGTH : offset + length]
        offset += length


def message_authenticator(
    secret: bytes, packet: bytes, authenticator: bytes, at: Optional[int]
) -> bytes:
    """The HMAC-MD5 of ``packet`` with ``authenticator`` in its header and,
    when ``at`` names the Message-Authenticator value's offset, that value
    zeroed (RFC 3579 section 3.2)."""
    body = bytearray(packet[HEADER_LENGTH:])
    if at is not None:
        body[at : at + AUTHENTICATOR_LENGTH] = bytes(AUTHENTICATOR_LENGTH)
    digest: bytes = hmac.new(
        secret, packet[:4] + authenticator + bytes(body), hashlib.md5
    ).digest()
    return digest


def verify_reply(request: Request, datagram: bytes, secret: bytes) -> Packet:
    """The decoded reply when ``datagram`` is a verified reply to
    ``request``; :class:`DiscardedReply` saying why when it is not."""
    if len(datagram) < HEADER_LENGTH:
        raise DiscardedReply("a datagram shorter than a RADIUS header")
    code, identifier, length = struct.unpack("!BBH", datagram[:4])
    if length < HEADER_LENGTH or length > MAX_PACKET_LENGTH or length > len(datagram):
        raise DiscardedReply("a packet whose Length is wrong")
    # RFC 2865 section 3: octets past Length are padding.
    reply = datagram[:length]
    if identifier != request.identifier:
        raise DiscardedReply("a reply to another request")
    if code not in REPLY_CODES:
        raise DiscardedReply(f"packet code {code}, not an Access reply")
    body = reply[HEADER_LENGTH:]
    expected = hashlib.md5(reply[:4] + request.authenticator + body + secret).digest()
    if not hmac.compare_digest(expected, reply[4:HEADER_LENGTH]):
        raise DiscardedReply(
            "a Response Authenticator that does not verify (wrong shared secret?)"
        )
    found = [
        (offset, value)
        for offset, kind, value in _attributes(body)
        if kind == MESSAGE_AUTHENTICATOR
    ]
    if not found:
        raise DiscardedReply("a reply without Message-Authenticator")
    if len(found) > 1 or len(found[0][1]) != AUTHENTICATOR_LENGTH:
        raise DiscardedReply("a malformed Message-Authenticator")
    offset, value = found[0]
    signed = message_authenticator(
        secret, reply, request.authenticator, offset + ATTRIBUTE_HEADER_LENGTH
    )
    if not hmac.compare_digest(signed, value):
        raise DiscardedReply("a Message-Authenticator that does not verify")
    try:
        return Packet(packet=reply, dict=DICTIONARY, secret=secret)
    except PacketError as exc:
        raise DiscardedReply(f"an undecodable reply ({exc})") from exc


def _first(packet: Packet, name: str) -> Any:
    values = packet.get(name)
    return values[0] if values else None


def read_reply(packet: Packet, endpoint_index: int) -> Reply:
    message = _first(packet, "Reply-Message")
    timeout = _first(packet, "Session-Timeout")
    return Reply(
        outcome=REPLY_CODES[packet.code],
        endpoint_index=endpoint_index,
        reply_message=str(message) if message is not None else None,
        state=_first(packet, "State"),
        session_timeout=int(timeout) if timeout is not None else None,
    )


def _resolve(endpoint: Endpoint, kind: socket.SocketKind) -> Tuple[Any, ...]:
    try:
        found = socket.getaddrinfo(endpoint.host, endpoint.port, type=kind)
    except socket.gaierror as exc:
        raise _Unanswered(f"{endpoint.label()} does not resolve ({exc})") from exc
    return found[0]


class RADIUSClient:
    """Sends Access-Requests to one RADIUS service."""

    def __init__(self, service: ServiceSettings) -> None:
        if not service.endpoints:
            raise InvalidInputExternalError(
                "A RADIUS service names no servers", provider=PROVIDER_NAME
            )
        if service.transport == "radsec" and service.tls is None:
            raise InvalidInputExternalError(
                "RadSec needs certificates", provider=PROVIDER_NAME
            )
        self.service = service

    def authenticate(
        self,
        username: str,
        password: str,
        *,
        state: Optional[bytes] = None,
        endpoint_index: Optional[int] = None,
    ) -> Reply:
        """The verified reply of the first server that gives one. A
        challenge's answer (``state``) goes only to ``endpoint_index``, the
        server that issued it.

        All unanswered: AuthExternalError when a server answered with
        replies that failed verification (the secret or the certificates
        are wrong, or someone forges replies); TransientExternalError when
        none answered at all."""
        indices: Sequence[int] = range(len(self.service.endpoints))
        if endpoint_index is not None:
            if not 0 <= endpoint_index < len(self.service.endpoints):
                raise InvalidInputExternalError(
                    "No such RADIUS server", provider=PROVIDER_NAME
                )
            indices = [endpoint_index]
        failures: List[_Unanswered] = []
        for index in indices:
            endpoint = self.service.endpoints[index]
            request = build_access_request(
                self.service.secret,
                username,
                password,
                nas_identifier=self.service.nas_identifier,
                state=state,
            )
            try:
                if self.service.transport == "udp":
                    packet = self._over_udp(endpoint, request)
                else:
                    packet = self._over_tls(endpoint, request)
            except _Unanswered as unanswered:
                failures.append(unanswered)
                continue
            return read_reply(packet, index)
        detail = "; ".join(
            f.reason
            + (f" after discarding {', '.join(f.discarded)}" if f.discarded else "")
            for f in failures
        )
        if any(f.discarded for f in failures):
            raise AuthExternalError(
                f"No RADIUS server sent a verifiable reply: {detail}",
                provider=PROVIDER_NAME,
            )
        raise TransientExternalError(
            f"No RADIUS server answered: {detail}", provider=PROVIDER_NAME
        )

    def _over_udp(self, endpoint: Endpoint, request: Request) -> Packet:
        family, kind, proto, _, address = _resolve(endpoint, socket.SOCK_DGRAM)
        discarded: List[str] = []
        with socket.socket(family, kind, proto) as sock:
            # Connected: the kernel drops datagrams from anyone else.
            sock.connect(address)
            for _ in range(self.service.retries + 1):
                try:
                    sock.send(request.raw)
                except OSError as exc:
                    raise _Unanswered(
                        f"{endpoint.label()} is unreachable ({exc})", discarded
                    ) from exc
                deadline = time.monotonic() + self.service.timeout_seconds
                while (remaining := deadline - time.monotonic()) > 0:
                    sock.settimeout(remaining)
                    try:
                        datagram = sock.recv(MAX_PACKET_LENGTH + 1)
                    except socket.timeout:
                        break
                    except OSError as exc:
                        raise _Unanswered(
                            f"{endpoint.label()} is unreachable ({exc})", discarded
                        ) from exc
                    try:
                        return verify_reply(request, datagram, self.service.secret)
                    except DiscardedReply as reason:
                        discarded.append(str(reason))
        raise _Unanswered(f"{endpoint.label()} timed out", discarded)

    def _over_tls(self, endpoint: Endpoint, request: Request) -> Packet:
        tls = self.service.tls
        if tls is None:
            raise _Unanswered(f"{endpoint.label()}: RadSec needs certificates")
        server_name = tls.server_name or endpoint.host
        deadline = time.monotonic() + self.service.timeout_seconds
        with tempfile.TemporaryDirectory(prefix="radsec-") as workdir:
            context = tls.context(Path(workdir))
        try:
            with socket.create_connection(
                (endpoint.host, endpoint.port), timeout=self.service.timeout_seconds
            ) as raw:
                with context.wrap_socket(raw, server_hostname=server_name) as stream:
                    stream.settimeout(max(deadline - time.monotonic(), 0.001))
                    stream.sendall(request.raw)
                    return self._read_tls_reply(endpoint, stream, request, deadline)
        except ssl.SSLCertVerificationError as exc:
            raise _Unanswered(
                f"{endpoint.label()} presented a certificate that does not verify",
                [f"its certificate ({exc.verify_message})"],
            ) from exc
        except ssl.SSLError as exc:
            raise _Unanswered(
                f"{endpoint.label()} refused the TLS handshake",
                [f"the TLS session ({exc.reason})"],
            ) from exc
        except socket.timeout as exc:
            raise _Unanswered(f"{endpoint.label()} timed out") from exc
        except OSError as exc:
            raise _Unanswered(f"{endpoint.label()} is unreachable ({exc})") from exc

    def _read_tls_reply(
        self,
        endpoint: Endpoint,
        stream: ssl.SSLSocket,
        request: Request,
        deadline: float,
    ) -> Packet:
        """Packets off the stream until one verifies. TCP keeps the
        framing: each packet's Length says where the next begins."""
        header = self._read_exactly(stream, 4, deadline)
        length = struct.unpack("!H", header[2:4])[0]
        if length < HEADER_LENGTH or length > MAX_PACKET_LENGTH:
            raise _Unanswered(
                f"{endpoint.label()} broke the stream's framing",
                ["a packet whose Length is wrong"],
            )
        packet = header + self._read_exactly(stream, length - 4, deadline)
        try:
            return verify_reply(request, packet, self.service.secret)
        except DiscardedReply as reason:
            # One request per connection, so this was the answer: over TLS
            # a reply that fails verification is not network noise.
            raise _Unanswered(
                f"{endpoint.label()} sent no verifiable reply", [str(reason)]
            ) from None

    @staticmethod
    def _read_exactly(stream: ssl.SSLSocket, count: int, deadline: float) -> bytes:
        chunks = bytearray()
        while len(chunks) < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise socket.timeout("timed out")
            stream.settimeout(remaining)
            chunk = stream.recv(count - len(chunks))
            if not chunk:
                raise ConnectionResetError("the server closed the connection")
            chunks.extend(chunk)
        return bytes(chunks)
