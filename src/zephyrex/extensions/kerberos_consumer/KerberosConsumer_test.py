# SPDX-License-Identifier: AGPL-3.0-or-later
"""Kerberos sign-in end to end, against a real MIT KDC.

The module starts a throwaway realm (``krb5kdc`` on a free high port, its
database, configuration and keytabs in a temporary directory, reached
through ``KRB5_CONFIG``/``KRB5_KDC_PROFILE``), gets real tickets for real
user principals with ``kinit``, builds real SPNEGO and Kerberos tokens with
python-gssapi, and drives the Negotiate route of a real app.
"""

import base64
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

import gssapi
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from gssapi.raw import ChannelBindings

from conftest import create_user, generate_test_email
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.kerberos_consumer.BLL_KerberosConsumer import (
    KerberosPrincipalManager,
    KerberosPrincipalModel,
)
from zephyrex.extensions.kerberos_consumer.EXT_KerberosConsumer import (
    EXT_KerberosConsumer,
)
from zephyrex.extensions.kerberos_consumer.PRV_KerberosKeytab import (
    PRV_KerberosKeytab,
    tls_server_end_point,
)
from zephyrex.lib.Environment import env
from zephyrex.testing.factories import INTERNAL_ACCOUNTS
from zephyrex.logic.BLL_Auth import UserModel
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceManager,
    ProviderInstanceModel,
    ProviderInstanceSettingManager,
    ProviderManager,
)

REALM = "MAIN.TEST"
SERVICE = f"HTTP/svc.test@{REALM}"
OTHER_SERVICE = f"HTTP/other.test@{REALM}"
USERS = ("alice", "bob", "carol", "dave", "erin", "frank", "grace", "heidi")
# The app database outlives a run; fresh principal names keep runs apart.
RUN = uuid.uuid4().hex[:8]


def principal_name(user: str) -> str:
    return f"{user}-{RUN}"


def principal(user: str) -> str:
    return f"{principal_name(user)}@{REALM}"


NEGOTIATE_PATH = "/v1/auth/kerberos/login/negotiate"
KRB5_SBIN = Path("/usr/sbin")
KRB5_BIN = Path("/usr/bin")
COMMAND_TIMEOUT_SECONDS = 60
# Tolerated clock skew in the throwaway realm, and the short-lived ticket
# the expiry test uses: the ticket is dead (skew included) after the wait.
CLOCK_SKEW_SECONDS = 2
SHORT_TICKET_SECONDS = 2
EXPIRY_WAIT_SECONDS = SHORT_TICKET_SECONDS + CLOCK_SKEW_SECONDS + 2
SPNEGO = gssapi.OID.from_int_seq("1.3.6.1.5.5.2")

pytestmark = pytest.mark.skipif(
    not (KRB5_SBIN / "krb5kdc").exists(), reason="MIT krb5kdc is not installed"
)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@dataclass
class ThrowawayRealm:
    """A running MIT KDC for ``REALM`` and what the tests need from it."""

    root: Path
    environment: Dict[str, str]

    def run(self, *command: str) -> str:
        done = subprocess.run(
            command,
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
        )
        assert done.returncode == 0, f"{command[0]} failed: {done.stderr}"
        return done.stdout

    def kadmin(self, query: str) -> None:
        self.run(str(KRB5_SBIN / "kadmin.local"), "-r", REALM, "-q", query)

    def keytab(self, file_name: str, *principals: str) -> Path:
        """A keytab of the principals' current keys (not re-randomized)."""
        path = self.root / file_name
        self.kadmin(f"ktadd -norandkey -k {path} {' '.join(principals)}")
        return path

    def kinit(self, user: str, lifetime: Optional[str] = None) -> gssapi.Credentials:
        """A real TGT for ``user`` (from their keytab), as GSSAPI credentials."""
        cache = f"FILE:{self.root}/{user}.{uuid.uuid4().hex}.ccache"
        lifetime_args = ["-l", lifetime] if lifetime else []
        self.run(
            str(KRB5_BIN / "kinit"),
            *lifetime_args,
            "-k",
            "-t",
            str(self.root / f"{user}.keytab"),
            "-c",
            cache,
            principal_name(user),
        )
        return gssapi.Credentials(usage="initiate", store={"ccache": cache})


