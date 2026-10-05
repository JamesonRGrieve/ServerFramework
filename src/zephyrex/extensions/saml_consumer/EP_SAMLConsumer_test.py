# SPDX-License-Identifier: AGPL-3.0-or-later
"""Signing in through a SAML IdP over HTTP, against a real IdP (pysaml2's
server): IdP administration, the SP-initiated round trip, the session it
issues, and each refusal (replay, foreign or missing request, unsolicited,
bad signature, account rules)."""

import uuid
from http.cookies import SimpleCookie
from typing import Any, Callable, Dict, Iterator, Optional, Tuple

import pytest
from fastapi.testclient import TestClient
from saml2.saml import NAMEID_FORMAT_PERSISTENT, NAMEID_FORMAT_TRANSIENT

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.saml_consumer.BLL_SAMLConsumer import (
    GRANT_TYPE,
    REQUEST_COOKIE,
    ROUTE_PREFIX,
    SamlIdentityManager,
    SamlIdentityModel,
    SamlIdentityProviderModel,
)
from zephyrex.extensions.saml_consumer.EXT_SAMLConsumer import EXT_SAMLConsumer
from zephyrex.extensions.saml_consumer.LocalIdP_test import LocalIdP, encoded, key_pair
from zephyrex.lib.Environment import env
from zephyrex.lib.SessionCookies import SESSION_COOKIE
from zephyrex.logic.BLL_Auth import SessionModel, UserManager, UserModel, UserTeamModel
from zephyrex.testing.factories import (
    INTERNAL_ACCOUNTS,
    create_user,
    current_if_match,
    if_match_of,
    internal_account_email,
)

SP_KEY, SP_CERT = key_pair("zephyrex-sp")
RETURN_TO = "/signed-in"


def root_headers() -> Dict[str, str]:
    return {"X-API-Key": env("ROOT_API_KEY")}


def registry_of(server: TestClient) -> Any:
    app: Any = server.app
    return app.state.model_registry


def new_email(prefix: str = "saml") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}@example.test"


def cookies_set(response: Any) -> Dict[str, str]:
    jar: SimpleCookie = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        jar.load(header)
    return {name: morsel.value for name, morsel in jar.items()}


class Browser:
    """One browser: its own cookie (the request cookie the server set),
    talking to the app with redirects left to the test."""

    def __init__(self, server: TestClient) -> None:
        self.client = TestClient(server.app)
        self.request_cookie: Optional[str] = None

    def login(self, idp_id: str, return_to: Optional[str] = RETURN_TO) -> Any:
        params = {"return_to": return_to} if return_to is not None else {}
        response = self.client.get(
            f"{ROUTE_PREFIX}/{idp_id}/login", params=params, follow_redirects=False
        )
        if response.status_code == 303:
            self.request_cookie = cookies_set(response)[REQUEST_COOKIE]
        return response

    def post(
        self, idp_id: str, saml_response: str, relay_state: Optional[str] = None
    ) -> Any:
        headers = (
            {"Cookie": f"{REQUEST_COOKIE}={self.request_cookie}"}
            if self.request_cookie
            else {}
        )
        return self.client.post(
            f"{ROUTE_PREFIX}/{idp_id}/acs",
            json={"SAMLResponse": saml_response, "RelayState": relay_state},
            headers=headers,
            follow_redirects=False,
        )


@pytest.fixture(scope="module")
def idp() -> Iterator[LocalIdP]:
    local = LocalIdP()
    yield local
    local.close()


