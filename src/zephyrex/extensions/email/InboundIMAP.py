# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading one configured IMAP mailbox: its settings, and a session on it.

A mailbox is a root- or system-scoped instance of an IMAP-reading email
provider (``polls_imap``) with ``inbound_enabled`` true. Its settings
(:data:`IMAP_INBOUND_SETTINGS`) name the server, the transport security,
the account (the password is write-only and encrypted) and what to do with
a message once it is delivered.

The transport is TLS: implicit (``tls``, port 993) or ``starttls``, with the
server's certificate and host name verified (against ``inbound_ca_certificate``
when set, else the system's trust store). ``plain`` is refused unless the
host is loopback and ``inbound_allow_plaintext`` is true; credentials are
never sent over a connection that is not encrypted otherwise.

:class:`IMAPMailbox` is a blocking stdlib ``imaplib`` session, used from a
worker thread; every socket operation is bounded by its timeout.
"""

import imaplib
import ipaddress
import re
import ssl
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple, Type, cast

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticProvider,
    InstanceSetting,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

Security = Literal["tls", "starttls", "plain"]
AfterDelivery = Literal["seen", "move", "delete"]
SECURITIES: Tuple[Security, ...] = ("tls", "starttls", "plain")
AFTER_DELIVERY: Tuple[AfterDelivery, ...] = ("seen", "move", "delete")

ENABLED_SETTING = "inbound_enabled"
DEFAULT_IMAPS_PORT = 993
DEFAULT_POLL_SECONDS = 60
MIN_POLL_SECONDS = 10
MAX_POLL_SECONDS = 86400
MAX_PORT = 65535
# The longest mailbox name accepted (printable ASCII; modified UTF-7 names
# are written as the server lists them).
MAX_MAILBOX_NAME = 255
LOOPBACK_NAMES = frozenset({"localhost"})
_FETCHED_UID = re.compile(rb"UID (\d+)")
_FETCHED_SIZE = re.compile(rb"RFC822\.SIZE (\d+)")

IMAP_INBOUND_SETTINGS: Tuple[InstanceSetting, ...] = (
    InstanceSetting(
        ENABLED_SETTING,
        "true to have the inbound poller read this mailbox (root- and "
        "system-scoped instances only)",
        default="false",
    ),
    InstanceSetting("inbound_host", "IMAP server host name"),
    InstanceSetting(
        "inbound_port", "IMAP server port", default=str(DEFAULT_IMAPS_PORT)
    ),
    InstanceSetting(
        "inbound_security",
        "tls (implicit TLS), starttls, or plain (loopback only, with "
        "inbound_allow_plaintext)",
        default="tls",
    ),
    InstanceSetting(
        "inbound_allow_plaintext",
        "true to allow plain IMAP to a loopback host",
        default="false",
    ),
    InstanceSetting(
        "inbound_ca_certificate",
        "PEM CA certificate(s) to verify the server against, instead of the "
        "system trust store",
        multiline=True,
    ),
    InstanceSetting("inbound_username", "IMAP account user name"),
    InstanceSetting(
        "inbound_password", "IMAP account password (write-only)", secret=True
    ),
    InstanceSetting("inbound_mailbox", "Mailbox to read", default="INBOX"),
    InstanceSetting(
        "inbound_poll_seconds",
        f"Seconds between polls ({MIN_POLL_SECONDS}-{MAX_POLL_SECONDS})",
        default=str(DEFAULT_POLL_SECONDS),
    ),
    InstanceSetting(
        "inbound_after",
        "What becomes of a delivered message: seen (flagged \\Seen; unseen "
        "mail is read), move (to inbound_move_to) or delete",
        default="seen",
    ),
    InstanceSetting(
        "inbound_move_to", "Mailbox delivered messages are moved to (for move)"
    ),
)


class InboundConfigError(ValueError):
    """The mailbox is configured so that it can never be read: fixed only by
    changing its settings (or the server)."""


class TLSRequiredError(InboundConfigError):
    """Reading the mailbox would send its credentials unencrypted."""


@dataclass(frozen=True)
class MailboxConfig:
    """One mailbox, as its instance's settings configure it."""

    provider_instance_id: str
    host: str
    port: int
    security: Security
    allow_plaintext: bool
    username: str
    password: str = field(repr=False)
    mailbox: str
    poll_seconds: int
    after: AfterDelivery
    move_to: Optional[str]
    ca_certificate: Optional[str] = field(default=None, repr=False)


def _flag(value: Optional[str]) -> bool:
    return (value or "").strip().lower() == "true"


def _number(value: Optional[str], name: str, low: int, high: int) -> int:
    text = (value or "").strip()
    if not text.isdigit() or not low <= int(text) <= high:
        raise InboundConfigError(f"{name} is a whole number from {low} to {high}")
    return int(text)