def _write_realm_files(root: Path, port: int) -> None:
    (root / "krb5.conf").write_text(
        "[libdefaults]\n"
        f" default_realm = {REALM}\n"
        " dns_lookup_kdc = false\n"
        " dns_lookup_realm = false\n"
        " rdns = false\n"
        " dns_canonicalize_hostname = false\n"
        f" clockskew = {CLOCK_SKEW_SECONDS}\n"
        "[realms]\n"
        f" {REALM} = {{\n  kdc = 127.0.0.1:{port}\n }}\n"
    )
    (root / "kdc.conf").write_text(
        "[kdcdefaults]\n"
        f" kdc_listen = 127.0.0.1:{port}\n"
        f" kdc_tcp_listen = 127.0.0.1:{port}\n"
        "[realms]\n"
        f" {REALM} = {{\n"
        f"  database_name = {root}/principal\n"
        f"  key_stash_file = {root}/stash\n"
        f"  acl_file = {root}/kadm5.acl\n"
        "  max_life = 1h\n"
        " }\n"
        "[logging]\n"
        f" kdc = FILE:{root}/kdc.log\n"
    )
    (root / "kadm5.acl").write_text("")


@pytest.fixture(scope="module")
def realm() -> Iterator[ThrowawayRealm]:
    """Start the KDC (it daemonizes once it listens) and stop it after."""
    root = Path(tempfile.mkdtemp(prefix="zx-kdc-"))
    _write_realm_files(root, _free_port())
    patch = pytest.MonkeyPatch()
    patch.setenv("KRB5_CONFIG", str(root / "krb5.conf"))
    patch.setenv("KRB5_KDC_PROFILE", str(root / "kdc.conf"))
    kdc = ThrowawayRealm(root=root, environment=dict(os.environ))
    pid_file = root / "krb5kdc.pid"
    try:
        kdc.run(
            str(KRB5_SBIN / "kdb5_util"),
            "create",
            "-s",
            "-r",
            REALM,
            "-P",
            uuid.uuid4().hex,
        )
        kdc.run(str(KRB5_SBIN / "krb5kdc"), "-r", REALM, "-P", str(pid_file))
        users = [principal_name(user) for user in USERS]
        for name in ("HTTP/svc.test", "HTTP/other.test", *users):
            kdc.kadmin(f"addprinc -randkey {name}")
        for user in USERS:
            kdc.keytab(f"{user}.keytab", principal_name(user))
        yield kdc
    finally:
        if pid_file.exists():
            os.kill(int(pid_file.read_text().strip()), signal.SIGTERM)
        patch.undo()
        shutil.rmtree(root, ignore_errors=True)


def _keytab_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def _certificate(common_name: str) -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.PEM).decode()


@dataclass
class ClientToken:
    """A token as a browser sends it, and the client's side of the context."""

    context: gssapi.SecurityContext
    token: bytes

    @property
    def header(self) -> Dict[str, str]:
        return {"Authorization": f"Negotiate {base64.b64encode(self.token).decode()}"}


def client_token(
    credentials: gssapi.Credentials,
    service: str = SERVICE,
    mech: gssapi.OID = SPNEGO,
    bindings: Optional[ChannelBindings] = None,
) -> ClientToken:
    context = gssapi.SecurityContext(
        name=gssapi.Name(service, gssapi.NameType.kerberos_principal),
        creds=credentials,
        usage="initiate",
        mech=mech,
        flags=gssapi.RequirementFlag.mutual_authentication,
        channel_bindings=bindings,
    )
    return ClientToken(context=context, token=context.step())


