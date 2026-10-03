# SPDX-License-Identifier: AGPL-3.0-or-later
"""Signing in through RADIUS, over the app's routes, against a real
FreeRADIUS (UDP and RadSec) and a bare UDP responder."""

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.radius_consumer.BLL_RADIUSConsumer import (
    ALLOW_UDP_ENV,
    LOCKOUT_SCOPE,
    RadiusChallengeModel,
    RadiusIdentityModel,
    RadiusServerModel,
)
from zephyrex.extensions.radius_consumer.EXT_RADIUSConsumer import (
    EXT_RADIUSConsumer,
)
from zephyrex.extensions.radius_consumer.RADIUSTestServers import (
    LOOPBACK,
    OTP_CODE,
    OTP_FIRST_FACTOR,
    OTP_PROMPT,
    OTP_STATE_HEX,
    OTP_USER,
    Certificates,
    FreeRADIUS,
    HandCraftedResponder,
    accept_without_message_authenticator,
    freeradius_available,
    issue_certificates,
    silent_udp_port,
)
from zephyrex.lib.Environment import env
from zephyrex.lib.InboundSecurity import reset_rate_limit_counts
from zephyrex.logic.BLL_Auth import UserManager, UserModel

SECRET = "a-long-shared-secret-for-the-tests-7f3a"
USERS = {"alice": "wonderland", "bob": "builder", "carol": "singer"}
SERVERS = "/v1/radius-consumer/servers"
IDENTITIES = "/v1/radius-consumer/identities"
LOGIN = "/v1/auth/radius/login"
CHALLENGE = "/v1/auth/radius/challenge"
CHOICES = "/v1/auth/radius/servers"
TESTCLIENT_ADDRESS = "testclient"

pytestmark = pytest.mark.skipif(
    not freeradius_available(), reason="FreeRADIUS is not installed"
)


def _root() -> Dict[str, str]:
    return {"X-API-Key": os.environ["ROOT_API_KEY"]}


