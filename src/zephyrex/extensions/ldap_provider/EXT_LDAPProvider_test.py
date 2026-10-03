# SPDX-License-Identifier: AGPL-3.0-or-later
"""The LDAP directory end to end: a real directory server started on free
loopback ports over a test app's users and teams, and real clients
(ldap3, and ldapsearch from ldap-utils) binding and searching over LDAPS
and StartTLS.

Covered: people and groups as service accounts and users see them;
plain-text binds, empty passwords, wrong passwords and unknown names
refused; writes refused and nothing changed; searches bounded by size,
time and filter complexity; released attributes configurable (and
unreleased ones useless in filters); the brute-force budget shared with
the login endpoint; malformed messages and surplus connections
disconnected; service accounts configurable by the server only, with
secrets that never come back; the listener started and stopped with the
app; and a user with a second factor unable to bind."""

import datetime
import ipaddress
import shutil
import socket
import ssl
import subprocess
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from fastapi import HTTPException
from ldap3 import (
    ANONYMOUS,
    BASE,
    EXTERNAL,
    LEVEL,
    MODIFY_REPLACE,
    NONE,
    SASL,
    SUBTREE,
    Connection,
    Server,
    Tls,
)
from ldap3.protocol.rfc4511 import LDAPMessage
from pyasn1.codec.ber import decoder

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.auth_mfa.BLL_Auth_MFA import (
    MultifactorMethodManager,
    MultifactorMethodType,
)
from zephyrex.extensions.auth_mfa.EXT_Auth_MFA import EXT_Auth_MFA
from zephyrex.extensions.ldap_provider.BLL_LDAPProvider import (
    LdapServiceAccountManager,
    LdapServiceAccountModel,
)
from zephyrex.extensions.ldap_provider.EXT_LDAPProvider import EXT_LDAPProvider
from zephyrex.extensions.ldap_provider.LDAPDirectory import (
    DEFAULT_RELEASED_ATTRIBUTES,
    LOGIN_FLOW,
    DirectoryConfig,
    LDAPDirectory,
)
from zephyrex.extensions.ldap_provider.LDAPFilter import MAX_FILTER_NODES
from zephyrex.extensions.ldap_provider.LDAPServer import (
    NOTICE_OF_DISCONNECTION_OID,
    ListenerConfig,
    LDAPServerThread,
    server_tls_context,
)
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Auth import UserManager, UserModel
from zephyrex.testing.factories import (
    TEST_PASSWORD,
    add_user_to_team,
    create_team,
    create_user,
)

BASE_DN = "dc=example,dc=org"
PEOPLE = f"ou=people,{BASE_DN}"
GROUPS = f"ou=groups,{BASE_DN}"
LOOPBACK = "127.0.0.1"
SECRET = "service-account-secret-1"
CERT_VALIDITY = datetime.timedelta(days=1)
CLIENT_TIMEOUT_SECONDS = 15
LDAPSEARCH_TIMEOUT_SECONDS = 30
READ_BYTES = 65536

SUCCESS = 0
TIME_LIMIT_EXCEEDED = 3
SIZE_LIMIT_EXCEEDED = 4
AUTH_METHOD_NOT_SUPPORTED = 7
ADMIN_LIMIT_EXCEEDED = 11
CONFIDENTIALITY_REQUIRED = 13
NO_SUCH_ATTRIBUTE = 16
NO_SUCH_OBJECT = 32
INVALID_CREDENTIALS = 49
INSUFFICIENT_ACCESS_RIGHTS = 50
UNAVAILABLE = 52
UNWILLING_TO_PERFORM = 53
PROTOCOL_ERROR = 2


# -- certificates ---------------------------------------------------------------


@dataclass(frozen=True)
class TLSFiles:
    ca: Path
    cert: Path
    key: Path


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _key_usage(*, signs_certificates: bool) -> x509.KeyUsage:
    return x509.KeyUsage(
        digital_signature=True,
        content_commitment=False,
        key_encipherment=False,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=signs_certificates,
        crl_sign=signs_certificates,
        encipher_only=False,
        decipher_only=False,
    )


