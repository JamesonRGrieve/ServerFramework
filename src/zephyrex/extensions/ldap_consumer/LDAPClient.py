# SPDX-License-Identifier: AGPL-3.0-or-later
"""Signing in against an LDAP directory: search, then bind as the person.

A sign-in opens two connections to the directory. The first binds as the
directory's service account and searches ``base_dn`` for the one entry
whose ``username_attribute`` equals the username (escaped per RFC 4515, so
a username can never widen or rewrite the filter). The second binds as
that entry's DN with the password given; only a successful bind proves the
password. The entry's stable id (``entryUUID``, or Active Directory's
``objectGUID``), not its DN, names the person, since a DN changes when an
entry is renamed or moved.

Every connection is LDAPS or StartTLS, verifying the directory's
certificate and its name against the system's trust store or the
directory's own CA. Plain LDAP is refused unless the operator sets
``LDAP_CONSUMER_ALLOW_PLAINTEXT_LOOPBACK=true`` and the directory's host
resolves only to loopback addresses (a directory on the same machine, as
in a test). An empty password is refused before any bind: LDAP treats a
DN with an empty password as an unauthenticated bind, which many
directories (Active Directory among them) answer with success.

Failures are typed: :class:`CredentialsRefused` when the person's
credentials are not accepted (unknown, ambiguous, wrong or empty),
:class:`AuthExternalError` when the service account is refused,
:class:`TransientExternalError` when the directory cannot be reached, and
:class:`PermanentExternalError` when it cannot be trusted or is
misconfigured."""

import ipaddress
import re
import socket
import ssl
import uuid
from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from ldap3 import NONE, SIMPLE, SUBTREE, Connection, Server, Tls
from ldap3.core.exceptions import (
    LDAPException,
    LDAPInvalidFilterError,
    LDAPStartTLSError,
)
from ldap3.operation.search import parse_filter
from ldap3.utils.conv import escape_filter_chars

from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    PermanentExternalError,
    TransientExternalError,
)
from zephyrex.lib.Environment import env

PROVIDER = "ldap_consumer"
Security = Literal["ldaps", "starttls", "plain"]
GroupSource = Literal["none", "member_of", "search"]
SECURITIES: Tuple[str, ...] = ("ldaps", "starttls", "plain")
GROUP_SOURCES: Tuple[str, ...] = ("none", "member_of", "search")
LDAPS_PORT = 636
LDAP_PORT = 389
MAX_PORT = 65535
ALLOW_PLAINTEXT_LOOPBACK = "LDAP_CONSUMER_ALLOW_PLAINTEXT_LOOPBACK"
# Room for a second match, so an ambiguous username is noticed.
USER_SEARCH_SIZE_LIMIT = 2
MAX_GROUPS = 1000
MAX_USERNAME_LENGTH = 256
MIN_TIMEOUT_SECONDS = 1
MAX_TIMEOUT_SECONDS = 60
MEMBER_OF = "memberOf"
OBJECT_GUID = "objectguid"
GUID_BYTES = 16
# LDAP result codes (RFC 4511 appendix A).
SUCCESS = 0
SIZE_LIMIT_EXCEEDED = 4
NO_SUCH_OBJECT = 32
# An attribute description: a name or an OID, optionally with options.
_ATTRIBUTE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9-]*|\d+(?:\.\d+)+)(?:;[A-Za-z0-9-]+)*$")


