# SPDX-License-Identifier: AGPL-3.0-or-later
"""The authorization server end to end, over its real routes: a client
registers, a signed-in user consents, the client redeems the code with
PKCE, verifies the ID token against the published JWKS with PyJWT, calls
UserInfo, refreshes, introspects and revokes. Then every refusal RFC 9700
and OIDC Core require.

Holes these close (each test names the one it guards):
- auth_oauth2_server stored client secrets, codes and tokens in plain
  text, accepted ``plain`` PKCE and no PKCE from confidential clients,
  let a public client (no secret) introspect and revoke, did not bind the
  code to the redirect URI when the token request omitted it, did not
  revoke tokens on code reuse or the family on refresh reuse, and allowed
  scope escalation on refresh.
- oauth_provider (1.0) registered plain-http redirect URIs, accepted
  ``plain`` PKCE, crashed registering any scope (an empty permission
  registry raised KeyError), had no refresh grant, stored no consent, and
  neither server sent ``iss``, restricted an access token's audience,
  signed ID tokens, or published discovery, JWKS or UserInfo (oidc_provider
  declared them and implemented none).
"""

import base64
import hashlib
import json
import secrets
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.fernet import Fernet

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.oauth_provider import Config
from zephyrex.extensions.oauth_provider.AuthorizationServer import AuthorizationServer
from zephyrex.extensions.oauth_provider.BLL_OAuthProvider import (
    ClientRegistration,
    OauthClientManager,
    now_utc,
)
from zephyrex.extensions.oauth_provider.EXT_OAuthProvider import EXT_OAuthProvider
from zephyrex.extensions.oauth_provider.OAuthProtocol import digest, s256
from zephyrex.lib.Environment import env
from zephyrex.lib.InboundSecurity import _RATE_LIMIT_REGISTRY
from zephyrex.testing.factories import create_user

ISSUER = "https://id.example.test"
CONSENT_URL = "https://ui.example.test/consent"
REDIRECT = "https://rp.example.test/callback"
RESOURCE = "https://api.example.test"
AUTHORIZE = "/v1/oauth2/authorize"
TOKEN = "/v1/oauth2/token"
INTROSPECT = "/v1/oauth2/introspect"
REVOKE = "/v1/oauth2/revoke"
USERINFO = "/v1/oauth2/userinfo"


def bearer(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def pkce() -> Tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    return verifier, s256(verifier)


def query_of(url: str) -> Dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


class Client:
    """A relying party, speaking the protocol as RFC 6749 and OIDC Core
    write it: an authorization request in the query, client credentials
    in an HTTP Basic header (or the body), tokens from the token
    endpoint."""

    def __init__(self, server: Any, registered: Dict[str, Any]) -> None:
        self.server = server
        self.client_id: str = registered["client_id"]
        self.secret: Optional[str] = registered["client_secret"]
        self.method: str = registered["token_endpoint_auth_method"]
        self.redirect_uri: str = registered["redirect_uris"][0]

    def credentials(self) -> Tuple[Dict[str, str], Dict[str, str]]:
        """(headers, body) authenticating this client its registered way."""
        if self.method == "client_secret_basic":
            raw = f"{self.client_id}:{self.secret}".encode()
            return {"Authorization": "Basic " + base64.b64encode(raw).decode()}, {}
        if self.method == "client_secret_post":
            return {}, {"client_id": self.client_id, "client_secret": self.secret or ""}
        return {}, {"client_id": self.client_id}

    def authorize(self, challenge: Optional[str], **extra: Any) -> Any:
        params: Dict[str, Any] = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": "openid profile email",
            "state": "st-" + secrets.token_hex(4),
            "nonce": "n-" + secrets.token_hex(4),
        }
        if challenge is not None:
            params.update(code_challenge=challenge, code_challenge_method="S256")
        params.update(extra)
        params = {k: v for k, v in params.items() if v is not None}
        return self.server.get(AUTHORIZE, params=params, follow_redirects=False)

    def post(self, path: str, **body: Any) -> Any:
        headers, auth = self.credentials()
        return self.server.post(path, json={**auth, **body}, headers=headers)

    def redeem(self, code: str, verifier: Optional[str], **extra: Any) -> Any:
        return self.post(
            TOKEN,
            grant_type="authorization_code",
            code=code,
            redirect_uri=self.redirect_uri,
            code_verifier=verifier,
            **extra,
        )

    def refresh(self, refresh_token: str, **extra: Any) -> Any:
        return self.post(
            TOKEN, grant_type="refresh_token", refresh_token=refresh_token, **extra
        )

    def introspect(self, token: str) -> Dict[str, Any]:
        response = self.post(INTROSPECT, token=token)
        assert response.status_code == 200, response.text
        answer: Dict[str, Any] = response.json()
        return answer


