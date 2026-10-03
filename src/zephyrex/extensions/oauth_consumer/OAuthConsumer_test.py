# SPDX-License-Identifier: AGPL-3.0-or-later
"""Signing in with another identity provider, end to end: the app's
routes, against a real provider on loopback (``LocalIdP_test``) that signs
real ID tokens and enforces PKCE.

Each check the callback makes is failed on its own: a bad signature, the
wrong audience or issuer, an expired or stale token, a nonce mismatch, a
missing or foreign browser binding, a replayed state, a code redeemed
under another sign-in's verifier, an unregistered redirect URI, a missing
RFC 9207 ``iss``. Then the account rules: identity on (provider, subject),
never email; just-in-time sign-up by ``REGISTRATION_MODE``; no auto-link
of an unverified email; linking and unlinking for a signed-in user.
"""

import asyncio
import hashlib
import secrets
import time
from http.cookies import SimpleCookie
from typing import Any, Dict, Iterator, List, Optional, Tuple, Type
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from fastapi import HTTPException
from sqlalchemy import update

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.oauth_consumer.BLL_OAuthConsumer import (
    BINDING_COOKIE,
    OAUTH_PREFIX,
    OAuthIdentityModel,
    OAuthLoginStateModel,
)
from zephyrex.extensions.oauth_consumer.EXT_OAuthConsumer import EXT_OAuthConsumer
from zephyrex.extensions.oauth_consumer.IdentityProvider import (
    AbstractIdentityProvider,
    Endpoints,
)
from zephyrex.extensions.oauth_consumer.LocalIdP_test import (
    REDIRECT,
    LocalIdP,
    s256,
    start_idp,
)
from zephyrex.extensions.oauth_consumer.PRV_Amazon import PRV_Amazon
from zephyrex.extensions.oauth_consumer.PRV_Forgejo import PRV_Forgejo
from zephyrex.extensions.oauth_consumer.PRV_GitHub import PRV_GitHub
from zephyrex.extensions.oauth_consumer.PRV_Google import (
    GOOGLE_ISSUER,
    GOOGLE_ISSUER_HOST,
    PRV_Google,
)
from zephyrex.extensions.oauth_consumer.PRV_Microsoft import PRV_Microsoft
from zephyrex.extensions.oauth_consumer.PRV_OIDC import PRV_OIDC
from zephyrex.lib.Environment import env
from zephyrex.lib.SecretEncryption import decrypt_secret
from zephyrex.logic.BLL_Auth import UserModel
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceManager,
    ProviderInstanceModel,
    ProviderInstanceSettingManager,
    ProviderManager,
)

Response = Any


def _detail(response: Response) -> str:
    detail = response.json().get("detail")
    return str(detail.get("message") if isinstance(detail, dict) else detail)


def _cookies(response: Response) -> Dict[str, Any]:
    """The cookies a response sets, by name, with their attributes."""
    found: Dict[str, Any] = {}
    for header in response.headers.get_list("set-cookie"):
        jar: SimpleCookie = SimpleCookie()
        jar.load(header)
        for name, morsel in jar.items():
            found[name] = morsel
    return found


