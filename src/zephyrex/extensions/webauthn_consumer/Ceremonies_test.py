# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ceremonies end to end, over the API, against a software
authenticator: registering, passkey sign-in, the MFA step, a user's own
credentials, and the abilities.

Holes these close (the extension had no protocol code at all): challenges
must be the server's, single use, unexpired and bound to the session, the
MFA token or the user they were issued for; an assertion must verify for
the rpId and an exact origin; a credential signs in only its own user; a
stale counter disables the credential; credentials are their owners'."""

import asyncio
import base64
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterator, Optional

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from conftest import CORE_COMPANION_EXTENSIONS
from zephyrex.extensions.webauthn_consumer.BLL_WebAuthnConsumer import (
    MFA_METHOD_TYPE,
    WebAuthnCeremonyModel,
    WebAuthnCredentialManager,
    WebAuthnCredentialModel,
)
from zephyrex.extensions.webauthn_consumer.EXT_WebAuthnConsumer import (
    EXT_WebAuthnConsumer,
)
from zephyrex.extensions.webauthn_consumer.SoftwareAuthenticator_test import (
    SoftwareAuthenticator,
    b64url,
)
from zephyrex.lib.Environment import env
from zephyrex.lib.SessionCookies import SESSION_COOKIE
from zephyrex.logic.BLL_Auth import UserModel
from zephyrex.testing.factories import (
    INTERNAL_ACCOUNTS,
    TEST_PASSWORD,
    authorize_user,
    create_user,
    current_if_match,
)

RP_ID = "example.test"
ORIGIN = "https://app.example.test"
CREDENTIALS = "/v1/webauthn/credential"


@pytest.fixture(scope="module")
def server() -> Iterator[TestClient]:
    from zephyrex.app import instance
    from zephyrex.pydantic2.sqlalchemy import prepare_test_registry

    prepare_test_registry()
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    app = instance(
        db_prefix=f"test.webauthn_consumer.{worker}",
        extensions=",".join(
            ["webauthn_consumer", "auth_mfa", *CORE_COMPANION_EXTENSIONS]
        ),
    )
    # Session cookies are Secure, so only an https client keeps them.
    yield TestClient(app, base_url="https://testserver")


@pytest.fixture(autouse=True)
def relying_party(set_env: Callable[[str, str], None]) -> None:
    for key, value in {
        "WEBAUTHN_CONSUMER_RP_ID": RP_ID,
        "WEBAUTHN_CONSUMER_ORIGINS": ORIGIN,
        "WEBAUTHN_CONSUMER_ATTESTATION_ROOTS": "",
        "WEBAUTHN_CONSUMER_USER_VERIFICATION": "preferred",
        "WEBAUTHN_CONSUMER_TIMEOUT_MS": "60000",
    }.items():
        set_env(key, value)


