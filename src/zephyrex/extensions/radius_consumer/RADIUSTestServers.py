# SPDX-License-Identifier: AGPL-3.0-or-later
"""Real RADIUS servers for the tests: FreeRADIUS, and a bare UDP responder.

:class:`FreeRADIUS` runs ``freeradius -fxx`` on high ports with a
configuration generated under a temporary directory: PAP users in its
``files`` module, an ``otpuser`` who must answer an Access-Challenge, plain
RADIUS on UDP and, with certificates, RADIUS over TLS (RadSec).

:class:`HandCraftedResponder` answers on UDP with replies built here byte by
byte (no Message-Authenticator, a wrong one), which no correct server sends:
the refusals the client must make. :func:`silent_udp_port` is a bound socket
that never answers, for timeouts.

Certificates come from :func:`issue_certificates` (a throwaway CA, a server
certificate for 127.0.0.1 and localhost, and a client certificate).
"""

from __future__ import annotations

import datetime
import hashlib
import ipaddress
import shutil
import socket
import struct
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from contextlib import contextmanager
from typing import Callable, Dict, Iterator, List, Optional, Tuple, Type

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

FREERADIUS = "/usr/sbin/freeradius"
READY_MARKER = "Ready to process requests"
STARTUP_TIMEOUT_SECONDS = 30
SHUTDOWN_TIMEOUT_SECONDS = 10
LOOPBACK = "127.0.0.1"
CERTIFICATE_DAYS = 2
MAX_DATAGRAM = 4096

# The challenge user: first factor, then a code.
OTP_USER = "otpuser"
OTP_FIRST_FACTOR = "first-factor"
OTP_CODE = "123456"
OTP_PROMPT = "Enter the code from your token"
OTP_STATE_HEX = "6f74702d7374657031"


def freeradius_available() -> bool:
    return shutil.which(FREERADIUS) is not None or Path(FREERADIUS).exists()


def free_port(kind: socket.SocketKind) -> int:
    """A port the kernel just handed out on loopback (for ``kind``)."""
    with socket.socket(socket.AF_INET, kind) as probe:
        probe.bind((LOOPBACK, 0))
        port: int = probe.getsockname()[1]
        return port


@contextmanager
def silent_udp_port() -> Iterator[int]:
    """A UDP port that receives and never answers."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((LOOPBACK, 0))
        yield sock.getsockname()[1]


@dataclass(frozen=True)
class Certificates:
    ca_pem: str
    server_cert_pem: str
    server_key_pem: str
    client_cert_pem: str
    client_key_pem: str


def _key_pem(key: ec.EllipticCurvePrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def _cert_pem(cert: x509.Certificate) -> str:
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def issue_certificates(common_name: str = "radius-test-ca") -> Certificates:
    """A fresh CA, and a server and a client certificate it signed."""
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=CERTIFICATE_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            True,
        )
        .sign(ca_key, hashes.SHA256())
    )

    def leaf(name: str, usage: x509.ObjectIdentifier) -> Tuple[str, str]:
        key = ec.generate_private_key(ec.SECP256R1())
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
            .issuer_name(ca_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=CERTIFICATE_DAYS))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
            .add_extension(x509.ExtendedKeyUsage([usage]), False)
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName("localhost"),
                        x509.IPAddress(ipaddress.ip_address(LOOPBACK)),
                    ]
                ),
                False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        return _cert_pem(cert), _key_pem(key)

    server_cert, server_key = leaf("localhost", ExtendedKeyUsageOID.SERVER_AUTH)
    client_cert, client_key = leaf("zephyrex", ExtendedKeyUsageOID.CLIENT_AUTH)
    return Certificates(_cert_pem(ca), server_cert, server_key, client_cert, client_key)


_USERS_TEMPLATE = (
    '{name}\tCleartext-Password := "{password}"\n\tReply-Message := "Welcome, {name}"\n'
)

_CONFIG_TEMPLATE = """\
prefix = /usr
exec_prefix = /usr
sysconfdir = /etc
localstatedir = /var
sbindir = /usr/sbin
confdir = {confdir}
raddbdir = ${{confdir}}
logdir = ${{confdir}}
run_dir = ${{confdir}}
db_dir = ${{confdir}}
libdir = /usr/lib/freeradius
pidfile = ${{run_dir}}/radiusd.pid
name = radiusd
max_request_time = 30
cleanup_delay = 2
max_requests = 1024
hostname_lookups = no