class Flow:
    """The app's sign-in routes, driven the way its web client drives them."""

    def __init__(self, server: Any) -> None:
        self.server = server
        self.registry = server.app.state.model_registry

    def configure(
        self,
        provider: Type[AbstractIdentityProvider],
        settings: Dict[str, str],
        name: Optional[str] = None,
    ) -> str:
        """A provider instance with ``settings``; its name."""
        root_id = env("ROOT_ID")
        record = ProviderManager(
            model_registry=self.registry, requester_id=root_id
        ).get(name=provider.name)
        instance = ProviderInstanceManager(
            model_registry=self.registry, requester_id=root_id
        ).create(
            name=name or f"test-{provider.public_name}-{secrets.token_hex(4)}",
            provider_id=record.id,
            scope="root",
        )
        setting_manager = ProviderInstanceSettingManager(
            model_registry=self.registry, requester_id=root_id
        )
        for key, value in settings.items():
            setting_manager.create(
                provider_instance_id=instance.id, key=key, value=value
            )
        return str(instance.name)

    def remove_all(self, provider: Type[AbstractIdentityProvider]) -> None:
        """Remove every instance of ``provider`` (the app's database
        outlives a run), so the next one is the only one configured."""
        root_id = env("ROOT_ID")
        record = ProviderManager(
            model_registry=self.registry, requester_id=root_id
        ).get(name=provider.name)
        InstanceDB = ProviderInstanceModel.DB(self.registry.DB.manager.Base)
        live = InstanceDB.list(
            requester_id=root_id,
            model_registry=self.registry,
            filters=[
                InstanceDB.provider_id == record.id,
                InstanceDB.deleted_at.is_(None),
            ],
            return_type="dto",
            override_dto=ProviderInstanceModel,
        )
        instances = ProviderInstanceManager(
            model_registry=self.registry, requester_id=root_id
        )
        for row in live or []:
            instances.delete(id=row.id)

    def oidc(
        self,
        idp: LocalIdP,
        provider: Type[AbstractIdentityProvider] = PRV_OIDC,
        **extra: str,
    ) -> str:
        return self.configure(
            provider,
            {
                "client_id": idp.client_id,
                "client_secret": idp.client_secret,
                "issuer": idp.issuer,
                "redirect_uris": REDIRECT,
                **extra,
            },
        )

    def authorize(
        self,
        provider: str,
        redirect_uri: Optional[str] = REDIRECT,
        token: Optional[str] = None,
    ) -> Response:
        body: Dict[str, Any] = {"provider": provider}
        if redirect_uri is not None:
            body["redirect_uri"] = redirect_uri
        path = "/link/authorize" if token else "/authorize"
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return self.server.post(f"{OAUTH_PREFIX}{path}", json=body, headers=headers)

    def begin(
        self, provider: str, token: Optional[str] = None, **kw: Any
    ) -> Tuple[str, str, str]:
        """The authorization URL, state and binding cookie of a new sign-in."""
        response = self.authorize(provider, token=token, **kw)
        assert response.status_code == 200, response.text
        return (
            response.json()["authorize_url"],
            response.json()["state"],
            _cookies(response)[BINDING_COOKIE].value,
        )

    def callback(
        self,
        provider: str,
        redirect: Dict[str, str],
        binding: Optional[str],
        token: Optional[str] = None,
        redirect_uri: Optional[str] = REDIRECT,
        **extra: Any,
    ) -> Response:
        body: Dict[str, Any] = {
            "provider": provider,
            "code": redirect["code"],
            "state": redirect["state"],
            **extra,
        }
        if redirect_uri is not None:
            body["redirect_uri"] = redirect_uri
        if "iss" in redirect and "iss" not in body:
            body["iss"] = redirect["iss"]
        headers = {}
        if binding is not None:
            headers["Cookie"] = f"{BINDING_COOKIE}={binding}"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        path = "/link/callback" if token else "/callback"
        return self.server.post(f"{OAUTH_PREFIX}{path}", json=body, headers=headers)

    def sign_in(self, idp: LocalIdP, provider: str, **kw: Any) -> Response:
        url, _, binding = self.begin(provider, **kw)
        return self.callback(provider, idp.sign_in(url), binding)

    def link(self, idp: LocalIdP, provider: str, token: str) -> Response:
        url, _, binding = self.begin(provider, token=token)
        return self.callback(provider, idp.sign_in(url), binding, token=token)

    def me(self, token: str) -> Dict[str, Any]:
        response = self.server.get(
            "/v1/user", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200, response.text
        found: Dict[str, Any] = response.json()
        user: Dict[str, Any] = found.get("user", found)
        return user

    def user_by_email(self, email: str) -> List[UserModel]:
        UserDB = UserModel.DB(self.registry.DB.manager.Base)
        found: List[UserModel] = UserDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.registry,
            filters=[UserDB.email == email],
            return_type="dto",
            override_dto=UserModel,
        )
        return found

    def identities(self, user_id: str) -> List[OAuthIdentityModel]:
        IdentityDB = OAuthIdentityModel.DB(self.registry.DB.manager.Base)
        found: List[OAuthIdentityModel] = IdentityDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.registry,
            filters=[IdentityDB.user_id == user_id, IdentityDB.deleted_at.is_(None)],
            return_type="dto",
            override_dto=OAuthIdentityModel,
        )
        return found

    def state_row(self, state: str) -> OAuthLoginStateModel:
        StateDB = OAuthLoginStateModel.DB(self.registry.DB.manager.Base)
        rows: List[OAuthLoginStateModel] = StateDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.registry,
            filters=[StateDB.state_hash == hashlib.sha256(state.encode()).hexdigest()],
            return_type="dto",
            override_dto=OAuthLoginStateModel,
        )
        assert len(rows) == 1
        return rows[0]

    def instance(self, name: str) -> ProviderInstanceModel:
        rows = ProviderInstanceManager(
            model_registry=self.registry, requester_id=env("ROOT_ID")
        ).list(name=name)
        return ProviderInstanceModel.model_validate(rows[0], from_attributes=True)


