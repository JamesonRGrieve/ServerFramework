# SPDX-License-Identifier: AGPL-3.0-or-later
"""RADIUS wire format for the authenticator: what a request must carry to be
read at all, and how a reply is signed.

pyrad encodes and decodes attributes. The two integrity checks that decide
whether a datagram is answered are done here over the raw bytes, with a
constant-time comparison:

* the request's Message-Authenticator (RFC 3579 §3.2), required on every
  Access-Request (the Blast-RADIUS mitigation, CVE-2024-3596); a request
  without one, or with one that does not verify, is silently discarded, which
  is also what a wrong shared secret looks like;
* the reply's Response Authenticator (RFC 2865 §3), with a
  Message-Authenticator always added as the reply's first attribute so a
  forged reply cannot be built from a chosen-prefix MD5 collision.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import struct
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Sequence, Tuple, Union

from pyrad.dictionary import Dictionary
from pyrad.packet import AccessAccept, AccessReject, AuthPacket, PacketError

HEADER_LENGTH = 20
# RFC 2865 §3: the largest packet a RADIUS peer sends or accepts.
MAX_PACKET_LENGTH = 4096
AUTHENTICATOR_LENGTH = 16
ATTRIBUTE_HEADER_LENGTH = 2

USER_PASSWORD = 2
CHAP_PASSWORD = 3
PROXY_STATE = 33
EAP_MESSAGE = 79
MESSAGE_AUTHENTICATOR = 80

# RFC 2865 §5.2: User-Password is 16 to 128 octets, in 16-octet blocks.
PASSWORD_BLOCK = 16
MAX_PASSWORD_LENGTH = 128

# The attributes read and written, in the FreeRADIUS dictionary format.
# User-Password is octets so pyrad hands over the obfuscated bytes as sent.
_DICTIONARY = """
ATTRIBUTE User-Name 1 string
ATTRIBUTE User-Password 2 octets
ATTRIBUTE CHAP-Password 3 octets
ATTRIBUTE NAS-IP-Address 4 ipaddr
ATTRIBUTE NAS-Port 5 integer
ATTRIBUTE Service-Type 6 integer
ATTRIBUTE Filter-Id 11 string
ATTRIBUTE Reply-Message 18 string
ATTRIBUTE State 24 octets
ATTRIBUTE Class 25 octets
ATTRIBUTE Session-Timeout 27 integer
ATTRIBUTE Called-Station-Id 30 string
ATTRIBUTE Calling-Station-Id 31 string
ATTRIBUTE NAS-Identifier 32 string
ATTRIBUTE Proxy-State 33 octets
ATTRIBUTE NAS-Port-Type 61 integer
ATTRIBUTE Tunnel-Type 64 integer
ATTRIBUTE Tunnel-Medium-Type 65 integer
ATTRIBUTE EAP-Message 79 octets
ATTRIBUTE Message-Authenticator 80 octets
ATTRIBUTE Tunnel-Private-Group-Id 81 string
ATTRIBUTE NAS-IPv6-Address 95 ipv6addr
VALUE Tunnel-Type VLAN 13
VALUE Tunnel-Medium-Type IEEE-802 6
"""

ReplyValue = Union[str, int]
ReplyAttributes = Sequence[Tuple[str, ReplyValue]]


class MalformedPacket(ValueError):
    """A datagram that is not a well-formed RADIUS packet."""


@dataclass(frozen=True)
class RawAttribute:
    offset: int
    type: int
    value: bytes


@lru_cache(maxsize=1)
def dictionary() -> Dictionary:
    return Dictionary(io.StringIO(_DICTIONARY))


def packet_header(datagram: bytes) -> Tuple[int, int, bytes, bytes]:
    """``(code, identifier, authenticator, packet)`` of a datagram, the
    packet cut to the length its header states (RFC 2865 §3: octets past
    it are padding)."""
    if len(datagram) < HEADER_LENGTH:
        raise MalformedPacket("shorter than a RADIUS header")
    code, identifier, length = struct.unpack("!BBH", datagram[:4])
    if not HEADER_LENGTH <= length <= min(len(datagram), MAX_PACKET_LENGTH):
        raise MalformedPacket("length field disagrees with the datagram")
    return code, identifier, datagram[4:HEADER_LENGTH], datagram[:length]


def raw_attributes(packet: bytes) -> List[RawAttribute]:
    attributes: List[RawAttribute] = []
    offset = HEADER_LENGTH
    while offset < len(packet):
        if offset + ATTRIBUTE_HEADER_LENGTH > len(packet):
            raise MalformedPacket("truncated attribute header")
        kind, length = packet[offset], packet[offset + 1]
        if length < ATTRIBUTE_HEADER_LENGTH or offset + length > len(packet):
            raise MalformedPacket("attribute length out of bounds")
        attributes.append(
            RawAttribute(
                offset, kind, packet[offset + ATTRIBUTE_HEADER_LENGTH : offset + length]
            )
        )
        offset += length
    return attributes


def request_is_authentic(packet: bytes, secret: bytes) -> bool:
    """Whether an Access-Request carries exactly one Message-Authenticator
    and it is the HMAC-MD5, under ``secret``, of the packet with that
    attribute's value zeroed."""
    found = [a for a in raw_attributes(packet) if a.type == MESSAGE_AUTHENTICATOR]
    if len(found) != 1 or len(found[0].value) != AUTHENTICATOR_LENGTH:
        return False
    start = found[0].offset + ATTRIBUTE_HEADER_LENGTH
    zeroed = (
        packet[:start]
        + bytes(AUTHENTICATOR_LENGTH)
        + packet[start + AUTHENTICATOR_LENGTH :]
    )
    expected = hmac.new(secret, zeroed, hashlib.md5).digest()
    return hmac.compare_digest(expected, found[0].value)