log {{
	destination = stdout
	auth = yes
}}

security {{
	allow_core_dumps = no
	max_attributes = 200
	reject_delay = 0
	status_server = yes
}}

thread pool {{
	start_servers = 1
	max_servers = 4
	min_spare_servers = 1
	max_spare_servers = 2
	max_requests_per_server = 0
}}

client local {{
	ipaddr = 127.0.0.1
	secret = {secret}
	require_message_authenticator = yes
}}

{radsec_clients}

modules {{
	pap {{
		normalise = yes
	}}
	files {{
		filename = ${{confdir}}/users
	}}
	always reject {{
		rcode = reject
	}}
	always ok {{
		rcode = ok
	}}
	always handled {{
		rcode = handled
	}}
}}

server default {{
	listen {{
		type = auth
		ipaddr = 127.0.0.1
		port = {udp_port}
	}}
{radsec_listen}
	authorize {{
		if (&User-Name == "{otp_user}") {{
			if (!&State) {{
				if (&User-Password == "{otp_first}") {{
					update reply {{
						&Reply-Message := "{otp_prompt}"
						&State := 0x{otp_state}
					}}
					update control {{
						&Response-Packet-Type := Access-Challenge
					}}
					handled
				}}
				reject
			}}
			if ((&State == 0x{otp_state}) && (&User-Password == "{otp_code}")) {{
				update control {{
					&Auth-Type := Accept
				}}
				ok
			}}
			else {{
				reject
			}}
		}}
		else {{
			files
			pap
		}}
	}}
	authenticate {{
		Auth-Type PAP {{
			pap
		}}
	}}
	post-auth {{
		Post-Auth-Type REJECT {{
			ok
		}}
	}}
}}
"""

_RADSEC_CLIENTS = """\
clients radsec {
	client local_tls {
		ipaddr = 127.0.0.1
		proto = tls
		secret = radsec
	}
}
"""

_RADSEC_LISTEN = """\
	listen {{
		type = auth
		ipaddr = 127.0.0.1
		port = {port}
		proto = tcp
		clients = radsec
		tls {{
			private_key_file = ${{confdir}}/server.key
			certificate_file = ${{confdir}}/server.pem
			ca_file = ${{confdir}}/ca.pem
			require_client_cert = yes
			fragment_size = 8192
			tls_min_version = "1.2"
			tls_max_version = "1.2"
			cipher_list = "DEFAULT"
		}}
	}}
"""


class FreeRADIUS:
    """FreeRADIUS in the foreground, debugging to stdout, under ``workdir``
    until the context exits."""

    def __init__(
        self,
        workdir: Path,
        secret: str,
        users: Dict[str, str],
        certificates: Optional[Certificates] = None,
    ) -> None:
        self.workdir = workdir
        self.secret = secret
        self.users = users
        self.certificates = certificates
        self.udp_port = free_port(socket.SOCK_DGRAM)
        self.radsec_port = free_port(socket.SOCK_STREAM) if certificates else None
        self.log: List[str] = []
        self._ready = threading.Event()
        self._process: Optional[subprocess.Popen[str]] = None
        self._reader: Optional[threading.Thread] = None

    def _write_config(self) -> None:
        self.workdir.mkdir(parents=True, exist_ok=True)
        radsec_listen = ""
        if self.certificates is not None:
            radsec_listen = _RADSEC_LISTEN.format(port=self.radsec_port)
            for name, pem in (
                ("ca.pem", self.certificates.ca_pem),
                ("server.pem", self.certificates.server_cert_pem),
                ("server.key", self.certificates.server_key_pem),
            ):
                (self.workdir / name).write_text(pem)
                (self.workdir / name).chmod(0o600)
        (self.workdir / "radiusd.conf").write_text(
            _CONFIG_TEMPLATE.format(
                confdir=self.workdir,
                secret=self.secret,
                udp_port=self.udp_port,
                radsec_clients=_RADSEC_CLIENTS if self.certificates else "",
                radsec_listen=radsec_listen,
                otp_user=OTP_USER,
                otp_first=OTP_FIRST_FACTOR,
                otp_code=OTP_CODE,
                otp_prompt=OTP_PROMPT,
                otp_state=OTP_STATE_HEX,
            )
        )
        (self.workdir / "users").write_text(
            "".join(
                _USERS_TEMPLATE.format(name=name, password=password)
                for name, password in self.users.items()
            )
        )

    def _read_output(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        for line in self._process.stdout:
            self.log.append(line)
            if READY_MARKER in line:
                self._ready.set()
        self._ready.set()

    def __enter__(self) -> "FreeRADIUS":
        self._write_config()
        self._process = subprocess.Popen(
            # -X would also turn threading off, which RadSec listeners need.
            [FREERADIUS, "-fxx", "-l", "stdout", "-d", str(self.workdir)]
            + ["-n", "radiusd"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self._reader = threading.Thread(target=self._read_output, daemon=True)
        self._reader.start()
        if (
            not self._ready.wait(STARTUP_TIMEOUT_SECONDS)
            or self._process.poll() is not None
        ):
            self.__exit__(None, None, None)
            raise RuntimeError("FreeRADIUS did not start:\n" + "".join(self.log[-40:]))
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        if self._process is None:
            return
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(SHUTDOWN_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(SHUTDOWN_TIMEOUT_SECONDS)
        if self._reader is not None:
            self._reader.join(SHUTDOWN_TIMEOUT_SECONDS)
        self._process = None

    def received(self, text: str) -> bool:
        """Whether the server's debug log mentions ``text``."""
        return any(text in line for line in self.log)