class TestKerberosNegotiate(ExtensionServerMixin):
    extension_class = EXT_KerberosConsumer

    @pytest.fixture
    def keytab_instance(self, server: Any) -> Iterator[Callable[..., Any]]:
        """Create kerberos_keytab provider instances (as ROOT unless another
        creator is named); every one is removed after the test, so each test
        sees only its own."""
        registry = server.app.state.model_registry
        root_id = env("ROOT_ID")
        created: List[str] = []

        def _create(settings: Dict[str, str], creator_id: Optional[str] = None) -> Any:
            creator = creator_id or root_id
            provider = ProviderManager(
                model_registry=registry, requester_id=root_id
            ).get(name=PRV_KerberosKeytab.name)
            instance = ProviderInstanceManager(
                model_registry=registry, requester_id=creator
            ).create(
                name=f"kerberos_{uuid.uuid4().hex}",
                provider_id=provider.id,
                scope="root" if creator == root_id else "user",
            )
            created.append(str(instance.id))
            setting_manager = ProviderInstanceSettingManager(
                model_registry=registry, requester_id=creator
            )
            for key, value in settings.items():
                setting_manager.create(
                    provider_instance_id=instance.id, key=key, value=value
                )
            return instance

        yield _create
        InstanceDB = ProviderInstanceModel.DB(registry.DB.manager.Base)
        live = {
            str(instance.id)
            for instance in InstanceDB.list(
                requester_id=root_id,
                model_registry=registry,
                return_type="dto",
                override_dto=ProviderInstanceModel,
                filters=[InstanceDB.id.in_(created), InstanceDB.deleted_at.is_(None)],
            )
        }
        manager = ProviderInstanceManager(model_registry=registry, requester_id=root_id)
        for instance_id in live:
            manager.delete(id=instance_id)

    @pytest.fixture
    def svc_keytab(self, realm: ThrowawayRealm) -> Path:
        return realm.keytab(f"svc.{uuid.uuid4().hex}.keytab", "HTTP/svc.test")

    @pytest.fixture
    def service(self, keytab_instance, svc_keytab: Path) -> Any:
        """The usual configuration: HTTP/svc.test, its own realm allowed."""
        return keytab_instance(
            {"keytab": _keytab_b64(svc_keytab), "service_principal": SERVICE}
        )

    @staticmethod
    def links(server: Any, principal: str) -> List[Any]:
        """The live links of ``principal`` (ROOT's reads include deleted rows)."""
        registry = server.app.state.model_registry
        LinkDB = KerberosPrincipalModel.DB(registry.DB.manager.Base)
        links: List[Any] = LinkDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            return_type="dto",
            override_dto=KerberosPrincipalModel,
            filters=[LinkDB.principal == principal, LinkDB.deleted_at.is_(None)],
        )
        return links

    @staticmethod
    def root_links(server: Any) -> KerberosPrincipalManager:
        return KerberosPrincipalManager(
            model_registry=server.app.state.model_registry,
            requester_id=env("ROOT_ID"),
        )

    # -- the challenge ----------------------------------------------------

    def test_no_credentials_get_the_negotiate_challenge(self, server, service):
        response = server.get(NEGOTIATE_PATH)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Negotiate"

    def test_another_scheme_gets_the_negotiate_challenge(self, server, service):
        basic = base64.b64encode(b"alice:secret").decode()
        response = server.get(
            NEGOTIATE_PATH, headers={"Authorization": f"Basic {basic}"}
        )
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Negotiate"

    def test_a_token_that_is_not_base64_is_a_bad_request(self, server, service):
        response = server.get(
            NEGOTIATE_PATH, headers={"Authorization": "Negotiate ***not-base64***"}
        )
        assert response.status_code == 400

    def test_garbage_is_refused_with_the_challenge(self, server, service):
        garbage = base64.b64encode(b"\x60\x03\x06\x01\x00").decode()
        response = server.get(
            NEGOTIATE_PATH, headers={"Authorization": f"Negotiate {garbage}"}
        )
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Negotiate"

    def test_without_a_keytab_sign_in_is_unavailable(self, server, realm):
        token = client_token(realm.kinit("alice"))
        assert server.get(NEGOTIATE_PATH, headers=token.header).status_code == 503

    # -- signing in -------------------------------------------------------

    def test_first_sign_in_creates_the_user_and_issues_a_session(
        self, server, realm, service, set_env
    ):
        set_env("REGISTRATION_MODE", "open")
        token = client_token(realm.kinit("alice"))
        response = server.get(NEGOTIATE_PATH, headers=token.header)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["principal"] == principal("alice")
        assert body["token"] and body["session_key"]
        assert body["user"]["display_name"] == principal("alice")
        # Mutual authentication: the client verifies the server's answer.
        scheme, _, reply = response.headers["www-authenticate"].partition(" ")
        assert scheme == "Negotiate" and reply
        token.context.step(base64.b64decode(reply))
        assert token.context.complete
        # The browser gets the session cookies, as with password login.
        cookies = response.headers.get_list("set-cookie")
        assert any(cookie.startswith("zx_session=") for cookie in cookies)
        # The token is a working session.
        me = server.get(
            "/v1/user", headers={"Authorization": f"Bearer {body['token']}"}
        )
        assert me.status_code == 200, me.text
        assert me.json()["user"]["id"] == body["user_id"]
        [link] = self.links(server, principal("alice"))
        assert link.user_id == body["user_id"] and link.realm == REALM
        assert link.last_login_at is not None

    def test_the_principal_signs_in_as_the_same_user_again(
        self, server, realm, service, set_env
    ):
        set_env("REGISTRATION_MODE", "open")
        credentials = realm.kinit("bob")
        first = server.get(NEGOTIATE_PATH, headers=client_token(credentials).header)
        second = server.get(NEGOTIATE_PATH, headers=client_token(credentials).header)
        assert first.status_code == second.status_code == 200
        assert first.json()["user_id"] == second.json()["user_id"]
        assert len(self.links(server, principal("bob"))) == 1

    def test_a_bare_kerberos_token_signs_in_too(self, server, realm, service, set_env):
        set_env("REGISTRATION_MODE", "open")
        token = client_token(realm.kinit("bob"), mech=gssapi.MechType.kerberos)
        assert server.get(NEGOTIATE_PATH, headers=token.header).status_code == 200

    # -- refusals ---------------------------------------------------------

    def test_a_replayed_token_is_refused(self, server, realm, service, set_env):
        set_env("REGISTRATION_MODE", "open")
        token = client_token(realm.kinit("bob"))
        assert server.get(NEGOTIATE_PATH, headers=token.header).status_code == 200
        replayed = server.get(NEGOTIATE_PATH, headers=token.header)
        assert replayed.status_code == 401
        assert "token" not in replayed.json()

    def test_a_ticket_for_another_service_is_refused(
        self, server, realm, service, set_env
    ):
        set_env("REGISTRATION_MODE", "open")
        token = client_token(realm.kinit("alice"), service=OTHER_SERVICE)
        assert server.get(NEGOTIATE_PATH, headers=token.header).status_code == 401

    def test_the_service_principal_setting_binds_the_keytab(
        self, server, realm, keytab_instance, set_env
    ):
        """The keytab holds two services' keys; only the named one is
        accepted for."""
        set_env("REGISTRATION_MODE", "open")
        both = realm.keytab(
            f"both.{uuid.uuid4().hex}.keytab", "HTTP/svc.test", "HTTP/other.test"
        )
        keytab_instance({"keytab": _keytab_b64(both), "service_principal": SERVICE})
        credentials = realm.kinit("bob")
        other = client_token(credentials, service=OTHER_SERVICE)
        assert server.get(NEGOTIATE_PATH, headers=other.header).status_code == 401
        named = client_token(credentials)
        assert server.get(NEGOTIATE_PATH, headers=named.header).status_code == 200

    def test_a_realm_not_allowed_is_refused(
        self, server, realm, keytab_instance, svc_keytab, set_env
    ):
        set_env("REGISTRATION_MODE", "open")
        keytab_instance(
            {
                "keytab": _keytab_b64(svc_keytab),
                "service_principal": SERVICE,
                "allowed_realms": "ELSEWHERE.TEST, CORP.TEST",
            }
        )
        token = client_token(realm.kinit("frank"))
        assert server.get(NEGOTIATE_PATH, headers=token.header).status_code == 403
        assert self.links(server, principal("frank")) == []

    def test_with_no_realm_named_nothing_is_allowed(
        self, server, realm, keytab_instance, svc_keytab, set_env
    ):
        """No service principal to take a realm from, and no realm listed."""
        set_env("REGISTRATION_MODE", "open")
        keytab_instance({"keytab": _keytab_b64(svc_keytab)})
        token = client_token(realm.kinit("frank"))
        assert server.get(NEGOTIATE_PATH, headers=token.header).status_code == 403

    def test_an_expired_ticket_is_refused(self, server, realm, service, set_env):
        set_env("REGISTRATION_MODE", "open")
        token = client_token(realm.kinit("erin", lifetime=f"{SHORT_TICKET_SECONDS}s"))
        time.sleep(EXPIRY_WAIT_SECONDS)
        assert server.get(NEGOTIATE_PATH, headers=token.header).status_code == 401
        assert self.links(server, principal("erin")) == []

    def test_a_keytab_a_user_configured_is_not_trusted(
        self, server, realm, keytab_instance, svc_keytab, user_b, set_env
    ):
        """Whoever holds a keytab vouches for every principal of its realm,
        so only an operator's instance may sign anyone in."""
        set_env("REGISTRATION_MODE", "open")
        keytab_instance(
            {"keytab": _keytab_b64(svc_keytab), "service_principal": SERVICE},
            creator_id=user_b.id,
        )
        token = client_token(realm.kinit("grace"))
        assert server.get(NEGOTIATE_PATH, headers=token.header).status_code == 503
        assert self.links(server, principal("grace")) == []

    def test_a_deleted_keytab_instance_signs_no_one_in(
        self, server, realm, service, set_env
    ):
        """ROOT's reads include deleted rows, so deletion must be checked."""
        set_env("REGISTRATION_MODE", "open")
        credentials = realm.kinit("bob")
        first = client_token(credentials)
        assert server.get(NEGOTIATE_PATH, headers=first.header).status_code == 200
        ProviderInstanceManager(
            model_registry=server.app.state.model_registry,
            requester_id=env("ROOT_ID"),
        ).delete(id=service.id)
        after = client_token(credentials)
        assert server.get(NEGOTIATE_PATH, headers=after.header).status_code == 503

    def test_the_keytab_does_not_stay_on_disk(self, server, realm, service, set_env):
        set_env("REGISTRATION_MODE", "open")
        spool = Path(tempfile.gettempdir())
        before = set(spool.glob("zx-keytab-*"))
        token = client_token(realm.kinit("bob"))
        assert server.get(NEGOTIATE_PATH, headers=token.header).status_code == 200
        assert set(spool.glob("zx-keytab-*")) == before

    def test_a_disabled_keytab_instance_signs_no_one_in(
        self, server, realm, service, set_env
    ):
        set_env("REGISTRATION_MODE", "open")
        # The instance manager's Update schema has no ``enabled``; the column
        # is written directly.
        registry = server.app.state.model_registry
        ProviderInstanceModel.DB(registry.DB.manager.Base).update(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            id=service.id,
            new_properties={"enabled": False},
        )
        token = client_token(realm.kinit("bob"))
        assert server.get(NEGOTIATE_PATH, headers=token.header).status_code == 503

    # -- registration mode and linking -----------------------------------

    @pytest.mark.parametrize("mode", ["closed", "invite"])
    def test_an_unknown_principal_is_refused_unless_registration_is_open(
        self, server, realm, service, set_env, mode
    ):
        set_env("REGISTRATION_MODE", mode)
        token = client_token(realm.kinit("carol"))
        assert server.get(NEGOTIATE_PATH, headers=token.header).status_code == 403
        assert self.links(server, principal("carol")) == []

    def test_a_principal_root_linked_signs_in_while_registration_is_closed(
        self, server, realm, service, set_env
    ):
        set_env("REGISTRATION_MODE", "closed")
        user = create_user(server, email=generate_test_email("kerberos_dave"))
        link = self.root_links(server).create(
            user_id=user.id, principal=principal("dave")
        )
        assert link.realm == REALM
        token = client_token(realm.kinit("dave"))
        response = server.get(NEGOTIATE_PATH, headers=token.header)
        assert response.status_code == 200, response.text
        assert response.json()["user_id"] == user.id
        # The link is the user's: they see it, and may remove it.
        mine = server.get(
            "/v1/auth/kerberos", headers={"Authorization": f"Bearer {user.jwt}"}
        )
        assert mine.status_code == 200, mine.text
        assert [p["principal"] for p in mine.json()["kerberos_principals"]] == [
            principal("dave")
        ]
        removed = server.delete(
            f"/v1/auth/kerberos/{link.id}",
            headers={"Authorization": f"Bearer {user.jwt}"},
        )
        assert removed.status_code == 204, removed.text
        again = client_token(realm.kinit("dave"))
        assert server.get(NEGOTIATE_PATH, headers=again.header).status_code == 403

    @pytest.mark.parametrize("internal", INTERNAL_ACCOUNTS)
    def test_nothing_links_to_an_internal_account(self, server, internal):
        with pytest.raises(Exception) as refused:
            self.root_links(server).create(
                user_id=env(internal), principal=principal(f"internal-{internal}")
            )
        assert getattr(refused.value, "status_code", None) == 403

    def test_a_principal_linked_to_root_signs_no_one_in(self, server, realm, service):
        """A link to ROOT written beneath the manager (by an older version,
        or directly) still issues no session."""
        user = f"rooted{uuid.uuid4().hex[:6]}"
        realm.kadmin(f"addprinc -randkey {principal_name(user)}")
        realm.keytab(f"{user}.keytab", principal_name(user))
        registry = server.app.state.model_registry
        KerberosPrincipalModel.DB(registry.DB.manager.Base).create(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            principal=principal(user),
            realm=REALM,
            user_id=env("ROOT_ID"),
        )
        token = client_token(realm.kinit(user))
        response = server.get(NEGOTIATE_PATH, headers=token.header)
        assert response.status_code == 403, response.text

    def test_a_disabled_account_cannot_sign_in(self, server, realm, service, set_env):
        set_env("REGISTRATION_MODE", "closed")
        user = create_user(server, email=generate_test_email("kerberos_disabled"))
        self.root_links(server).create(user_id=user.id, principal=principal("heidi"))
        registry = server.app.state.model_registry
        UserModel.DB(registry.DB.manager.Base).update(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            id=user.id,
            new_properties={"active": False},
        )
        token = client_token(realm.kinit("heidi"))
        assert server.get(NEGOTIATE_PATH, headers=token.header).status_code == 403

    def test_a_user_cannot_claim_a_principal(self, server, user_b):
        """Claiming another's principal would take over their sign-in."""
        claim = server.post(
            "/v1/auth/kerberos",
            json={"kerberos_principal": {"principal": principal("alice")}},
            headers={"Authorization": f"Bearer {user_b.jwt}"},
        )
        assert claim.status_code in (404, 405)
        manager = KerberosPrincipalManager(
            model_registry=server.app.state.model_registry, requester_id=user_b.id
        )
        with pytest.raises(Exception) as refused:
            manager.create(user_id=user_b.id, principal=principal("alice"))
        assert getattr(refused.value, "status_code", None) == 403

    def test_a_principal_links_to_one_user(self, server, user_b, admin_a):
        principal = f"linked-once-{uuid.uuid4().hex[:8]}@{REALM}"
        self.root_links(server).create(user_id=user_b.id, principal=principal)
        with pytest.raises(Exception) as refused:
            self.root_links(server).create(user_id=admin_a.id, principal=principal)
        assert getattr(refused.value, "status_code", None) == 409

    def test_a_link_never_moves_to_another_user(self, server, user_b, admin_a):
        principal = f"fixed-{uuid.uuid4().hex[:8]}@{REALM}"
        link = self.root_links(server).create(user_id=user_b.id, principal=principal)
        with pytest.raises(Exception) as refused:
            self.root_links(server).update(id=link.id, user_id=admin_a.id)
        assert getattr(refused.value, "status_code", None) == 403
        [kept] = self.links(server, principal)
        assert kept.user_id == user_b.id

    def test_users_see_only_their_own_links(self, server, user_b, admin_a):
        principal = f"private-{uuid.uuid4().hex[:8]}@{REALM}"
        self.root_links(server).create(user_id=admin_a.id, principal=principal)
        theirs = server.get(
            "/v1/auth/kerberos", headers={"Authorization": f"Bearer {user_b.jwt}"}
        )
        assert theirs.status_code == 200, theirs.text
        assert principal not in [
            p["principal"] for p in theirs.json()["kerberos_principals"]
        ]

    # -- channel bindings -------------------------------------------------

    def test_channel_bindings_must_match_the_certificate(
        self, server, realm, keytab_instance, svc_keytab, set_env
    ):
        set_env("REGISTRATION_MODE", "open")
        certificate = _certificate("svc.test")
        keytab_instance(
            {
                "keytab": _keytab_b64(svc_keytab),
                "service_principal": SERVICE,
                "channel_binding_certificate": certificate,
            }
        )
        credentials = realm.kinit("bob")
        wrong = ChannelBindings(
            application_data=tls_server_end_point(_certificate("mitm.test"))
        )
        bad = client_token(credentials, bindings=wrong)
        assert server.get(NEGOTIATE_PATH, headers=bad.header).status_code == 401
        right = ChannelBindings(application_data=tls_server_end_point(certificate))
        good = client_token(credentials, bindings=right)
        assert server.get(NEGOTIATE_PATH, headers=good.header).status_code == 200
