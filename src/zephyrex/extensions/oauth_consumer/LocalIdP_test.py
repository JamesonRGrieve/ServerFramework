# SPDX-License-Identifier: AGPL-3.0-or-later
"""A real identity provider on loopback, for the oauth_consumer tests.

One HTTP server speaks three providers' protocols, each as its
specification (or, for GitHub and Amazon, their documentation) has it:

- an OpenID provider: discovery metadata, an authorization endpoint that
  requires PKCE S256, a token endpoint that authenticates the client and
  checks the code verifier, ID tokens signed with a real RSA key published
  in a JWKS (``rotate_key`` replaces it), and a userinfo endpoint;
- GitHub's OAuth 2.0 (``/login/oauth/*``, ``/api/user``, ``/api/user/emails``);
- Login with Amazon (``/ap/oa``, ``/auth/o2/token``, ``/user/profile``).

Codes are single-use and bound to their client, redirect URI and code
challenge. Knobs turn one property wrong at a time: ``id_token_claims``
overrides the next ID tokens' claims (``None`` removes one), and
``forge_signatures`` signs them with a key that is not published.

The tests here check the provider itself refuses what a real one would.
"""

import base64
import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Iterator, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

ID_TOKEN_LIFETIME_SECONDS = 300
ACCESS_TOKEN_LIFETIME_SECONDS = 3600
RSA_KEY_BITS = 2048
RSA_PUBLIC_EXPONENT = 65537
ALGORITHM = "RS256"


def _new_key() -> Tuple[str, rsa.RSAPrivateKey]:
    key = rsa.generate_private_key(
        public_exponent=RSA_PUBLIC_EXPONENT, key_size=RSA_KEY_BITS
    )
    return secrets.token_hex(8), key


def _public_jwk(kid: str, key: rsa.RSAPrivateKey) -> Dict[str, Any]:
    jwk: Dict[str, Any] = json.loads(
        jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())
    )
    return {**jwk, "kid": kid, "use": "sig", "alg": ALGORITHM}


def s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def at_hash(access_token: str) -> str:
    digest = hashlib.sha256(access_token.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest[:16]).rstrip(b"=").decode("ascii")


@dataclass
class _Grant:
    flavor: str
    client_id: str
    redirect_uri: str
    challenge: str
    nonce: Optional[str]
    scope: str


@dataclass
class Account:
    """The user signed in at the provider."""

    sub: str = field(default_factory=lambda: secrets.token_hex(8))
    email: Optional[str] = None
    email_verified: bool = True
    given_name: str = "Ada"
    family_name: str = "Lovelace"
    preferred_username: Optional[str] = None