def _mailbox_name(value: Optional[str], name: str) -> str:
    text = value or ""
    if (
        not text
        or len(text) > MAX_MAILBOX_NAME
        or not all(" " <= character <= "~" for character in text)
    ):
        raise InboundConfigError(f"{name} is a mailbox name of printable ASCII")
    return text


def is_loopback(host: str) -> bool:
    """Whether ``host`` is a loopback address, or ``localhost``."""
    if host.lower() in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def inbound_enabled(
    provider: Type[AbstractStaticProvider], instance: ProviderInstanceModel
) -> bool:
    return _flag(provider.setting(instance, ENABLED_SETTING))


def mailbox_config(
    provider: Type[AbstractStaticProvider], instance: ProviderInstanceModel
) -> MailboxConfig:
    """The instance's mailbox, from its settings. InboundConfigError for
    settings it can never be read with, TLSRequiredError among them."""

    def setting(key: str) -> Optional[str]:
        return provider.setting(instance, key)

    default_host: str = getattr(provider, "default_imap_host", "")
    host = (setting("inbound_host") or default_host).strip()
    username = setting("inbound_username") or ""
    password = setting("inbound_password") or ""
    if not host or not username or not password:
        raise InboundConfigError(
            "inbound_host, inbound_username and inbound_password are required"
        )
    security = (setting("inbound_security") or "").strip().lower()
    if security not in SECURITIES:
        raise InboundConfigError(f"inbound_security is one of {', '.join(SECURITIES)}")
    allow_plaintext = _flag(setting("inbound_allow_plaintext"))
    if security == "plain" and not (allow_plaintext and is_loopback(host)):
        raise TLSRequiredError(
            "plain IMAP is refused: use tls or starttls (plain is allowed only "
            "to a loopback host, with inbound_allow_plaintext)"
        )
    after = (setting("inbound_after") or "").strip().lower()
    if after not in AFTER_DELIVERY:
        raise InboundConfigError(f"inbound_after is one of {', '.join(AFTER_DELIVERY)}")
    move_to = setting("inbound_move_to")
    mailbox = _mailbox_name(setting("inbound_mailbox"), "inbound_mailbox")
    if after == "move":
        move_to = _mailbox_name(move_to, "inbound_move_to")
        if move_to == mailbox:
            raise InboundConfigError("inbound_move_to is another mailbox")
    return MailboxConfig(
        provider_instance_id=str(instance.id),
        host=host,
        port=_number(setting("inbound_port"), "inbound_port", 1, MAX_PORT),
        security=cast(Security, security),
        allow_plaintext=allow_plaintext,
        username=username,
        password=password,
        mailbox=mailbox,
        poll_seconds=_number(
            setting("inbound_poll_seconds"),
            "inbound_poll_seconds",
            MIN_POLL_SECONDS,
            MAX_POLL_SECONDS,
        ),
        after=cast(AfterDelivery, after),
        move_to=move_to if after == "move" else None,
        ca_certificate=setting("inbound_ca_certificate") or None,
    )


def quoted(mailbox: str) -> str:
    """A mailbox name as an IMAP quoted string."""
    return '"' + mailbox.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _tls_context(ca_certificate: Optional[str]) -> ssl.SSLContext:
    context = ssl.create_default_context(cadata=ca_certificate)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def _ok(answer: Tuple[str, Any], doing: str) -> List[Any]:
    status, data = answer
    if status != "OK":
        raise imaplib.IMAP4.error(f"{doing} refused: {_text(data)}")
    return cast(List[Any], data)


def _text(data: Any) -> str:
    parts = data if isinstance(data, list) else [data]
    return " ".join(
        part.decode("utf-8", "replace") if isinstance(part, bytes) else str(part)
        for part in parts
        if part is not None
    )