class OAuthCase(ExtensionServerMixin):
    extension_class = EXT_OAuthConsumer

    @pytest.fixture
    def flow(self, server) -> Flow:
        return Flow(server)

    @pytest.fixture
    def idp(self, monkeypatch) -> Iterator[LocalIdP]:
        provider = start_idp(monkeypatch, redirect_uris=[REDIRECT])
        yield provider
        provider.stop()


class TestAuthorize(OAuthCase):
    def test_the_request_carries_pkce_s256_a_nonce_and_a_hashed_state(self, flow, idp):
        name = flow.oidc(idp)
        response = flow.authorize(name)
        assert response.status_code == 200, response.text
        query = {
            k: v[0]
            for k, v in parse_qs(
                urlparse(response.json()["authorize_url"]).query
            ).items()
        }
        state = response.json()["state"]
        assert query["state"] == state
        assert query["code_challenge_method"] == "S256"
        assert query["nonce"] and query["redirect_uri"] == REDIRECT
        assert query["response_type"] == "code" and query["client_id"] == idp.client_id

        row = flow.state_row(state)
        verifier = decrypt_secret(row.code_verifier)
        assert row.code_verifier != verifier, "the verifier is stored encrypted"
        assert s256(str(verifier)) == query["code_challenge"]
        assert row.nonce == query["nonce"]

    def test_the_binding_cookie_is_httponly_secure_lax_and_scoped(self, flow, idp):
        response = flow.authorize(flow.oidc(idp))
        cookie = _cookies(response)[BINDING_COOKIE]
        assert cookie["httponly"] and cookie["secure"]
        assert cookie["samesite"].lower() == "lax"
        assert cookie["path"] == OAUTH_PREFIX
        row = flow.state_row(response.json()["state"])
        assert row.binding_hash == hashlib.sha256(cookie.value.encode()).hexdigest()

    def test_an_unregistered_redirect_uri_is_refused(self, flow, idp):
        name = flow.oidc(idp)
        for wrong in (
            "https://evil.example.com/user/close/test",
            REDIRECT + "/../../evil",
            REDIRECT + "x",
            REDIRECT.upper(),
        ):
            response = flow.authorize(name, redirect_uri=wrong)
            assert response.status_code == 400, wrong
            assert "redirect_uri" in _detail(response)

    def test_an_unknown_provider_is_refused(self, flow):
        assert flow.authorize("nobody-configured-this").status_code == 400
        assert flow.authorize("../../etc").status_code == 400

    def test_providers_list_names_configured_ones_and_no_secrets(self, flow, idp):
        name = flow.oidc(idp)
        response = flow.server.get(f"{OAUTH_PREFIX}/providers")
        assert response.status_code == 200
        entry = next(p for p in response.json()["providers"] if p["name"] == name)
        assert entry == {
            "name": name,
            "provider": "oidc",
            "friendly_name": "OpenID Connect",
            "kind": "oidc",
        }
        assert idp.client_secret not in response.text

    def test_a_deleted_provider_instance_signs_no_one_in(self, flow, idp):
        """Root reads see deleted rows; a removed provider must not keep
        working through them."""
        name = flow.oidc(idp)
        url, _, binding = flow.begin(name)
        ProviderInstanceManager(
            model_registry=flow.registry, requester_id=env("ROOT_ID")
        ).delete(id=flow.instance(name).id)
        refused = flow.callback(name, idp.sign_in(url), binding)
        assert refused.status_code == 400
        assert flow.authorize(name).status_code == 400
        listed = flow.server.get(f"{OAUTH_PREFIX}/providers").json()["providers"]
        assert name not in [p["name"] for p in listed]

    def test_the_client_secret_is_write_only(self, flow, idp):
        instance = flow.instance(flow.oidc(idp))
        rows = ProviderInstanceSettingManager(
            model_registry=flow.registry, requester_id=env("ROOT_ID")
        ).list(provider_instance_id=instance.id, key="client_secret")
        assert rows and rows[0].write_only
        assert rows[0].model_dump()["value"] is None
        assert PRV_OIDC.setting(instance, "client_secret") == idp.client_secret