@pytest.fixture(scope="module")
def tls_files(tmp_path_factory: pytest.TempPathFactory) -> TLSFiles:
    """A throwaway CA, and a certificate it issued for 127.0.0.1."""
    folder = tmp_path_factory.mktemp("ldap_tls")
    now = datetime.datetime.now(datetime.timezone.utc)
    start, end = now - datetime.timedelta(minutes=5), now + CERT_VALIDITY
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = (
        x509.CertificateBuilder()
        .subject_name(_name("ldap_provider test CA"))
        .issuer_name(_name("ldap_provider test CA"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(end)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(_key_usage(signs_certificates=True), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name("localhost"))
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(end)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address(LOOPBACK)),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(_key_usage(signs_certificates=False), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    files = TLSFiles(folder / "ca.pem", folder / "server.pem", folder / "server.key")
    files.ca.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    files.cert.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    files.key.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return files


# -- servers and clients ----------------------------------------------------------

DEFAULT_CONFIG = DirectoryConfig(
    base_dn=BASE_DN,
    released=DirectoryConfig.released_from(DEFAULT_RELEASED_ATTRIBUTES),
    size_limit=500,
    time_limit_seconds=10.0,
)

Listen = Callable[..., LDAPServerThread]


@pytest.fixture(scope="module")
def listen(tls_files: TLSFiles) -> Iterator[Listen]:
    """Start a directory server over ``registry`` with StartTLS and LDAPS
    listeners on free loopback ports; keyword arguments replace fields of
    the default configuration. Each is stopped when the module ends."""
    started: List[LDAPServerThread] = []

    def _listen(
        registry: Any, max_connections: int = 50, **config: Any
    ) -> LDAPServerThread:
        thread = LDAPServerThread(
            LDAPDirectory(registry, replace(DEFAULT_CONFIG, **config)),
            ListenerConfig(
                host=LOOPBACK,
                starttls_port=0,
                ldaps_port=0,
                tls=server_tls_context(str(tls_files.cert), str(tls_files.key)),
                max_connections=max_connections,
            ),
        )
        thread.start()
        started.append(thread)
        return thread

    yield _listen
    for thread in started:
        thread.stop()


Connect = Callable[..., Connection]


@pytest.fixture(scope="module")
def connect(tls_files: TLSFiles) -> Connect:
    """An ldap3 connection to a directory server: LDAPS, or the plain port
    with ``ldaps=False``."""

    def _connect(
        thread: LDAPServerThread,
        user: Optional[str] = None,
        password: Optional[str] = None,
        ldaps: bool = True,
        **options: Any,
    ) -> Connection:
        server = Server(
            LOOPBACK,
            port=thread.ports["ldaps" if ldaps else "starttls"],
            use_ssl=ldaps,
            tls=Tls(ca_certs_file=str(tls_files.ca), validate=ssl.CERT_REQUIRED),
            get_info=NONE,
            connect_timeout=CLIENT_TIMEOUT_SECONDS,
        )
        return Connection(
            server,
            user=user,
            password=password,
            raise_exceptions=False,
            receive_timeout=CLIENT_TIMEOUT_SECONDS,
            **options,
        )

    return _connect


def tls_socket(tls_files: TLSFiles, port: int) -> ssl.SSLSocket:
    context = ssl.create_default_context(cafile=str(tls_files.ca))
    raw = socket.create_connection((LOOPBACK, port), timeout=CLIENT_TIMEOUT_SECONDS)
    return context.wrap_socket(raw, server_hostname=LOOPBACK)


def read_messages(sock: ssl.SSLSocket) -> List[Any]:
    """Every LDAP message the server sends until it closes."""
    data = b""
    while chunk := sock.recv(READ_BYTES):
        data += chunk
    messages = []
    while data:
        message, data = decoder.decode(data, asn1Spec=LDAPMessage())
        messages.append(message)
    return messages


def entries(conn: Connection) -> Dict[str, Dict[str, List[str]]]:
    """The search's entries: DN -> attribute -> values."""
    return {
        item["dn"]: {
            name: [v.decode() for v in values]
            for name, values in item["raw_attributes"].items()
        }
        for item in conn.response
        if item["type"] == "searchResEntry"
    }


def suffix() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class Directory:
    alice: Any
    bob: Any
    carol: Any
    dave: Any
    inactive: Any
    deleted: Any
    engineering: Any
    sales: Any
    service: str
    disabled_service: str

    def person(self, user: Any) -> str:
        return f"uid={user.username or user.email},{PEOPLE}"

    def group(self, team: Any) -> str:
        return f"cn={team.id},{GROUPS}"


def set_user(registry: Any, user_id: str, **fields: Any) -> None:
    UserModel.DB(registry.DB.manager.Base).update(
        requester_id=env("ROOT_ID"),
        model_registry=registry,
        id=user_id,
        new_properties=fields,
    )


def as_root(registry: Any) -> LdapServiceAccountManager:
    return LdapServiceAccountManager(
        model_registry=registry, requester_id=env("ROOT_ID")
    )


def ldapsearch(
    tls_files: TLSFiles,
    tmp_path: Path,
    uri: str,
    bind_dn: str,
    password: str,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    """ldapsearch (ldap-utils) with the password in a file, trusting the
    test CA."""
    password_file = tmp_path / f"password-{suffix()}"
    password_file.write_text(password)
    executable = shutil.which("ldapsearch")
    assert executable, "ldapsearch (ldap-utils) is not installed"
    return subprocess.run(
        [executable, "-LLL", "-x", "-H", uri, "-D", bind_dn, "-y", str(password_file)]
        + list(arguments),
        capture_output=True,
        text=True,
        timeout=LDAPSEARCH_TIMEOUT_SECONDS,
        env={
            "LDAPTLS_CACERT": str(tls_files.ca),
            "LDAPTLS_REQCERT": "demand",
            "PATH": "/usr/bin:/bin",
        },
    )


class TestLDAPDirectory(ExtensionServerMixin):
    extension_class = EXT_LDAPProvider

    @pytest.fixture(scope="module")
    def registry(self, server: Any) -> Any:
        return server.app.state.model_registry

    @pytest.fixture(scope="module")
    def directory(self, server: Any, registry: Any) -> Directory:
        """Engineering (alice, bob) and Sales (carol); dave alone; one
        inactive and one deleted user; an enabled and a disabled service
        account. Bob has no username, so his uid is his email."""
        tag = suffix()

        def person(name: str, last: str) -> Any:
            return create_user(
                server,
                email=f"{name}.{tag}@example.org",
                first_name=name.title(),
                last_name=last,
            )

        alice = person("alice", "Liddell")
        bob = person("bob", "Builder")
        set_user(registry, bob.id, username=None)
        bob = replace_username(bob)
        carol = person("carol", "Danvers")
        dave = person("dave", "Bowman")
        inactive = person("erin", "Inactive")
        set_user(registry, inactive.id, active=False)
        deleted = person("frank", "Deleted")
        UserModel.DB(registry.DB.manager.Base).delete(
            requester_id=env("ROOT_ID"), model_registry=registry, id=deleted.id
        )
        engineering = create_team(server, alice.id, name=f"Engineering {tag}")
        add_user_to_team(server, bob.id, engineering.id, env("USER_ROLE_ID"))
        sales = create_team(server, carol.id, name=f"Sales {tag}")
        accounts = as_root(registry)
        accounts.create(name=f"app-{tag}", secret=SECRET, description="a legacy app")
        accounts.create(name=f"off-{tag}", secret=SECRET, enabled=False)
        return Directory(
            alice=alice,
            bob=bob,
            carol=carol,
            dave=dave,
            inactive=inactive,
            deleted=deleted,
            engineering=engineering,
            sales=sales,
            service=f"cn=app-{tag},ou=services,{BASE_DN}",
            disabled_service=f"cn=off-{tag},ou=services,{BASE_DN}",
        )

    @pytest.fixture(scope="module")
    def ldap(
        self, registry: Any, directory: Directory, listen: Listen
    ) -> LDAPServerThread:
        return listen(registry)

    def bound(
        self, connect: Connect, ldap: LDAPServerThread, user: str, password: str
    ) -> Connection:
        conn = connect(ldap, user, password)
        assert conn.bind(), conn.result
        return conn

    # -- reading ---------------------------------------------------------------

    def test_a_service_account_reads_people_over_ldaps(
        self, connect: Connect, ldap: LDAPServerThread, directory: Directory
    ) -> None:
        conn = self.bound(connect, ldap, directory.service, SECRET)
        alice = directory.alice
        assert conn.search(PEOPLE, f"(uid={alice.username})", SUBTREE, attributes=["*"])
        found = entries(conn)
        assert found == {
            directory.person(alice): {
                "objectClass": [
                    "top",
                    "person",
                    "organizationalPerson",
                    "inetOrgPerson",
                ],
                "uid": [alice.username],
                "cn": [alice.display_name],
                "sn": ["Liddell"],
                "entryUUID": [alice.id],
                "memberOf": [directory.group(directory.engineering)],
                "givenName": ["Alice"],
                "displayName": [alice.display_name],
                "mail": [alice.email],
            }
        }
        assert conn.search(
            PEOPLE, f"(mail={directory.bob.email})", LEVEL, attributes=["uid"]
        )
        assert entries(conn) == {
            f"uid={directory.bob.email},{PEOPLE}": {"uid": [directory.bob.email]}
        }
        conn.unbind()

    def test_groups_carry_their_members(
        self, connect: Connect, ldap: LDAPServerThread, directory: Directory
    ) -> None:
        conn = self.bound(connect, ldap, directory.service, SECRET)
        name = directory.engineering.name
        assert conn.search(
            GROUPS,
            f"(&(objectClass=groupOfNames)(cn={name}))",
            SUBTREE,
            attributes=["cn", "member"],
        )
        found = entries(conn)
        assert list(found) == [directory.group(directory.engineering)]
        group = found[directory.group(directory.engineering)]
        assert group["cn"] == [directory.engineering.id, name]
        assert sorted(group["member"]) == sorted(
            [directory.person(directory.alice), directory.person(directory.bob)]
        )
        conn.unbind()

    def test_the_tree_and_its_scopes(
        self, connect: Connect, ldap: LDAPServerThread, directory: Directory
    ) -> None:
        conn = self.bound(connect, ldap, directory.service, SECRET)
        assert conn.search(BASE_DN, "(objectClass=*)", BASE)
        assert list(entries(conn)) == [BASE_DN]
        assert conn.search(BASE_DN, "(objectClass=*)", LEVEL)
        assert list(entries(conn)) == [PEOPLE, GROUPS]
        assert conn.search(
            BASE_DN, f"(|(uid={directory.alice.username})(ou=*))", SUBTREE
        )
        assert set(entries(conn)) == {PEOPLE, GROUPS, directory.person(directory.alice)}
        for hidden in (directory.inactive, directory.deleted):
            conn.search(BASE_DN, f"(mail={hidden.email})", SUBTREE)
            assert conn.result["result"] == SUCCESS
            assert entries(conn) == {}
        assert not conn.search(f"ou=nowhere,{BASE_DN}", "(objectClass=*)", BASE)
        assert conn.result["result"] == NO_SUCH_OBJECT
        assert conn.result["dn"] == BASE_DN
        assert not conn.search("dc=elsewhere", "(objectClass=*)", BASE)
        assert conn.result["result"] == NO_SUCH_OBJECT
        conn.unbind()

    def test_starttls_then_bind_and_search(
        self, connect: Connect, ldap: LDAPServerThread, directory: Directory
    ) -> None:
        conn = connect(ldap, directory.service, SECRET, ldaps=False)
        conn.open()
        assert conn.start_tls(), conn.result
        assert conn.bind(), conn.result
        assert conn.search(
            PEOPLE, f"(uid={directory.carol.username})", attributes=["mail"]
        )
        assert entries(conn) == {
            directory.person(directory.carol): {"mail": [directory.carol.email]}
        }
        assert conn.extend.standard.who_am_i() == f"dn:{directory.service}"
        conn.unbind()

    def test_ldapsearch_reads_the_directory_over_ldaps_and_starttls(
        self,
        ldap: LDAPServerThread,
        directory: Directory,
        tls_files: TLSFiles,
        tmp_path: Path,
    ) -> None:
        uid = directory.alice.username
        for uri, extra in (
            (f"ldaps://{LOOPBACK}:{ldap.ports['ldaps']}", ()),
            (f"ldap://{LOOPBACK}:{ldap.ports['starttls']}", ("-ZZ",)),
        ):
            result = ldapsearch(
                tls_files,
                tmp_path,
                uri,
                directory.service,
                SECRET,
                *extra,
                "-b",
                PEOPLE,
                f"(uid={uid})",
                "mail",
            )
            assert result.returncode == SUCCESS, result.stderr
            assert result.stdout.split("\n")[:2] == [
                f"dn: {directory.person(directory.alice)}",
                f"mail: {directory.alice.email}",
            ]

    def test_a_user_sees_their_teams_and_teammates_only(
        self, connect: Connect, ldap: LDAPServerThread, directory: Directory
    ) -> None:
        alice_dn = directory.person(directory.alice)
        conn = self.bound(connect, ldap, alice_dn, TEST_PASSWORD)
        assert conn.extend.standard.who_am_i() == f"dn:{alice_dn}"
        assert conn.search(PEOPLE, "(objectClass=inetOrgPerson)", attributes=["uid"])
        assert set(entries(conn)) == {alice_dn, directory.person(directory.bob)}
        assert conn.search(GROUPS, "(objectClass=*)", LEVEL, attributes=["cn"])
        assert list(entries(conn)) == [directory.group(directory.engineering)]
        assert not conn.search(
            directory.person(directory.carol), "(objectClass=*)", BASE
        )
        assert conn.result["result"] == NO_SUCH_OBJECT
        assert not conn.search(
            directory.group(directory.sales), "(objectClass=*)", BASE
        )
        assert conn.result["result"] == NO_SUCH_OBJECT
        conn.unbind()

    def test_compare(
        self, connect: Connect, ldap: LDAPServerThread, directory: Directory
    ) -> None:
        conn = self.bound(connect, ldap, directory.service, SECRET)
        dn = directory.person(directory.alice)
        assert conn.compare(dn, "mail", directory.alice.email.upper()) is True
        assert conn.compare(dn, "mail", "someone@else.org") is False
        assert conn.compare(dn, "userPassword", TEST_PASSWORD) is False
        assert conn.result["result"] == NO_SUCH_ATTRIBUTE
        conn.unbind()

    def test_anonymous_reads_only_the_root_dse(
        self, connect: Connect, ldap: LDAPServerThread
    ) -> None:
        conn = connect(ldap)
        conn.open()
        assert conn.search("", "(objectClass=*)", BASE, attributes=["+"])
        root = entries(conn)[""]
        assert root["namingContexts"] == [BASE_DN]
        assert root["supportedLDAPVersion"] == ["3"]
        assert not conn.search(PEOPLE, "(objectClass=*)")
        assert conn.result["result"] == INSUFFICIENT_ACCESS_RIGHTS
        assert not conn.compare(BASE_DN, "dc", "example")
        assert conn.result["result"] == INSUFFICIENT_ACCESS_RIGHTS
        assert conn.extend.standard.who_am_i() in ("", None)
        conn.unbind()

    # -- refusals: binds ----------------------------------------------------------

    def test_a_plain_text_bind_is_refused(
        self, connect: Connect, ldap: LDAPServerThread, directory: Directory
    ) -> None:
        conn = connect(ldap, directory.service, SECRET, ldaps=False)
        assert not conn.bind()
        assert conn.result["result"] == CONFIDENTIALITY_REQUIRED
        assert not conn.search(PEOPLE, "(objectClass=*)")
        assert conn.result["result"] == CONFIDENTIALITY_REQUIRED
        assert conn.extend.standard.who_am_i() is None
        assert conn.result["result"] == CONFIDENTIALITY_REQUIRED
        conn.unbind()

    def test_wrong_passwords_and_unknown_names_are_refused(
        self, connect: Connect, ldap: LDAPServerThread, directory: Directory
    ) -> None:
        try:
            for user, password in (
                (directory.person(directory.dave), "not-daves-password"),
                (directory.service, "not-the-service-secret"),
                (f"uid=nobody-{suffix()},{PEOPLE}", TEST_PASSWORD),
                (f"cn=nobody-{suffix()},ou=services,{BASE_DN}", SECRET),
                (f"uid={directory.dave.username},ou=groups,{BASE_DN}", TEST_PASSWORD),
                (f"uid={directory.dave.username},{PEOPLE[:-3]}com", TEST_PASSWORD),
            ):
                conn = connect(ldap, user, password)
                assert not conn.bind(), user
                assert conn.result["result"] == INVALID_CREDENTIALS, user
                conn.unbind()
        finally:
            UserManager._lockout_tracker.clear(LOOPBACK, LOGIN_FLOW)
        conn = self.bound(
            connect, ldap, directory.person(directory.dave), TEST_PASSWORD
        )
        conn.unbind()

    def test_inactive_deleted_and_disabled_accounts_cannot_bind(
        self, connect: Connect, ldap: LDAPServerThread, directory: Directory
    ) -> None:
        try:
            for user, password in (
                (directory.person(directory.inactive), TEST_PASSWORD),
                (directory.person(directory.deleted), TEST_PASSWORD),
                (directory.disabled_service, SECRET),
            ):
                conn = connect(ldap, user, password)
                assert not conn.bind(), user
                assert conn.result["result"] == INVALID_CREDENTIALS, user
                conn.unbind()
        finally:
            UserManager._lockout_tracker.clear(LOOPBACK, LOGIN_FLOW)

    def test_empty_password_binds_are_refused(
        self,
        connect: Connect,
        ldap: LDAPServerThread,
        directory: Directory,
        tls_files: TLSFiles,
        tmp_path: Path,
    ) -> None:
        anonymous = connect(ldap, authentication=ANONYMOUS)
        assert not anonymous.bind()
        assert anonymous.result["result"] == UNWILLING_TO_PERFORM
        anonymous.unbind()
        # ldap3 will not send an empty password with a name; ldapsearch does
        # (an unauthenticated bind, RFC 4513 5.1.2).
        uri = f"ldaps://{LOOPBACK}:{ldap.ports['ldaps']}"
        for name in (directory.person(directory.alice), directory.service):
            result = ldapsearch(
                tls_files, tmp_path, uri, name, "", "-b", PEOPLE, "(objectClass=*)"
            )
            assert result.returncode == UNWILLING_TO_PERFORM, result.stderr
            assert "dn:" not in result.stdout

    def test_only_simple_binds_are_served(
        self, connect: Connect, ldap: LDAPServerThread
    ) -> None:
        conn = connect(ldap, authentication=SASL, sasl_mechanism=EXTERNAL)
        assert not conn.bind()
        assert conn.result["result"] == AUTH_METHOD_NOT_SUPPORTED
        conn.unbind()

    def test_failed_binds_spend_the_login_brute_force_budget(
        self, connect: Connect, ldap: LDAPServerThread, directory: Directory
    ) -> None:
        tracker = UserManager._lockout_tracker
        try:
            for _ in range(tracker.policy.failures_per_window):
                conn = connect(ldap, f"uid=nobody-{suffix()},{PEOPLE}", "guess-guess")
                assert not conn.bind()
                conn.unbind()
            conn = connect(ldap, directory.service, SECRET)
            assert not conn.bind()
            assert conn.result["result"] == UNWILLING_TO_PERFORM
            conn.unbind()
            assert tracker.is_locked(LOOPBACK, LOGIN_FLOW)
        finally:
            tracker.clear(LOOPBACK, LOGIN_FLOW)
        conn = self.bound(connect, ldap, directory.service, SECRET)
        conn.unbind()

    # -- refusals: writes -----------------------------------------------------------

    def test_writes_are_refused_and_nothing_changes(
        self,
        connect: Connect,
        ldap: LDAPServerThread,
        directory: Directory,
        registry: Any,
    ) -> None:
        alice_dn = directory.person(directory.alice)
        for user, password in ((directory.service, SECRET), (alice_dn, TEST_PASSWORD)):
            conn = self.bound(connect, ldap, user, password)
            assert not conn.add(
                f"uid=mallory,{PEOPLE}", ["inetOrgPerson"], {"sn": "M", "cn": "M"}
            )
            assert conn.result["result"] == UNWILLING_TO_PERFORM
            assert not conn.modify(alice_dn, {"sn": [(MODIFY_REPLACE, ["Changed"])]})
            assert conn.result["result"] == UNWILLING_TO_PERFORM
            assert not conn.delete(alice_dn)
            assert conn.result["result"] == UNWILLING_TO_PERFORM
            assert not conn.modify_dn(alice_dn, "uid=renamed")
            assert conn.result["result"] == UNWILLING_TO_PERFORM
            assert not conn.extend.standard.modify_password(
                alice_dn, TEST_PASSWORD, "a-new-password-1"
            )
            assert conn.result["result"] == UNWILLING_TO_PERFORM
            conn.unbind()
        alice = UserModel.DB(registry.DB.manager.Base).get(
            requester_id=env("ROOT_ID"), model_registry=registry, id=directory.alice.id
        )
        assert alice["last_name"] == "Liddell"
        assert alice["deleted_at"] is None
        assert alice["username"] == directory.alice.username
        conn = self.bound(connect, ldap, alice_dn, TEST_PASSWORD)
        conn.unbind()

    # -- bounds ------------------------------------------------------------------

    def test_searches_are_bounded_by_size(
        self,
        connect: Connect,
        ldap: LDAPServerThread,
        listen: Listen,
        registry: Any,
        directory: Directory,
    ) -> None:
        small = listen(registry, size_limit=2)
        conn = self.bound(connect, small, directory.service, SECRET)
        conn.search(PEOPLE, "(objectClass=inetOrgPerson)", LEVEL)
        assert conn.result["result"] == SIZE_LIMIT_EXCEEDED
        assert len(entries(conn)) == 2
        conn.unbind()
        conn = self.bound(connect, ldap, directory.service, SECRET)
        conn.search(PEOPLE, "(objectClass=inetOrgPerson)", LEVEL, size_limit=1)
        assert conn.result["result"] == SIZE_LIMIT_EXCEEDED
        assert len(entries(conn)) == 1
        conn.unbind()

    def test_searches_are_bounded_by_time(
        self, connect: Connect, listen: Listen, registry: Any, directory: Directory
    ) -> None:
        hurried = listen(registry, time_limit_seconds=1e-9)
        conn = self.bound(connect, hurried, directory.service, SECRET)
        assert not conn.search(BASE_DN, "(objectClass=*)", SUBTREE)
        assert conn.result["result"] == TIME_LIMIT_EXCEEDED
        conn.unbind()

    def test_an_oversized_filter_is_refused(
        self, connect: Connect, ldap: LDAPServerThread, directory: Directory
    ) -> None:
        conn = self.bound(connect, ldap, directory.service, SECRET)
        wide = "(|" + "".join(f"(uid=u{i})" for i in range(MAX_FILTER_NODES)) + ")"
        assert not conn.search(PEOPLE, wide)
        assert conn.result["result"] == ADMIN_LIMIT_EXCEEDED
        assert entries(conn) == {}
        conn.unbind()

    def test_released_attributes_are_configurable(
        self, connect: Connect, listen: Listen, registry: Any, directory: Directory
    ) -> None:
        narrow = listen(registry, released=DirectoryConfig.released_from(["sn"]))
        conn = self.bound(connect, narrow, directory.service, SECRET)
        alice = directory.alice
        assert conn.search(PEOPLE, f"(uid={alice.username})", attributes=["*"])
        assert entries(conn)[directory.person(alice)] == {
            "objectClass": ["top", "person", "organizationalPerson", "inetOrgPerson"],
            "uid": [alice.username],
            "cn": [alice.display_name],
            "sn": ["Liddell"],
        }
        # An attribute that is not released cannot be probed with a filter.
        for probe in (f"(mail={alice.email})", f"(!(mail={alice.email}))"):
            conn.search(PEOPLE, probe)
            assert conn.result["result"] == SUCCESS
            assert entries(conn) == {}
        assert not conn.compare(directory.person(alice), "mail", alice.email)
        assert conn.result["result"] == NO_SUCH_ATTRIBUTE
        conn.unbind()

    def test_a_malformed_message_gets_a_notice_of_disconnection(
        self, ldap: LDAPServerThread, tls_files: TLSFiles
    ) -> None:
        with tls_socket(tls_files, ldap.ports["ldaps"]) as sock:
            sock.sendall(b"\x30\x05\x02\x01\x01\x04\x00")
            [notice] = read_messages(sock)
        assert int(notice["messageID"]) == 0
        response = notice["protocolOp"].getComponent()
        assert str(response["responseName"]) == NOTICE_OF_DISCONNECTION_OID
        assert int(response["resultCode"]) == PROTOCOL_ERROR

    def test_connections_past_the_maximum_are_turned_away(
        self,
        connect: Connect,
        listen: Listen,
        registry: Any,
        directory: Directory,
        tls_files: TLSFiles,
    ) -> None:
        single = listen(registry, max_connections=1)
        first = self.bound(connect, single, directory.service, SECRET)
        with tls_socket(tls_files, single.ports["ldaps"]) as sock:
            [notice] = read_messages(sock)
        assert int(notice["protocolOp"].getComponent()["resultCode"]) == UNAVAILABLE
        assert first.search(PEOPLE, f"(uid={directory.alice.username})")
        first.unbind()

    # -- service accounts --------------------------------------------------------

    def test_service_accounts_are_configured_by_the_server_only(
        self, registry: Any, server: Any, directory: Directory
    ) -> None:
        as_user = LdapServiceAccountManager(
            model_registry=registry, requester_id=directory.alice.id
        )
        with pytest.raises(HTTPException) as refused:
            as_user.create(name=f"mine-{suffix()}", secret=SECRET)
        assert refused.value.status_code == 403
        accounts = as_root(registry)
        account = accounts.list(name=directory.service.split(",")[0][3:])[0]
        with pytest.raises(HTTPException) as refused:
            as_user.update(account.id, enabled=False)
        assert refused.value.status_code == 403
        with pytest.raises(HTTPException) as refused:
            as_user.delete(account.id)
        assert refused.value.status_code == 403
        response = server.post(
            "/v1/ldap/service-account",
            json={
                "ldap_service_account": {"name": f"web-{suffix()}", "secret": SECRET}
            },
            headers={"Authorization": f"Bearer {directory.alice.jwt}"},
        )
        assert response.status_code in (401, 403), response.text

    def test_secrets_are_checked_and_never_returned(
        self, registry: Any, directory: Directory
    ) -> None:
        accounts = as_root(registry)
        for bad in (None, "short", "x" * 73):
            with pytest.raises(HTTPException) as refused:
                accounts.create(name=f"bad-{suffix()}", secret=bad)
            assert refused.value.status_code == 422
        for bad_name in ("", "has space", "comma,name", "x" * 65):
            with pytest.raises(HTTPException) as refused:
                accounts.create(name=bad_name, secret=SECRET)
            assert refused.value.status_code == 422
        name = directory.service.split(",")[0][3:]
        with pytest.raises(HTTPException) as refused:
            accounts.create(name=name, secret=SECRET)
        assert refused.value.status_code == 409
        created = accounts.create(
            name=f"plain-{suffix()}", secret=SECRET, secret_hash="caller-chosen"
        )
        assert "secret_hash" not in created.model_dump()
        assert "secret" not in created.model_dump(mode="json")
        stored = LdapServiceAccountModel.DB(registry.DB.manager.Base).get(
            requester_id=env("ROOT_ID"), model_registry=registry, id=created.id
        )
        assert stored["secret_hash"].startswith("$2")
        assert stored["secret_hash"] != "caller-chosen"
        fetched = accounts.get(id=created.id)
        assert "secret_hash" not in fetched.model_dump(mode="json")
        batch = accounts.create(
            entities=[
                {"name": f"b1-{suffix()}", "secret": SECRET},
                {"name": f"b2-{suffix()}", "secret": SECRET},
            ]
        )
        for account in batch:
            row = LdapServiceAccountModel.DB(registry.DB.manager.Base).get(
                requester_id=env("ROOT_ID"), model_registry=registry, id=account.id
            )
            assert row["secret_hash"].startswith("$2")

    def test_a_new_secret_replaces_the_old(
        self, connect: Connect, ldap: LDAPServerThread, registry: Any
    ) -> None:
        accounts = as_root(registry)
        name = f"rotate-{suffix()}"
        account = accounts.create(name=name, secret=SECRET)
        dn = f"cn={name},ou=services,{BASE_DN}"
        self.bound(connect, ldap, dn, SECRET).unbind()
        rotated = "a-rotated-secret-2"
        accounts.update(account.id, secret=rotated, secret_hash="caller-chosen")
        try:
            old = connect(ldap, dn, SECRET)
            assert not old.bind()
            assert old.result["result"] == INVALID_CREDENTIALS
            old.unbind()
        finally:
            UserManager._lockout_tracker.clear(LOOPBACK, LOGIN_FLOW)
        self.bound(connect, ldap, dn, rotated).unbind()

    # -- lifecycle ---------------------------------------------------------------

    def test_the_listener_starts_and_stops_with_the_app(
        self,
        set_env: Callable[[str, str], None],
        tls_files: TLSFiles,
        connect: Connect,
        directory: Directory,
    ) -> None:
        set_env("LDAP_PROVIDER_BASE_DN", "")
        assert any("BASE_DN" in issue for issue in EXT_LDAPProvider.validate_config())
        EXT_LDAPProvider.on_start()
        assert EXT_LDAPProvider._listener is None
        for name, value in (
            ("LDAP_PROVIDER_BASE_DN", BASE_DN),
            ("LDAP_PROVIDER_LISTEN_HOST", LOOPBACK),
            ("LDAP_PROVIDER_LISTEN_PORT", "0"),
            ("LDAP_PROVIDER_LDAPS_PORT", "0"),
            ("LDAP_PROVIDER_TLS_CERT_PATH", str(tls_files.cert)),
            ("LDAP_PROVIDER_TLS_KEY_PATH", str(tls_files.key)),
        ):
            set_env(name, value)
        assert EXT_LDAPProvider.validate_config() == []
        EXT_LDAPProvider.on_start()
        listener = EXT_LDAPProvider._listener
        assert listener is not None
        try:
            self.bound(connect, listener, directory.service, SECRET).unbind()
        finally:
            EXT_LDAPProvider.on_stop()
        assert EXT_LDAPProvider._listener is None
        with pytest.raises(OSError):
            socket.create_connection(
                (LOOPBACK, listener.ports["ldaps"]), timeout=CLIENT_TIMEOUT_SECONDS
            ).close()


class TestSecondFactor(ExtensionServerMixin):
    """A simple bind carries only a password, so a user who has enrolled a
    second factor cannot bind: the directory must not be a way around
    MFA."""

    extension_class = EXT_Auth_MFA

    def test_a_user_with_a_second_factor_cannot_bind(
        self, server: Any, listen: Listen, connect: Connect
    ) -> None:
        pyotp = pytest.importorskip("pyotp")
        registry = server.app.state.model_registry
        user = create_user(server, email=f"mfa.{suffix()}@example.org")
        listener = listen(registry)
        dn = f"uid={user.username},{PEOPLE}"
        before = connect(listener, dn, TEST_PASSWORD)
        assert before.bind(), before.result
        before.unbind()
        methods = MultifactorMethodManager(
            requester_id=user.id, model_registry=registry
        )
        method = methods.create(method_type=MultifactorMethodType.TOTP)
        secret = methods.totp_provisioning_route(method.id)["secret"]
        assert methods.verify_mfa_code(method.id, pyotp.TOTP(secret).now())
        after = connect(listener, dn, TEST_PASSWORD)
        assert not after.bind()
        assert after.result["result"] == UNWILLING_TO_PERFORM
        after.unbind()


def replace_username(user: Any) -> Any:
    """``user`` as it reads after its username was cleared."""
    return user.model_copy(update={"username": None})