ReplyBuilder = Callable[[bytes, bytes], bytes]


def accept_without_message_authenticator(request: bytes, secret: bytes) -> bytes:
    """An Access-Accept with a correct Response Authenticator and no
    Message-Authenticator: what a server open to Blast-RADIUS sends."""
    return _signed_reply(request, secret, b"")


def accept_with_forged_message_authenticator(request: bytes, secret: bytes) -> bytes:
    """An Access-Accept whose Message-Authenticator is not the HMAC."""
    return _signed_reply(request, secret, bytes([80, 18]) + b"\x5a" * 16)


def _signed_reply(request: bytes, secret: bytes, attributes: bytes) -> bytes:
    """Code 2 for ``request``: header, then the Response Authenticator
    computed as RFC 2865 section 3 says, independently of the client."""
    header = struct.pack("!BBH", 2, request[1], 20 + len(attributes))
    authenticator = hashlib.md5(header + request[4:20] + attributes + secret).digest()
    return header + authenticator + attributes


class HandCraftedResponder:
    """A UDP server answering every request with ``build(request, secret)``."""

    def __init__(self, secret: bytes, build: ReplyBuilder) -> None:
        self.secret = secret
        self.build = build
        self.requests: List[bytes] = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind((LOOPBACK, 0))
        self.port: int = self._sock.getsockname()[1]
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def answers(self, request: bytes) -> List[bytes]:
        """The datagrams sent back for ``request``, in order."""
        return [self.build(request, self.secret)]

    def _serve(self) -> None:
        while True:
            request, peer = self._sock.recvfrom(MAX_DATAGRAM)
            if self._stopping.is_set():
                return
            self.requests.append(request)
            for answer in self.answers(request):
                self._sock.sendto(answer, peer)

    def __enter__(self) -> "HandCraftedResponder":
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        # A datagram of its own wakes the blocked receive to see the flag.
        self._stopping.set()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as waker:
            waker.sendto(b"", (LOOPBACK, self.port))
        self._thread.join(SHUTDOWN_TIMEOUT_SECONDS)
        self._sock.close()


class ForgeryThenRelay(HandCraftedResponder):
    """Answers each request with a forged Accept, then with the real reply
    of the RADIUS server on ``upstream_port`` (the request relayed as is)."""

    def __init__(self, secret: bytes, upstream_port: int) -> None:
        super().__init__(secret, accept_with_forged_message_authenticator)
        self.upstream = (LOOPBACK, upstream_port)

    def answers(self, request: bytes) -> List[bytes]:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as relay:
            relay.settimeout(STARTUP_TIMEOUT_SECONDS)
            relay.sendto(request, self.upstream)
            real, _ = relay.recvfrom(MAX_DATAGRAM)
        return [*super().answers(request), real]