class OAuthFlows(ExtensionServerMixin):
    extension_class = EXT_OAuthProvider

    @pytest.fixture(autouse=True)
    def configured(self, set_env):
        set_env(Config.ISSUER, ISSUER)
        set_env(Config.CONSENT_URL, CONSENT_URL)

    @pytest.fixture
    def user(self, server):
        """A fresh signed-in user, with no consents yet."""
        return create_user(server)

    def register(self, server, owner, **fields: Any) -> Client:
        body = {
            "name": "Relying Party",
            "redirect_uris": [REDIRECT],
            "allowed_scopes": ["openid", "profile", "email"],
            **fields,
        }
        response = server.post(
            "/v1/oauth2/clients/register", json=body, headers=bearer(owner.jwt)
        )
        assert response.status_code == 200, response.text
        return Client(server, response.json())

    def registry(self, server) -> Any:
        return server.app.state.model_registry

    def resource_server(self, server) -> Client:
        """The resource server ``RESOURCE``, registered by the operator (once
        per module; later calls take a fresh secret for it)."""
        operator = OauthClientManager(
            requester_id=env("ROOT_ID"), model_registry=self.registry(server)
        )
        engine = AuthorizationServer(self.registry(server))
        existing = engine.clients.first(engine.clients.db.resource_uri == RESOURCE)
        if existing is None:
            registered = operator.register_client(
                ClientRegistration(
                    name="API",
                    redirect_uris=["https://api.example.test/unused"],
                    resource_uri=RESOURCE,
                )
            )
            return Client(server, registered.model_dump())
        rotated = operator.rotate_secret(existing.id)
        return Client(
            server,
            {
                "client_id": existing.client_id,
                "client_secret": rotated.client_secret,
                "token_endpoint_auth_method": existing.token_endpoint_auth_method,
                "redirect_uris": json.loads(existing.redirect_uris),
            },
        )

    def consent(
        self, server, user, response, approve: bool = True, **body: Any
    ) -> Dict[str, str]:
        """The UI's part: follow the redirect to the consent page, show the
        request, post the user's decision; the browser lands on the
        client's redirect URI with this query."""
        assert response.status_code == 302, response.text
        location = response.headers["location"]
        assert location.startswith(CONSENT_URL + "?")
        request_id = query_of(location)["request_id"]
        shown = server.get(
            f"/v1/oauth2/authorize/requests/{request_id}", headers=bearer(user.jwt)
        )
        assert shown.status_code == 200, shown.text
        decided = server.post(
            f"/v1/oauth2/authorize/requests/{request_id}/decision",
            json={"approve": approve, **body},
            headers=bearer(user.jwt),
        )
        assert decided.status_code == 200, decided.text
        return query_of(decided.json()["redirect_to"])

    def tokens(self, server, client: Client, user, **authorize: Any) -> Dict[str, Any]:
        verifier, challenge = pkce()
        landed = self.consent(server, user, client.authorize(challenge, **authorize))
        response = client.redeem(landed["code"], verifier)
        assert response.status_code == 200, response.text
        issued: Dict[str, Any] = response.json()
        return issued

    def jwks(self, server) -> jwt.PyJWKSet:
        metadata = server.get("/.well-known/openid-configuration").json()
        keys = server.get(urlsplit(metadata["jwks_uri"]).path)
        assert keys.status_code == 200, keys.text
        return jwt.PyJWKSet.from_dict(keys.json())

    def verify_id_token(self, server, id_token: str, client: Client) -> Dict[str, Any]:
        key = self.jwks(server)[jwt.get_unverified_header(id_token)["kid"]]
        claims: Dict[str, Any] = jwt.decode(
            id_token,
            key.key,
            algorithms=["RS256", "ES256"],
            audience=client.client_id,
            issuer=ISSUER,
            options={"require": ["iss", "sub", "aud", "exp", "iat", "auth_time"]},
        )
        return claims

    def expire(self, server, model: str, raw: str) -> None:
        """Move a code's or token's expiry into the past, in the database."""
        engine = AuthorizationServer(self.registry(server))
        rows = engine.codes if model == "code" else engine.tokens
        column = rows.db.code_hash if model == "code" else rows.db.token_hash
        row = rows.first(column == digest(raw))
        assert row is not None
        rows.update(row.id, expires_at=now_utc().replace(year=2000))


