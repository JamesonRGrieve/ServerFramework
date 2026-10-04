# SPDX-License-Identifier: AGPL-3.0-or-later
"""A real IMAP4rev1 server for the tests, in process, on a loopback port.

No IMAP server (dovecot, cyrus) is installed where the tests run, so this
speaks the protocol (RFC 3501) for the commands an inbound reader uses:
CAPABILITY, NOOP, STARTTLS, LOGIN, SELECT/EXAMINE, CLOSE, LOGOUT, and UID
SEARCH, FETCH (UID, FLAGS, RFC822.SIZE, BODY[] and BODY.PEEK[]), STORE,
COPY, MOVE (RFC 6851) and EXPUNGE (UIDPLUS, RFC 4315). Tagged and untagged
responses, quoted strings, literals and response codes are as a real server
sends them, so the client under test runs unchanged against it.

The transport is implicit TLS, STARTTLS (offered or not) or plain, with a
throwaway CA (:func:`issue_certificates`). Each LOGIN is recorded with
whether it arrived encrypted, so a test can prove a refused connection sent
no credentials. :meth:`IMAPTestServer.renumber` gives a mailbox a new
UIDVALIDITY and numbers its messages afresh, as a server does after a
rebuild.
"""

from __future__ import annotations

import datetime
import ipaddress
import socket
import socketserver
import ssl
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Dict, Iterable, List, Literal, Optional, Set, Tuple, Type

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

LOOPBACK = "127.0.0.1"
CERTIFICATE_DAYS = 2
SOCKET_TIMEOUT_SECONDS = 10
BASE_CAPABILITIES = ("IMAP4rev1",)
SEEN, DELETED = "\\Seen", "\\Deleted"
Transport = Literal["tls", "starttls", "plain"]