class TestCallbackChecks(OAuthCase):
    """Every check fails on its own, and a failed callback has spent its
    state."""

    def test_a_valid_sign_in_returns_the_token_and_sets_session_cookies(
        self, flow, idp
    ):
        name = flow.oidc(idp)
        url, state, binding = flow.begin(name)
        response = flow.callback(name, idp.sign_in(url), binding)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["grant_type"] == "oauth_consumer" and body["new_user"] is True
        assert body["token"] and body["session_key"] and not body["mfa_required"]
        assert flow.me(body["token"])["email"] == idp.account.email

        cookies = _cookies(response)
        assert cookies["zx_session"].value == body["token"]
        assert cookies["zx_session"]["httponly"] and cookies["zx_session"]["secure"]
        assert not cookies["zx_csrf"]["httponly"]
        assert cookies[BINDING_COOKIE].value == ""
        assert flow.state_row(state).consumed_at is not None

        sent = idp.token_forms[-1]
        assert len(sent["code_verifier"]) >= 43 and sent["redirect_uri"] == REDIRECT

    def test_a_replayed_state_is_refused(self, flow, idp):
        name = flow.oidc(idp)
        url, _, binding = flow.begin(name)
        redirect = idp.sign_in(url)
        assert flow.callback(name, redirect, binding).status_code == 200
        replay = flow.callback(name, redirect, binding)
        assert (
            replay.status_code == 400
            and _detail(replay) == "Invalid or expired OAuth state"
        )

    def test_a_missing_binding_cookie_is_refused_and_spends_the_state(self, flow, idp):
        name = flow.oidc(idp)
        url, _, binding = flow.begin(name)
        redirect = idp.sign_in(url)
        refused = flow.callback(name, redirect, None)
        assert refused.status_code == 400
        assert _detail(refused) == "Invalid or expired OAuth state"
        assert flow.callback(name, redirect, binding).status_code == 400

    def test_another_browsers_binding_is_refused(self, flow, idp):
        """Login CSRF: the attacker's code and state, completed in the
        victim's browser (which carries its own binding, or none)."""
        name = flow.oidc(idp)
        url, _, _attacker_binding = flow.begin(name)
        attacker_redirect = idp.sign_in(url)
        _, _, victim_binding = flow.begin(name)
        refused = flow.callback(name, attacker_redirect, victim_binding)
        assert refused.status_code == 400
        assert _detail(refused) == "Invalid or expired OAuth state"

    def test_a_code_redeemed_under_another_sign_ins_verifier_is_refused(
        self, flow, idp
    ):
        """Code injection: a code issued for one sign-in's challenge,
        posted with another sign-in's state, is exchanged with that one's
        verifier, which the provider refuses (PKCE)."""
        name = flow.oidc(idp)
        first_url, _, _ = flow.begin(name)
        stolen_code = idp.sign_in(first_url)["code"]
        second_url, second_state, second_binding = flow.begin(name)
        refused = flow.callback(
            name, {"code": stolen_code, "state": second_state}, second_binding
        )
        assert refused.status_code == 400
        assert idp.token_forms[-1]["code_verifier"] == decrypt_secret(
            flow.state_row(second_state).code_verifier
        )

    def test_a_state_for_another_provider_is_refused(self, flow, idp):
        name = flow.oidc(idp)
        other = flow.oidc(idp)
        url, _, binding = flow.begin(name)
        refused = flow.callback(other, idp.sign_in(url), binding)
        assert refused.status_code == 400
        assert _detail(refused) == "Invalid or expired OAuth state"

    def test_a_different_redirect_uri_at_the_callback_is_refused(
        self, flow, monkeypatch
    ):
        second = "https://app.example.com/user/close/second"
        idp = start_idp(monkeypatch, redirect_uris=[REDIRECT, second])
        try:
            name = flow.configure(
                PRV_OIDC,
                {
                    "client_id": idp.client_id,
                    "client_secret": idp.client_secret,
                    "issuer": idp.issuer,
                    "redirect_uris": f"{REDIRECT},{second}",
                },
            )
            url, _, binding = flow.begin(name)
            refused = flow.callback(
                name, idp.sign_in(url), binding, redirect_uri=second
            )
            assert refused.status_code == 400
        finally:
            idp.stop()

    def test_an_expired_state_is_refused(self, flow, idp):
        name = flow.oidc(idp)
        url, state, binding = flow.begin(name)
        StateDB = OAuthLoginStateModel.DB(flow.registry.DB.manager.Base)
        session = flow.registry.DB.session()
        try:
            session.execute(
                update(StateDB)
                .where(StateDB.state_hash == hashlib.sha256(state.encode()).hexdigest())
                .values(expires_at=StateDB.created_at)
            )
            session.commit()
        finally:
            session.close()
        refused = flow.callback(name, idp.sign_in(url), binding)
        assert refused.status_code == 400
        assert _detail(refused) == "Invalid or expired OAuth state"

    @pytest.mark.parametrize(
        "claims, reason",
        [
            ({"aud": "someone-else"}, "InvalidAudienceError"),
            ({"iss": "https://evil.example.com"}, "InvalidIssuerError"),
            ({"exp": int(time.time()) - 3600}, "ExpiredSignatureError"),
            ({"iat": int(time.time()) - 7200}, "issued too long ago"),
            ({"iat": int(time.time()) + 3600}, "ImmatureSignatureError"),
            ({"nonce": "not-the-nonce"}, "nonce mismatch"),
            ({"nonce": None}, "nonce mismatch"),
            ({"sub": None}, "MissingRequiredClaimError"),
            ({"at_hash": "AAAAAAAAAAAAAAAAAAAAAA"}, "access token hash mismatch"),
            ({"azp": "someone-else"}, "issued to another party"),
        ],
    )
    def test_each_id_token_check(self, flow, idp, claims, reason):
        name = flow.oidc(idp)
        idp.id_token_claims = claims
        refused = flow.sign_in(idp, name)
        assert refused.status_code == 400, refused.text
        assert reason in _detail(refused)
        assert not flow.user_by_email(str(idp.account.email))

    def test_a_bad_signature_is_refused(self, flow, idp):
        name = flow.oidc(idp)
        idp.forge_signatures = True
        refused = flow.sign_in(idp, name)
        assert refused.status_code == 400
        assert "InvalidSignatureError" in _detail(refused)

    def test_a_rotated_key_is_fetched_but_an_unknown_one_not_refetched_at_once(
        self, flow, idp
    ):
        name = flow.oidc(idp)
        assert flow.sign_in(idp, name).status_code == 200
        assert idp.jwks_fetches == 1
        idp.rotate_key()
        assert flow.sign_in(idp, name).status_code == 200
        assert idp.jwks_fetches == 2
        idp.id_token_kid = "a-key-nobody-published"
        refused = flow.sign_in(idp, name)
        assert refused.status_code == 400 and "unknown key" in _detail(refused)
        assert idp.jwks_fetches == 2

    def test_rfc9207_iss_is_required_when_promised_and_must_match(
        self, flow, monkeypatch
    ):
        idp = start_idp(monkeypatch, redirect_uris=[REDIRECT], iss_parameter=True)
        try:
            name = flow.oidc(idp)
            url, _, binding = flow.begin(name)
            redirect = idp.sign_in(url)
            assert redirect["iss"] == idp.issuer
            redirect.pop("iss")
            missing = flow.callback(name, redirect, binding)
            assert (
                missing.status_code == 400
                and _detail(missing) == "Missing iss parameter"
            )

            url, _, binding = flow.begin(name)
            mixed_up = flow.callback(
                name, idp.sign_in(url), binding, iss="https://other.example.com"
            )
            assert (
                mixed_up.status_code == 400 and _detail(mixed_up) == "Issuer mismatch"
            )

            assert flow.sign_in(idp, name).status_code == 200
        finally:
            idp.stop()


