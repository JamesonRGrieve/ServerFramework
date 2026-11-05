# SPDX-License-Identifier: AGPL-3.0-or-later
"""The RADIUS authentication listener: a UDP socket answering Access-Requests
from registered NAS clients, run as a framework background service.

Per datagram, in order:

1. A source address no enabled client covers is dropped (the most specific
   client network wins).
2. Anything but a well-formed Access-Request is dropped.
3. A request without a valid Message-Authenticator is dropped; a wrong
   shared secret fails here too. Nothing unauthenticated reaches the caches.
4. A retransmission, the same (client, identifier, authenticator) within
   ``DUPLICATE_WINDOW_SECONDS``, gets the first answer again, byte for byte,
   without being decided twice (RFC 5080 §2.2.2). One still being decided is
   dropped; the NAS retransmits.
5. A request authenticator seen from the client before, outside that window,
   is a replay and is dropped.
6. EAP and CHAP requests are rejected: only PAP is decided.

Only one process can bind the port, so with several uvicorn workers the
first to start listens and the rest log that the address is taken.
"""

import asyncio
import ipaddress
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple, cast

from pyrad.packet import AccessRequest, AuthPacket

from zephyrex.extensions.radius_provider import RADIUSPackets as wire
from zephyrex.extensions.radius_provider.BLL_RADIUSProvider import (
    RadiusClientManager,
    RadiusClientModel,
    root_manager,
)
from zephyrex.extensions.radius_provider.RADIUSAuthenticator import (
    REJECT,
    Decision,
    PAPAuthenticator,
)
from zephyrex.lib.Logging import logger
from zephyrex.lib.ReplayCache import get_replay_cache
from zephyrex.lib.SecretEncryption import decrypt_secret
from zephyrex.logic.AbstractService import AbstractService

SERVICE_ID = "radius_provider"
# How long an answer is kept to resend to a retransmission. NASes retry for
# a few seconds; FreeRADIUS keeps answers for 5.
DUPLICATE_WINDOW_SECONDS = 30
# How long a request authenticator stays spent. It is 16 random octets a NAS
# never reuses, so a second sighting after the duplicate window is a replay.
REPLAY_WINDOW_SECONDS = 3600
MAX_TRACKED_REQUESTS = 65536
MAX_REQUESTS_IN_FLIGHT = 64

Address = Tuple[str, int]
RequestKey = Tuple[str, int, bytes]


@dataclass
class _Answer:
    expires_at: float
    reply: Optional[bytes] = None


class DuplicateCache:
    """Answers by (client, identifier, authenticator), for resending."""

    def __init__(self, window_seconds: float = DUPLICATE_WINDOW_SECONDS) -> None:
        self.window_seconds = window_seconds
        self._answers: Dict[RequestKey, _Answer] = {}
        self._lock = threading.Lock()

    def claim(self, key: RequestKey) -> Tuple[bool, Optional[bytes]]:
        """``(True, None)`` when the caller is first to see ``key`` and must
        answer it; otherwise ``(False, the answer so far)``."""
        now = time.monotonic()
        with self._lock:
            known = self._answers.get(key)
            if known is not None and known.expires_at > now:
                return False, known.reply
            if len(self._answers) >= MAX_TRACKED_REQUESTS:
                self._answers = {
                    k: a for k, a in self._answers.items() if a.expires_at > now
                }
                if len(self._answers) >= MAX_TRACKED_REQUESTS:
                    return False, None
            self._answers[key] = _Answer(expires_at=now + self.window_seconds)
            return True, None

    def answer(self, key: RequestKey, reply: Optional[bytes]) -> None:
        with self._lock:
            known = self._answers.get(key)
            if known is not None:
                known.reply = reply