class LocalIdP:
    """An identity provider at ``base_url`` with one client registration."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uris: List[str],
        iss_parameter: bool = False,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uris = list(redirect_uris)
        self.iss_parameter = iss_parameter
        self.account = Account(email=f"{secrets.token_hex(6)}@example.com")
        self.id_token_claims: Dict[str, Any] = {}
        self.forge_signatures = False
        self.id_token_kid: Optional[str] = None
        # A multi-tenant provider publishes a templated issuer.
        self.metadata_issuer: Optional[str] = None
        self.userinfo_sub: Optional[str] = None
        self.jwks_fetches = 0
        self.token_forms: List[Dict[str, str]] = []
        self._keys = [_new_key()]
        self._forger = _new_key()[1]
        self._codes: Dict[str, _Grant] = {}
        self._access: Dict[str, str] = {}
        self._refresh: Dict[str, str] = {}
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(self))
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.host = f"127.0.0.1:{self._server.server_address[1]}"
        self.base_url = f"http://{self.host}"

    @property
    def issuer(self) -> str:
        return self.base_url

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def rotate_key(self) -> None:
        """Sign with a new key and publish only it."""
        self._keys = [_new_key()]

    def metadata(self) -> Dict[str, Any]:
        base = self.base_url
        return {
            "issuer": self.metadata_issuer or self.issuer,
            "authorization_endpoint": f"{base}/authorize",
            "token_endpoint": f"{base}/token",
            "userinfo_endpoint": f"{base}/userinfo",
            "jwks_uri": f"{base}/jwks",
            "response_types_supported": ["code"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": [ALGORITHM],
            "token_endpoint_auth_methods_supported": [
                "client_secret_basic",
                "client_secret_post",
            ],
            "code_challenge_methods_supported": ["S256"],
            "authorization_response_iss_parameter_supported": self.iss_parameter,
        }

    def jwks(self) -> Dict[str, Any]:
        self.jwks_fetches += 1
        return {"keys": [_public_jwk(kid, key) for kid, key in self._keys]}

    # -- the browser ---------------------------------------------------------

    def sign_in(self, authorize_url: str) -> Dict[str, str]:
        """What a browser does with ``authorize_url``: the user signs in
        and the provider redirects; the redirect's query parameters."""
        answer = httpx.get(authorize_url, follow_redirects=False, timeout=10)
        assert answer.status_code == 302, answer.text
        location = urlparse(answer.headers["location"])
        return {k: v[0] for k, v in parse_qs(location.query).items()}

    # -- endpoints -----------------------------------------------------------

    def authorize(
        self, flavor: str, query: Dict[str, str]
    ) -> Tuple[int, Dict[str, str], bytes]:
        redirect_uri = query.get("redirect_uri", "")
        if (
            query.get("client_id") != self.client_id
            or redirect_uri not in self.redirect_uris
            or query.get("response_type") != "code"
            or query.get("code_challenge_method") != "S256"
            or not query.get("code_challenge")
        ):
            return 400, {}, b"invalid authorization request"
        code = secrets.token_urlsafe(24)
        with self._lock:
            self._codes[code] = _Grant(
                flavor=flavor,
                client_id=self.client_id,
                redirect_uri=redirect_uri,
                challenge=query["code_challenge"],
                nonce=query.get("nonce"),
                scope=query.get("scope", ""),
            )
        params = {"code": code, "state": query.get("state", "")}
        if self.iss_parameter and flavor == "oidc":
            params["iss"] = self.issuer
        return 302, {"Location": f"{redirect_uri}?{urlencode(params)}"}, b""

    def _client_authenticated(
        self, headers: Dict[str, str], form: Dict[str, str]
    ) -> bool:
        basic = headers.get("authorization", "")
        if basic.startswith("Basic "):
            client_id, _, secret = base64.b64decode(basic[6:]).decode().partition(":")
            client_id, secret = unquote(client_id), unquote(secret)
        else:
            client_id, secret = form.get("client_id", ""), form.get("client_secret", "")
        return client_id == self.client_id and secrets.compare_digest(
            secret.encode(), self.client_secret.encode()
        )

    def token(
        self, flavor: str, headers: Dict[str, str], form: Dict[str, str]
    ) -> Tuple[int, Dict[str, Any]]:
        self.token_forms.append(dict(form))
        refused = 200 if flavor == "github" else 400
        if not self._client_authenticated(headers, form):
            return (200 if flavor == "github" else 401), {"error": "invalid_client"}
        if form.get("grant_type") == "refresh_token":
            with self._lock:
                sub = self._refresh.get(form.get("refresh_token", ""))
            if sub is None:
                return refused, {"error": "invalid_grant"}
            return 200, self._tokens(sub, flavor, None, "openid")
        if form.get("grant_type") != "authorization_code":
            return 400, {"error": "unsupported_grant_type"}
        with self._lock:
            grant = self._codes.pop(form.get("code", ""), None)
        if (
            grant is None
            or grant.flavor != flavor
            or grant.redirect_uri != form.get("redirect_uri")
            or s256(form.get("code_verifier", "")) != grant.challenge
        ):
            return refused, {"error": "invalid_grant"}
        return 200, self._tokens(self.account.sub, flavor, grant.nonce, grant.scope)

    def _tokens(
        self, sub: str, flavor: str, nonce: Optional[str], scope: str
    ) -> Dict[str, Any]:
        access, refresh = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        with self._lock:
            self._access[access] = sub
            self._refresh[refresh] = sub
        answer: Dict[str, Any] = {
            "access_token": access,
            "token_type": "bearer" if flavor == "github" else "Bearer",
            "expires_in": ACCESS_TOKEN_LIFETIME_SECONDS,
            "refresh_token": refresh,
            "scope": scope,
        }
        if flavor == "oidc" and nonce is not None:
            answer["id_token"] = self.id_token(sub, nonce, access)
        return answer

    def id_token(self, sub: str, nonce: str, access_token: str) -> str:
        now = int(time.time())
        account = self.account
        claims: Dict[str, Any] = {
            "iss": self.issuer,
            "sub": sub,
            "aud": self.client_id,
            "exp": now + ID_TOKEN_LIFETIME_SECONDS,
            "iat": now,
            "nonce": nonce,
            "at_hash": at_hash(access_token),
            "email": account.email,
            "email_verified": account.email_verified,
            "given_name": account.given_name,
            "family_name": account.family_name,
            "name": f"{account.given_name} {account.family_name}",
        }
        if account.preferred_username:
            claims["preferred_username"] = account.preferred_username
        for name, value in self.id_token_claims.items():
            if value is None:
                claims.pop(name, None)
            else:
                claims[name] = value
        kid, key = self._keys[0]
        signing_key = self._forger if self.forge_signatures else key
        return jwt.encode(
            claims,
            signing_key,
            algorithm=ALGORITHM,
            headers={"kid": self.id_token_kid or kid},
        )

    def bearer(self, headers: Dict[str, str]) -> Optional[str]:
        token = headers.get("authorization", "").removeprefix("Bearer ").strip()
        with self._lock:
            return self._access.get(token)

    def userinfo(self, sub: str) -> Dict[str, Any]:
        account = self.account
        return {
            "sub": self.userinfo_sub or sub,
            "name": f"{account.given_name} {account.family_name}",
            "given_name": account.given_name,
            "family_name": account.family_name,
            "preferred_username": account.preferred_username or "ada",
            "picture": "https://example.com/ada.png",
        }

    def github_user(self, sub: str) -> Dict[str, Any]:
        return {"id": int(sub, 16), "login": "ada", "name": "Ada Lovelace"}

    def github_emails(self) -> List[Dict[str, Any]]:
        return [
            {"email": "public@example.com", "primary": False, "verified": True},
            {
                "email": self.account.email,
                "primary": True,
                "verified": self.account.email_verified,
            },
        ]

    def amazon_profile(self, sub: str) -> Dict[str, Any]:
        return {
            "user_id": f"amzn1.account.{sub}",
            "name": "Ada Lovelace",
            "email": self.account.email,
        }