def bearer(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def new_user(server: TestClient) -> Any:
    return create_user(server, email=f"wa_{uuid.uuid4().hex[:10]}@example.com")


def registry(server: TestClient) -> Any:
    app: Any = server.app
    return app.state.model_registry


def begin_registration(server: TestClient, token: str) -> Dict[str, Any]:
    started = server.post(
        "/v1/webauthn/register/options", json={}, headers=bearer(token)
    )
    assert started.status_code == 200, started.text
    begun: Dict[str, Any] = started.json()
    return begun


def finish_registration(
    server: TestClient, token: str, ceremony_id: str, credential: Dict[str, Any]
) -> Any:
    return server.post(
        "/v1/webauthn/register/verify",
        json={
            "ceremony_id": ceremony_id,
            "credential": credential,
            "device_name": "Key",
        },
        headers=bearer(token),
    )


def register(
    server: TestClient, user: Any, authenticator: SoftwareAuthenticator, **create: Any
) -> Dict[str, Any]:
    begun = begin_registration(server, user.jwt)
    finished = finish_registration(
        server,
        user.jwt,
        begun["ceremony_id"],
        authenticator.create(begun["public_key"], ORIGIN, **create),
    )
    assert finished.status_code == 200, finished.text
    registered: Dict[str, Any] = finished.json()["credential"]
    return registered


def begin_sign_in(server: TestClient, email: Optional[str] = None) -> Dict[str, Any]:
    server.cookies.clear()  # a signed-out browser
    started = server.post("/v1/webauthn/authenticate/options", json={"email": email})
    assert started.status_code == 200, started.text
    begun: Dict[str, Any] = started.json()
    return begun


def finish_sign_in(
    server: TestClient, ceremony_id: str, credential: Dict[str, Any]
) -> Any:
    server.cookies.clear()
    return server.post(
        "/v1/webauthn/authenticate/verify",
        json={"ceremony_id": ceremony_id, "credential": credential},
    )


def sign_in(
    server: TestClient,
    authenticator: SoftwareAuthenticator,
    email: Optional[str] = None,
    **get: Any,
) -> Any:
    begun = begin_sign_in(server, email)
    return finish_sign_in(
        server,
        begun["ceremony_id"],
        authenticator.get(begun["public_key"], get.pop("origin", ORIGIN), **get),
    )


def stored_credential(server: TestClient, record_id: str) -> WebAuthnCredentialModel:
    CredentialDB = WebAuthnCredentialModel.DB(registry(server).DB.manager.Base)
    found = CredentialDB.list(
        requester_id=env("ROOT_ID"),
        model_registry=registry(server),
        filters=[CredentialDB.id == record_id],
        return_type="dto",
        override_dto=WebAuthnCredentialModel,
    )
    assert len(found) == 1
    credential: WebAuthnCredentialModel = found[0]
    return credential


class TestRegistration:
    def test_a_signed_in_user_registers_a_key(self, server) -> None:
        user = new_user(server)
        authenticator = SoftwareAuthenticator(transports=("usb", "nfc"))
        registered = register(server, user, authenticator)
        assert registered["user_id"] == user.id
        assert registered["credential_id"] == b64url(authenticator.credential_id)
        assert registered["transports"] == "usb nfc"
        assert registered["attestation_format"] == "none"
        assert registered["is_discoverable"] is True
        assert registered["device_name"] == "Key"
        listed = server.get(CREDENTIALS, headers=bearer(user.jwt))
        assert listed.status_code == 200, listed.text
        assert [c["id"] for c in listed.json()["web_authn_credentials"]] == [
            registered["id"]
        ]

    def test_the_options_are_the_users_and_exclude_their_keys(self, server) -> None:
        user = new_user(server)
        authenticator = SoftwareAuthenticator()
        register(server, user, authenticator)
        options = begin_registration(server, user.jwt)["public_key"]
        assert options["rp"]["id"] == RP_ID
        assert options["user"]["id"] == b64url(user.id.encode())
        assert options["user"]["name"] == user.username
        assert [c["id"] for c in options["excludeCredentials"]] == [
            b64url(authenticator.credential_id)
        ]

    def test_registration_needs_a_signed_in_user(self, server) -> None:
        server.cookies.clear()
        assert server.post("/v1/webauthn/register/options", json={}).status_code == 401

    def test_a_registration_ceremony_is_single_use(self, server) -> None:
        user = new_user(server)
        begun = begin_registration(server, user.jwt)
        credential = SoftwareAuthenticator().create(begun["public_key"], ORIGIN)
        first = finish_registration(server, user.jwt, begun["ceremony_id"], credential)
        assert first.status_code == 200, first.text
        replayed = finish_registration(
            server, user.jwt, begun["ceremony_id"], credential
        )
        assert replayed.status_code == 401
        assert "already used" in replayed.json()["detail"]["message"]

    def test_a_ceremony_is_bound_to_the_session_that_began_it(self, server) -> None:
        user = new_user(server)
        begun = begin_registration(server, user.jwt)
        other_session = authorize_user(server, user.email, TEST_PASSWORD)
        assert other_session != user.jwt
        credential = SoftwareAuthenticator().create(begun["public_key"], ORIGIN)
        refused = finish_registration(
            server, other_session, begun["ceremony_id"], credential
        )
        assert refused.status_code == 401
        finished = finish_registration(
            server, user.jwt, begun["ceremony_id"], credential
        )
        assert finished.status_code == 200, finished.text

    def test_another_users_ceremony_cannot_be_finished(self, server) -> None:
        owner, intruder = new_user(server), new_user(server)
        begun = begin_registration(server, owner.jwt)
        credential = SoftwareAuthenticator().create(begun["public_key"], ORIGIN)
        refused = finish_registration(
            server, intruder.jwt, begun["ceremony_id"], credential
        )
        assert refused.status_code == 401
        listed = server.get(CREDENTIALS, headers=bearer(intruder.jwt))
        assert listed.json()["web_authn_credentials"] == []

    def test_an_expired_ceremony_is_refused(self, server) -> None:
        user = new_user(server)
        begun = begin_registration(server, user.jwt)
        CeremonyDB = WebAuthnCeremonyModel.DB(registry(server).DB.manager.Base)
        CeremonyDB.update(
            requester_id=env("ROOT_ID"),
            model_registry=registry(server),
            id=begun["ceremony_id"],
            new_properties={
                "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)
            },
        )
        credential = SoftwareAuthenticator().create(begun["public_key"], ORIGIN)
        refused = finish_registration(
            server, user.jwt, begun["ceremony_id"], credential
        )
        assert refused.status_code == 401
        assert "expired" in refused.json()["detail"]["message"]

    def test_wrong_origin_and_rp_id_are_refused(self, server) -> None:
        user = new_user(server)
        for origin, rp_id in (("https://evil.example", None), (ORIGIN, "evil.example")):
            begun = begin_registration(server, user.jwt)
            credential = SoftwareAuthenticator().create(
                begun["public_key"], origin, rp_id=rp_id
            )
            refused = finish_registration(
                server, user.jwt, begun["ceremony_id"], credential
            )
            assert refused.status_code == 400
            assert "registration refused" in refused.json()["detail"]["message"]

    def test_a_key_registers_once(self, server) -> None:
        user = new_user(server)
        authenticator = SoftwareAuthenticator()
        register(server, user, authenticator)
        begun = begin_registration(server, user.jwt)
        again = finish_registration(
            server,
            user.jwt,
            begun["ceremony_id"],
            authenticator.create(begun["public_key"], ORIGIN),
        )
        assert again.status_code == 409


class TestCredentials:
    def test_a_credential_is_its_owners(self, server) -> None:
        owner, other = new_user(server), new_user(server)
        registered = register(server, owner, SoftwareAuthenticator())
        path = f"{CREDENTIALS}/{registered['id']}"
        assert server.get(path, headers=bearer(other.jwt)).status_code == 404
        assert server.delete(path, headers=bearer(other.jwt)).status_code in (403, 404)
        renamed = server.put(
            path,
            json={"web_authn_credential": {"device_name": "Mine"}},
            headers=bearer(other.jwt),
        )
        assert renamed.status_code in (403, 404)
        assert server.get(path, headers=bearer(owner.jwt)).status_code == 200

    def test_the_owner_renames_but_cannot_rewrite_the_key(self, server) -> None:
        owner = new_user(server)
        registered = register(server, owner, SoftwareAuthenticator())
        path = f"{CREDENTIALS}/{registered['id']}"
        renamed = server.put(
            path,
            json={
                "web_authn_credential": {
                    "device_name": "Laptop",
                    "public_key": "AAAA",
                    "is_enabled": True,
                    "user_id": str(uuid.uuid4()),
                }
            },
            headers={
                **bearer(owner.jwt),
                **current_if_match(server, path, bearer(owner.jwt)),
            },
        )
        stored = stored_credential(server, registered["id"])
        assert stored.public_key == registered["public_key"]
        assert stored.user_id == owner.id
        if renamed.status_code == 200:
            assert stored.device_name == "Laptop"
        else:
            assert renamed.status_code == 422, renamed.text

    def test_create_forces_the_owner_singly_and_in_a_batch(self, server) -> None:
        owner, victim = new_user(server), new_user(server)
        manager = WebAuthnCredentialManager(
            requester_id=owner.id, model_registry=registry(server)
        )
        fields = {
            "public_key": "AAAA",
            "attestation_format": "none",
            "attestation_trust": "none",
            "user_id": victim.id,
        }
        single = manager.create(credential_id=uuid.uuid4().hex, **fields)
        batch = manager.create(
            entities=[{"credential_id": uuid.uuid4().hex, **fields} for _ in range(2)]
        )
        assert {c.user_id for c in [single, *batch]} == {owner.id}

    @pytest.mark.parametrize("internal", INTERNAL_ACCOUNTS)
    def test_no_credential_is_registered_to_an_internal_account(
        self, server, internal
    ) -> None:
        manager = WebAuthnCredentialManager(
            requester_id=env("ROOT_ID"), model_registry=registry(server)
        )
        with pytest.raises(HTTPException) as refused:
            manager.create(
                credential_id=uuid.uuid4().hex,
                public_key="AAAA",
                attestation_format="none",
                attestation_trust="none",
                user_id=env(internal),
            )
        assert refused.value.status_code == 403

    def test_a_credential_of_roots_signs_no_one_in(self, server) -> None:
        """A credential moved to ROOT beneath the manager (by an older
        version, or directly), asserting ROOT's handle, issues no session."""
        authenticator = SoftwareAuthenticator()
        registered = register(server, new_user(server), authenticator)
        WebAuthnCredentialModel.DB(registry(server).DB.manager.Base).update(
            requester_id=env("ROOT_ID"),
            model_registry=registry(server),
            id=registered["id"],
            new_properties={"user_id": env("ROOT_ID")},
        )
        authenticator.user_handle = env("ROOT_ID").encode("utf-8")
        refused = sign_in(server, authenticator)
        assert refused.status_code == 403, refused.text
        assert SESSION_COOKIE not in server.cookies

    def test_a_removed_credential_no_longer_signs_in(self, server) -> None:
        user = new_user(server)
        authenticator = SoftwareAuthenticator()
        registered = register(server, user, authenticator)
        path = f"{CREDENTIALS}/{registered['id']}"
        removed = server.delete(
            path,
            headers={
                **bearer(user.jwt),
                **current_if_match(server, path, bearer(user.jwt)),
            },
        )
        assert removed.status_code in (200, 204), removed.text
        assert sign_in(server, authenticator).status_code == 401


class TestSignIn:
    def test_a_passkey_signs_in_without_a_username(self, server) -> None:
        user = new_user(server)
        authenticator = SoftwareAuthenticator()
        registered = register(server, user, authenticator)
        begun = begin_sign_in(server)
        assert begun["public_key"]["userVerification"] == "required"
        assert begun["public_key"].get("allowCredentials", []) == []
        signed_in = finish_sign_in(
            server,
            begun["ceremony_id"],
            authenticator.get(begun["public_key"], ORIGIN),
        )
        assert signed_in.status_code == 200, signed_in.text
        body = signed_in.json()
        assert body["user"]["id"] == user.id and body["session_key"]
        assert server.cookies[SESSION_COOKIE] == body["token"]
        server.cookies.clear()
        me = server.get("/v1/user", headers=bearer(body["token"]))
        assert me.status_code == 200, me.text
        used = stored_credential(server, registered["id"])
        assert used.sign_count == 1 and used.last_used_at is not None

    def test_sign_in_by_email_offers_only_that_users_keys(self, server) -> None:
        user = new_user(server)
        authenticator = SoftwareAuthenticator()
        register(server, user, authenticator)
        begun = begin_sign_in(server, user.email)
        assert [c["id"] for c in begun["public_key"]["allowCredentials"]] == [
            b64url(authenticator.credential_id)
        ]
        signed_in = finish_sign_in(
            server, begun["ceremony_id"], authenticator.get(begun["public_key"], ORIGIN)
        )
        assert signed_in.status_code == 200, signed_in.text
        assert signed_in.json()["user"]["id"] == user.id

    def test_an_unknown_account_gets_a_stable_stand_in(self, server) -> None:
        def offered(email: str) -> Any:
            return begin_sign_in(server, email)["public_key"]["allowCredentials"]

        nobody = f"nobody_{uuid.uuid4().hex[:8]}@example.com"
        assert offered(nobody) == offered(nobody)
        assert len(offered(nobody)) == 1
        assert offered(nobody) != offered(f"x{nobody}")
        keyless = new_user(server)
        assert len(offered(keyless.email)) == 1

    def test_sign_in_requires_user_verification(self, server) -> None:
        user = new_user(server)
        authenticator = SoftwareAuthenticator()
        register(server, user, authenticator)
        refused = sign_in(server, authenticator, user_verified=False)
        assert refused.status_code == 401
        assert "verified" in refused.json()["detail"]["message"]

    def test_a_replayed_sign_in_is_refused(self, server) -> None:
        user = new_user(server)
        authenticator = SoftwareAuthenticator()
        register(server, user, authenticator)
        begun = begin_sign_in(server)
        assertion = authenticator.get(begun["public_key"], ORIGIN)
        assert (
            finish_sign_in(server, begun["ceremony_id"], assertion).status_code == 200
        )
        replayed = finish_sign_in(server, begun["ceremony_id"], assertion)
        assert replayed.status_code == 401
        assert SESSION_COOKIE not in server.cookies

    def test_an_assertion_over_another_challenge_is_refused(self, server) -> None:
        user = new_user(server)
        authenticator = SoftwareAuthenticator()
        register(server, user, authenticator)
        first, second = begin_sign_in(server), begin_sign_in(server)
        answer_to_first = authenticator.get(first["public_key"], ORIGIN)
        refused = finish_sign_in(server, second["ceremony_id"], answer_to_first)
        assert refused.status_code == 401
        assert "challenge" in refused.json()["detail"]["message"]

    def test_a_bad_signature_is_refused(self, server) -> None:
        user = new_user(server)
        authenticator = SoftwareAuthenticator()
        register(server, user, authenticator)
        refused = sign_in(server, authenticator, corrupt_signature=True)
        assert refused.status_code == 401
        assert "signature" in refused.json()["detail"]["message"]

    def test_wrong_origin_and_rp_id_are_refused(self, server) -> None:
        user = new_user(server)
        authenticator = SoftwareAuthenticator()
        register(server, user, authenticator)
        for get, reason in (
            ({"origin": "https://evil.example"}, "origin"),
            ({"rp_id": "evil.example"}, "RP ID"),
        ):
            refused = sign_in(server, authenticator, **get)
            assert refused.status_code == 401
            assert reason in refused.json()["detail"]["message"]

    def test_another_users_credential_is_refused(self, server) -> None:
        owner, target = new_user(server), new_user(server)
        owners_key = SoftwareAuthenticator()
        register(server, owner, owners_key)
        register(server, target, SoftwareAuthenticator())
        refused = sign_in(server, owners_key, email=target.email)
        assert refused.status_code == 401
        assert "unknown credential" in refused.json()["detail"]["message"]

    def test_a_user_handle_naming_someone_else_is_refused(self, server) -> None:
        owner, target = new_user(server), new_user(server)
        authenticator = SoftwareAuthenticator()
        register(server, owner, authenticator)
        refused = sign_in(server, authenticator, user_handle=target.id.encode())
        assert refused.status_code == 401

    def test_a_counter_regression_disables_the_credential(self, server) -> None:
        user = new_user(server)
        authenticator = SoftwareAuthenticator()
        registered = register(server, user, authenticator)
        authenticator.sign_count = 6
        assert sign_in(server, authenticator).status_code == 200
        clone = SoftwareAuthenticator(
            credential_id=authenticator.credential_id,
            user_handle=authenticator.user_handle,
            sign_count=2,
        )
        clone.private_key = authenticator.private_key
        refused = sign_in(server, clone)
        assert refused.status_code == 401
        assert "cloned" in refused.json()["detail"]["message"]
        flagged = stored_credential(server, registered["id"])
        assert flagged.is_enabled is False and flagged.clone_detected_at is not None
        assert flagged.sign_count == 7
        # Not even the genuine authenticator signs in with it now.
        assert sign_in(server, authenticator).status_code == 401

    def test_a_disabled_account_cannot_sign_in(self, server) -> None:
        user = new_user(server)
        authenticator = SoftwareAuthenticator()
        register(server, user, authenticator)
        UserModel.DB(registry(server).DB.manager.Base).update(
            requester_id=env("ROOT_ID"),
            model_registry=registry(server),
            id=user.id,
            new_properties={"active": False},
        )
        assert sign_in(server, authenticator).status_code == 401


class TestSecondFactor:
    """A password login that yields an MFA challenge, completed with the
    user's registered key."""

    @pytest.fixture
    def mfa_user(self, server) -> Any:
        pyotp = pytest.importorskip("pyotp")
        from zephyrex.extensions.auth_mfa.BLL_Auth_MFA import (
            MultifactorMethodManager,
            MultifactorMethodType,
        )

        user = new_user(server)
        manager = MultifactorMethodManager(
            requester_id=user.id, model_registry=registry(server)
        )
        method = manager.create(method_type=MultifactorMethodType.TOTP)
        totp = pyotp.TOTP(manager.totp_provisioning_route(method.id)["secret"])
        assert manager.verify_mfa_code(method.id, totp.now())
        return user

    @staticmethod
    def _password_login(server: TestClient, user: Any) -> str:
        server.cookies.clear()
        basic = base64.b64encode(f"{user.email}:{TEST_PASSWORD}".encode()).decode()
        login = server.post(
            "/v1/user/authorize", headers={"Authorization": f"Basic {basic}"}
        )
        assert login.status_code == 200, login.text
        assert login.json()["mfa_required"] is True
        token: str = login.json()["challenge_token"]
        return token

    @staticmethod
    def _answer(
        server: TestClient,
        challenge_token: str,
        authenticator: SoftwareAuthenticator,
    ) -> Any:
        server.cookies.clear()
        started = server.post(
            "/v1/webauthn/mfa/options", json={"challenge_token": challenge_token}
        )
        assert started.status_code == 200, started.text
        begun = started.json()
        return server.post(
            "/v1/webauthn/mfa/verify",
            json={
                "challenge_token": challenge_token,
                "ceremony_id": begun["ceremony_id"],
                "credential": authenticator.get(
                    begun["public_key"], ORIGIN, user_verified=False
                ),
            },
        )

    def test_a_key_alone_is_a_second_factor(self, server) -> None:
        """A user whose only second factor is a key is challenged at
        password login, finishes with the key, and no code stands in."""
        user = new_user(server)
        authenticator = SoftwareAuthenticator()
        registered = register(server, user, authenticator)
        server.cookies.clear()
        basic = base64.b64encode(f"{user.email}:{TEST_PASSWORD}".encode()).decode()
        login = server.post(
            "/v1/user/authorize", headers={"Authorization": f"Basic {basic}"}
        )
        assert login.status_code == 200, login.text
        body = login.json()
        assert body["mfa_required"] is True and not body.get("token")
        assert body["methods"] == [
            {"id": registered["id"], "method_type": MFA_METHOD_TYPE}
        ]
        challenge = body["challenge_token"]
        coded = server.post(
            "/v1/user/authorize/mfa",
            json={"challenge_token": challenge, "code": "000000"},
        )
        assert coded.status_code == 401, coded.text
        completed = self._answer(server, challenge, authenticator)
        assert completed.status_code == 200, completed.text
        assert completed.json()["user"]["id"] == user.id

    def test_a_removed_key_no_longer_challenges(self, server) -> None:
        user = new_user(server)
        registered = register(server, user, SoftwareAuthenticator())
        path = f"{CREDENTIALS}/{registered['id']}"
        removed = server.delete(
            path,
            headers={
                **bearer(user.jwt),
                **current_if_match(server, path, bearer(user.jwt)),
            },
        )
        assert removed.status_code in (200, 204), removed.text
        assert authorize_user(server, user.email, TEST_PASSWORD)

    def test_the_challenge_lists_codes_and_keys(self, server, mfa_user) -> None:
        registered = register(server, mfa_user, SoftwareAuthenticator())
        server.cookies.clear()
        basic = base64.b64encode(f"{mfa_user.email}:{TEST_PASSWORD}".encode()).decode()
        login = server.post(
            "/v1/user/authorize", headers={"Authorization": f"Basic {basic}"}
        )
        methods = login.json()["methods"]
        assert {"id": registered["id"], "method_type": MFA_METHOD_TYPE} in methods
        assert any(m["method_type"] != MFA_METHOD_TYPE for m in methods)

    def test_a_key_completes_the_mfa_login_once(self, server, mfa_user) -> None:
        authenticator = SoftwareAuthenticator()
        register(server, mfa_user, authenticator)
        challenge = self._password_login(server, mfa_user)
        completed = self._answer(server, challenge, authenticator)
        assert completed.status_code == 200, completed.text
        assert completed.json()["user"]["id"] == mfa_user.id
        assert server.cookies[SESSION_COOKIE] == completed.json()["token"]
        replayed = self._answer(server, challenge, authenticator)
        assert replayed.status_code == 401
        assert "already used" in replayed.json()["detail"]["message"]

    def test_another_users_key_does_not_answer(self, server, mfa_user) -> None:
        own_key = SoftwareAuthenticator()
        register(server, mfa_user, own_key)
        stranger = new_user(server)
        strangers_key = SoftwareAuthenticator()
        register(server, stranger, strangers_key)
        challenge = self._password_login(server, mfa_user)
        refused = self._answer(server, challenge, strangers_key)
        assert refused.status_code == 401
        assert "unknown credential" in refused.json()["detail"]["message"]
        # The refusal did not spend the challenge token.
        completed = self._answer(server, challenge, own_key)
        assert completed.status_code == 200, completed.text
        assert completed.json()["user"]["id"] == mfa_user.id

    def test_required_user_verification_applies(
        self, server, mfa_user, set_env
    ) -> None:
        authenticator = SoftwareAuthenticator()
        register(server, mfa_user, authenticator)
        set_env("WEBAUTHN_CONSUMER_USER_VERIFICATION", "required")
        refused = self._answer(
            server, self._password_login(server, mfa_user), authenticator
        )
        assert refused.status_code == 401
        assert "verified" in refused.json()["detail"]["message"]

    def test_a_forged_challenge_token_is_refused(self, server) -> None:
        server.cookies.clear()
        started = server.post(
            "/v1/webauthn/mfa/options", json={"challenge_token": "not-a-token"}
        )
        assert started.status_code == 401

    def test_an_account_without_keys_is_refused(self, server, mfa_user) -> None:
        server.cookies.clear()
        started = server.post(
            "/v1/webauthn/mfa/options",
            json={"challenge_token": self._password_login(server, mfa_user)},
        )
        assert started.status_code == 401


class TestAbilities:
    def test_list_and_remove_act_on_the_requesters_own_keys(self, server) -> None:
        owner, other = new_user(server), new_user(server)
        mine = register(server, owner, SoftwareAuthenticator())
        theirs = register(server, other, SoftwareAuthenticator())
        listed = asyncio.run(EXT_WebAuthnConsumer.list_credentials(owner.id))
        assert [c["id"] for c in listed] == [mine["id"]]
        with pytest.raises(HTTPException) as refused:
            asyncio.run(EXT_WebAuthnConsumer.remove_credential(owner.id, theirs["id"]))
        assert refused.value.status_code in (403, 404)
        asyncio.run(EXT_WebAuthnConsumer.remove_credential(owner.id, mine["id"]))
        assert asyncio.run(EXT_WebAuthnConsumer.list_credentials(owner.id)) == []

    def test_an_ability_needs_a_requester(self) -> None:
        with pytest.raises(HTTPException) as refused:
            asyncio.run(EXT_WebAuthnConsumer.list_credentials(""))
        assert refused.value.status_code == 400


def test_challenges_expire_with_the_configured_timeout(server, set_env) -> None:
    set_env("WEBAUTHN_CONSUMER_TIMEOUT_MS", "5000")
    before = time.time()
    begun = begin_sign_in(server)
    expires = datetime.fromisoformat(begun["expires_at"]).timestamp()
    assert before + 4 <= expires <= time.time() + 6
    assert begun["public_key"]["timeout"] == 5000
