# SPDX-License-Identifier: AGPL-3.0-or-later
"""Signing in to the server with directory credentials, end to end: the
app's routes against the real OpenLDAP directory in conftest.py.

Holes these close (the scaffold had models only): a directory's service
password readable by anyone who could list it; nobody able to sign in with
a directory at all; and the ways a directory sign-in commonly goes wrong:
filter injection, empty-password binds, plain-text or unverified
connections, identities keyed by a renameable DN, a directory's email
claim taking over a local account, account creation that ignores
REGISTRATION_MODE, and failures that escape the login lockout."""

import os
import uuid
from typing import Any, Callable, Dict, Iterator

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from ldap3 import Connection, Server

from conftest import CORE_COMPANION_EXTENSIONS
from zephyrex.extensions.ldap_consumer.BLL_LDAPConsumer import (
    INVALID_CREDENTIALS,
    LOCKOUT_FLOW,
    LdapDirectoryManager,
    LdapDirectoryModel,
    LdapIdentityModel,
)
from zephyrex.extensions.ldap_consumer.conftest import (
    PEOPLE,
    ROOT_DN,
    ROOT_PASSWORD,
    SERVICE_PASSWORD,
    Directory,
)
from zephyrex.extensions.ldap_consumer.LDAPClient import ALLOW_PLAINTEXT_LOOPBACK
from zephyrex.lib.Environment import env
from zephyrex.lib.InboundSecurity import (
    DEFAULT_AUTH_RATE_LIMIT,
    parse_rate_spec,
    reset_rate_limit_counts,
)
from zephyrex.lib.SecretEncryption import FERNET_PREFIX
from zephyrex.lib.SessionCookies import SESSION_COOKIE
from zephyrex.logic.BLL_Auth import UserManager
from zephyrex.testing.factories import (
    INTERNAL_ACCOUNTS,
    create_user,
    if_match_of,
    internal_account_email,
)

DIRECTORY_ROUTE = "/v1/ldap/directory"
IDENTITY_ROUTE = "/v1/ldap/identity"
LOGIN_ROUTE = "/v1/auth/ldap/login"
TEST_CLIENT_HOST = "testclient"
PER_USER_LOCKOUT = 5
LDAPS_NAME = "Corp LDAPS"


@pytest.fixture(scope="module")
def app() -> FastAPI:
    from zephyrex.app import instance
    from zephyrex.pydantic2.sqlalchemy import prepare_test_registry

    prepare_test_registry()
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    extensions = ["ldap_consumer", "auth_session", "auth_lockout", "auth_mfa"]
    built: FastAPI = instance(
        db_prefix=f"test.ldap_consumer.{worker}",
        extensions=",".join(
            extensions + [c for c in CORE_COMPANION_EXTENSIONS if c not in extensions]
        ),
    )
    return built


@pytest.fixture(scope="module")
def registry(app: FastAPI) -> Any:
    return app.state.model_registry


@pytest.fixture(scope="module")
def server(app: FastAPI) -> TestClient:
    # Session cookies are Secure: they travel over https only.
    return TestClient(app, base_url="https://testserver")


@pytest.fixture(autouse=True)
def fresh_counters() -> Iterator[None]:
    """Each test starts with no requests counted against the client's
    address, by the rate limit or the per-IP lockout."""
    reset_rate_limit_counts()
    UserManager._lockout_tracker.clear(TEST_CLIENT_HOST, LOCKOUT_FLOW)
    yield
    UserManager._lockout_tracker.clear(TEST_CLIENT_HOST, LOCKOUT_FLOW)


def root() -> Dict[str, str]:
    return {"X-API-Key": env("ROOT_API_KEY")}


def create_directory(
    server: TestClient, directory: Directory, **overrides: Any
) -> Dict[str, Any]:
    response = server.post(
        DIRECTORY_ROUTE,
        json={"ldap_directory": directory.settings(**overrides)},
        headers=root(),
    )
    assert response.status_code == 201, response.text
    created: Dict[str, Any] = response.json()["ldap_directory"]
    return created