def _handler(idp: LocalIdP) -> type:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def _send(self, status: int, headers: Dict[str, str], body: bytes) -> None:
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: Any) -> None:
            self._send(
                status,
                {"Content-Type": "application/json"},
                json.dumps(payload).encode(),
            )

        def _headers(self) -> Dict[str, str]:
            return {k.lower(): v for k, v in self.headers.items()}

        def do_GET(self) -> None:
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            flavors = {
                "/authorize": "oidc",
                "/login/oauth/authorize": "github",
                "/ap/oa": "amazon",
            }
            if url.path in flavors:
                self._send(*idp.authorize(flavors[url.path], query))
                return
            if url.path.endswith("/.well-known/openid-configuration"):
                self._json(200, idp.metadata())
                return
            if url.path == "/jwks":
                self._json(200, idp.jwks())
                return
            sub = idp.bearer(self._headers())
            if sub is None:
                self._json(401, {"error": "invalid_token"})
                return
            answers = {
                "/userinfo": lambda: idp.userinfo(sub),
                "/api/user": lambda: idp.github_user(sub),
                "/api/user/emails": idp.github_emails,
                "/user/profile": lambda: idp.amazon_profile(sub),
            }
            if url.path in answers:
                self._json(200, answers[url.path]())
                return
            self._json(404, {"error": "not_found"})

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            form = {
                k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()
            }
            flavors = {
                "/token": "oidc",
                "/login/oauth/access_token": "github",
                "/auth/o2/token": "amazon",
            }
            flavor = flavors.get(urlparse(self.path).path)
            if flavor is None:
                self._json(404, {"error": "not_found"})
                return
            self._json(*idp.token(flavor, self._headers(), form))

    return Handler