class TestTheFlow(OAuthFlows):
    def test_code_flow_with_pkce_id_token_userinfo_refresh(self, server, admin_a, user):
        client = self.register(server, admin_a)
        verifier, challenge = pkce()
        started = client.authorize(challenge, nonce="n-123", state="s-456")
        landed = self.consent(server, user, started)
        # RFC 9207: the response names its issuer, and carries the state.
        assert landed["iss"] == ISSUER
        assert landed["state"] == "s-456"

        response = client.redeem(landed["code"], verifier)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        issued = response.json()
        assert issued["token_type"] == "Bearer"
        assert issued["scope"] == "openid profile email"
        assert 0 < issued["expires_in"] <= 600

        claims = self.verify_id_token(server, issued["id_token"], client)
        assert claims["sub"] == user.id
        assert claims["nonce"] == "n-123"
        assert claims["exp"] - claims["iat"] <= 300
        digest_half = jwt.utils.base64url_encode(
            hashlib.sha256(issued["access_token"].encode()).digest()[:16]
        ).decode()
        assert claims["at_hash"] == digest_half

        info = server.get(USERINFO, headers=bearer(issued["access_token"]))
        assert info.status_code == 200, info.text
        assert info.json()["sub"] == user.id
        assert info.json()["email"] == user.email
        assert info.json()["given_name"] == user.first_name

        refreshed = client.refresh(issued["refresh_token"])
        assert refreshed.status_code == 200, refreshed.text
        renewed = refreshed.json()
        assert renewed["refresh_token"] != issued["refresh_token"]
        assert (
            self.verify_id_token(server, renewed["id_token"], client)["sub"] == user.id
        )
        assert (
            server.get(USERINFO, headers=bearer(renewed["access_token"])).status_code
            == 200
        )

    def test_public_client_authenticates_by_pkce_alone(self, server, admin_a, user):
        client = self.register(server, admin_a, is_confidential=False)
        assert client.secret is None and client.method == "none"
        issued = self.tokens(server, client, user)
        assert (
            self.verify_id_token(server, issued["id_token"], client)["sub"] == user.id
        )

    def test_native_client_loopback_redirect_on_any_port(self, server, admin_a, user):
        client = self.register(
            server,
            admin_a,
            is_confidential=False,
            application_type="native",
            redirect_uris=["http://127.0.0.1/cb"],
        )
        client.redirect_uri = "http://127.0.0.1:53682/cb"
        issued = self.tokens(server, client, user)
        assert issued["access_token"]

    def test_client_secret_post(self, server, admin_a, user):
        client = self.register(
            server, admin_a, token_endpoint_auth_method="client_secret_post"
        )
        assert self.tokens(server, client, user)["access_token"]

    def test_discovery(self, server):
        for path in (
            "/.well-known/openid-configuration",
            "/.well-known/oauth-authorization-server",
        ):
            metadata = server.get(path).json()
            assert metadata["issuer"] == ISSUER
            assert metadata["response_types_supported"] == ["code"]
            assert metadata["grant_types_supported"] == [
                "authorization_code",
                "refresh_token",
            ]
            assert metadata["code_challenge_methods_supported"] == ["S256"]
            assert "RS256" in metadata["id_token_signing_alg_values_supported"]
            assert metadata["authorization_response_iss_parameter_supported"] is True
            assert metadata["token_endpoint"] == ISSUER + TOKEN

    def test_prompt_none(self, server, admin_a, user):
        client = self.register(server, admin_a)
        _, challenge = pkce()
        before = client.authorize(challenge, prompt="none")
        assert before.status_code == 302
        assert query_of(before.headers["location"])["error"] == "login_required"
        signed_in = client.server.get(
            AUTHORIZE,
            params={
                "response_type": "code",
                "client_id": client.client_id,
                "redirect_uri": client.redirect_uri,
                "scope": "openid",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "prompt": "none",
            },
            headers=bearer(user.jwt),
            follow_redirects=False,
        )
        assert query_of(signed_in.headers["location"])["error"] == "consent_required"
        self.tokens(server, client, user)
        verifier, challenge = pkce()
        silent = client.server.get(
            AUTHORIZE,
            params={
                "response_type": "code",
                "client_id": client.client_id,
                "redirect_uri": client.redirect_uri,
                "scope": "openid",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "prompt": "none",
            },
            headers=bearer(user.jwt),
            follow_redirects=False,
        )
        code = query_of(silent.headers["location"])["code"]
        assert client.redeem(code, verifier).status_code == 200

    def test_denied_consent(self, server, admin_a, user):
        client = self.register(server, admin_a)
        _, challenge = pkce()
        landed = self.consent(
            server, user, client.authorize(challenge, state="s1"), approve=False
        )
        assert landed["error"] == "access_denied"
        assert landed["state"] == "s1" and landed["iss"] == ISSUER
        assert "code" not in landed

    def test_user_grants_a_subset(self, server, admin_a, user):
        client = self.register(server, admin_a)
        verifier, challenge = pkce()
        landed = self.consent(
            server, user, client.authorize(challenge), scopes=["openid"]
        )
        issued = client.redeem(landed["code"], verifier).json()
        assert issued["scope"] == "openid"
        info = server.get(USERINFO, headers=bearer(issued["access_token"])).json()
        assert "email" not in info

    def test_routes_are_rate_limited(self, server):
        paths = {path for (_method, path) in _RATE_LIMIT_REGISTRY}
        for path in (
            AUTHORIZE,
            TOKEN,
            INTROSPECT,
            REVOKE,
            USERINFO,
            "/v1/oauth2/jwks",
            "/v1/oauth2/keys/rotate",
            "/v1/oauth2/authorize/requests/{request_id}",
            "/v1/oauth2/authorize/requests/{request_id}/decision",
            "/v1/oauth2/clients/register",
            "/v1/oauth2/clients/{id}/secret",
            "/.well-known/openid-configuration",
            "/.well-known/oauth-authorization-server",
        ):
            assert path in paths, path

    def test_consent_decisions_are_throttled(self, server, user):
        for _ in range(10):
            server.post(
                "/v1/oauth2/authorize/requests/none/decision",
                json={"approve": True},
                headers=bearer(user.jwt),
            )
        throttled = server.post(
            "/v1/oauth2/authorize/requests/none/decision",
            json={"approve": True},
            headers=bearer(user.jwt),
        )
        assert throttled.status_code == 429


