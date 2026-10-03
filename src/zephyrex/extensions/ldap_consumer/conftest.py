# SPDX-License-Identifier: AGPL-3.0-or-later
"""A real OpenLDAP directory for this extension's tests.

``slapd`` (installed, never run as a service) is started under a temporary
directory on free high ports with a generated configuration: an MDB
database under ``dc=example,dc=test``, the memberOf overlay, a TLS
certificate signed by a throwaway CA, and the people, groups and service
account the tests sign in with. It listens for LDAPS and for LDAP (with
StartTLS) on ``localhost``, and is stopped when the session ends.

The certificate names only ``localhost``, so the same directory reached as
``127.0.0.1`` presents a certificate for the wrong name. It allows
unauthenticated binds (a DN with an empty password), as Active Directory
does, so a client that sent one would be let in."""

import datetime
import os
import shutil
import signal
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Tuple

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

SLAPD = Path("/usr/sbin/slapd")
SCHEMA_DIR = Path("/etc/ldap/schema")
MODULE_DIR = Path("/usr/lib/ldap")
SCHEMAS = ("core", "cosine", "inetorgperson", "nis")
BASE_DN = "dc=example,dc=test"
ROOT_DN = f"cn=admin,{BASE_DN}"
ROOT_PASSWORD = "root-secret-1"
SERVICE_DN = f"cn=svc,ou=services,{BASE_DN}"
SERVICE_PASSWORD = "service-secret-1"
PEOPLE = f"ou=people,{BASE_DN}"
GROUPS = f"ou=groups,{BASE_DN}"
START_TIMEOUT_SECONDS = 20.0
STOP_TIMEOUT_SECONDS = 10.0
PORT_PROBE_INTERVAL_SECONDS = 0.1
CERT_DAYS = 2


@dataclass(frozen=True)
class Person:
    uid: str
    password: str
    mail: str | None
    display_name: str
    groups: Tuple[str, ...] = ()

    @property
    def dn(self) -> str:
        return f"uid={_dn_value(self.uid)},{PEOPLE}"


PEOPLE_SEEDED: Tuple[Person, ...] = (
    Person("alice", "alice-pass-1", "alice@example.com", "Alice A", ("engineers",)),
    Person("alicia", "alicia-pass-1", "alicia@example.com", "Alicia B"),
    Person("bob", "bob-pass-1", "bob@example.com", "Bob C", ("engineers", "ops")),
    Person("carol", "carol-pass-1", None, "Carol, no mail"),
    Person("rob(admin)", "rob-pass-1", "rob@example.com", "Rob Parens"),
    Person("dora", "dora-pass-1", "dora@example.com", "Dora D"),
    Person("erin", "erin-pass-1", "erin@example.com", "Erin E"),
    Person("frank", "frank-pass-1", "frank@example.com", "Frank F"),
    Person("gina", "gina-pass-1", "gina@example.com", "Gina G"),
    Person("hank", "hank-pass-1", "hank@example.com", "Hank H"),
    Person("ivy", "ivy-pass-1", "ivy@example.com", "Ivy I"),
    Person("judy", "judy-pass-1", "judy@example.com", "Judy J"),
)
GROUP_NAMES = ("engineers", "ops")


def _dn_value(value: str) -> str:
    """``value`` as an RDN value (RFC 4514): parentheses need no escape."""
    from ldap3.utils.dn import escape_rdn

    escaped: str = escape_rdn(value)
    return escaped


@dataclass
class Directory:
    """The running test directory."""

    root: Path
    ldap_port: int
    ldaps_port: int
    ca_pem: str
    other_ca_pem: str
    people: Dict[str, Person] = field(default_factory=dict)

    def group_dn(self, name: str) -> str:
        return f"cn={name},{GROUPS}"

    def settings(self, **overrides: object) -> Dict[str, object]:
        """The fields of a directory record that reaches this directory over
        LDAPS, trusting its CA; ``overrides`` replace any of them."""
        base: Dict[str, object] = {
            "name": "Test directory",
            "host": "localhost",
            "port": self.ldaps_port,
            "security": "ldaps",
            "ca_certificate": self.ca_pem,
            "bind_dn": SERVICE_DN,
            "bind_password": SERVICE_PASSWORD,
            "base_dn": PEOPLE,
            "user_object_filter": "(objectClass=inetOrgPerson)",
            "username_attribute": "uid",
            "id_attribute": "entryUUID",
            "email_attribute": "mail",
            "display_name_attribute": "displayName",
            "group_source": "member_of",
            "group_search_base": GROUPS,
            "timeout_seconds": 5,
        }
        base.update(overrides)
        return base


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _pem(certificate: x509.Certificate) -> str:
    return certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")


def _certificate_authority(
    common_name: str,
) -> Tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(_name(common_name))
        .issuer_name(_name(common_name))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=CERT_DAYS))
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
        .sign(key, hashes.SHA256())
    )
    return key, certificate