class CredentialsRefused(AuthExternalError):
    """The person's credentials were not accepted. One error (and, at the
    route, one message) for every reason, so a caller learns nothing about
    which accounts exist."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"directory sign-in refused: {reason}", provider=PROVIDER)
        self.reason = reason


@dataclass(frozen=True)
class DirectorySettings:
    """How to reach one directory and find people in it."""

    host: str
    port: Optional[int]
    security: str
    ca_certificate: Optional[str]
    bind_dn: str
    bind_password: str
    base_dn: str
    user_object_filter: str
    username_attribute: str
    id_attribute: str
    email_attribute: Optional[str]
    display_name_attribute: Optional[str]
    group_source: str
    group_search_base: Optional[str]
    group_object_filter: str
    group_member_attribute: str
    timeout_seconds: int


@dataclass(frozen=True)
class DirectoryAccount:
    """A person found in a directory."""

    dn: str
    external_id: str
    username: str
    email: Optional[str]
    display_name: Optional[str]
    groups: Tuple[str, ...]


def is_attribute(value: Optional[str]) -> bool:
    return bool(value) and bool(_ATTRIBUTE.match(str(value)))


def is_filter(value: Optional[str]) -> bool:
    """``value`` parses as an LDAP search filter (RFC 4515)."""
    if not value or not value.startswith("(") or not value.endswith(")"):
        return False
    try:
        parse_filter(value, None, False, False, None, False)
    except (LDAPInvalidFilterError, LDAPException, ValueError):
        return False
    return True


def resolves_to_loopback(host: str) -> bool:
    """Every address ``host`` resolves to is a loopback address."""
    try:
        addresses = {
            info[4][0]
            for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        }
    except socket.gaierror:
        return False
    try:
        return bool(addresses) and all(
            ipaddress.ip_address(str(address).split("%")[0]).is_loopback
            for address in addresses
        )
    except ValueError:
        return False


def plaintext_permitted(host: str) -> bool:
    """Plain LDAP is allowed: the operator opted in, and the directory is
    on this machine, so nothing crosses a network unencrypted."""
    opted_in = (env(ALLOW_PLAINTEXT_LOOPBACK) or "").strip().lower() == "true"
    return opted_in and resolves_to_loopback(host)


def configuration_problems(settings: Mapping[str, Any]) -> List[str]:
    """What is wrong with a directory's settings, as stored or as given:
    empty when it can be used."""
    problems: List[str] = []
    security = settings.get("security")
    host = str(settings.get("host") or "").strip()
    if not host:
        problems.append("host is required")
    if security not in SECURITIES:
        problems.append(f"security is one of {', '.join(SECURITIES)}")
    elif security == "plain" and host and not plaintext_permitted(host):
        problems.append(
            "plain LDAP sends passwords unencrypted: use ldaps or starttls "
            f"(plain is accepted only with {ALLOW_PLAINTEXT_LOOPBACK}=true "
            "and a host that resolves to loopback)"
        )
    port = settings.get("port")
    if port is not None and not 1 <= int(port) <= MAX_PORT:
        problems.append(f"port is 1-{MAX_PORT}")
    for key in ("username_attribute", "id_attribute"):
        if not is_attribute(settings.get(key)):
            problems.append(f"{key} is an attribute name")
    for key in ("email_attribute", "display_name_attribute"):
        if settings.get(key) and not is_attribute(settings.get(key)):
            problems.append(f"{key} is an attribute name")
    if not is_attribute(settings.get("group_member_attribute") or "member"):
        problems.append("group_member_attribute is an attribute name")
    for key in ("user_object_filter", "group_object_filter"):
        if settings.get(key) and not is_filter(settings.get(key)):
            problems.append(f"{key} is an LDAP filter, such as (objectClass=person)")
    if not str(settings.get("base_dn") or "").strip():
        problems.append("base_dn is required")
    group_source = settings.get("group_source") or "none"
    if group_source not in GROUP_SOURCES:
        problems.append(f"group_source is one of {', '.join(GROUP_SOURCES)}")
    elif group_source == "search" and not settings.get("group_search_base"):
        problems.append("group_search_base is required to search for groups")
    timeout = settings.get("timeout_seconds")
    if timeout is not None and not (
        MIN_TIMEOUT_SECONDS <= int(timeout) <= MAX_TIMEOUT_SECONDS
    ):
        problems.append(
            f"timeout_seconds is {MIN_TIMEOUT_SECONDS}-{MAX_TIMEOUT_SECONDS}"
        )
    ca = settings.get("ca_certificate")
    if ca and not _loads_as_ca(str(ca)):
        problems.append("ca_certificate is one or more PEM certificates")
    return problems


def _loads_as_ca(pem: str) -> bool:
    try:
        ssl.create_default_context(cadata=pem)
    except (ssl.SSLError, ValueError):
        return False
    return True


def _attribute(attributes: Mapping[str, Any], name: Optional[str]) -> List[bytes]:
    """The raw values of ``name`` (matched without regard to case)."""
    if not name:
        return []
    wanted = name.lower()
    for key, values in attributes.items():
        if key.lower() == wanted:
            return [bytes(v) for v in values if isinstance(v, (bytes, bytearray))]
    return []


def _text(values: Sequence[bytes]) -> Optional[str]:
    for value in values:
        try:
            decoded = bytes(value).decode("utf-8").strip()
        except UnicodeDecodeError:
            continue
        if decoded:
            return decoded
    return None


def external_id_of(attribute: str, values: Sequence[bytes]) -> Optional[str]:
    """An entry's stable id as text: Active Directory's ``objectGUID`` is
    16 bytes in its mixed-endian layout, shown as the GUID AD tools print;
    ``entryUUID`` (OpenLDAP, 389 DS) and ``ipaUniqueID`` are text."""
    if not values:
        return None
    raw = bytes(values[0])
    if attribute.lower() == OBJECT_GUID and len(raw) == GUID_BYTES:
        return str(uuid.UUID(bytes_le=raw))
    return _text(values)


def user_filter(settings: DirectorySettings, username: str) -> str:
    """The filter finding ``username``: the object filter AND an equality
    match whose value is escaped, so ``*``, parentheses, backslashes and NUL
    in a username match only themselves."""
    match = f"({settings.username_attribute}={escape_filter_chars(username)})"
    if settings.user_object_filter:
        return f"(&{settings.user_object_filter}{match})"
    return match


def group_filter(settings: DirectorySettings, member_dn: str) -> str:
    match = f"({settings.group_member_attribute}={escape_filter_chars(member_dn)})"
    if settings.group_object_filter:
        return f"(&{settings.group_object_filter}{match})"
    return match


def close(connection: Connection) -> None:
    """Unbind ``connection``, which may already be broken."""
    try:
        connection.unbind()
    except (LDAPException, OSError):
        return


class LDAPDirectoryClient:
    """Sign-ins and lookups against one directory."""

    def __init__(self, settings: DirectorySettings) -> None:
        self.settings = settings

    def server(self) -> Server:
        settings = self.settings
        if settings.security not in SECURITIES:
            raise PermanentExternalError(
                f"unknown security {settings.security!r}", provider=PROVIDER
            )
        if settings.security == "plain" and not plaintext_permitted(settings.host):
            raise PermanentExternalError(
                "plain LDAP is refused for a directory that is not on loopback "
                f"or without {ALLOW_PLAINTEXT_LOOPBACK}=true",
                provider=PROVIDER,
            )
        tls = Tls(
            validate=ssl.CERT_REQUIRED,
            ca_certs_data=settings.ca_certificate or None,
        )
        ldaps = settings.security == "ldaps"
        port = settings.port or (LDAPS_PORT if ldaps else LDAP_PORT)
        return Server(
            settings.host,
            port=port,
            use_ssl=ldaps,
            tls=tls,
            get_info=NONE,
            connect_timeout=settings.timeout_seconds,
        )

    def _fail(self, exc: BaseException, doing: str) -> Exception:
        text = str(exc).lower()
        if "certificate" in text or "ssl" in text or "tls" in text:
            return PermanentExternalError(
                f"directory TLS could not be verified while {doing}",
                provider=PROVIDER,
            )
        return TransientExternalError(
            f"directory unreachable while {doing} ({type(exc).__name__})",
            provider=PROVIDER,
        )

    def _open(self, server: Server, user: str, password: str) -> Connection:
        """A connection to ``server``, secured, bound as ``user``. Raises
        :class:`CredentialsRefused` when the bind is refused."""
        if not user or not password:
            raise CredentialsRefused(
                "an empty DN or password is an unauthenticated bind"
            )
        connection = Connection(
            server,
            user=user,
            password=password,
            authentication=SIMPLE,
            auto_bind=False,
            read_only=True,
            raise_exceptions=False,
            receive_timeout=self.settings.timeout_seconds,
            return_empty_attributes=False,
        )
        try:
            connection.open()
            if self.settings.security == "starttls" and not connection.start_tls():
                raise PermanentExternalError(
                    "the directory refused StartTLS", provider=PROVIDER
                )
            if not connection.bind():
                raise CredentialsRefused(
                    f"bind refused ({connection.result.get('description')})"
                )
        except (CredentialsRefused, PermanentExternalError):
            close(connection)
            raise
        except LDAPStartTLSError as exc:
            close(connection)
            raise PermanentExternalError(
                "the directory's StartTLS could not be verified", provider=PROVIDER
            ) from exc
        except (LDAPException, OSError) as exc:
            close(connection)
            raise self._fail(exc, "connecting") from exc
        return connection

    def _service(self, server: Server) -> Connection:
        try:
            return self._open(
                server, self.settings.bind_dn, self.settings.bind_password
            )
        except CredentialsRefused as exc:
            raise AuthExternalError(
                "the directory refused the service account", provider=PROVIDER
            ) from exc

    def check(self) -> None:
        """Reach the directory securely and bind as the service account."""
        close(self._service(self.server()))

    def find(self, username: str) -> DirectoryAccount:
        """The one entry for ``username``, found as the service account."""
        server = self.server()
        service = self._service(server)
        try:
            return self._account(service, self._entry(service, username), username)
        finally:
            close(service)

    def authenticate(
        self,
        username: str,
        password: str,
        admit: Optional[Callable[[DirectoryAccount], None]] = None,
    ) -> DirectoryAccount:
        """The entry for ``username``, once its password is proven by a bind.

        ``admit`` sees the account found before its password is tried, and
        may refuse the attempt by raising (an account locked out by too many
        failures is not offered another guess)."""
        if not password:
            raise CredentialsRefused("empty password")
        if not username or not username.strip():
            raise CredentialsRefused("empty username")
        server = self.server()
        service = self._service(server)
        try:
            account = self._account(service, self._entry(service, username), username)
            if admit is not None:
                admit(account)
            close(self._open(server, account.dn, password))
            return account
        finally:
            close(service)

    def _search(
        self,
        connection: Connection,
        base: str,
        search_filter: str,
        attributes: List[str],
        size_limit: int,
    ) -> List[Dict[str, Any]]:
        try:
            connection.search(
                base,
                search_filter,
                search_scope=SUBTREE,
                attributes=attributes,
                size_limit=size_limit,
                time_limit=self.settings.timeout_seconds,
            )
        except LDAPInvalidFilterError as exc:
            raise PermanentExternalError(
                "the directory's search filter is invalid", provider=PROVIDER
            ) from exc
        except (LDAPException, OSError) as exc:
            raise self._fail(exc, "searching") from exc
        code = connection.result.get("result")
        if code == NO_SUCH_OBJECT:
            raise PermanentExternalError(
                f"the directory has no entry {base!r} to search", provider=PROVIDER
            )
        # A size limit exceeded still returns the entries found.
        if code not in (SUCCESS, SIZE_LIMIT_EXCEEDED):
            raise TransientExternalError(
                f"directory search failed ({connection.result.get('description')})",
                provider=PROVIDER,
            )
        return [
            item
            for item in connection.response or []
            if item.get("type") == "searchResEntry"
        ]

    def _entry(self, service: Connection, username: str) -> Dict[str, Any]:
        if len(username) > MAX_USERNAME_LENGTH:
            raise CredentialsRefused("username too long")
        settings = self.settings
        attributes = [
            a
            for a in (
                settings.username_attribute,
                settings.id_attribute,
                settings.email_attribute,
                settings.display_name_attribute,
                MEMBER_OF if settings.group_source == "member_of" else None,
            )
            if a
        ]
        entries = self._search(
            service,
            settings.base_dn,
            user_filter(settings, username),
            attributes,
            USER_SEARCH_SIZE_LIMIT,
        )
        if not entries:
            raise CredentialsRefused("no such user")
        if len(entries) > 1:
            raise CredentialsRefused("username matches more than one entry")
        entry = entries[0]
        if not entry.get("dn"):
            raise CredentialsRefused("entry has no DN")
        return entry

    def _account(
        self, service: Connection, entry: Dict[str, Any], username: str
    ) -> DirectoryAccount:
        settings = self.settings
        raw: Mapping[str, Any] = entry.get("raw_attributes") or {}
        external_id = external_id_of(
            settings.id_attribute, _attribute(raw, settings.id_attribute)
        )
        if not external_id:
            raise PermanentExternalError(
                f"the directory entry has no {settings.id_attribute}",
                provider=PROVIDER,
            )
        return DirectoryAccount(
            dn=str(entry["dn"]),
            external_id=external_id,
            username=_text(_attribute(raw, settings.username_attribute)) or username,
            email=_text(_attribute(raw, settings.email_attribute)),
            display_name=_text(_attribute(raw, settings.display_name_attribute)),
            groups=self._groups(service, str(entry["dn"]), raw),
        )

    def _groups(
        self, service: Connection, dn: str, raw: Mapping[str, Any]
    ) -> Tuple[str, ...]:
        settings = self.settings
        if settings.group_source == "member_of":
            values = _attribute(raw, MEMBER_OF)
        elif settings.group_source == "search" and settings.group_search_base:
            entries = self._search(
                service,
                settings.group_search_base,
                group_filter(settings, dn),
                [],
                MAX_GROUPS,
            )
            return tuple(sorted(str(e["dn"]) for e in entries if e.get("dn")))
        else:
            return ()
        found = {
            bytes(v).decode("utf-8", errors="replace") for v in values[:MAX_GROUPS]
        }
        return tuple(sorted(found))