class TestAuthorizationRefusals(OAuthFlows):
    def test_unregistered_redirect_is_answered_not_redirected(self, server, admin_a):
        client = self.register(server, admin_a)
        _, challenge = pkce()
        for uri in (
            "https://evil.example.test/callback",
            REDIRECT + "/",
            REDIRECT + "?x=1",
        ):
            response = client.authorize(challenge, redirect_uri=uri)
            assert response.status_code == 400, uri
            assert "location" not in response.headers
            assert response.json()["error"] == "invalid_request"

    def test_unknown_client_is_answered_not_redirected(self, server, admin_a):
        client = self.register(server, admin_a)
        client.client_id = "zxc_unknown"
        response = client.authorize(pkce()[1])
        assert response.status_code == 400 and "location" not in response.headers

    def _error(self, response) -> str:
        assert response.status_code == 302, response.text
        landed = query_of(response.headers["location"])
        assert landed["iss"] == ISSUER
        assert "code" not in landed
        return landed["error"]

    def test_pkce_is_required_for_confidential_clients(self, server, admin_a):
        """Hole: auth_oauth2_server issued codes to confidential clients
        without PKCE."""
        client = self.register(server, admin_a)
        assert self._error(client.authorize(None)) == "invalid_request"

    def test_plain_pkce_is_refused(self, server, admin_a):
        """Hole: both replaced servers accepted code_challenge_method=plain."""
        client = self.register(server, admin_a)
        verifier = secrets.token_urlsafe(48)
        response = client.authorize(
            None, code_challenge=verifier, code_challenge_method="plain"
        )
        assert self._error(response) == "invalid_request"

    def test_pkce_method_must_be_named(self, server, admin_a):
        client = self.register(server, admin_a)
        response = client.authorize(None, code_challenge=pkce()[1])
        assert self._error(response) == "invalid_request"

    def test_implicit_and_hybrid_flows_are_refused(self, server, admin_a):
        client = self.register(server, admin_a)
        for response_type in ("token", "id_token", "code id_token", "code token"):
            response = client.authorize(pkce()[1], response_type=response_type)
            assert self._error(response) == "unsupported_response_type"

    def test_scope_beyond_the_client_is_refused(self, server, admin_a):
        client = self.register(server, admin_a, allowed_scopes=["openid"])
        response = client.authorize(pkce()[1], scope="openid email")
        assert self._error(response) == "invalid_scope"

    def test_repeated_parameters_are_refused(self, server, admin_a):
        client = self.register(server, admin_a)
        response = server.get(
            AUTHORIZE
            + f"?response_type=code&client_id={client.client_id}"
            + f"&redirect_uri={REDIRECT}&scope=openid&scope=email"
            + f"&code_challenge={pkce()[1]}&code_challenge_method=S256",
            follow_redirects=False,
        )
        assert self._error(response) == "invalid_request"

    def test_request_objects_are_refused(self, server, admin_a):
        client = self.register(server, admin_a)
        response = client.authorize(pkce()[1], request="eyJhbGciOiJub25lIn0.e30.")
        assert self._error(response) == "request_not_supported"

    def test_unknown_resource_is_refused(self, server, admin_a):
        client = self.register(server, admin_a)
        response = client.authorize(pkce()[1], resource="https://unknown.example")
        assert self._error(response) == "invalid_target"

    def test_a_request_is_decided_once(self, server, admin_a, user):
        client = self.register(server, admin_a)
        started = client.authorize(pkce()[1])
        request_id = query_of(started.headers["location"])["request_id"]
        path = f"/v1/oauth2/authorize/requests/{request_id}/decision"
        first = server.post(path, json={"approve": True}, headers=bearer(user.jwt))
        assert first.status_code == 200, first.text
        again = server.post(path, json={"approve": True}, headers=bearer(user.jwt))
        assert again.status_code == 404

    def test_granted_scopes_are_a_subset(self, server, admin_a, user):
        client = self.register(server, admin_a, allowed_scopes=["openid", "email"])
        started = client.authorize(pkce()[1], scope="openid")
        request_id = query_of(started.headers["location"])["request_id"]
        widened = server.post(
            f"/v1/oauth2/authorize/requests/{request_id}/decision",
            json={"approve": True, "scopes": ["openid", "email"]},
            headers=bearer(user.jwt),
        )
        assert widened.status_code == 400

    def test_prompt_login_needs_a_fresh_sign_in(self, server, admin_a, user):
        client = self.register(server, admin_a)
        started = client.authorize(pkce()[1], prompt="login")
        request_id = query_of(started.headers["location"])["request_id"]
        stale = server.post(
            f"/v1/oauth2/authorize/requests/{request_id}/decision",
            json={"approve": True},
            headers=bearer(user.jwt),
        )
        assert stale.status_code == 401
        assert "login_required" in stale.text

    def test_consent_needs_a_session(self, server, admin_a, user):
        client = self.register(server, admin_a)
        started = client.authorize(pkce()[1])
        request_id = query_of(started.headers["location"])["request_id"]
        anonymous = server.post(
            f"/v1/oauth2/authorize/requests/{request_id}/decision",
            json={"approve": True},
        )
        assert anonymous.status_code == 401