class IMAPMailbox:
    """An authenticated session with the mailbox selected. Blocking; one
    thread at a time."""

    def __init__(self, connection: imaplib.IMAP4, config: MailboxConfig) -> None:
        self.connection = connection
        self.config = config
        self.capabilities: frozenset[str] = frozenset()
        self.uid_validity = ""

    @classmethod
    def open(cls, config: MailboxConfig, timeout: float) -> "IMAPMailbox":
        """Connect as the config says, sign in and select the mailbox.
        TLSRequiredError, before anything is sent, when the transport would
        not be encrypted; InboundConfigError when the server cannot do what
        the config asks of a delivered message."""
        connection = cls._connect(config, timeout)
        try:
            mailbox = cls(connection, config)
            mailbox._sign_in()
            return mailbox
        except BaseException:
            cls._drop(connection)
            raise

    @staticmethod
    def _connect(config: MailboxConfig, timeout: float) -> imaplib.IMAP4:
        if config.security == "tls":
            return imaplib.IMAP4_SSL(
                config.host,
                config.port,
                ssl_context=_tls_context(config.ca_certificate),
                timeout=timeout,
            )
        if config.security == "plain" and not (
            config.allow_plaintext and is_loopback(config.host)
        ):
            raise TLSRequiredError("plain IMAP is allowed only to a loopback host")
        connection = imaplib.IMAP4(config.host, config.port, timeout=timeout)
        if config.security == "starttls":
            if "STARTTLS" not in connection.capabilities:
                IMAPMailbox._drop(connection)
                raise TLSRequiredError("the server does not offer STARTTLS")
            try:
                connection.starttls(ssl_context=_tls_context(config.ca_certificate))
            except BaseException:
                IMAPMailbox._drop(connection)
                raise
        return connection

    def _sign_in(self) -> None:
        self.connection.login(self.config.username, self.config.password)
        capability = _ok(self.connection.capability(), "CAPABILITY")
        self.capabilities = frozenset(_text(capability).upper().split())
        if self.config.after == "delete" and "UIDPLUS" not in self.capabilities:
            raise InboundConfigError("delete needs a server with UIDPLUS")
        if self.config.after == "move" and not (
            {"MOVE", "UIDPLUS"} & self.capabilities
        ):
            raise InboundConfigError("move needs a server with MOVE or UIDPLUS")
        _ok(self.connection.select(quoted(self.config.mailbox)), "SELECT")
        _, validity = self.connection.response("UIDVALIDITY")
        value = _text(validity).strip()
        if not value.isdigit():
            raise imaplib.IMAP4.error("the server sent no UIDVALIDITY")
        self.uid_validity = value

    def new_uids(self, after_uid: int) -> List[int]:
        """UIDs above ``after_uid``, ascending; only unseen ones when
        delivered mail is flagged \\Seen."""
        criteria = ["UID", f"{after_uid + 1}:*"]
        if self.config.after == "seen":
            criteria.append("UNSEEN")
        found = _ok(self.connection.uid("SEARCH", *criteria), "SEARCH")
        uids = {int(uid) for uid in _text(found).split() if uid.isdigit()}
        # ``n:*`` names the highest UID even when it is below n.
        return sorted(uid for uid in uids if uid > after_uid)

    def sizes(self, uids: List[int]) -> Dict[int, int]:
        """Each message's size, by UID; a message gone since is missing."""
        if not uids:
            return {}
        fetched = _ok(
            self.connection.uid(
                "FETCH", ",".join(str(uid) for uid in uids), "(RFC822.SIZE)"
            ),
            "FETCH",
        )
        found: Dict[int, int] = {}
        for item in fetched:
            line = item[0] if isinstance(item, tuple) else item
            if not isinstance(line, bytes):
                continue
            uid, size = _FETCHED_UID.search(line), _FETCHED_SIZE.search(line)
            if uid and size:
                found[int(uid.group(1))] = int(size.group(1))
        return found

    def fetch(self, uid: int) -> Optional[bytes]:
        """The raw message, without flagging it \\Seen; None when it is
        gone."""
        fetched = _ok(self.connection.uid("FETCH", str(uid), "(BODY.PEEK[])"), "FETCH")
        for item in fetched:
            if isinstance(item, tuple) and len(item) == 2:
                header, body = item
                found = (
                    _FETCHED_UID.search(header) if isinstance(header, bytes) else None
                )
                if isinstance(body, bytes) and found and int(found.group(1)) == uid:
                    return body
        return None

    def finish(self, uid: int) -> None:
        """Do with a delivered message what the config says."""
        target = str(uid)
        if self.config.after == "seen":
            _ok(
                self.connection.uid("STORE", target, "+FLAGS.SILENT", r"(\Seen)"),
                "STORE",
            )
            return
        if self.config.after == "move" and self.config.move_to is not None:
            destination = quoted(self.config.move_to)
            if "MOVE" in self.capabilities:
                _ok(self.connection.uid("MOVE", target, destination), "MOVE")
                return
            _ok(self.connection.uid("COPY", target, destination), "COPY")
        _ok(
            self.connection.uid("STORE", target, "+FLAGS.SILENT", r"(\Deleted)"),
            "STORE",
        )
        # UID EXPUNGE removes this message only, not others flagged \Deleted.
        _ok(self.connection.uid("EXPUNGE", target), "EXPUNGE")

    def close(self) -> None:
        self._drop(self.connection)

    @staticmethod
    def _drop(connection: imaplib.IMAP4) -> None:
        """Log out, or at least close the socket."""
        try:
            connection.logout()
        except (OSError, imaplib.IMAP4.error):
            connection.shutdown()