def start_idp(
    monkeypatch: pytest.MonkeyPatch,
    *,
    redirect_uris: List[str],
    client_id: Optional[str] = None,
    iss_parameter: bool = False,
) -> LocalIdP:
    """A provider whose host this server may reach (the SSRF guard refuses
    loopback otherwise). The caller stops it."""
    idp = LocalIdP(
        client_id=client_id or f"client-{secrets.token_hex(4)}",
        client_secret=secrets.token_urlsafe(16),
        redirect_uris=redirect_uris,
        iss_parameter=iss_parameter,
    )
    monkeypatch.setenv("EGRESS_ALLOWED_HOSTS", idp.host)
    return idp


REDIRECT = "https://app.example.com/user/close/test"


@pytest.fixture
def idp(monkeypatch: pytest.MonkeyPatch) -> Iterator[LocalIdP]:
    provider = start_idp(monkeypatch, redirect_uris=[REDIRECT])
    yield provider
    provider.stop()


def _authorize(idp: LocalIdP, verifier: str) -> str:
    query = urlencode(
        {
            "client_id": idp.client_id,
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "scope": "openid email",
            "state": "s",
            "nonce": "n",
            "code_challenge": s256(verifier),
            "code_challenge_method": "S256",
        }
    )
    return idp.sign_in(f"{idp.base_url}/authorize?{query}")["code"]


def _exchange(idp: LocalIdP, code: str, verifier: str) -> httpx.Response:
    return httpx.post(
        f"{idp.base_url}/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "code_verifier": verifier,
        },
        auth=(idp.client_id, idp.client_secret),
        timeout=10,
    )


class TestTheLocalProviderIsStrict:
    """The provider the flow is tested against refuses what a real one does,
    so a passing flow test means the flow did it right."""

    def test_an_authorization_request_without_pkce_is_refused(self, idp):
        query = urlencode(
            {
                "client_id": idp.client_id,
                "redirect_uri": REDIRECT,
                "response_type": "code",
                "state": "s",
            }
        )
        answer = httpx.get(f"{idp.base_url}/authorize?{query}", follow_redirects=False)
        assert answer.status_code == 400

    def test_a_wrong_verifier_is_refused_and_the_code_is_single_use(self, idp):
        verifier = secrets.token_urlsafe(64)
        code = _authorize(idp, verifier)
        assert _exchange(idp, code, "x" * 64).json() == {"error": "invalid_grant"}
        assert _exchange(idp, code, verifier).status_code == 400

        code = _authorize(idp, verifier)
        issued = _exchange(idp, code, verifier)
        assert issued.status_code == 200 and "id_token" in issued.json()
        assert _exchange(idp, code, verifier).status_code == 400

    def test_id_tokens_verify_against_the_published_keys(self, idp):
        verifier = secrets.token_urlsafe(64)
        tokens = _exchange(idp, _authorize(idp, verifier), verifier).json()
        keys = jwt.PyJWKSet.from_dict(httpx.get(f"{idp.base_url}/jwks").json())
        claims = jwt.decode(
            tokens["id_token"],
            keys.keys[0].key,
            algorithms=[ALGORITHM],
            audience=idp.client_id,
            issuer=idp.issuer,
        )
        assert claims["nonce"] == "n" and claims["at_hash"] == at_hash(
            tokens["access_token"]
        )