class TestTokenRefusals(OAuthFlows):
    def _code(self, server, client: Client, user, **authorize: Any) -> Tuple[str, str]:
        verifier, challenge = pkce()
        landed = self.consent(server, user, client.authorize(challenge, **authorize))
        return landed["code"], verifier

    def _error(self, response, status: int = 400) -> str:
        assert response.status_code == status, response.text
        assert response.headers["cache-control"] == "no-store"
        error: str = response.json()["error"]
        return error

    def test_code_reuse_revokes_what_it_issued(self, server, admin_a, user):
        """Hole: neither replaced server revoked tokens when a code came back."""
        client = self.register(server, admin_a)
        code, verifier = self._code(server, client, user)
        first = client.redeem(code, verifier).json()
        assert client.introspect(first["access_token"])["active"] is True
        assert self._error(client.redeem(code, verifier)) == "invalid_grant"
        assert client.introspect(first["access_token"])["active"] is False
        assert self._error(client.refresh(first["refresh_token"])) == "invalid_grant"

    def test_wrong_verifier_burns_the_code(self, server, admin_a, user):
        client = self.register(server, admin_a)
        code, verifier = self._code(server, client, user)
        wrong = client.redeem(code, secrets.token_urlsafe(48))
        assert self._error(wrong) == "invalid_grant"
        assert self._error(client.redeem(code, verifier)) == "invalid_grant"

    def test_missing_verifier(self, server, admin_a, user):
        client = self.register(server, admin_a)
        code, _ = self._code(server, client, user)
        assert self._error(client.redeem(code, None)) == "invalid_grant"

    def test_redirect_uri_must_match_the_code(self, server, admin_a, user):
        """Hole: auth_oauth2_server skipped the check when the token request
        left redirect_uri out."""
        client = self.register(
            server, admin_a, redirect_uris=[REDIRECT, REDIRECT + "/two"]
        )
        code, verifier = self._code(server, client, user)
        client.redirect_uri = REDIRECT + "/two"
        assert self._error(client.redeem(code, verifier)) == "invalid_grant"
        code, verifier = self._code(server, client, user)
        missing = client.post(
            TOKEN, grant_type="authorization_code", code=code, code_verifier=verifier
        )
        assert self._error(missing) == "invalid_request"

    def test_code_is_bound_to_its_client(self, server, admin_a, user):
        client = self.register(server, admin_a)
        other = self.register(server, admin_a)
        code, verifier = self._code(server, client, user)
        assert self._error(other.redeem(code, verifier)) == "invalid_grant"
        assert client.redeem(code, verifier).status_code == 200

    def test_expired_code(self, server, admin_a, user):
        client = self.register(server, admin_a)
        code, verifier = self._code(server, client, user)
        self.expire(server, "code", code)
        assert self._error(client.redeem(code, verifier)) == "invalid_grant"

    def test_bad_client_secret(self, server, admin_a, user):
        client = self.register(server, admin_a)
        code, verifier = self._code(server, client, user)
        client.secret = "zxcs_" + secrets.token_urlsafe(32)
        response = client.redeem(code, verifier)
        assert self._error(response, 401) == "invalid_client"
        assert response.headers["www-authenticate"].startswith("Basic")

    def test_registered_auth_method_is_enforced(self, server, admin_a, user):
        client = self.register(server, admin_a)
        code, verifier = self._code(server, client, user)
        client.method = "client_secret_post"
        assert self._error(client.redeem(code, verifier), 401) == "invalid_client"
        client.method = "none"
        assert self._error(client.redeem(code, verifier), 401) == "invalid_client"

    def test_public_client_sending_a_secret(self, server, admin_a, user):
        client = self.register(server, admin_a, is_confidential=False)
        code, verifier = self._code(server, client, user)
        response = server.post(
            TOKEN,
            json={
                "grant_type": "authorization_code",
                "client_id": client.client_id,
                "client_secret": "anything",
                "code": code,
                "redirect_uri": client.redirect_uri,
                "code_verifier": verifier,
            },
        )
        assert self._error(response, 401) == "invalid_client"

    def test_other_grants_are_refused(self, server, admin_a):
        client = self.register(server, admin_a)
        for grant_type in ("password", "client_credentials", "implicit"):
            response = client.post(
                TOKEN, grant_type=grant_type, username="u", password="p"
            )
            assert self._error(response) == "unsupported_grant_type"

    def test_refresh_reuse_revokes_the_family(self, server, admin_a, user):
        """Hole: auth_oauth2_server only refused a replayed refresh token;
        the tokens rotated from it stayed live for whoever replayed it."""
        client = self.register(server, admin_a)
        first = self.tokens(server, client, user)
        second = client.refresh(first["refresh_token"]).json()
        assert client.introspect(second["access_token"])["active"] is True
        replay = client.refresh(first["refresh_token"])
        assert self._error(replay) == "invalid_grant"
        assert client.introspect(second["access_token"])["active"] is False
        assert client.introspect(first["access_token"])["active"] is False
        assert self._error(client.refresh(second["refresh_token"])) == "invalid_grant"

    def test_refresh_cannot_widen_the_scope(self, server, admin_a, user):
        """Hole: auth_oauth2_server refreshed with whatever scope was stored,
        oauth_provider had no refresh at all; here a wider scope is refused
        and a narrower one granted."""
        client = self.register(server, admin_a)
        issued = self.tokens(server, client, user, scope="openid email")
        wider = client.refresh(issued["refresh_token"], scope="openid email profile")
        assert self._error(wider) == "invalid_scope"
        issued = self.tokens(server, client, user, scope="openid email")
        narrower = client.refresh(issued["refresh_token"], scope="openid")
        assert narrower.status_code == 200 and narrower.json()["scope"] == "openid"
        again = client.refresh(narrower.json()["refresh_token"], scope="openid email")
        assert again.status_code == 200, "the family keeps the authorized scope"

    def test_refresh_token_of_another_client(self, server, admin_a, user):
        client = self.register(server, admin_a)
        other = self.register(server, admin_a)
        issued = self.tokens(server, client, user)
        assert self._error(other.refresh(issued["refresh_token"])) == "invalid_grant"

    def test_expired_refresh_token(self, server, admin_a, user):
        client = self.register(server, admin_a)
        issued = self.tokens(server, client, user)
        self.expire(server, "token", issued["refresh_token"])
        assert self._error(client.refresh(issued["refresh_token"])) == "invalid_grant"

    def test_tokens_are_single_purpose(self, server, admin_a, user):
        client = self.register(server, admin_a)
        issued = self.tokens(server, client, user)
        assert self._error(client.refresh(issued["access_token"])) == "invalid_grant"
        as_bearer = server.get(USERINFO, headers=bearer(issued["refresh_token"]))
        assert as_bearer.status_code == 401
        id_token_bearer = server.get(USERINFO, headers=bearer(issued["id_token"]))
        assert id_token_bearer.status_code == 401
        code, verifier = self._code(server, client, user)
        assert server.get(USERINFO, headers=bearer(code)).status_code == 401