def _write_tls(root: Path) -> Tuple[str, str]:
    """A CA, a server certificate it signs for ``localhost`` only, and an
    unrelated CA: their PEMs (the CA's, the other's)."""
    ca_key, ca = _certificate_authority("Test LDAP CA")
    _, other_ca = _certificate_authority("Unrelated CA")
    server_key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.timezone.utc)
    server = (
        x509.CertificateBuilder()
        .subject_name(_name("localhost"))
        .issuer_name(ca.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=CERT_DAYS))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), False
        )
        .sign(ca_key, hashes.SHA256())
    )
    (root / "ca.pem").write_text(_pem(ca))
    (root / "server.pem").write_text(_pem(server))
    key_path = root / "server.key"
    key_path.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    return _pem(ca), _pem(other_ca)


def _config(root: Path) -> str:
    schemas = "\n".join(f"include {SCHEMA_DIR / name}.schema" for name in SCHEMAS)
    return f"""{schemas}
modulepath {MODULE_DIR}
moduleload back_mdb
moduleload memberof
pidfile {root / "slapd.pid"}
argsfile {root / "slapd.args"}
allow bind_anon_dn
TLSCACertificateFile {root / "ca.pem"}
TLSCertificateFile {root / "server.pem"}
TLSCertificateKeyFile {root / "server.key"}

database mdb
maxsize 33554432
suffix "{BASE_DN}"
rootdn "{ROOT_DN}"
rootpw {ROOT_PASSWORD}
directory {root / "data"}
index objectClass eq
index uid eq
index member eq
overlay memberof
memberof-group-oc groupOfNames
memberof-member-ad member
memberof-memberof-ad memberOf
memberof-refint TRUE

access to attrs=userPassword
    by self read
    by anonymous auth
    by * none
access to *
    by dn.exact="{SERVICE_DN}" read
    by self read
    by * none
"""


def _wait_for_port(port: int, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"slapd exited with status {process.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(PORT_PROBE_INTERVAL_SECONDS)
    raise RuntimeError(f"slapd did not listen on {port}")


def _seed(directory: Directory) -> None:
    from ldap3 import Connection, Server

    connection = Connection(
        Server("127.0.0.1", port=directory.ldap_port, get_info="NO_INFO"),
        user=ROOT_DN,
        password=ROOT_PASSWORD,
        auto_bind=True,
        receive_timeout=10,
    )
    try:

        def add(dn: str, classes: List[str], attributes: Dict[str, object]) -> None:
            if not connection.add(dn, classes, attributes):
                raise RuntimeError(f"seeding {dn}: {connection.result}")

        add(BASE_DN, ["dcObject", "organization"], {"dc": "example", "o": "Ex"})
        for unit in ("people", "groups", "services"):
            add(f"ou={unit},{BASE_DN}", ["organizationalUnit"], {"ou": unit})
        add(
            SERVICE_DN,
            ["organizationalRole", "simpleSecurityObject"],
            {"cn": "svc", "userPassword": SERVICE_PASSWORD},
        )
        for person in PEOPLE_SEEDED:
            attributes: Dict[str, object] = {
                "uid": person.uid,
                "cn": person.display_name,
                "sn": person.uid,
                "displayName": person.display_name,
                "userPassword": person.password,
            }
            if person.mail:
                attributes["mail"] = person.mail
            add(person.dn, ["inetOrgPerson"], attributes)
            directory.people[person.uid] = person
        for group in GROUP_NAMES:
            members = [p.dn for p in PEOPLE_SEEDED if group in p.groups]
            add(
                directory.group_dn(group),
                ["groupOfNames"],
                {"cn": group, "member": members},
            )
    finally:
        connection.unbind()


@pytest.fixture(scope="session")
def directory(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Directory]:
    if not SLAPD.exists():
        pytest.skip(f"OpenLDAP's slapd is not installed at {SLAPD}")
    root = tmp_path_factory.mktemp("slapd")
    (root / "data").mkdir()
    ca_pem, other_ca_pem = _write_tls(root)
    (root / "slapd.conf").write_text(_config(root))
    directory = Directory(
        root=root,
        ldap_port=_free_port(),
        ldaps_port=_free_port(),
        ca_pem=ca_pem,
        other_ca_pem=other_ca_pem,
    )
    urls = f"ldap://localhost:{directory.ldap_port}/ ldaps://localhost:{directory.ldaps_port}/"
    log = (root / "slapd.log").open("wb")
    # -d keeps slapd in the foreground, so this process owns it.
    process = subprocess.Popen(
        [str(SLAPD), "-f", str(root / "slapd.conf"), "-h", urls, "-d", "0"],
        stdout=log,
        stderr=subprocess.STDOUT,
        env={**os.environ, "LDAPNOINIT": "1"},
    )
    try:
        _wait_for_port(directory.ldap_port, process)
        _wait_for_port(directory.ldaps_port, process)
        _seed(directory)
        yield directory
    finally:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=STOP_TIMEOUT_SECONDS)
        log.close()
        shutil.rmtree(root, ignore_errors=True)


def unauthenticated_bind(
    directory: Directory, dn: str
) -> subprocess.CompletedProcess[str]:
    """OpenLDAP's own client binding as ``dn`` with an empty password over
    plain LDAP: what the directory itself answers such a bind."""
    return subprocess.run(
        [
            "ldapwhoami",
            "-x",
            "-H",
            f"ldap://127.0.0.1:{directory.ldap_port}",
            "-D",
            dn,
            "-w",
            "",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "LDAPNOINIT": "1"},
    )