class TestUnsignedAndSymmetricTokens:
    """``none`` and HMAC-signed ID tokens are refused before any key is
    fetched."""

    @pytest.mark.parametrize(
        "algorithm, key", [("none", None), ("HS256", "the-client-secret-as-a-key!!")]
    )
    def test_refused(self, algorithm, key):
        token = jwt.encode({"sub": "x", "nonce": "n"}, key, algorithm=algorithm)
        endpoints = Endpoints(
            authorize="https://idp.example.com/a",
            token="https://idp.example.com/t",
            issuer="https://idp.example.com",
            jwks_uri="https://idp.example.com/jwks",
            signing_algorithms=("RS256",),
        )
        with pytest.raises(Exception) as refused:
            asyncio.run(
                PRV_OIDC.verify_id_token(
                    ProviderInstanceModel.model_construct(),
                    endpoints,
                    token,
                    nonce="n",
                    access_token=None,
                )
            )
        assert "signature algorithm not allowed" in str(refused.value)


class TestAccounts(OAuthCase):
    def test_identity_is_the_subject_not_the_email(self, flow, idp, admin_a):
        """A later token naming another user's email still signs in the
        account its subject is linked to."""
        name = flow.oidc(idp)
        first = flow.sign_in(idp, name).json()
        idp.account.email = admin_a.email
        again = flow.sign_in(idp, name)
        assert again.status_code == 200
        assert again.json()["user_id"] == first["user_id"] != admin_a.id
        assert again.json()["new_user"] is False

    def test_a_verified_email_joins_the_existing_account(self, flow, idp, admin_a):
        name = flow.oidc(idp)
        idp.account.email = admin_a.email
        joined = flow.sign_in(idp, name)
        assert joined.status_code == 200, joined.text
        assert joined.json()["user_id"] == admin_a.id and not joined.json()["new_user"]
        assert idp.account.sub in [i.subject for i in flow.identities(admin_a.id)]

    def test_an_unverified_email_never_joins_an_existing_account(
        self, flow, idp, admin_b
    ):
        name = flow.oidc(idp)
        idp.account.email, idp.account.email_verified = admin_b.email, False
        refused = flow.sign_in(idp, name)
        assert refused.status_code == 403
        assert "did not confirm" in _detail(refused)
        assert flow.identities(admin_b.id) == []

    def test_an_unverified_email_creates_no_account(self, flow, idp):
        name = flow.oidc(idp)
        idp.account.email_verified = False
        assert flow.sign_in(idp, name).status_code == 403
        assert flow.user_by_email(str(idp.account.email)) == []

    def test_an_inactive_account_does_not_sign_in(self, flow, idp):
        name = flow.oidc(idp)
        user_id = flow.sign_in(idp, name).json()["user_id"]
        UserDB = UserModel.DB(flow.registry.DB.manager.Base)
        UserDB.update(
            requester_id=env("ROOT_ID"),
            model_registry=flow.registry,
            id=user_id,
            new_properties={"active": False},
        )
        refused = flow.sign_in(idp, name)
        assert (
            refused.status_code == 403
            and _detail(refused) == "User account is disabled"
        )

    def test_open_registration_creates_the_user_from_the_profile(
        self, flow, idp, set_env
    ):
        set_env("REGISTRATION_MODE", "open")
        idp.account.preferred_username = f"ada{secrets.token_hex(3)}"
        created = flow.sign_in(idp, flow.oidc(idp))
        assert created.status_code == 200 and created.json()["new_user"]
        user = flow.user_by_email(str(idp.account.email))[0]
        assert (user.first_name, user.last_name) == ("Ada", "Lovelace")
        assert user.username == idp.account.preferred_username

    def test_closed_registration_refuses_a_new_user(self, flow, idp, set_env):
        set_env("REGISTRATION_MODE", "closed")
        refused = flow.sign_in(idp, flow.oidc(idp))
        assert refused.status_code == 403
        assert flow.user_by_email(str(idp.account.email)) == []

    def test_closed_registration_still_signs_in_existing_users(
        self, flow, idp, admin_a, set_env
    ):
        set_env("REGISTRATION_MODE", "closed")
        idp.account.email = admin_a.email
        assert flow.sign_in(idp, flow.oidc(idp)).json()["user_id"] == admin_a.id

    def test_invite_registration_needs_an_invitation_to_that_address(
        self, flow, idp, admin_a, team_a, set_env
    ):
        from zephyrex.extensions.auth_invitations.BLL_Invitations import (
            InvitationManager,
        )

        set_env("REGISTRATION_MODE", "invite")
        name = flow.oidc(idp)
        refused = flow.sign_in(idp, name)
        assert refused.status_code == 403 and "invitation" in _detail(refused)

        with InvitationManager(
            requester_id=admin_a.id, model_registry=flow.registry
        ) as invitations:
            invitations.create(
                team_id=team_a.id, role_id=env("USER_ROLE_ID"), email=["x@example.com"]
            )
        assert flow.sign_in(idp, name).status_code == 403

        with InvitationManager(
            requester_id=admin_a.id, model_registry=flow.registry
        ) as invitations:
            invitations.create(
                team_id=team_a.id,
                role_id=env("USER_ROLE_ID"),
                email=[str(idp.account.email)],
            )
        admitted = flow.sign_in(idp, name)
        assert admitted.status_code == 200, admitted.text
        assert admitted.json()["new_user"] is True