class TestAccessTokens(OAuthFlows):
    def test_audience_restriction(self, server, admin_a, user):
        api = self.resource_server(server)
        client = self.register(server, admin_a)
        bystander = self.register(server, admin_a)
        for_api = self.tokens(server, client, user, resource=RESOURCE)
        assert api.introspect(for_api["access_token"])["aud"] == [RESOURCE]
        assert bystander.introspect(for_api["access_token"]) == {"active": False}
        wrong = server.get(USERINFO, headers=bearer(for_api["access_token"]))
        assert wrong.status_code == 401
        assert 'error="invalid_token"' in wrong.headers["www-authenticate"]
        default = self.tokens(server, client, user)
        assert api.introspect(default["access_token"]) == {"active": False}
        assert client.introspect(default["access_token"])["aud"] == [ISSUER]

    def test_token_endpoint_cannot_widen_the_audience(self, server, admin_a, user):
        self.resource_server(server)
        client = self.register(server, admin_a)
        verifier, challenge = pkce()
        landed = self.consent(server, user, client.authorize(challenge))
        unauthorized = client.redeem(landed["code"], verifier, resource=RESOURCE)
        assert unauthorized.json()["error"] == "invalid_target"

    def test_expired_access_token(self, server, admin_a, user):
        client = self.register(server, admin_a)
        issued = self.tokens(server, client, user)
        self.expire(server, "token", issued["access_token"])
        assert (
            server.get(USERINFO, headers=bearer(issued["access_token"])).status_code
            == 401
        )
        assert client.introspect(issued["access_token"]) == {"active": False}

    def test_userinfo_needs_openid(self, server, admin_a, user):
        client = self.register(server, admin_a, allowed_scopes=["openid", "email"])
        issued = self.tokens(server, client, user, scope="email")
        assert "id_token" not in issued
        response = server.get(USERINFO, headers=bearer(issued["access_token"]))
        assert response.status_code == 403
        assert 'error="insufficient_scope"' in response.headers["www-authenticate"]

    def test_introspection_is_for_confidential_clients(self, server, admin_a, user):
        """Hole: auth_oauth2_server let a public client, which has no
        secret, introspect: anyone knowing its client_id could."""
        public = self.register(server, admin_a, is_confidential=False)
        issued = self.tokens(server, public, user)
        response = public.post(INTROSPECT, token=issued["access_token"])
        assert response.status_code == 401
        assert response.json()["error"] == "invalid_client"

    def test_introspection_shape(self, server, admin_a, user):
        client = self.register(server, admin_a)
        issued = self.tokens(server, client, user)
        answer = client.introspect(issued["access_token"])
        assert answer["active"] is True
        assert answer["sub"] == user.id and answer["client_id"] == client.client_id
        assert answer["iss"] == ISSUER and answer["token_type"] == "Bearer"
        assert answer["scope"] == "openid profile email"
        assert client.introspect("zxat_" + secrets.token_urlsafe(32)) == {
            "active": False
        }

    def test_revocation(self, server, admin_a, user):
        client = self.register(server, admin_a)
        other = self.register(server, admin_a)
        issued = self.tokens(server, client, user)
        not_theirs = other.post(REVOKE, token=issued["access_token"])
        assert not_theirs.status_code == 200
        assert client.introspect(issued["access_token"])["active"] is True
        assert client.post(REVOKE, token=issued["access_token"]).status_code == 200
        assert client.introspect(issued["access_token"]) == {"active": False}
        assert client.refresh(issued["refresh_token"]).status_code == 200

    def test_revoking_a_refresh_token_revokes_its_family(self, server, admin_a, user):
        client = self.register(server, admin_a)
        issued = self.tokens(server, client, user)
        assert client.post(REVOKE, token=issued["refresh_token"]).status_code == 200
        assert client.introspect(issued["access_token"]) == {"active": False}
        assert (
            client.refresh(issued["refresh_token"]).json()["error"] == "invalid_grant"
        )