class TestSAMLSignIn(ExtensionServerMixin):
    extension_class = EXT_SAMLConsumer

    # -- helpers --------------------------------------------------------------

    def add_idp(
        self, server: TestClient, idp: LocalIdP, **options: Any
    ) -> Dict[str, Any]:
        body = {
            "name": f"Corporate SSO {uuid.uuid4().hex[:6]}",
            "metadata_xml": idp.metadata_xml,
            "sp_private_key": SP_KEY,
            "sp_certificate": SP_CERT,
            "emails_verified": True,
            **options,
        }
        response = server.post(
            f"{ROUTE_PREFIX}/import", json=body, headers=root_headers()
        )
        assert response.status_code == 200, response.text
        summary: Dict[str, Any] = response.json()
        return summary

    def sp_metadata(self, server: TestClient, idp_id: str) -> str:
        response = server.get(f"{ROUTE_PREFIX}/{idp_id}/metadata")
        assert response.status_code == 200, response.text
        return response.text

    def answer(
        self,
        server: TestClient,
        idp: LocalIdP,
        summary: Dict[str, Any],
        in_response_to: Optional[str],
        **kwargs: Any,
    ) -> str:
        """The IdP's response for this SP, base64 as the form posts it."""
        return encoded(
            idp.response_xml(
                self.sp_metadata(server, summary["id"]),
                sp_entity_id=summary["sp_entity_id"],
                acs_url=summary["acs_url"],
                in_response_to=in_response_to,
                **kwargs,
            )
        )

    def round_trip(
        self, server: TestClient, idp: LocalIdP, summary: Dict[str, Any], **kwargs: Any
    ) -> Tuple[Browser, str, Any]:
        """Start a sign-in, let the IdP read the request (checking its
        signature) and answer it; the browser, its response and the ACS's
        answer."""
        browser = Browser(server)
        started = browser.login(summary["id"])
        assert started.status_code == 303, started.text
        request = idp.read_request(
            self.sp_metadata(server, summary["id"]), started.headers["location"]
        )
        saml_response = self.answer(server, idp, summary, request.id, **kwargs)
        return browser, saml_response, browser.post(summary["id"], saml_response)

    def signed_in_user(self, server: TestClient, finished: Any) -> UserModel:
        assert finished.status_code == 303, finished.text
        token = cookies_set(finished)[SESSION_COOKIE]
        registry = registry_of(server)
        user: UserModel = UserManager.auth(
            model_registry=registry, authorization=f"Bearer {token}"
        )
        return user

    def refusal(self, response: Any) -> str:
        assert response.status_code == 401, response.text
        detail = response.json()["detail"]
        message = detail["message"] if isinstance(detail, dict) else detail
        return str(message).rsplit(": ", 1)[-1]

    # -- administration ---------------------------------------------------------

    def test_the_sp_key_is_stored_encrypted_and_never_returned(
        self, server: TestClient, idp: LocalIdP
    ) -> None:
        summary = self.add_idp(server, idp)
        assert summary["entity_id"] == idp.entity_id
        assert summary["sp_key_configured"] is True
        assert summary["acs_url"].endswith(f"{ROUTE_PREFIX}/{summary['id']}/acs")
        read = server.get(f"{ROUTE_PREFIX}/{summary['id']}", headers=root_headers())
        assert read.status_code == 200, read.text
        assert "sp_private_key" not in read.text and "PRIVATE KEY" not in read.text
        assert "sp_certificate" not in read.json()["saml_identity_provider"]
        registry = registry_of(server)
        stored = SamlIdentityProviderModel.DB(registry.DB.manager.Base).get(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            id=summary["id"],
            return_type="dto",
            override_dto=SamlIdentityProviderModel,
        )
        assert stored.sp_private_key.startswith("fernet:")

    def test_a_user_cannot_add_or_change_an_idp(
        self, server: TestClient, idp: LocalIdP, admin_a: Any
    ) -> None:
        user_auth = {"Authorization": f"Bearer {admin_a.jwt}"}
        body = {"name": "Mine", "metadata_xml": idp.metadata_xml}
        assert (
            server.post(
                f"{ROUTE_PREFIX}/import", json=body, headers=user_auth
            ).status_code
            == 403
        )
        created = server.post(
            ROUTE_PREFIX, json={"saml_identity_provider": body}, headers=user_auth
        )
        assert created.status_code in (401, 403), created.text
        summary = self.add_idp(server, idp)
        # The user names the provider's current version: the refusal is for
        # who they are, not for a missing one.
        changed = server.put(
            f"{ROUTE_PREFIX}/{summary['id']}",
            json={"saml_identity_provider": {"emails_verified": True}},
            headers={
                **user_auth,
                **current_if_match(
                    server, f"{ROUTE_PREFIX}/{summary['id']}", root_headers()
                ),
            },
        )
        assert changed.status_code in (401, 403), changed.text

    def test_metadata_that_is_not_an_idp_is_refused(
        self, server: TestClient, idp: LocalIdP
    ) -> None:
        summary = self.add_idp(server, idp)
        response = server.post(
            f"{ROUTE_PREFIX}/import",
            json={
                "name": "Not an IdP",
                "metadata_xml": self.sp_metadata(server, summary["id"]),
            },
            headers=root_headers(),
        )
        assert response.status_code == 400, response.text

    def test_a_key_without_its_certificate_is_refused(
        self, server: TestClient, idp: LocalIdP
    ) -> None:
        response = server.post(
            f"{ROUTE_PREFIX}/import",
            json={
                "name": "Half a key",
                "metadata_xml": idp.metadata_xml,
                "sp_private_key": SP_KEY,
            },
            headers=root_headers(),
        )
        assert response.status_code == 400, response.text

    def test_metadata_is_imported_from_its_url_and_refreshed(
        self, server: TestClient, idp: LocalIdP, local_http_server: Callable[..., Any]
    ) -> None:
        metadata_host = local_http_server(
            {
                "/idp/metadata": (
                    200,
                    {"Content-Type": "application/samlmetadata+xml"},
                    idp.metadata_xml.encode(),
                )
            }
        )
        url = f"{metadata_host.base_url}/idp/metadata"
        response = server.post(
            f"{ROUTE_PREFIX}/import",
            json={"name": "From URL", "metadata_url": url},
            headers=root_headers(),
        )
        assert response.status_code == 200, response.text
        summary = response.json()
        assert summary["entity_id"] == idp.entity_id and summary["metadata_url"] == url
        refreshed = server.post(
            f"{ROUTE_PREFIX}/{summary['id']}/refresh", json={}, headers=root_headers()
        )
        assert refreshed.status_code == 200, refreshed.text
        assert len(metadata_host.requests) == 2

    def test_metadata_is_not_fetched_over_plain_http_from_an_unlisted_host(
        self, server: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EGRESS_ALLOWED_HOSTS", "")
        response = server.post(
            f"{ROUTE_PREFIX}/import",
            json={"name": "Plain", "metadata_url": "http://idp.example.test/metadata"},
            headers=root_headers(),
        )
        assert response.status_code == 400, response.text
        assert "https" in response.text

    def test_the_public_listing_names_enabled_idps_only(
        self, server: TestClient, idp: LocalIdP
    ) -> None:
        shown = self.add_idp(server, idp)
        hidden = self.add_idp(server, idp, is_enabled=False)
        listed = server.get(ROUTE_PREFIX)
        assert listed.status_code == 200, listed.text
        ids = [entry["id"] for entry in listed.json()["identity_providers"]]
        assert shown["id"] in ids and hidden["id"] not in ids
        assert (
            server.get(
                f"{ROUTE_PREFIX}/{hidden['id']}/login", follow_redirects=False
            ).status_code
            == 404
        )

    def test_the_sp_metadata_describes_this_sp(
        self, server: TestClient, idp: LocalIdP
    ) -> None:
        summary = self.add_idp(server, idp)
        metadata = self.sp_metadata(server, summary["id"])
        assert f'entityID="{summary["sp_entity_id"]}"' in metadata
        assert (
            summary["acs_url"] in metadata
            and "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" in metadata
        )

    # -- signing in -------------------------------------------------------------

    def test_a_round_trip_signs_a_new_user_in_with_a_session(
        self, server: TestClient, idp: LocalIdP
    ) -> None:
        summary = self.add_idp(server, idp)
        email = new_email()
        _, _, finished = self.round_trip(server, idp, summary, name_id=email)
        assert finished.headers["location"] == RETURN_TO
        user = self.signed_in_user(server, finished)
        assert user.email == email
        token = cookies_set(finished)[SESSION_COOKIE]
        registry = registry_of(server)
        sessions = SessionModel.DB(registry.DB.manager.Base).list(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            session_key=UserManager._decode_jwt(token)["jti"],
            return_type="dto",
            override_dto=SessionModel,
        )
        assert [s.grant_type for s in sessions] == [GRANT_TYPE]
        identities = TestClient(server.app).get(
            "/v1/auth/saml/identity", headers={"Authorization": f"Bearer {token}"}
        )
        assert identities.status_code == 200, identities.text
        [identity] = identities.json()["saml_identities"]
        assert (
            identity["subject"] == email and identity["idp_entity_id"] == idp.entity_id
        )

    def test_the_same_identity_signs_in_as_the_same_user(
        self, server: TestClient, idp: LocalIdP
    ) -> None:
        summary = self.add_idp(server, idp)
        email = new_email()
        first = self.signed_in_user(
            server, self.round_trip(server, idp, summary, name_id=email)[2]
        )
        second = self.signed_in_user(
            server, self.round_trip(server, idp, summary, name_id=email)[2]
        )
        assert first.id == second.id

    def test_a_replayed_response_is_refused(
        self, server: TestClient, idp: LocalIdP
    ) -> None:
        summary = self.add_idp(server, idp)
        browser, saml_response, finished = self.round_trip(
            server, idp, summary, name_id=new_email()
        )
        assert finished.status_code == 303, finished.text
        assert self.refusal(browser.post(summary["id"], saml_response)) == "replayed"

    def test_a_response_without_this_browsers_request_cookie_is_refused(
        self, server: TestClient, idp: LocalIdP
    ) -> None:
        summary = self.add_idp(server, idp)
        browser = Browser(server)
        started = browser.login(summary["id"])
        request = idp.read_request(
            self.sp_metadata(server, summary["id"]), started.headers["location"]
        )
        saml_response = self.answer(
            server, idp, summary, request.id, name_id=new_email()
        )
        assert (
            self.refusal(Browser(server).post(summary["id"], saml_response))
            == "in_response_to"
        )

    def test_a_response_to_another_browsers_request_is_refused(
        self, server: TestClient, idp: LocalIdP
    ) -> None:
        summary = self.add_idp(server, idp)
        victim, attacker = Browser(server), Browser(server)
        victim.login(summary["id"])
        started = attacker.login(summary["id"])
        request = idp.read_request(
            self.sp_metadata(server, summary["id"]), started.headers["location"]
        )
        saml_response = self.answer(
            server, idp, summary, request.id, name_id=new_email("attacker")
        )
        assert (
            self.refusal(victim.post(summary["id"], saml_response)) == "in_response_to"
        )

    def test_an_unsolicited_response_is_refused_unless_allowed(
        self, server: TestClient, idp: LocalIdP
    ) -> None:
        summary = self.add_idp(server, idp)
        saml_response = self.answer(server, idp, summary, None, name_id=new_email())
        assert (
            self.refusal(Browser(server).post(summary["id"], saml_response))
            == "unsolicited"
        )

    def test_an_allowed_unsolicited_response_signs_in_once(
        self, server: TestClient, idp: LocalIdP
    ) -> None:
        summary = self.add_idp(server, idp, allow_unsolicited=True)
        saml_response = self.answer(server, idp, summary, None, name_id=new_email())
        finished = Browser(server).post(
            summary["id"], saml_response, relay_state="/dashboard"
        )
        assert finished.headers["location"] == "/dashboard"
        self.signed_in_user(server, finished)
        assert (
            self.refusal(Browser(server).post(summary["id"], saml_response))
            == "replayed"
        )

    def test_a_relay_state_off_site_is_not_followed(
        self, server: TestClient, idp: LocalIdP
    ) -> None:
        summary = self.add_idp(server, idp, allow_unsolicited=True)
        saml_response = self.answer(server, idp, summary, None, name_id=new_email())
        finished = Browser(server).post(
            summary["id"], saml_response, relay_state="https://evil.example.test/"
        )
        assert finished.status_code == 303 and finished.headers["location"] == "/"

    def test_a_tampered_response_is_refused(
        self, server: TestClient, idp: LocalIdP
    ) -> None:
        summary = self.add_idp(server, idp)
        browser = Browser(server)
        started = browser.login(summary["id"])
        request = idp.read_request(
            self.sp_metadata(server, summary["id"]), started.headers["location"]
        )
        xml = idp.response_xml(
            self.sp_metadata(server, summary["id"]),
            sp_entity_id=summary["sp_entity_id"],
            acs_url=summary["acs_url"],
            in_response_to=request.id,
            name_id="victim@example.test",
        ).replace("victim@example.test", "mallory@example.test")
        assert (
            self.refusal(browser.post(summary["id"], encoded(xml))) == "bad_signature"
        )

    def test_a_response_for_another_sp_is_refused(
        self, server: TestClient, idp: LocalIdP
    ) -> None:
        mine, other = self.add_idp(server, idp), self.add_idp(
            server, idp, allow_unsolicited=True
        )
        saml_response = self.answer(server, idp, mine, None, name_id=new_email())
        assert (
            self.refusal(Browser(server).post(other["id"], saml_response))
            == "destination"
        )

    def test_return_to_off_site_is_refused(
        self, server: TestClient, idp: LocalIdP
    ) -> None:
        summary = self.add_idp(server, idp)
        assert (
            Browser(server)
            .login(summary["id"], "https://evil.example.test/")
            .status_code
            == 400
        )
        assert (
            Browser(server).login(summary["id"], "//evil.example.test/").status_code
            == 400
        )

    # -- whose account ------------------------------------------------------------

    def test_a_vouched_for_email_signs_in_to_the_existing_account(
        self, server: TestClient, idp: LocalIdP, admin_a: Any
    ) -> None:
        summary = self.add_idp(server, idp, emails_verified=True)
        finished = self.round_trip(server, idp, summary, name_id=admin_a.email)[2]
        assert self.signed_in_user(server, finished).id == admin_a.id

    @staticmethod
    def identities_of(server: TestClient, user_id: str) -> list:
        registry = registry_of(server)
        IdentityDB = SamlIdentityModel.DB(registry.DB.manager.Base)
        return list(
            IdentityDB.list(
                requester_id=env("ROOT_ID"),
                model_registry=registry,
                filters=[
                    IdentityDB.user_id == user_id,
                    IdentityDB.deleted_at.is_(None),
                ],
            )
        )

    @pytest.mark.parametrize("internal", INTERNAL_ACCOUNTS)
    def test_no_vouched_for_email_reaches_an_internal_account(
        self, server: TestClient, idp: LocalIdP, internal: str
    ) -> None:
        """ROOT's seeded email is predictable; an IdP trusted for emails
        asserting it must not sign in as the superuser."""
        summary = self.add_idp(server, idp, emails_verified=True)
        with internal_account_email(registry_of(server), env(internal)) as email:
            finished = self.round_trip(server, idp, summary, name_id=email)[2]
        assert finished.status_code == 403, finished.text
        assert SESSION_COOKIE not in cookies_set(finished)
        assert self.identities_of(server, env(internal)) == []

    @pytest.mark.parametrize("internal", INTERNAL_ACCOUNTS)
    def test_nothing_links_to_an_internal_account(
        self, server: TestClient, idp: LocalIdP, internal: str
    ) -> None:
        summary = self.add_idp(server, idp)
        with pytest.raises(Exception) as refused:
            SamlIdentityManager(
                model_registry=registry_of(server), requester_id=env("ROOT_ID")
            ).create(
                user_id=env(internal),
                identity_provider_id=summary["id"],
                idp_entity_id=idp.entity_id,
                subject=new_email("internal"),
            )
        assert getattr(refused.value, "status_code", None) == 403

    def test_an_email_the_idp_does_not_vouch_for_does_not_take_an_account(
        self, server: TestClient, idp: LocalIdP
    ) -> None:
        # A user no SAML identity is linked to yet (an identity, once
        # linked, is found by IdP and subject before any email is read).
        account = create_user(server, email=new_email("password_user"))
        summary = self.add_idp(server, idp, emails_verified=False)
        finished = self.round_trip(server, idp, summary, name_id=account.email)[2]
        assert finished.status_code == 409, finished.text

    def test_a_new_account_for_an_unvouched_idp_has_no_email(
        self, server: TestClient, idp: LocalIdP
    ) -> None:
        summary = self.add_idp(server, idp, emails_verified=False)
        user = self.signed_in_user(
            server, self.round_trip(server, idp, summary, name_id=new_email())[2]
        )
        assert user.email is None

    def test_no_new_account_when_registration_is_closed(
        self, server: TestClient, idp: LocalIdP, set_env: Callable[[str, str], None]
    ) -> None:
        set_env("REGISTRATION_MODE", "closed")
        summary = self.add_idp(server, idp)
        assert (
            self.round_trip(server, idp, summary, name_id=new_email())[2].status_code
            == 403
        )

    def test_invite_only_registration_needs_an_invitation(
        self, server: TestClient, idp: LocalIdP, set_env: Callable[[str, str], None]
    ) -> None:
        set_env("REGISTRATION_MODE", "invite")
        summary = self.add_idp(server, idp)
        assert (
            self.round_trip(server, idp, summary, name_id=new_email())[2].status_code
            == 403
        )

    def test_an_invited_email_gets_an_account_and_its_team(
        self,
        server: TestClient,
        idp: LocalIdP,
        admin_a: Any,
        team_a: Any,
        set_env: Callable[[str, str], None],
    ) -> None:
        from zephyrex.extensions.auth_invitations.BLL_Invitations import (
            InvitationManager,
        )

        registry = registry_of(server)
        email = new_email("invited")
        InvitationManager(
            requester_id=admin_a.id, target_team_id=team_a.id, model_registry=registry
        ).create(team_id=team_a.id, role_id=env("USER_ROLE_ID"), email=email)
        set_env("REGISTRATION_MODE", "invite")
        summary = self.add_idp(server, idp, emails_verified=True)
        user = self.signed_in_user(
            server, self.round_trip(server, idp, summary, name_id=email)[2]
        )
        memberships = UserTeamModel.DB(registry.DB.manager.Base).list(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            user_id=user.id,
            team_id=team_a.id,
        )
        assert len(memberships) == 1

    def test_a_transient_name_id_identifies_no_one(
        self, server: TestClient, idp: LocalIdP
    ) -> None:
        summary = self.add_idp(server, idp)
        finished = self.round_trip(
            server,
            idp,
            summary,
            name_id=uuid.uuid4().hex,
            name_id_format=NAMEID_FORMAT_TRANSIENT,
        )[2]
        assert self.refusal(finished) == "no_persistent_identity"

    def test_a_configured_attribute_identifies_the_user(
        self, server: TestClient, idp: LocalIdP
    ) -> None:
        summary = self.add_idp(server, idp, identity_attribute="employeeNumber")
        email = new_email()

        def sign_in(name_id: str) -> UserModel:
            attributes = {"mail": [email], "employeeNumber": ["E-1001"]}
            finished = self.round_trip(
                server,
                idp,
                summary,
                name_id=name_id,
                name_id_format=NAMEID_FORMAT_TRANSIENT,
                attributes=attributes,
            )[2]
            return self.signed_in_user(server, finished)

        assert sign_in(uuid.uuid4().hex).id == sign_in(uuid.uuid4().hex).id

    def test_a_disabled_account_cannot_sign_in(
        self, server: TestClient, idp: LocalIdP
    ) -> None:
        summary = self.add_idp(server, idp)
        email = new_email()
        user = self.signed_in_user(
            server,
            self.round_trip(
                server,
                idp,
                summary,
                name_id=email,
                name_id_format=NAMEID_FORMAT_PERSISTENT,
            )[2],
        )
        registry = registry_of(server)
        UserModel.DB(registry.DB.manager.Base).update(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            id=user.id,
            new_properties={"active": False},
        )
        finished = self.round_trip(
            server, idp, summary, name_id=email, name_id_format=NAMEID_FORMAT_PERSISTENT
        )[2]
        assert finished.status_code == 403, finished.text

    def test_identities_are_their_users_own(
        self, server: TestClient, idp: LocalIdP, admin_b: Any
    ) -> None:
        summary = self.add_idp(server, idp)
        finished = self.round_trip(server, idp, summary, name_id=new_email())[2]
        token = cookies_set(finished)[SESSION_COOKIE]
        mine = TestClient(server.app).get(
            "/v1/auth/saml/identity", headers={"Authorization": f"Bearer {token}"}
        )
        [identity] = mine.json()["saml_identities"]
        others = server.get(
            f"/v1/auth/saml/identity/{identity['id']}",
            headers={"Authorization": f"Bearer {admin_b.jwt}"},
        )
        assert others.status_code == 404, others.text
        unlinked = TestClient(server.app).delete(
            f"/v1/auth/saml/identity/{identity['id']}",
            headers={"Authorization": f"Bearer {token}", **if_match_of(identity)},
        )
        assert unlinked.status_code in (200, 204), unlinked.text