@dataclass(frozen=True)
class Certificates:
    ca_pem: str
    certificate_pem: bytes
    key_pem: bytes


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def issue_certificates() -> Certificates:
    """A throwaway CA, and a server certificate it signed for 127.0.0.1 and
    localhost."""
    now = datetime.datetime.now(datetime.timezone.utc)
    until = now + datetime.timedelta(days=CERTIFICATE_DAYS)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = (
        x509.CertificateBuilder()
        .subject_name(_name("IMAP test CA"))
        .issuer_name(_name("IMAP test CA"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(until)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
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
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    certificate = (
        x509.CertificateBuilder()
        .subject_name(_name("localhost"))
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(until)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address(LOOPBACK)),
                ]
            ),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return Certificates(
        ca_pem=ca.public_bytes(serialization.Encoding.PEM).decode(),
        certificate_pem=certificate.public_bytes(serialization.Encoding.PEM),
        key_pem=key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )


@dataclass
class StoredMessage:
    uid: int
    body: bytes
    flags: Set[str] = field(default_factory=set)


@dataclass
class Mailbox:
    uid_validity: int
    uid_next: int = 1
    messages: List[StoredMessage] = field(default_factory=list)

    def add(self, body: bytes, flags: Iterable[str] = ()) -> int:
        uid = self.uid_next
        self.uid_next += 1
        self.messages.append(StoredMessage(uid=uid, body=body, flags=set(flags)))
        return uid

    def uids(self, uid_set: str) -> List[int]:
        """The UIDs in use that ``uid_set`` (``1:*``, ``4``, ``2,5:7``)
        names; ``*`` is the highest in use."""
        in_use = [message.uid for message in self.messages]
        if not in_use:
            return []
        highest = max(in_use)
        chosen: Set[int] = set()
        for part in uid_set.split(","):
            low_text, _, high_text = part.partition(":")
            low = highest if low_text == "*" else int(low_text)
            high = (
                low
                if not high_text
                else highest if high_text == "*" else int(high_text)
            )
            low, high = min(low, high), max(low, high)
            chosen.update(uid for uid in in_use if low <= uid <= high)
        return sorted(chosen)


@dataclass(frozen=True)
class Login:
    username: str
    encrypted: bool


def tokens(text: str) -> List[str]:
    """An IMAP command's arguments: atoms, quoted strings (unescaped) and
    parenthesized lists (kept whole)."""
    found: List[str] = []
    index = 0
    while index < len(text):
        character = text[index]
        if character == " ":
            index += 1
        elif character == '"':
            index += 1
            value: List[str] = []
            while text[index] != '"':
                if text[index] == "\\":
                    index += 1
                value.append(text[index])
                index += 1
            found.append("".join(value))
            index += 1
        elif character == "(":
            depth, start = 0, index
            while True:
                depth += {"(": 1, ")": -1}.get(text[index], 0)
                index += 1
                if depth == 0:
                    break
            found.append(text[start:index])
        else:
            end = text.find(" ", index)
            end = len(text) if end < 0 else end
            found.append(text[index:end])
            index = end
    return found


class _Refused(Exception):
    """A command answered with NO (or BAD)."""

    def __init__(self, status: str, text: str) -> None:
        super().__init__(text)
        self.status = status
        self.text = text


class IMAPTestServer:
    """``with IMAPTestServer(...) as server:`` serves on ``server.port``."""

    def __init__(
        self,
        username: str,
        password: str,
        *,
        transport: Transport = "tls",
        offer_starttls: bool = True,
        extensions: Tuple[str, ...] = ("UIDPLUS", "MOVE"),
        uid_validity: int = 1000,
    ) -> None:
        self.username = username
        self.password = password
        self.transport = transport
        self.offer_starttls = offer_starttls
        self.extensions = extensions
        self.certificates = issue_certificates()
        self.logins: List[Login] = []
        self.lock = threading.Lock()
        self.mailboxes: Dict[str, Mailbox] = {"INBOX": Mailbox(uid_validity)}
        self._directory = tempfile.TemporaryDirectory()
        self.tls_context = self._server_context()
        self._server: Optional[socketserver.ThreadingTCPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.port = 0

    def _server_context(self) -> ssl.SSLContext:
        directory = Path(self._directory.name)
        (directory / "cert.pem").write_bytes(self.certificates.certificate_pem)
        (directory / "key.pem").write_bytes(self.certificates.key_pem)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(directory / "cert.pem", directory / "key.pem")
        return context

    # -- what a test does to the mailboxes ----------------------------------

    def create_mailbox(self, name: str) -> None:
        with self.lock:
            self.mailboxes.setdefault(name, Mailbox(uid_validity=1))

    def deliver(
        self, raw: bytes, mailbox: str = "INBOX", flags: Iterable[str] = ()
    ) -> int:
        with self.lock:
            return self.mailboxes[mailbox].add(raw, flags)

    def messages(self, mailbox: str = "INBOX") -> List[StoredMessage]:
        with self.lock:
            return [
                StoredMessage(m.uid, m.body, set(m.flags))
                for m in self.mailboxes[mailbox].messages
            ]

    def clear_flags(self, mailbox: str = "INBOX") -> None:
        """Every message unflagged, as when another client marks all unread."""
        with self.lock:
            for message in self.mailboxes[mailbox].messages:
                message.flags.clear()

    def renumber(self, mailbox: str, uid_validity: int) -> None:
        """A new UIDVALIDITY, and every message numbered afresh from 1."""
        with self.lock:
            box = self.mailboxes[mailbox]
            box.uid_validity, box.uid_next = uid_validity, 1
            for message in box.messages:
                message.uid = box.uid_next
                box.uid_next += 1

    # -- lifecycle -----------------------------------------------------------

    def __enter__(self) -> "IMAPTestServer":
        owner = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                _Session(owner, self.request).run()

        server = socketserver.ThreadingTCPServer((LOOPBACK, 0), Handler)
        server.daemon_threads = True
        self._server = server
        self.port = int(server.server_address[1])
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(SOCKET_TIMEOUT_SECONDS)
        self._directory.cleanup()


class _Session:
    """One client connection."""

    def __init__(self, server: IMAPTestServer, connection: socket.socket) -> None:
        self.server = server
        self.socket: socket.socket = connection
        self.socket.settimeout(SOCKET_TIMEOUT_SECONDS)
        self.encrypted = False
        self.user: Optional[str] = None
        self.selected: Optional[str] = None
        self.read_only = False

    def run(self) -> None:
        try:
            if self.server.transport == "tls":
                self._start_tls()
            self._send(b"* OK IMAP4rev1 test server ready\r\n")
            reader = self.socket.makefile("rb")
            while True:
                line = reader.readline()
                if not line:
                    return
                tag, _, rest = line.decode("utf-8").rstrip("\r\n").partition(" ")
                command, _, arguments = rest.partition(" ")
                outcome = self._dispatch(tag, command.upper(), arguments)
                if outcome == "logout":
                    return
                if outcome == "starttls":
                    self._start_tls()
                    reader = self.socket.makefile("rb")
        except (OSError, ssl.SSLError, UnicodeDecodeError):
            return
        finally:
            self.socket.close()

    def _start_tls(self) -> None:
        self.socket = self.server.tls_context.wrap_socket(self.socket, server_side=True)
        self.encrypted = True

    def _send(self, data: bytes) -> None:
        self.socket.sendall(data)

    def _untagged(self, text: str) -> None:
        self._send(f"* {text}\r\n".encode())

    def _capabilities(self) -> str:
        offered = list(BASE_CAPABILITIES)
        if (
            self.server.transport == "starttls"
            and self.server.offer_starttls
            and not self.encrypted
        ):
            offered.append("STARTTLS")
        offered.extend(self.server.extensions)
        return " ".join(offered)

    def _dispatch(self, tag: str, command: str, arguments: str) -> Optional[str]:
        try:
            outcome = self._run(tag, command, tokens(arguments))
        except _Refused as refused:
            self._send(f"{tag} {refused.status} {refused.text}\r\n".encode())
            return None
        except (IndexError, ValueError, KeyError):
            self._send(f"{tag} BAD malformed {command}\r\n".encode())
            return None
        self._send(f"{tag} OK {command} completed\r\n".encode())
        return outcome

    def _run(self, tag: str, command: str, args: List[str]) -> Optional[str]:
        if command == "CAPABILITY":
            self._untagged(f"CAPABILITY {self._capabilities()}")
        elif command == "NOOP":
            pass
        elif command == "LOGOUT":
            self._untagged("BYE logging out")
            return "logout"
        elif command == "STARTTLS":
            if "STARTTLS" not in self._capabilities().split():
                raise _Refused("BAD", "STARTTLS is not offered")
            return "starttls"
        elif command == "LOGIN":
            self._login(args[0], args[1])
        elif self.user is None:
            raise _Refused("NO", "sign in first")
        elif command in ("SELECT", "EXAMINE"):
            self._select(args[0], read_only=command == "EXAMINE")
        elif command == "CLOSE":
            self.selected = None
        elif command == "UID":
            self._uid(args[0].upper(), args[1:])
        else:
            raise _Refused("BAD", f"{command} is not known here")
        return None

    def _login(self, username: str, password: str) -> None:
        with self.server.lock:
            self.server.logins.append(Login(username, self.encrypted))
        if (username, password) != (self.server.username, self.server.password):
            raise _Refused("NO", "[AUTHENTICATIONFAILED] Invalid credentials")
        self.user = username

    def _select(self, name: str, read_only: bool) -> None:
        with self.server.lock:
            box = self.server.mailboxes.get(name)
            if box is None:
                raise _Refused("NO", "[NONEXISTENT] No such mailbox")
            self.selected, self.read_only = name, read_only
            self._untagged(f"FLAGS ({SEEN} {DELETED})")
            self._untagged(f"{len(box.messages)} EXISTS")
            self._untagged("0 RECENT")
            self._untagged(f"OK [UIDVALIDITY {box.uid_validity}] UIDs valid")
            self._untagged(f"OK [UIDNEXT {box.uid_next}] Predicted next UID")

    def _box(self) -> Mailbox:
        if self.selected is None:
            raise _Refused("BAD", "select a mailbox first")
        return self.server.mailboxes[self.selected]

    def _writable(self) -> Mailbox:
        if self.read_only:
            raise _Refused("NO", "the mailbox is read-only")
        return self._box()

    def _uid(self, command: str, args: List[str]) -> None:
        with self.server.lock:
            if command == "SEARCH":
                self._search(args)
            elif command == "FETCH":
                self._fetch(args[0], args[1])
            elif command == "STORE":
                self._store(args[0], args[1], args[2])
            elif command in ("COPY", "MOVE"):
                self._copy(args[0], args[1], move=command == "MOVE")
            elif command == "EXPUNGE":
                self._expunge(args[0])
            else:
                raise _Refused("BAD", f"UID {command} is not known here")

    def _search(self, args: List[str]) -> None:
        box = self._box()
        found = {message.uid for message in box.messages}
        index = 0
        while index < len(args):
            key = args[index].upper()
            if key == "UID":
                found &= set(box.uids(args[index + 1]))
                index += 2
            elif key == "UNSEEN":
                found &= {m.uid for m in box.messages if SEEN not in m.flags}
                index += 1
            elif key == "ALL":
                index += 1
            else:
                raise _Refused("BAD", f"search key {key} is not known here")
        self._untagged(" ".join(["SEARCH", *(str(uid) for uid in sorted(found))]))

    def _fetch(self, uid_set: str, items: str) -> None:
        box = self._box()
        wanted = items.strip("()").upper().split()
        for sequence, message in enumerate(box.messages, start=1):
            if message.uid not in box.uids(uid_set):
                continue
            parts = [f"UID {message.uid}"]
            literal: Optional[bytes] = None
            for item in wanted:
                if item == "FLAGS":
                    parts.append(f"FLAGS ({' '.join(sorted(message.flags))})")
                elif item == "RFC822.SIZE":
                    parts.append(f"RFC822.SIZE {len(message.body)}")
                elif item in ("BODY[]", "BODY.PEEK[]"):
                    literal = message.body
                    if item == "BODY[]" and not self.read_only:
                        message.flags.add(SEEN)
                elif item != "UID":
                    raise _Refused("BAD", f"fetch item {item} is not known here")
            head = f"* {sequence} FETCH ({' '.join(parts)}"
            if literal is None:
                self._send(f"{head})\r\n".encode())
            else:
                self._send(f"{head} BODY[] {{{len(literal)}}}\r\n".encode())
                self._send(literal + b")\r\n")

    def _store(self, uid_set: str, mode: str, flag_list: str) -> None:
        box = self._writable()
        flags = set(flag_list.strip("()").split())
        silent = mode.upper().endswith(".SILENT")
        operation = mode.upper().removesuffix(".SILENT")
        for sequence, message in enumerate(box.messages, start=1):
            if message.uid not in box.uids(uid_set):
                continue
            if operation == "+FLAGS":
                message.flags |= flags
            elif operation == "-FLAGS":
                message.flags -= flags
            elif operation == "FLAGS":
                message.flags = set(flags)
            else:
                raise _Refused("BAD", f"STORE {mode} is not known here")
            if not silent:
                self._untagged(
                    f"{sequence} FETCH (UID {message.uid} "
                    f"FLAGS ({' '.join(sorted(message.flags))}))"
                )

    def _copy(self, uid_set: str, destination: str, move: bool) -> None:
        if move and "MOVE" not in self.server.extensions:
            raise _Refused("BAD", "MOVE is not offered")
        box = self._writable() if move else self._box()
        target = self.server.mailboxes.get(destination)
        if target is None:
            raise _Refused("NO", "[TRYCREATE] No such mailbox")
        chosen = set(box.uids(uid_set))
        for message in box.messages:
            if message.uid in chosen:
                target.add(message.body, message.flags - {DELETED})
        if move:
            self._remove(box, chosen)

    def _expunge(self, uid_set: str) -> None:
        if "UIDPLUS" not in self.server.extensions:
            raise _Refused("BAD", "UID EXPUNGE needs UIDPLUS, which is not offered")
        box = self._writable()
        chosen = set(box.uids(uid_set))
        self._remove(
            box, {m.uid for m in box.messages if m.uid in chosen and DELETED in m.flags}
        )

    def _remove(self, box: Mailbox, uids: Set[int]) -> None:
        # EXPUNGE responses name sequence numbers, each counted after the
        # ones before it are gone.
        sequence = 1
        kept: List[StoredMessage] = []
        for message in box.messages:
            if message.uid in uids:
                self._untagged(f"{sequence} EXPUNGE")
            else:
                kept.append(message)
                sequence += 1
        box.messages = kept