def _bearer(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def certificates() -> Certificates:
    return issue_certificates()


@pytest.fixture(scope="module")
def freeradius(tmp_path_factory, certificates) -> Iterator[FreeRADIUS]:
    with FreeRADIUS(
        tmp_path_factory.mktemp("freeradius"), SECRET, USERS, certificates
    ) as server:
        yield server


@pytest.fixture(autouse=True)
def _fresh_counters(set_env) -> Iterator[None]:
    """UDP allowed, registration open, and no throttling carried over from
    earlier tests (they all come from one client address)."""
    set_env(ALLOW_UDP_ENV, "true")
    set_env("REGISTRATION_MODE", "open")
    reset_rate_limit_counts()
    UserManager._lockout_tracker.clear(TESTCLIENT_ADDRESS, LOCKOUT_SCOPE)
    yield
    reset_rate_limit_counts()
    UserManager._lockout_tracker.clear(TESTCLIENT_ADDRESS, LOCKOUT_SCOPE)


class TestRadiusSignIn(ExtensionServerMixin):
    extension_class = EXT_RADIUSConsumer

    # -- helpers ---------------------------------------------------------

    def _create_server(
        self, server: Any, expect: int = 201, **fields: Any
    ) -> Dict[str, Any]:
        payload = {"name": f"radius-{uuid.uuid4().hex[:8]}", **fields}
        response = server.post(
            SERVERS, json={"radius_server": payload}, headers=_root()
        )
        assert response.status_code == expect, response.text
        body: Dict[str, Any] = response.json()
        created: Dict[str, Any] = body.get("radius_server", body)
        return created

    def _udp(self, server: Any, *ports: int, **fields: Any) -> Dict[str, Any]:
        defaults: Dict[str, Any] = {
            "hosts": [f"{LOOPBACK}:{port}" for port in ports],
            "transport": "udp",
            "shared_secret": SECRET,
            "timeout_seconds": 1,
            "retries": 0,
        }
        return self._create_server(server, **{**defaults, **fields})

    def _radsec(
        self, server: Any, port: int, certificates: Certificates, **fields: Any
    ) -> Dict[str, Any]:
        defaults: Dict[str, Any] = {
            "hosts": [f"{LOOPBACK}:{port}"],
            "tls_ca_pem": certificates.ca_pem,
            "tls_client_cert_pem": certificates.client_cert_pem,
            "tls_client_key_pem": certificates.client_key_pem,
            "timeout_seconds": 2,
        }
        return self._create_server(server, **{**defaults, **fields})

    @staticmethod
    def _login(
        server: Any, radius_server: Dict[str, Any], username: str, password: str
    ) -> Any:
        return server.post(
            LOGIN,
            json={
                "server_id": radius_server["id"],
                "username": username,
                "password": password,
            },
        )

    @staticmethod
    def _link_response(
        server: Any,
        radius_server: Dict[str, Any],
        user_id: str,
        username: str,
        headers: Dict[str, str],
    ) -> Any:
        return server.post(
            IDENTITIES,
            json={
                "radius_identity": {
                    "radius_server_id": radius_server["id"],
                    "user_id": user_id,
                    "username": username,
                }
            },
            headers=headers,
        )

    def _link(
        self, server: Any, radius_server: Dict[str, Any], user_id: str, username: str
    ) -> Dict[str, Any]:
        response = self._link_response(
            server, radius_server, user_id, username, _root()
        )
        assert response.status_code == 201, response.text
        body: Dict[str, Any] = response.json()["radius_identity"]
        return body

    @staticmethod
    def _identities(model_registry: Any, server_id: str) -> List[RadiusIdentityModel]:
        found: List[RadiusIdentityModel] = RadiusIdentityModel.DB(
            model_registry.DB.manager.Base
        ).list(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            radius_server_id=server_id,
            return_type="dto",
            override_dto=RadiusIdentityModel,
        )
        return found

    # -- managing servers ------------------------------------------------

    def test_only_root_creates_a_server(self, server, admin_a, freeradius):
        response = server.post(
            SERVERS,
            json={
                "radius_server": {
                    "name": "mine",
                    "hosts": [f"{LOOPBACK}:{freeradius.udp_port}"],
                    "transport": "udp",
                    "shared_secret": SECRET,
                }
            },
            headers=_bearer(admin_a.jwt),
        )
        assert response.status_code == 403, response.text

    def test_other_users_cannot_see_a_server(self, server, admin_a, freeradius):
        created = self._udp(server, freeradius.udp_port)
        assert server.get(
            f"{SERVERS}/{created['id']}", headers=_bearer(admin_a.jwt)
        ).status_code in (403, 404)
        listed = server.get(SERVERS, headers=_bearer(admin_a.jwt))
        assert created["id"] not in listed.text

    def test_only_root_changes_or_deletes_a_server(self, server, admin_a, freeradius):
        created = self._udp(server, freeradius.udp_port)
        path = f"{SERVERS}/{created['id']}"
        changed = server.put(
            path, json={"radius_server": {"name": "x"}}, headers=_bearer(admin_a.jwt)
        )
        assert changed.status_code in (403, 404)
        deleted = server.delete(path, headers=_bearer(admin_a.jwt))
        assert deleted.status_code in (403, 404)
        assert server.get(path, headers=_root()).status_code == 200

    def test_the_secrets_are_write_only_and_encrypted(
        self, server, model_registry, freeradius, certificates
    ):
        created = self._radsec(
            server, freeradius.radsec_port, certificates, shared_secret="radsec"
        )
        assert "shared_secret" not in created
        assert "tls_client_key_pem" not in created
        read = server.get(f"{SERVERS}/{created['id']}", headers=_root()).text
        assert "PRIVATE KEY" not in read and "shared_secret" not in read
        session = model_registry.DB.session()
        try:
            Table = RadiusServerModel.DB(model_registry.DB.manager.Base)
            row = session.query(Table).filter(Table.id == created["id"]).one()
            assert row.tls_client_key_pem.startswith("fernet:")
            assert row.shared_secret.startswith("fernet:")
        finally:
            session.close()

    def test_plain_udp_needs_explicit_enabling(self, server, set_env, freeradius):
        set_env(ALLOW_UDP_ENV, "false")
        response = server.post(
            SERVERS,
            json={
                "radius_server": {
                    "name": "udp",
                    "hosts": [f"{LOOPBACK}:{freeradius.udp_port}"],
                    "transport": "udp",
                    "shared_secret": SECRET,
                }
            },
            headers=_root(),
        )
        assert response.status_code == 422
        assert ALLOW_UDP_ENV in response.text

    def test_a_udp_server_stops_signing_in_once_udp_is_disabled(
        self, server, set_env, freeradius
    ):
        created = self._udp(server, freeradius.udp_port)
        set_env(ALLOW_UDP_ENV, "false")
        response = self._login(server, created, "alice", "wonderland")
        assert response.status_code == 503
        assert SECRET not in response.text

    @pytest.mark.parametrize(
        "fields",
        [
            {"hosts": [], "transport": "udp", "shared_secret": SECRET},
            {"hosts": ["host:99999"], "transport": "udp", "shared_secret": SECRET},
            {"hosts": ["h"] * 9, "transport": "udp", "shared_secret": SECRET},
            {"hosts": ["h"], "transport": "udp", "shared_secret": "short"},
            {"hosts": ["h"], "transport": "carrier-pigeon"},
            {"hosts": ["h"], "transport": "udp", "shared_secret": SECRET, "retries": 9},
            {
                "hosts": ["h"],
                "transport": "udp",
                "shared_secret": SECRET,
                "timeout_seconds": 0,
            },
            {"hosts": ["h"]},
        ],
    )
    def test_unusable_settings_are_refused(self, server, fields):
        self._create_server(server, expect=422, **fields)

    def test_certificates_that_do_not_load_are_refused(self, server, certificates):
        self._create_server(
            server,
            expect=422,
            hosts=["h"],
            tls_ca_pem=certificates.ca_pem,
            tls_client_cert_pem="garbage",
            tls_client_key_pem=certificates.client_key_pem,
        )

    def test_an_update_is_checked_and_keeps_the_secret(self, server, freeradius):
        created = self._udp(server, freeradius.udp_port)
        path = f"{SERVERS}/{created['id']}"
        refused = server.put(
            path, json={"radius_server": {"hosts": []}}, headers=_root()
        )
        assert refused.status_code == 422
        renamed = server.put(
            path, json={"radius_server": {"name": "renamed"}}, headers=_root()
        )
        assert renamed.status_code == 200, renamed.text
        assert self._login(server, created, "alice", "wonderland").status_code == 200

    def test_the_sign_in_page_lists_enabled_servers_by_name_only(
        self, server, freeradius
    ):
        shown = self._udp(server, freeradius.udp_port)
        hidden = self._udp(server, freeradius.udp_port, is_enabled=False)
        response = server.get(CHOICES)
        assert response.status_code == 200
        choices = {s["id"]: s for s in response.json()["servers"]}
        assert choices[shown["id"]] == {"id": shown["id"], "name": shown["name"]}
        assert hidden["id"] not in choices
        assert str(freeradius.udp_port) not in response.text

    # -- signing in ------------------------------------------------------

    def test_an_accept_creates_the_account_and_signs_in(
        self, server, model_registry, freeradius
    ):
        radius_server = self._udp(server, freeradius.udp_port)
        response = self._login(server, radius_server, "alice", "wonderland")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["token"] and body["session_key"]
        assert "zx_session" in response.cookies
        me = server.get("/v1/user", headers=_bearer(body["token"]))
        assert me.status_code == 200, me.text
        assert me.json()["user"]["id"] == body["user"]["id"]
        [identity] = self._identities(model_registry, radius_server["id"])
        assert (identity.username, identity.user_id) == ("alice", body["user"]["id"])
        assert identity.last_login_at is not None

    def test_the_next_sign_in_reaches_the_same_account(self, server, freeradius):
        radius_server = self._udp(server, freeradius.udp_port)
        first = self._login(server, radius_server, "bob", "builder").json()
        second = self._login(server, radius_server, "bob", "builder").json()
        assert first["user"]["id"] == second["user"]["id"]
        assert first["session_key"] != second["session_key"]

    def test_the_identity_is_the_server_and_the_username(self, server, freeradius):
        """The same username on another server is someone else."""
        one = self._udp(server, freeradius.udp_port)
        other = self._udp(server, freeradius.udp_port)
        a = self._login(server, one, "carol", "singer").json()
        b = self._login(server, other, "carol", "singer").json()
        assert a["user"]["id"] != b["user"]["id"]

    def test_a_wrong_password_is_401(self, server, model_registry, freeradius):
        radius_server = self._udp(server, freeradius.udp_port)
        response = self._login(server, radius_server, "alice", "not-it")
        assert response.status_code == 401
        assert "zx_session" not in response.cookies
        assert self._identities(model_registry, radius_server["id"]) == []

    def test_repeated_failures_lock_the_address_out(self, server, freeradius):
        """The lockout password sign-in has, counted for RADIUS separately.
        The per-minute rate limit is reset between attempts so the 429 is
        the lockout's."""
        radius_server = self._udp(server, freeradius.udp_port)
        failures = UserManager._lockout_tracker.policy.failures_per_window
        for _ in range(failures):
            reset_rate_limit_counts()
            assert self._login(server, radius_server, "alice", "x").status_code == 401
        reset_rate_limit_counts()
        locked = self._login(server, radius_server, "alice", "wonderland")
        assert locked.status_code == 429
        assert "Too many failed attempts" in locked.text

    def test_a_wrong_shared_secret_signs_no_one_in(
        self, server, model_registry, freeradius
    ):
        radius_server = self._udp(
            server, freeradius.udp_port, shared_secret="not-the-shared-secret-at-all"
        )
        response = self._login(server, radius_server, "alice", "wonderland")
        assert response.status_code == 502
        assert "not-the-shared-secret" not in response.text
        assert self._identities(model_registry, radius_server["id"]) == []

    def test_an_accept_without_message_authenticator_signs_no_one_in(
        self, server, model_registry
    ):
        with HandCraftedResponder(
            SECRET.encode(), accept_without_message_authenticator
        ) as responder:
            radius_server = self._udp(server, responder.port)
            response = self._login(server, radius_server, "alice", "anything")
        assert response.status_code == 502
        assert "token" not in response.json()
        assert self._identities(model_registry, radius_server["id"]) == []

    def test_a_timed_out_server_fails_over_to_the_next(self, server, freeradius):
        with silent_udp_port() as silent:
            radius_server = self._udp(server, silent, freeradius.udp_port)
            response = self._login(server, radius_server, "alice", "wonderland")
        assert response.status_code == 200, response.text

    def test_no_server_answering_is_502(self, server):
        with silent_udp_port() as silent:
            radius_server = self._udp(server, silent)
            response = self._login(server, radius_server, "alice", "wonderland")
        assert response.status_code == 502

    def test_a_disabled_or_unknown_server_signs_no_one_in(self, server, freeradius):
        disabled = self._udp(server, freeradius.udp_port, is_enabled=False)
        assert self._login(server, disabled, "alice", "wonderland").status_code == 404
        unknown = {"id": str(uuid.uuid4())}
        assert self._login(server, unknown, "alice", "wonderland").status_code == 404

    def test_a_deleted_server_signs_no_one_in(self, server, freeradius):
        """Root's reads include soft-deleted rows; sign-in must not."""
        radius_server = self._udp(server, freeradius.udp_port)
        deleted = server.delete(f"{SERVERS}/{radius_server['id']}", headers=_root())
        assert deleted.status_code in (200, 204), deleted.text
        assert (
            self._login(server, radius_server, "alice", "wonderland").status_code == 404
        )
        assert radius_server["id"] not in server.get(CHOICES).text

    def test_a_deleted_identity_signs_no_one_in(
        self, server, set_env, admin_a, freeradius
    ):
        set_env("REGISTRATION_MODE", "closed")
        radius_server = self._udp(server, freeradius.udp_port)
        identity = self._link(server, radius_server, admin_a.id, "alice")
        assert (
            self._login(server, radius_server, "alice", "wonderland").status_code == 200
        )
        server.delete(f"{IDENTITIES}/{identity['id']}", headers=_root())
        assert (
            self._login(server, radius_server, "alice", "wonderland").status_code == 403
        )
        # Its username is free to link again.
        self._link(server, radius_server, admin_a.id, "alice")

    def test_radsec_signs_in(self, server, freeradius, certificates):
        radius_server = self._radsec(server, freeradius.radsec_port, certificates)
        response = self._login(server, radius_server, "alice", "wonderland")
        assert response.status_code == 200, response.text
        assert response.json()["token"]

    # -- challenges ------------------------------------------------------

    def _challenged(self, server: Any, radius_server: Dict[str, Any]) -> Any:
        response = self._login(server, radius_server, OTP_USER, OTP_FIRST_FACTOR)
        assert response.status_code == 200, response.text
        return response

    def test_a_challenge_then_the_code_signs_in(self, server, freeradius):
        radius_server = self._udp(server, freeradius.udp_port)
        challenged = self._challenged(server, radius_server)
        body = challenged.json()
        assert body["radius_challenge"] is True
        assert body["reply_message"] == OTP_PROMPT
        assert body["token"] is None
        assert "zx_session" not in challenged.cookies
        # The RADIUS State stays on this server.
        assert OTP_STATE_HEX not in challenged.text
        answered = server.post(
            CHALLENGE,
            json={
                "radius_challenge_token": body["radius_challenge_token"],
                "response": OTP_CODE,
            },
        )
        assert answered.status_code == 200, answered.text
        assert answered.json()["token"]
        assert "zx_session" in answered.cookies

    def test_a_challenge_is_answered_once(self, server, freeradius):
        radius_server = self._udp(server, freeradius.udp_port)
        token = self._challenged(server, radius_server).json()["radius_challenge_token"]
        answer = {"radius_challenge_token": token, "response": OTP_CODE}
        assert server.post(CHALLENGE, json=answer).status_code == 200
        assert server.post(CHALLENGE, json=answer).status_code == 401

    def test_a_wrong_code_is_401(self, server, freeradius):
        radius_server = self._udp(server, freeradius.udp_port)
        token = self._challenged(server, radius_server).json()["radius_challenge_token"]
        response = server.post(
            CHALLENGE, json={"radius_challenge_token": token, "response": "000000"}
        )
        assert response.status_code == 401

    def test_an_expired_challenge_is_401(self, server, model_registry, freeradius):
        radius_server = self._udp(server, freeradius.udp_port)
        token = self._challenged(server, radius_server).json()["radius_challenge_token"]
        Challenges = RadiusChallengeModel.DB(model_registry.DB.manager.Base)
        [held] = Challenges.list(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            radius_server_id=radius_server["id"],
            return_type="dto",
            override_dto=RadiusChallengeModel,
        )
        Challenges.update(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            id=held.id,
            new_properties={
                "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)
            },
        )
        response = server.post(
            CHALLENGE, json={"radius_challenge_token": token, "response": OTP_CODE}
        )
        assert response.status_code == 401

    def test_a_forged_challenge_token_is_401(self, server):
        response = server.post(
            CHALLENGE,
            json={"radius_challenge_token": "made-up", "response": OTP_CODE},
        )
        assert response.status_code == 401

    def test_the_answer_goes_to_the_host_that_asked(self, server, freeradius):
        with silent_udp_port() as silent:
            radius_server = self._udp(server, silent, freeradius.udp_port)
            token = self._challenged(server, radius_server).json()[
                "radius_challenge_token"
            ]
            answered = server.post(
                CHALLENGE, json={"radius_challenge_token": token, "response": OTP_CODE}
            )
        assert answered.status_code == 200, answered.text

    # -- registration modes and identities --------------------------------

    def test_closed_registration_signs_in_linked_identities_only(
        self, server, set_env, admin_b, freeradius
    ):
        set_env("REGISTRATION_MODE", "closed")
        radius_server = self._udp(server, freeradius.udp_port)
        refused = self._login(server, radius_server, "bob", "builder")
        assert refused.status_code == 403
        self._link(server, radius_server, admin_b.id, "bob")
        response = self._login(server, radius_server, "bob", "builder")
        assert response.status_code == 200, response.text
        assert response.json()["user"]["id"] == admin_b.id

    def test_invite_registration_does_not_create_accounts(
        self, server, set_env, freeradius
    ):
        set_env("REGISTRATION_MODE", "invite")
        radius_server = self._udp(server, freeradius.udp_port)
        assert (
            self._login(server, radius_server, "alice", "wonderland").status_code == 403
        )

    def test_a_server_may_refuse_to_create_accounts(self, server, freeradius):
        radius_server = self._udp(server, freeradius.udp_port, jit_create_users=False)
        assert (
            self._login(server, radius_server, "alice", "wonderland").status_code == 403
        )

    def test_a_deactivated_account_does_not_sign_in(
        self, server, model_registry, freeradius
    ):
        radius_server = self._udp(server, freeradius.udp_port)
        user_id = self._login(server, radius_server, "alice", "wonderland").json()[
            "user"
        ]["id"]
        UserModel.DB(model_registry.DB.manager.Base).update(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            id=user_id,
            new_properties={"active": False},
        )
        assert (
            self._login(server, radius_server, "alice", "wonderland").status_code == 401
        )

    def test_only_root_links_identities(self, server, admin_a, freeradius):
        radius_server = self._udp(server, freeradius.udp_port)
        response = self._link_response(
            server, radius_server, admin_a.id, "alice", _bearer(admin_a.jwt)
        )
        assert response.status_code in (403, 404)

    def test_a_username_links_to_one_account_per_server(
        self, server, admin_a, admin_b, freeradius
    ):
        radius_server = self._udp(server, freeradius.udp_port)
        self._link(server, radius_server, admin_a.id, "dave")
        again = self._link_response(server, radius_server, admin_b.id, "dave", _root())
        assert again.status_code == 409

    def test_an_identity_names_an_existing_server(self, server, admin_a):
        unknown = {"id": str(uuid.uuid4())}
        response = self._link_response(server, unknown, admin_a.id, "erin", _root())
        assert response.status_code == 404