class TestLinking(OAuthCase):
    def test_a_signed_in_user_links_a_provider_then_signs_in_with_it(
        self, flow, idp, user_b
    ):
        name = flow.oidc(idp)
        idp.account.email_verified = False
        linked = flow.link(idp, name, user_b.jwt)
        assert linked.status_code == 200, linked.text
        assert linked.json()["subject"] == idp.account.sub
        assert "access_token" not in linked.text
        signed_in = flow.sign_in(idp, name)
        assert signed_in.status_code == 200 and signed_in.json()["user_id"] == user_b.id

    def test_a_link_state_is_not_a_sign_in_and_not_another_users(
        self, flow, idp, user_b, admin_b
    ):
        name = flow.oidc(idp)
        url, _, binding = flow.begin(name, token=user_b.jwt)
        redirect = idp.sign_in(url)
        assert flow.callback(name, redirect, binding).status_code == 400

        url, _, binding = flow.begin(name, token=user_b.jwt)
        foreign = flow.callback(name, idp.sign_in(url), binding, token=admin_b.jwt)
        assert foreign.status_code == 400

    def test_an_identity_linked_to_another_user_is_not_relinked(
        self, flow, idp, user_b, admin_b
    ):
        name = flow.oidc(idp)
        assert flow.link(idp, name, user_b.jwt).status_code == 200
        taken = flow.link(idp, name, admin_b.jwt)
        assert taken.status_code == 409

    def test_identities_are_listed_to_their_owner_only_and_unlinked(
        self, flow, idp, admin_b, user_b
    ):
        name = flow.oidc(idp)
        identity_id = flow.link(idp, name, admin_b.jwt).json()["id"]
        own = flow.server.get(
            f"{OAUTH_PREFIX}/identity",
            headers={"Authorization": f"Bearer {admin_b.jwt}"},
        )
        assert own.status_code == 200
        assert identity_id in own.text and "access_token" not in own.text
        other = flow.server.get(
            f"{OAUTH_PREFIX}/identity/{identity_id}",
            headers={"Authorization": f"Bearer {user_b.jwt}"},
        )
        assert other.status_code in (403, 404)
        unlinked = flow.server.delete(
            f"{OAUTH_PREFIX}/identity/{identity_id}",
            headers={"Authorization": f"Bearer {admin_b.jwt}"},
        )
        assert unlinked.status_code in (200, 204), unlinked.text
        assert all(i.id != identity_id for i in flow.identities(admin_b.id))