def decode_request(packet: bytes, secret: bytes) -> AuthPacket:
    try:
        return AuthPacket(packet=packet, secret=secret, dict=dictionary())
    except PacketError as exc:
        raise MalformedPacket(str(exc)) from exc


def raw_values(request: AuthPacket, kind: int) -> List[bytes]:
    values: List[bytes] = dict.get(request, kind, [])
    return values


def user_password(request: AuthPacket) -> str:
    """The User-Password in clear (RFC 2865 §5.2)."""
    values = raw_values(request, USER_PASSWORD)
    if len(values) != 1:
        raise MalformedPacket("one User-Password is required")
    hidden = values[0]
    if not hidden or len(hidden) % PASSWORD_BLOCK or len(hidden) > MAX_PASSWORD_LENGTH:
        raise MalformedPacket("User-Password is not 16-128 octets in 16-octet blocks")
    password: str = request.PwDecrypt(hidden)
    return password


def user_name(request: AuthPacket) -> str:
    try:
        values = request["User-Name"] if "User-Name" in request else []
    except UnicodeDecodeError as exc:
        raise MalformedPacket("User-Name is not UTF-8") from exc
    if len(values) != 1 or not values[0]:
        raise MalformedPacket("one User-Name is required")
    name: str = values[0]
    return name


def reply(
    request: AuthPacket, accepted: bool, attributes: ReplyAttributes = ()
) -> bytes:
    """The signed Access-Accept or Access-Reject answering ``request``.

    The Message-Authenticator is added first, so it is the first attribute
    on the wire; Proxy-State is echoed unmodified (RFC 2865 §5.33)."""
    answer = request.CreateReply()
    answer.code = AccessAccept if accepted else AccessReject
    answer.add_message_authenticator()
    for name, value in attributes:
        answer.AddAttribute(name, value)
    proxy_states = raw_values(request, PROXY_STATE)
    if proxy_states:
        answer[PROXY_STATE] = list(proxy_states)
    encoded: bytes = answer.ReplyPacket()
    return encoded