class RequestHandler:
    """One datagram in, at most one datagram out."""

    def __init__(self, model_registry: Any) -> None:
        self.model_registry = model_registry
        self.authenticator = PAPAuthenticator(model_registry)
        self.duplicates = DuplicateCache()

    def client_for(self, host: str) -> Optional[RadiusClientModel]:
        """The enabled client whose network most specifically covers
        ``host``."""
        source = ipaddress.ip_address(host.split("%", 1)[0])
        if isinstance(source, ipaddress.IPv6Address) and source.ipv4_mapped:
            source = source.ipv4_mapped
        clients: RadiusClientManager = root_manager(
            RadiusClientManager, self.model_registry
        )
        covering = [
            (ipaddress.ip_network(c.address), c)
            for c in clients.enabled()
            if source in ipaddress.ip_network(c.address)
        ]
        if not covering:
            return None
        return max(covering, key=lambda pair: pair[0].prefixlen)[1]

    def handle(self, datagram: bytes, source: Address) -> Optional[bytes]:
        host = source[0]
        client = self.client_for(host)
        if client is None or not client.shared_secret:
            logger.warning("radius_provider: dropped a request from unknown %s", host)
            return None
        try:
            code, identifier, authenticator, packet = wire.packet_header(datagram)
            if code != AccessRequest:
                logger.debug("radius_provider: dropped code %s from %s", code, host)
                return None
            secret = decrypt_secret(client.shared_secret).encode()
            if not wire.request_is_authentic(packet, secret):
                logger.warning(
                    "radius_provider: dropped a request from %s (%s) without a "
                    "valid Message-Authenticator",
                    host,
                    client.name,
                )
                return None
        except wire.MalformedPacket as exc:
            logger.warning("radius_provider: dropped malformed %s: %s", host, exc)
            return None

        key: RequestKey = (client.id, identifier, authenticator)
        first, earlier = self.duplicates.claim(key)
        if not first:
            return earlier
        replay_key = f"{SERVICE_ID}:{client.id}:{authenticator.hex()}"
        if not get_replay_cache().mark_if_unused(replay_key, REPLAY_WINDOW_SECONDS):
            logger.warning("radius_provider: dropped a replayed request from %s", host)
            return None

        try:
            request = wire.decode_request(packet, secret)
            decision = self._decide(client, request, host)
            answer = wire.reply(request, decision.accepted, decision.attributes)
        except wire.MalformedPacket as exc:
            logger.warning("radius_provider: dropped malformed %s: %s", host, exc)
            answer = None
        self.duplicates.answer(key, answer)
        return answer

    def _decide(
        self, client: RadiusClientModel, request: AuthPacket, host: str
    ) -> Decision:
        if wire.raw_values(request, wire.EAP_MESSAGE) or wire.raw_values(
            request, wire.CHAP_PASSWORD
        ):
            logger.info("radius_provider: rejected a non-PAP request from %s", host)
            return REJECT
        return self.authenticator.decide(
            client, wire.user_name(request), wire.user_password(request), host
        )


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, handler: RequestHandler) -> None:
        self.handler = handler
        self.transport: Optional[asyncio.DatagramTransport] = None
        self.closed: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._in_flight: Set[asyncio.Task[None]] = set()

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        # A datagram endpoint's transport has sendto(), whatever its class
        # (the loop's own class failed an isinstance check here).
        self.transport = cast(asyncio.DatagramTransport, transport)

    def datagram_received(self, data: bytes, addr: Address) -> None:
        if len(self._in_flight) >= MAX_REQUESTS_IN_FLIGHT:
            logger.warning("radius_provider: busy, dropped a request from %s", addr[0])
            return
        task = asyncio.ensure_future(self._answer(data, addr))
        self._in_flight.add(task)
        task.add_done_callback(self._in_flight.discard)

    async def _answer(self, data: bytes, addr: Address) -> None:
        try:
            reply = await asyncio.to_thread(self.handler.handle, data, addr)
        except Exception as exc:
            # One bad request must not stop the listener; it goes unanswered.
            # No traceback: the logger renders frame locals, and these hold
            # the decrypted shared secret and the user's password.
            logger.error(
                "radius_provider: failed answering %s: %s", addr[0], type(exc).__name__
            )
            return
        if reply is not None and self.transport and not self.transport.is_closing():
            self.transport.sendto(reply, addr)

    def connection_lost(self, exc: Optional[Exception]) -> None:
        if not self.closed.done():
            self.closed.set_result(None)


class RADIUSAuthService(AbstractService):
    """Listens on ``bind_address``:``port`` for as long as the app runs."""

    def __init__(
        self,
        requester_id: str,
        model_registry: Any,
        bind_address: str,
        port: int,
        **kwargs: Any,
    ) -> None:
        super().__init__(requester_id=requester_id, service_id=SERVICE_ID, **kwargs)
        self.handler = RequestHandler(model_registry)
        self.bind_address = bind_address
        self.port = port
        self.local_address: Optional[Address] = None
        self.listening = threading.Event()
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def update(self) -> None:
        """Unused: the listener answers datagrams as they arrive."""

    async def run_service_loop(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            transport, protocol = await loop.create_datagram_endpoint(
                lambda: _Protocol(self.handler),
                local_addr=(self.bind_address, self.port),
            )
        except OSError as exc:
            logger.error(
                "radius_provider: cannot listen on %s:%s (%s); another worker "
                "may hold it",
                self.bind_address,
                self.port,
                exc,
            )
            self.running = False
            return
        self._loop, self._transport = loop, transport
        sockname: List[Any] = list(transport.get_extra_info("sockname"))
        self.local_address = (str(sockname[0]), int(sockname[1]))
        self.listening.set()
        logger.info("radius_provider: listening on %s:%s", *self.local_address)
        try:
            await protocol.closed
        finally:
            transport.close()
            self.listening.clear()

    def stop(self) -> None:
        super().stop()
        if self._loop is not None and self._transport is not None:
            try:
                self._loop.call_soon_threadsafe(self._transport.close)
            except RuntimeError:
                pass  # the loop has already closed, and the socket with it