class TestAbilities(OAuthCase):
    def test_the_owner_gets_a_current_access_token_refreshed_once_lapsed(
        self, flow, idp
    ):
        name = flow.oidc(idp)
        user_id = flow.sign_in(idp, name).json()["user_id"]
        identity = flow.identities(user_id)[0]

        listed = asyncio.run(
            EXT_OAuthConsumer.list_oauth_identities(requester_id=user_id)
        )
        assert [i["id"] for i in listed] == [identity.id]

        current = asyncio.run(
            EXT_OAuthConsumer.oauth_access_token(
                requester_id=user_id, identity_id=identity.id
            )
        )
        assert current["access_token"] == decrypt_secret(identity.access_token)

        IdentityDB = OAuthIdentityModel.DB(flow.registry.DB.manager.Base)
        IdentityDB.update(
            requester_id=env("ROOT_ID"),
            model_registry=flow.registry,
            id=identity.id,
            new_properties={"token_expires_at": identity.created_at},
        )
        refreshed = asyncio.run(
            EXT_OAuthConsumer.oauth_access_token(
                requester_id=user_id, identity_id=identity.id
            )
        )
        assert refreshed["access_token"] != current["access_token"]
        assert idp.token_forms[-1]["grant_type"] == "refresh_token"

    def test_another_user_cannot_take_the_token(self, flow, idp, user_b):
        name = flow.oidc(idp)
        user_id = flow.sign_in(idp, name).json()["user_id"]
        identity = flow.identities(user_id)[0]
        with pytest.raises(HTTPException):
            asyncio.run(
                EXT_OAuthConsumer.oauth_access_token(
                    requester_id=user_b.id, identity_id=identity.id
                )
            )