@pytest.fixture(scope="module")
def ldaps(server: TestClient, registry: Any, directory: Directory) -> Dict[str, Any]:
    """This run's directory record, made once per run. The module's app is
    rebuilt when xdist resumes the module after another one, but the
    database stays, and the people linked through the first record would
    meet a second as a directory email taking over an account (409). The
    run's directory listens on its own port, so the record reaching it is
    this run's."""
    existing = LdapDirectoryModel.DB(registry.DB.manager.Base).list(
        requester_id=env("ROOT_ID"),
        model_registry=registry,
        name=LDAPS_NAME,
        port=directory.ldaps_port,
    )
    if not existing:
        return create_directory(server, directory, name=LDAPS_NAME)
    fetched = server.get(f"{DIRECTORY_ROUTE}/{existing[0]['id']}", headers=root())
    assert fetched.status_code == 200, fetched.text
    record: Dict[str, Any] = fetched.json()["ldap_directory"]
    return record


def login(server: TestClient, directory_id: str, username: str, password: str) -> Any:
    server.cookies.clear()
    return server.post(
        LOGIN_ROUTE,
        json={"directory_id": directory_id, "username": username, "password": password},
    )


def signed_in(
    server: TestClient, directory_id: str, directory: Directory, uid: str
) -> Dict[str, Any]:
    response = login(server, directory_id, uid, directory.people[uid].password)
    assert response.status_code == 200, response.text
    body: Dict[str, Any] = response.json()
    return body


def identity_of(registry: Any, user_id: str) -> LdapIdentityModel:
    IdentityDB = LdapIdentityModel.DB(registry.DB.manager.Base)
    rows = IdentityDB.list(
        requester_id=env("ROOT_ID"),
        model_registry=registry,
        user_id=user_id,
        return_type="dto",
        override_dto=LdapIdentityModel,
    )
    assert len(rows) == 1
    found: LdapIdentityModel = rows[0]
    return found


def entry_uuid(directory: Directory, dn: str) -> str:
    connection = Connection(
        Server("127.0.0.1", port=directory.ldap_port, get_info="NO_INFO"),
        user=ROOT_DN,
        password=ROOT_PASSWORD,
        auto_bind=True,
        receive_timeout=10,
    )
    try:
        assert connection.search(
            dn, "(objectClass=*)", "BASE", attributes=["entryUUID"]
        )
        value: str = str(connection.entries[0].entryUUID.value)
        return value
    finally:
        connection.unbind()