class TestClientsAndGrants(OAuthFlows):
    def test_secrets_and_tokens_are_stored_as_digests(self, server, admin_a, user):
        """Hole: auth_oauth2_server stored client secrets, codes and tokens
        as they were issued."""
        client = self.register(server, admin_a)
        issued = self.tokens(server, client, user)
        engine = AuthorizationServer(self.registry(server))
        stored = engine.clients.first(engine.clients.db.client_id == client.client_id)
        assert stored.client_secret_hash == digest(client.secret)
        for raw in (issued["access_token"], issued["refresh_token"]):
            assert engine.tokens.first(engine.tokens.db.token_hash == digest(raw))
            assert not engine.tokens.find(engine.tokens.db.token_hash == raw)
        listed = server.get("/v1/oauth2/clients", headers=bearer(admin_a.jwt))
        assert listed.status_code == 200, listed.text
        assert "client_secret_hash" not in listed.text
        assert client.secret not in listed.text
        assert stored.client_secret_hash not in listed.text

    def test_registration_rules(self, server, admin_a):
        def register(**fields: Any) -> Any:
            body = {"name": "x", "redirect_uris": [REDIRECT], **fields}
            return server.post(
                "/v1/oauth2/clients/register", json=body, headers=bearer(admin_a.jwt)
            )

        assert register(redirect_uris=["http://rp.example.test/cb"]).status_code == 400
        assert register(allowed_scopes=["no-such-scope"]).status_code == 400
        assert register(application_type="native").status_code == 400
        assert (
            register(
                is_confidential=False, token_endpoint_auth_method="client_secret_basic"
            ).status_code
            == 400
        )
        assert register(resource_uri=RESOURCE).status_code == 403
        created = server.post(
            "/v1/oauth2/clients",
            json={"o_auth_client": {"name": "x"}, "oauth_client": {"name": "x"}},
            headers=bearer(admin_a.jwt),
        )
        assert created.status_code in (403, 404, 405)

    def test_updates_are_validated(self, server, admin_a, user_b):
        client = self.register(server, admin_a)
        engine = AuthorizationServer(self.registry(server))
        row = engine.clients.first(engine.clients.db.client_id == client.client_id)
        path = f"/v1/oauth2/clients/{row.id}"

        def put(user, **fields: Any) -> Any:
            return server.put(
                path, json={"oauth_client": fields}, headers=bearer(user.jwt)
            )

        insecure = put(admin_a, redirect_uris=json.dumps(["http://rp.example.test/cb"]))
        assert insecure.status_code == 400, insecure.text
        assert put(admin_a, allowed_scopes="openid no-such-scope").status_code == 400
        assert put(user_b, name="taken over").status_code in (403, 404)
        renamed = put(
            admin_a, name="Renamed", redirect_uris=json.dumps([REDIRECT + "/v2"])
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["oauth_client"]["redirect_uris"] == json.dumps(
            [REDIRECT + "/v2"]
        )

    def test_graphql_exposes_no_protocol_state(self, server, admin_a):
        """The registration manager must not read as the self-scoped user
        manager (a method named ``register``), which would expose every CRUD
        mutation of clients; tokens, codes and keys have no GraphQL at all."""
        query = "{ __schema { queryType { fields { name } } mutationType { fields { name } } } }"
        response = server.post(
            "/graphql", json={"query": query}, headers=bearer(admin_a.jwt)
        )
        assert response.status_code == 200, response.text
        schema = response.json()["data"]["__schema"]
        names = {f["name"].lower() for f in schema["queryType"]["fields"]}
        names |= {
            f["name"].lower()
            for f in (schema["mutationType"] or {"fields": []})["fields"]
        }
        oauth = {name for name in names if "oauth" in name}
        assert oauth, names
        for forbidden in (
            "token",
            "code",
            "signing",
            "authorization_request",
            "authorizationrequest",
        ):
            assert not any(forbidden in name for name in oauth), oauth
        assert not any("create" in name for name in oauth), oauth

    def test_only_the_owner_manages_a_client(self, server, admin_a, user_b):
        client = self.register(server, admin_a)
        engine = AuthorizationServer(self.registry(server))
        row = engine.clients.first(engine.clients.db.client_id == client.client_id)
        assert server.get(
            f"/v1/oauth2/clients/{row.id}", headers=bearer(user_b.jwt)
        ).status_code in (403, 404)
        assert server.delete(
            f"/v1/oauth2/clients/{row.id}", headers=bearer(user_b.jwt)
        ).status_code in (403, 404)
        rotate = server.post(
            f"/v1/oauth2/clients/{row.id}/secret", json={}, headers=bearer(user_b.jwt)
        )
        assert rotate.status_code in (403, 404)

    def test_secret_rotation(self, server, admin_a, user):
        client = self.register(server, admin_a)
        engine = AuthorizationServer(self.registry(server))
        row = engine.clients.first(engine.clients.db.client_id == client.client_id)
        rotated = server.post(
            f"/v1/oauth2/clients/{row.id}/secret", json={}, headers=bearer(admin_a.jwt)
        )
        assert rotated.status_code == 200, rotated.text
        old_secret, client.secret = client.secret, rotated.json()["client_secret"]
        assert self.tokens(server, client, user)["access_token"]
        client.secret = old_secret
        assert client.post(INTROSPECT, token="x").status_code == 401

    def test_deleting_a_client_ends_its_tokens(self, server, admin_a, user):
        client = self.register(server, admin_a)
        issued = self.tokens(server, client, user)
        engine = AuthorizationServer(self.registry(server))
        row = engine.clients.first(engine.clients.db.client_id == client.client_id)
        deleted = server.delete(
            f"/v1/oauth2/clients/{row.id}", headers=bearer(admin_a.jwt)
        )
        assert deleted.status_code in (200, 204), deleted.text
        assert (
            server.get(USERINFO, headers=bearer(issued["access_token"])).status_code
            == 401
        )

    def test_user_lists_and_revokes_consent(self, server, admin_a, user, user_b):
        """Hole: neither replaced server stored a consent the user could see
        or withdraw."""
        client = self.register(server, admin_a)
        issued = self.tokens(server, client, user)
        listed = server.get("/v1/oauth2/grants", headers=bearer(user.jwt))
        assert listed.status_code == 200, listed.text
        grants = [
            g
            for g in listed.json()["oauth_grants"]
            if g["client_id"] == client.client_id
        ]
        assert len(grants) == 1
        assert set(grants[0]["scopes"].split()) == {"openid", "profile", "email"}
        grant_id = grants[0]["id"]
        theirs = server.delete(
            f"/v1/oauth2/grants/{grant_id}", headers=bearer(user_b.jwt)
        )
        assert theirs.status_code in (403, 404)
        revoked = server.delete(
            f"/v1/oauth2/grants/{grant_id}", headers=bearer(user.jwt)
        )
        assert revoked.status_code in (200, 204), revoked.text
        assert (
            server.get(USERINFO, headers=bearer(issued["access_token"])).status_code
            == 401
        )
        assert (
            client.refresh(issued["refresh_token"]).json()["error"] == "invalid_grant"
        )


class TestSigningKeys(OAuthFlows):
    def test_private_keys_are_encrypted_at_rest(self, server):
        self.jwks(server)
        engine = AuthorizationServer(self.registry(server))
        keys = engine.keys.rows.find(engine.keys.rows.db.status == "active")
        assert keys
        assert all(key.private_key.startswith("fernet:") for key in keys)
        published = server.get("/v1/oauth2/jwks").json()["keys"]
        assert all("d" not in key and "p" not in key for key in published)

    def test_rotation_keeps_old_tokens_verifiable(self, server, admin_a, user):
        client = self.register(server, admin_a)
        before = self.tokens(server, client, user)["id_token"]
        old_kid = jwt.get_unverified_header(before)["kid"]
        refused = server.post(
            "/v1/oauth2/keys/rotate", json={}, headers=bearer(user.jwt)
        )
        assert refused.status_code == 403
        engine = AuthorizationServer(self.registry(server))
        new_kids = engine.keys.rotate()
        kids = {key["kid"] for key in server.get("/v1/oauth2/jwks").json()["keys"]}
        assert old_kid in kids and set(new_kids) <= kids
        assert self.verify_id_token(server, before, client)["sub"] == user.id
        after = self.tokens(server, client, user)["id_token"]
        assert jwt.get_unverified_header(after)["kid"] in new_kids
        assert self.verify_id_token(server, after, client)["sub"] == user.id

    def test_keys_sealed_under_another_fernet_key(self, server, admin_a, user, set_env):
        """The suite's FRAMEWORK_FERNET_KEY sealed the signing keys; under a
        different one they cannot be opened, the server says so (503), and a
        rotation recovers."""
        client = self.register(server, admin_a)
        suite_key = env("FRAMEWORK_FERNET_KEY")
        self.tokens(server, client, user)
        set_env("FRAMEWORK_FERNET_KEY", Fernet.generate_key().decode())
        verifier, challenge = pkce()
        landed = self.consent(server, user, client.authorize(challenge))
        assert client.redeem(landed["code"], verifier).status_code == 503
        engine = AuthorizationServer(self.registry(server))
        engine.keys.rotate()
        assert self.tokens(server, client, user)["id_token"]
        set_env("FRAMEWORK_FERNET_KEY", suite_key)
        engine.keys.rotate()
        assert self.tokens(server, client, user)["id_token"]

    def test_a_forged_id_token_does_not_verify(self, server, admin_a, user):
        client = self.register(server, admin_a)
        genuine = self.tokens(server, client, user)["id_token"]
        header, payload, signature = genuine.split(".")
        claims = jwt.decode(genuine, options={"verify_signature": False})
        claims["sub"] = "someone-else"
        forged_payload = jwt.utils.base64url_encode(
            json.dumps(claims).encode()
        ).decode()
        with pytest.raises(jwt.InvalidSignatureError):
            self.verify_id_token(
                server, f"{header}.{forged_payload}.{signature}", client
            )


class TestFormEncoding(OAuthFlows):
    def test_token_request_form_encoded(self, server, admin_a, user):
        client = self.register(server, admin_a)
        verifier, challenge = pkce()
        landed = self.consent(server, user, client.authorize(challenge))
        headers, _ = client.credentials()
        response = server.post(
            TOKEN,
            data={
                "grant_type": "authorization_code",
                "code": landed["code"],
                "redirect_uri": client.redirect_uri,
                "code_verifier": verifier,
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text