class TestNamedProviders(OAuthCase):
    def test_github_verified_primary_email_signs_up(self, flow, idp):
        name = flow.configure(
            PRV_GitHub,
            {
                "client_id": idp.client_id,
                "client_secret": idp.client_secret,
                "web_url": idp.base_url,
                "api_url": f"{idp.base_url}/api",
                "redirect_uris": REDIRECT,
            },
        )
        created = flow.sign_in(idp, name)
        assert created.status_code == 200, created.text
        user = flow.user_by_email(str(idp.account.email))[0]
        assert flow.identities(str(user.id))[0].subject == str(int(idp.account.sub, 16))

    def test_github_unverified_primary_email_is_refused(self, flow, idp):
        name = flow.configure(
            PRV_GitHub,
            {
                "client_id": idp.client_id,
                "client_secret": idp.client_secret,
                "web_url": idp.base_url,
                "api_url": f"{idp.base_url}/api",
                "redirect_uris": REDIRECT,
            },
        )
        idp.account.email_verified = False
        assert flow.sign_in(idp, name).status_code == 403

    def test_amazon_signs_in_only_a_linked_account(self, flow, idp, admin_a):
        name = flow.configure(
            PRV_Amazon,
            {
                "client_id": idp.client_id,
                "client_secret": idp.client_secret,
                "authorize_url": f"{idp.base_url}/ap/oa",
                "api_url": idp.base_url,
                "redirect_uris": REDIRECT,
            },
        )
        assert flow.sign_in(idp, name).status_code == 403
        assert flow.link(idp, name, admin_a.jwt).status_code == 200
        assert flow.sign_in(idp, name).json()["user_id"] == admin_a.id

    def test_microsoft_multi_tenant_issuer_is_the_tokens_tenant(
        self, flow, monkeypatch
    ):
        idp = start_idp(monkeypatch, redirect_uris=[REDIRECT])
        try:
            idp.metadata_issuer = f"{idp.base_url}/{{tenantid}}/v2.0"
            name = flow.configure(
                PRV_Microsoft,
                {
                    "client_id": idp.client_id,
                    "client_secret": idp.client_secret,
                    "authority": idp.base_url,
                    "tenant": "common",
                    "redirect_uris": REDIRECT,
                },
            )
            tenant = "11111111-2222-3333-4444-555555555555"
            idp.id_token_claims = {
                "iss": f"{idp.base_url}/{tenant}/v2.0",
                "tid": "99999999-0000-0000-0000-000000000000",
                "email_verified": None,
                "xms_edov": True,
            }
            assert "InvalidIssuerError" in _detail(flow.sign_in(idp, name))

            idp.id_token_claims = {
                **idp.id_token_claims,
                "tid": tenant,
                "xms_edov": None,
            }
            unverified = flow.sign_in(idp, name)
            assert unverified.status_code == 403, unverified.text

            idp.id_token_claims = {**idp.id_token_claims, "xms_edov": True}
            assert flow.sign_in(idp, name).status_code == 200
        finally:
            idp.stop()

    def test_forgejo_by_its_public_name_with_the_default_redirect(
        self, flow, monkeypatch
    ):
        default = f"{env('APP_URI').rstrip('/')}/user/close/forgejo"
        idp = start_idp(monkeypatch, redirect_uris=[default])
        flow.remove_all(PRV_Forgejo)
        try:
            flow.configure(
                PRV_Forgejo,
                {
                    "client_id": idp.client_id,
                    "client_secret": idp.client_secret,
                    "issuer": f"{idp.issuer}/",
                },
            )
            idp.account.preferred_username = f"forgejo{secrets.token_hex(3)}"
            url, _, binding = flow.begin("forgejo", redirect_uri=None)
            signed_in = flow.callback(
                "forgejo", idp.sign_in(url), binding, redirect_uri=None
            )
            assert signed_in.status_code == 200, signed_in.text
            user = flow.user_by_email(str(idp.account.email))[0]
            assert user.username == idp.account.preferred_username
        finally:
            idp.stop()

    def test_google_accepts_its_issuer_host_form_only_for_google(self):
        instance = ProviderInstanceModel.model_construct()
        google = Endpoints(authorize="a", token="t", issuer=GOOGLE_ISSUER)
        other = Endpoints(authorize="a", token="t", issuer="https://idp.example.com")
        assert GOOGLE_ISSUER_HOST in PRV_Google.accepted_issuers(instance, google, {})
        assert PRV_Google.accepted_issuers(instance, other, {}) == [
            "https://idp.example.com"
        ]

    def test_every_provider_is_an_identity_provider_with_unique_names(self):
        providers = EXT_OAuthConsumer.providers
        assert {p.name for p in providers} == {
            "oidc",
            "oauth_google",
            "oauth_microsoft",
            "oauth_github",
            "oauth_amazon",
            "oauth_forgejo",
        }
        assert all(issubclass(p, AbstractIdentityProvider) for p in providers)
        assert len({p.public_name for p in providers}) == len(providers)
        for provider in providers:
            assert provider.instance_setting("client_secret").secret