class TestDirectories:
    def test_secrets_are_write_only_and_sealed(
        self, server: TestClient, registry: Any, ldaps: Dict[str, Any]
    ) -> None:
        assert "bind_password" not in ldaps and "bind_dn" not in ldaps
        fetched = server.get(f"{DIRECTORY_ROUTE}/{ldaps['id']}", headers=root())
        assert fetched.status_code == 200, fetched.text
        assert "bind_password" not in fetched.json()["ldap_directory"]
        stored = LdapDirectoryModel.DB(registry.DB.manager.Base).get(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            id=ldaps["id"],
            return_type="dto",
            override_dto=LdapDirectoryModel,
        )
        assert stored.bind_password.startswith(FERNET_PREFIX)
        assert stored.bind_dn.startswith(FERNET_PREFIX)

    def test_a_signed_in_user_cannot_manage_directories(
        self, server: TestClient, directory: Directory, ldaps: Dict[str, Any]
    ) -> None:
        user = create_user(server)
        bearer = {"Authorization": f"Bearer {user.jwt}"}
        assert server.get(DIRECTORY_ROUTE, headers=bearer).status_code in (401, 403)
        created = server.post(
            DIRECTORY_ROUTE,
            json={"ldap_directory": directory.settings()},
            headers=bearer,
        )
        assert created.status_code in (401, 403)

    def test_a_users_own_api_key_is_not_an_administrator(
        self, server: TestClient, registry: Any, directory: Directory
    ) -> None:
        """An API key a user issues resolves to that user: the manager still
        refuses them."""
        user = create_user(server)
        manager = LdapDirectoryManager(requester_id=user.id, model_registry=registry)
        with pytest.raises(HTTPException) as refused:
            manager.create(**directory.settings())
        assert refused.value.status_code == 403

    @pytest.mark.parametrize(
        "overrides",
        [
            {"security": "plain"},
            {"user_object_filter": "objectClass=person"},
            {"username_attribute": "uid)(x"},
            {"ca_certificate": "not a certificate"},
        ],
    )
    def test_an_unusable_directory_is_refused(
        self, server: TestClient, directory: Directory, overrides: Dict[str, Any]
    ) -> None:
        response = server.post(
            DIRECTORY_ROUTE,
            json={"ldap_directory": directory.settings(**overrides)},
            headers=root(),
        )
        assert response.status_code == 422, response.text

    def test_an_update_cannot_downgrade_to_plain(
        self, server: TestClient, directory: Directory
    ) -> None:
        created = create_directory(server, directory, name="To downgrade")
        response = server.put(
            f"{DIRECTORY_ROUTE}/{created['id']}",
            json={"ldap_directory": {"security": "plain"}},
            headers={**root(), **if_match_of(created)},
        )
        assert response.status_code == 422, response.text

    @pytest.mark.parametrize("cleared", ["host", "timeout_seconds", "bind_password"])
    def test_an_update_cannot_clear_a_required_setting(
        self, server: TestClient, directory: Directory, cleared: str
    ) -> None:
        created = create_directory(server, directory, name=f"Clear {cleared}")
        response = server.put(
            f"{DIRECTORY_ROUTE}/{created['id']}",
            json={"ldap_directory": {cleared: None}},
            headers={**root(), **if_match_of(created)},
        )
        assert response.status_code == 422, response.text

    def test_a_rotated_service_password_is_sealed_and_used(
        self, server: TestClient, directory: Directory
    ) -> None:
        created = create_directory(
            server, directory, name="Rotated", bind_password="stale-password"
        )
        checked = server.get(f"{DIRECTORY_ROUTE}/{created['id']}/check", headers=root())
        assert checked.status_code == 502, checked.text
        updated = server.put(
            f"{DIRECTORY_ROUTE}/{created['id']}",
            json={"ldap_directory": {"bind_password": SERVICE_PASSWORD}},
            headers={**root(), **if_match_of(created)},
        )
        assert updated.status_code == 200, updated.text
        assert "bind_password" not in updated.json()["ldap_directory"]
        checked = server.get(f"{DIRECTORY_ROUTE}/{created['id']}/check", headers=root())
        assert checked.status_code == 200, checked.text

    def test_the_sign_in_list_names_directories_only(
        self, server: TestClient, ldaps: Dict[str, Any]
    ) -> None:
        listed = server.get("/v1/auth/ldap/directories")
        assert listed.status_code == 200, listed.text
        entries = listed.json()["directories"]
        assert {"id": ldaps["id"], "name": LDAPS_NAME} in entries
        assert all(set(entry) == {"id", "name"} for entry in entries)

    def test_check_and_lookup(
        self, server: TestClient, directory: Directory, ldaps: Dict[str, Any]
    ) -> None:
        checked = server.get(f"{DIRECTORY_ROUTE}/{ldaps['id']}/check", headers=root())
        assert checked.status_code == 200, checked.text
        assert checked.json() == {"reachable": True, "security": "ldaps"}
        found = server.post(
            f"{DIRECTORY_ROUTE}/{ldaps['id']}/lookup",
            json={"username": "alice"},
            headers=root(),
        )
        assert found.status_code == 200, found.text
        assert found.json()["external_id"] == entry_uuid(
            directory, directory.people["alice"].dn
        )


class TestSignIn:
    def test_the_same_session_as_a_password_login(
        self, server: TestClient, directory: Directory, ldaps: Dict[str, Any]
    ) -> None:
        body = signed_in(server, ldaps["id"], directory, "alice")
        assert body["user"]["email"] == directory.people["alice"].mail
        assert body["token"] and body["session_key"]
        assert body["mfa_required"] is False
        assert SESSION_COOKIE in server.cookies
        me = server.get(
            "/v1/user", headers={"Authorization": f"Bearer {body['token']}"}
        )
        assert me.status_code == 200, me.text
        assert me.json()["user"]["email"] == directory.people["alice"].mail

    def test_the_identity_is_the_entry_uuid_and_survives_a_move(
        self,
        server: TestClient,
        registry: Any,
        directory: Directory,
        ldaps: Dict[str, Any],
    ) -> None:
        dora = directory.people["dora"]
        first = signed_in(server, ldaps["id"], directory, "dora")
        identity = identity_of(registry, first["user"]["id"])
        assert identity.external_id == entry_uuid(directory, dora.dn)
        assert identity.ldap_directory_id == ldaps["id"]

        admin = Connection(
            Server("127.0.0.1", port=directory.ldap_port, get_info="NO_INFO"),
            user=ROOT_DN,
            password=ROOT_PASSWORD,
            auto_bind=True,
            receive_timeout=10,
        )
        staff = f"ou=staff,{PEOPLE}"
        try:
            assert admin.add(staff, ["organizationalUnit"], {"ou": "staff"})
            assert admin.modify_dn(dora.dn, "uid=dora", new_superior=staff)
        finally:
            admin.unbind()

        again = signed_in(server, ldaps["id"], directory, "dora")
        assert again["user"]["id"] == first["user"]["id"]
        assert identity_of(registry, first["user"]["id"]).dn == f"uid=dora,{staff}"

    def test_groups_are_recorded_on_the_identity(
        self,
        server: TestClient,
        registry: Any,
        directory: Directory,
        ldaps: Dict[str, Any],
    ) -> None:
        body = signed_in(server, ldaps["id"], directory, "bob")
        assert identity_of(registry, body["user"]["id"]).groups == sorted(
            directory.group_dn(g) for g in ("engineers", "ops")
        )

    def test_starttls(self, server: TestClient, directory: Directory) -> None:
        starttls = create_directory(
            server,
            directory,
            name="Corp StartTLS",
            security="starttls",
            port=directory.ldap_port,
        )
        body = signed_in(server, starttls["id"], directory, "rob(admin)")
        assert body["user"]["email"] == directory.people["rob(admin)"].mail

    def test_a_second_factor_still_stands(
        self,
        server: TestClient,
        registry: Any,
        directory: Directory,
        ldaps: Dict[str, Any],
    ) -> None:
        pyotp = pytest.importorskip("pyotp")
        from zephyrex.extensions.auth_mfa.BLL_Auth_MFA import (
            MultifactorMethodManager,
            MultifactorMethodType,
        )

        user_id = signed_in(server, ldaps["id"], directory, "frank")["user"]["id"]
        manager = MultifactorMethodManager(
            requester_id=user_id, model_registry=registry
        )
        method = manager.create(method_type=MultifactorMethodType.TOTP)
        totp = pyotp.TOTP(manager.totp_provisioning_route(method.id)["secret"])
        assert manager.verify_mfa_code(method.id, totp.now())

        response = login(
            server, ldaps["id"], "frank", directory.people["frank"].password
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["mfa_required"] is True and body["challenge_token"]
        assert body["token"] is None
        assert SESSION_COOKIE not in server.cookies


class TestRefusals:
    @pytest.mark.parametrize(
        "username,password",
        [
            ("gina", "not-her-password"),
            ("gina", ""),
            ("nobody", "whatever-1"),
            ("al*ce", "alice-pass-1"),
            ("*)(uid=*", "alice-pass-1"),
            ("alice)(|(uid=*", "alice-pass-1"),
            ("*", "alice-pass-1"),
        ],
    )
    def test_refused_alike(
        self,
        server: TestClient,
        ldaps: Dict[str, Any],
        username: str,
        password: str,
    ) -> None:
        response = login(server, ldaps["id"], username, password)
        assert response.status_code == 401, response.text
        assert INVALID_CREDENTIALS in response.text
        assert SESSION_COOKIE not in server.cookies

    def test_an_untrusted_certificate(
        self, server: TestClient, directory: Directory
    ) -> None:
        untrusted = create_directory(
            server, directory, name="Untrusted", ca_certificate=directory.other_ca_pem
        )
        response = login(
            server, untrusted["id"], "alice", directory.people["alice"].password
        )
        assert response.status_code == 503, response.text

    def test_a_certificate_for_another_name(
        self, server: TestClient, directory: Directory
    ) -> None:
        wrong_name = create_directory(
            server, directory, name="Wrong name", host="127.0.0.1"
        )
        response = login(
            server, wrong_name["id"], "alice", directory.people["alice"].password
        )
        assert response.status_code == 503, response.text

    def test_plain_ldap_needs_the_operator_and_loopback_at_sign_in_too(
        self,
        server: TestClient,
        directory: Directory,
        set_env: Callable[[str, str], None],
    ) -> None:
        set_env(ALLOW_PLAINTEXT_LOOPBACK, "true")
        plain = create_directory(
            server,
            directory,
            name="Loopback plain",
            security="plain",
            port=directory.ldap_port,
        )
        judy = directory.people["judy"]
        assert login(server, plain["id"], "judy", judy.password).status_code == 200
        set_env(ALLOW_PLAINTEXT_LOOPBACK, "false")
        refused = login(server, plain["id"], "judy", judy.password)
        assert refused.status_code == 503, refused.text

    def test_a_disabled_directory(
        self, server: TestClient, directory: Directory
    ) -> None:
        disabled = create_directory(server, directory, name="Off", enabled=False)
        response = login(
            server, disabled["id"], "alice", directory.people["alice"].password
        )
        assert response.status_code == 404, response.text

    def test_a_linked_users_failures_lock_them_out(
        self, server: TestClient, directory: Directory, ldaps: Dict[str, Any]
    ) -> None:
        """Failures count against the user (auth_lockout), so the right
        password stops working too once the threshold is reached."""
        signed_in(server, ldaps["id"], directory, "ivy")
        for _ in range(PER_USER_LOCKOUT):
            assert login(server, ldaps["id"], "ivy", "wrong-guess-1").status_code == 401
        locked = login(server, ldaps["id"], "ivy", directory.people["ivy"].password)
        assert locked.status_code == 429, locked.text

    def test_failures_from_one_address_lock_it_out(
        self, server: TestClient, ldaps: Dict[str, Any]
    ) -> None:
        tracker = UserManager._lockout_tracker
        for _ in range(tracker.policy.failures_per_window):
            login(server, ldaps["id"], f"nobody-{uuid.uuid4().hex[:6]}", "guess-1")
        # The lockout, not the request rate, refuses the right password.
        reset_rate_limit_counts()
        locked = login(server, ldaps["id"], "alice", "alice-pass-1")
        assert locked.status_code == 429, locked.text
        assert tracker.is_locked(TEST_CLIENT_HOST, LOCKOUT_FLOW)

    def test_sign_ins_are_rate_limited(
        self, server: TestClient, directory: Directory, ldaps: Dict[str, Any]
    ) -> None:
        count, _ = parse_rate_spec(DEFAULT_AUTH_RATE_LIMIT)
        alice = directory.people["alice"]
        for _ in range(count):
            assert (
                login(server, ldaps["id"], "alice", alice.password).status_code == 200
            )
        limited = login(server, ldaps["id"], "alice", alice.password)
        assert limited.status_code == 429, limited.text


class TestAccounts:
    def test_a_directory_email_does_not_take_over_a_local_account(
        self, server: TestClient, directory: Directory, ldaps: Dict[str, Any]
    ) -> None:
        local = create_user(server, email=directory.people["erin"].mail)
        response = login(server, ldaps["id"], "erin", directory.people["erin"].password)
        assert response.status_code == 409, response.text
        assert SESSION_COOKIE not in server.cookies
        trusted = create_directory(
            server, directory, name="Trusted email", link_existing_by_email=True
        )
        linked = signed_in(server, trusted["id"], directory, "erin")
        assert linked["user"]["id"] == local.id

    @staticmethod
    def add_person(directory: Directory, uid: str, password: str, mail: str) -> None:
        """A new entry in the real directory, written as its administrator."""
        connection = Connection(
            Server("127.0.0.1", port=directory.ldap_port, get_info="NO_INFO"),
            user=ROOT_DN,
            password=ROOT_PASSWORD,
            auto_bind=True,
            receive_timeout=10,
        )
        try:
            assert connection.add(
                f"uid={uid},{PEOPLE}",
                ["inetOrgPerson"],
                {
                    "uid": uid,
                    "cn": uid,
                    "sn": uid,
                    "mail": mail,
                    "userPassword": password,
                },
            ), connection.result
        finally:
            connection.unbind()

    @pytest.mark.parametrize("internal", INTERNAL_ACCOUNTS)
    def test_no_directory_email_reaches_an_internal_account(
        self,
        server: TestClient,
        registry: Any,
        directory: Directory,
        internal: str,
    ) -> None:
        """ROOT's seeded email is predictable; a directory trusted to link
        by email whose entry carries it must not sign in as the superuser,
        and no administrator may link an entry to an internal account."""
        trusted = create_directory(
            server, directory, name="Trusted email", link_existing_by_email=True
        )
        uid = f"usurper{uuid.uuid4().hex[:8]}"
        password = "usurper-pass-1"
        with internal_account_email(registry, env(internal)) as email:
            self.add_person(directory, uid, password, email.upper())
            response = login(server, trusted["id"], uid, password)
        assert response.status_code == 403, response.text
        assert SESSION_COOKIE not in server.cookies
        found = server.post(
            f"{DIRECTORY_ROUTE}/{trusted['id']}/lookup",
            json={"username": uid},
            headers=root(),
        ).json()
        linked = server.post(
            IDENTITY_ROUTE,
            json={
                "ldap_identity": {
                    "user_id": env(internal),
                    "ldap_directory_id": trusted["id"],
                    "external_id": found["external_id"],
                    "dn": found["dn"],
                }
            },
            headers=root(),
        )
        assert linked.status_code == 403, linked.text

    def test_an_identity_belongs_to_one_directory(
        self, server: TestClient, directory: Directory, ldaps: Dict[str, Any]
    ) -> None:
        """The same entry reached through another directory record is not
        the identity linked through the first: it meets the account it
        signed up as like any directory email."""
        signed_in(server, ldaps["id"], directory, "alice")
        other = create_directory(server, directory, name="Second record")
        response = login(
            server, other["id"], "alice", directory.people["alice"].password
        )
        assert response.status_code == 409, response.text

    def test_an_administrator_links_an_existing_account(
        self,
        server: TestClient,
        directory: Directory,
        ldaps: Dict[str, Any],
        set_env: Callable[[str, str], None],
    ) -> None:
        set_env("REGISTRATION_MODE", "closed")
        local = create_user(server, email=f"hank_{uuid.uuid4().hex[:6]}@example.com")
        found = server.post(
            f"{DIRECTORY_ROUTE}/{ldaps['id']}/lookup",
            json={"username": "hank"},
            headers=root(),
        ).json()
        linked = server.post(
            IDENTITY_ROUTE,
            json={
                "ldap_identity": {
                    "user_id": local.id,
                    "ldap_directory_id": ldaps["id"],
                    "external_id": found["external_id"],
                    "dn": found["dn"],
                }
            },
            headers=root(),
        )
        assert linked.status_code == 201, linked.text
        assert (
            signed_in(server, ldaps["id"], directory, "hank")["user"]["id"] == local.id
        )
        duplicate = server.post(
            IDENTITY_ROUTE,
            json={
                "ldap_identity": {
                    "user_id": local.id,
                    "ldap_directory_id": ldaps["id"],
                    "external_id": found["external_id"],
                }
            },
            headers=root(),
        )
        assert duplicate.status_code == 409, duplicate.text

    @pytest.mark.parametrize("mode", ["closed", "invite"])
    def test_new_accounts_follow_registration_mode(
        self,
        server: TestClient,
        directory: Directory,
        ldaps: Dict[str, Any],
        set_env: Callable[[str, str], None],
        mode: str,
    ) -> None:
        set_env("REGISTRATION_MODE", mode)
        response = login(
            server, ldaps["id"], "alicia", directory.people["alicia"].password
        )
        assert response.status_code == 403, response.text
        assert SESSION_COOKIE not in server.cookies

    def test_an_account_without_email_is_not_created(
        self, server: TestClient, directory: Directory, ldaps: Dict[str, Any]
    ) -> None:
        response = login(
            server, ldaps["id"], "carol", directory.people["carol"].password
        )
        assert response.status_code == 403, response.text

    def test_a_created_account_has_no_usable_password(
        self, server: TestClient, directory: Directory, ldaps: Dict[str, Any]
    ) -> None:
        """Signing in the password way fails like a wrong password (401),
        not as a missing credential."""
        gina = directory.people["gina"]
        signed_in(server, ldaps["id"], directory, "gina")
        server.cookies.clear()
        response = server.post(
            "/v1/user/authorize",
            json={"email": gina.mail, "password": gina.password},
        )
        assert response.status_code == 401, response.text
