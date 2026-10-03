# SPDX-License-Identifier: AGPL-3.0-or-later
"""The OAuth 2.0 authorization server and OpenID Connect provider.

Authorization code flow only, to RFC 9700 (the OAuth 2.0 Security BCP):

- No implicit grant (``response_type`` is ``code`` only) and no resource
  owner password, client credentials or any other grant at the token
  endpoint.
- PKCE with S256 for every client, confidential or public (RFC 7636).
- Exact redirect URI matching (any port for a native client's loopback
  redirect, RFC 8252 §7.3). An unknown client or an unregistered redirect
  is answered here, never redirected to.
- Codes are single-use, last a minute, and are bound to the client, the
  redirect URI and the PKCE challenge; a replayed code revokes every token
  issued from it.
- Refresh tokens rotate on every use; a replayed one revokes its family.
  They are not sender-constrained (no DPoP or mTLS binding).
- Access tokens are opaque, short-lived, restricted to an audience (the
  RFC 8707 ``resource`` requested, else this issuer), introspectable
  (RFC 7662) and revocable (RFC 7009).
- Authorization responses carry ``iss`` (RFC 9207).
- Confidential clients authenticate with the method they registered
  (``client_secret_basic`` or ``client_secret_post``); public clients
  with nothing but PKCE.

OpenID Connect: ID tokens signed (RS256, and ES256 when configured) by
keys from a rotatable set (:class:`SigningKeys`) whose private halves are
encrypted at rest with ``lib.SecretEncryption`` (``FRAMEWORK_FERNET_KEY``);
discovery, JWKS and UserInfo. The subject is public: the user's id, the
same for every client.

Sign-in is the framework's: the authorization endpoint stores the
validated request and sends the browser to the UI's consent page
(``OAUTH_PROVIDER_CONSENT_URL``) with its id; the UI, with the user signed
in, shows the request and posts the user's decision, which answers with
the URL to send the browser to.
"""

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi import HTTPException
from starlette.responses import JSONResponse, RedirectResponse, Response

from zephyrex.extensions.oauth_provider import Config
from zephyrex.extensions.oauth_provider.BLL_OAuthProvider import (
    ACCESS_TOKEN,
    KEY_ACTIVE,
    KEY_RETIRED,
    KEY_RETIRING,
    REFRESH_TOKEN,
    OauthAuthorizationCodeModel,
    OauthAuthorizationRequestModel,
    OauthClientModel,
    OauthGrantManager,
    OauthGrantModel,
    OauthSigningKeyModel,
    OauthTokenModel,
    now_utc,
    revoke_tokens,
    supported_scopes,
)
from zephyrex.extensions.oauth_provider.OAuthProtocol import (
    AUTH_METHOD_BASIC,
    AUTH_METHOD_NONE,
    AUTH_METHOD_POST,
    CONFIDENTIAL_AUTH_METHODS,
    NO_STORE_HEADERS,
    PKCE_METHOD,
    SCOPE_EMAIL,
    SCOPE_OPENID,
    SCOPE_PROFILE,
    TOKEN_TYPE_BEARER,
    OAuthError,
    b64url,
    digest,
    digest_matches,
    half_hash,
    invalid_request,
    is_s256_challenge,
    join_scope,
    json_list,
    mint_secret,
    parse_basic_credentials,
    parse_scope,
    pkce_verifies,
    redirect_uri_registered,
    string_parameters,
    with_query,
)
from zephyrex.lib.DateTimeUtils import ensure_utc
from zephyrex.lib.Environment import env
from zephyrex.lib.SecretEncryption import (
    MissingFernetKeyError,
    decrypt_secret,
    encrypt_secret,
)
from zephyrex.logic.BLL_Auth import UserModel

CODE_PREFIX = "zxac_"
ACCESS_PREFIX = "zxat_"
REFRESH_PREFIX = "zxrt_"
RSA_KEY_BITS = 3072
RSA_PUBLIC_EXPONENT = 65537
EC_COORDINATE_BYTES = 32
PROMPT_NONE = "none"
PROMPT_LOGIN = "login"
PROMPTS = (PROMPT_NONE, PROMPT_LOGIN, "consent")
# The authorization request parameters the server reads; others are
# ignored (RFC 6749 §3.1), and none of these may be sent twice.
AUTHORIZE_PARAMETERS = (
    "response_type",
    "client_id",
    "redirect_uri",
    "scope",
    "state",
    "nonce",
    "code_challenge",
    "code_challenge_method",
    "prompt",
    "max_age",
    "response_mode",
    "request",
    "request_uri",
)
# RFC 8707 resource indicators, the one parameter that may repeat.
RESOURCE_PARAMETER = "resource"


def _timestamp(moment: datetime) -> int:
    return int(ensure_utc(moment).timestamp())


def _expired(moment: datetime) -> bool:
    return ensure_utc(moment) <= now_utc()


def _invalid_grant(description: str) -> OAuthError:
    return OAuthError("invalid_grant", description)


def _bearer_refusal(error: str, description: str, status_code: int) -> OAuthError:
    """An RFC 6750 §3 refusal of a bearer token."""
    challenge = f'Bearer error="{error}", error_description="{description}"'
    return OAuthError(error, description, status_code, {"WWW-Authenticate": challenge})


class _Rows:
    """The server's own rows of one model, read and written as ROOT: the
    protocol state belongs to the server, and each verb checks the client
    and the user itself."""

    def __init__(self, model_registry: Any, model: Any) -> None:
        self.model_registry = model_registry
        self.model = model
        self.db = model.DB(model_registry.DB.manager.Base)

    def create(self, **fields: Any) -> Any:
        return self.db.create(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=self.model,
            **fields,
        )

    def find(self, *filters: Any) -> List[Any]:
        rows: Optional[List[Any]] = self.db.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            filters=list(filters),
            return_type="dto",
            override_dto=self.model,
        )
        return rows or []

    def first(self, *filters: Any) -> Optional[Any]:
        rows = self.find(*filters)
        return rows[0] if rows else None

    def update(self, id: str, **properties: Any) -> None:
        self.db.update(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            id=id,
            new_properties=properties,
        )

    def claim(self, id: str, column: str) -> bool:
        """Set ``column`` to now on row ``id`` while it is still unset, in one
        statement: of two concurrent claims exactly one wins."""
        stamp = getattr(self.db, column)
        with self.model_registry.DB.manager._get_db_session() as session:
            count: int = (
                session.query(self.db)
                .filter(self.db.id == id, stamp.is_(None), self.db.deleted_at.is_(None))
                .update({stamp: now_utc()}, synchronize_session=False)
            )
        return count == 1


def _unsigned_bytes(value: int, length: Optional[int] = None) -> bytes:
    return value.to_bytes(length or (value.bit_length() + 7) // 8, "big")


class SigningKeys:
    """The ID-token signing key set. Each configured algorithm has one
    active key, replaced when older than the rotation period or on demand;
    a replaced key stays published for the retirement period, so tokens it
    signed still verify, then is retired with its private half erased.
    Private halves are stored encrypted by ``lib.SecretEncryption``."""

    def __init__(self, model_registry: Any) -> None:
        self.rows = _Rows(model_registry, OauthSigningKeyModel)

    @staticmethod
    def _generate(algorithm: str) -> Any:
        if algorithm == Config.ES256:
            return ec.generate_private_key(ec.SECP256R1())
        return rsa.generate_private_key(
            public_exponent=RSA_PUBLIC_EXPONENT, key_size=RSA_KEY_BITS
        )

    @staticmethod
    def public_jwk(private_key: Any, algorithm: str) -> Dict[str, str]:
        numbers = private_key.public_key().public_numbers()
        if algorithm == Config.ES256:
            jwk = {
                "crv": "P-256",
                "kty": "EC",
                "x": b64url(_unsigned_bytes(numbers.x, EC_COORDINATE_BYTES)),
                "y": b64url(_unsigned_bytes(numbers.y, EC_COORDINATE_BYTES)),
            }
        else:
            jwk = {
                "e": b64url(_unsigned_bytes(numbers.e)),
                "kty": "RSA",
                "n": b64url(_unsigned_bytes(numbers.n)),
            }
        # RFC 7638: the thumbprint of the required members, sorted, compact.
        members = json.dumps(jwk, sort_keys=True, separators=(",", ":"))
        kid = b64url(hashlib.sha256(members.encode("utf-8")).digest())
        return {**jwk, "kid": kid, "alg": algorithm, "use": "sig"}

    def _create(self, algorithm: str) -> OauthSigningKeyModel:
        private_key = self._generate(algorithm)
        pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode("ascii")
        try:
            sealed = encrypt_secret(pem)
        except MissingFernetKeyError as error:
            raise HTTPException(status_code=503, detail=str(error)) from None
        jwk = self.public_jwk(private_key, algorithm)
        created: OauthSigningKeyModel = self.rows.create(
            kid=jwk["kid"],
            algorithm=algorithm,
            public_jwk=json.dumps(jwk),
            private_key=sealed,
            status=KEY_ACTIVE,
            activated_at=now_utc(),
        )
        return created

    def _demote(self, key: OauthSigningKeyModel) -> None:
        self.rows.update(key.id, status=KEY_RETIRING, retiring_at=now_utc())

    def _retire_expired(self) -> None:
        cutoff = now_utc() - timedelta(seconds=Config.key_retirement_seconds())
        for key in self.rows.find(self.rows.db.status == KEY_RETIRING):
            if key.retiring_at is None or ensure_utc(key.retiring_at) <= cutoff:
                self.rows.update(key.id, status=KEY_RETIRED, private_key=None)

    def rotate(self) -> List[str]:
        """A new active key for every configured algorithm; the keys they
        replace start retiring. Returns the new keys' ids."""
        kids = []
        for algorithm in Config.signing_algorithms():
            for key in self.rows.find(
                self.rows.db.status == KEY_ACTIVE, self.rows.db.algorithm == algorithm
            ):
                self._demote(key)
            kids.append(self._create(algorithm).kid)
        self._retire_expired()
        return kids

    def active(self, algorithm: str) -> OauthSigningKeyModel:
        """The key that signs with ``algorithm``, made or replaced as the
        rotation period requires."""
        keys = sorted(
            self.rows.find(
                self.rows.db.status == KEY_ACTIVE, self.rows.db.algorithm == algorithm
            ),
            key=lambda k: ensure_utc(k.activated_at),
            reverse=True,
        )
        for stale in keys[1:]:
            self._demote(stale)
        if keys:
            age = now_utc() - ensure_utc(keys[0].activated_at)
            if age.total_seconds() < Config.key_rotation_seconds():
                current: OauthSigningKeyModel = keys[0]
                return current
            self._demote(keys[0])
        return self._create(algorithm)

    def sign(self, claims: Dict[str, Any], algorithm: str) -> str:
        key = self.active(algorithm)
        try:
            pem = decrypt_secret(key.private_key)
        except MissingFernetKeyError as error:
            raise HTTPException(status_code=503, detail=str(error)) from None
        except RuntimeError:
            # Sealed under another FRAMEWORK_FERNET_KEY; POST /keys/rotate
            # replaces it.
            raise HTTPException(
                status_code=503, detail="The signing key cannot be decrypted"
            ) from None
        if not pem:
            raise HTTPException(
                status_code=503, detail="The signing key has no private half"
            )
        private_key = serialization.load_pem_private_key(pem.encode("ascii"), None)
        if not isinstance(private_key, (rsa.RSAPrivateKey, ec.EllipticCurvePrivateKey)):
            raise HTTPException(
                status_code=503, detail="The signing key is not RSA or EC"
            )
        token: str = jwt.encode(
            claims,
            private_key,
            algorithm=algorithm,
            headers={"kid": key.kid, "typ": "JWT"},
        )
        return token

    def jwks(self) -> Dict[str, List[Dict[str, Any]]]:
        """The published keys: every active and retiring one."""
        self._retire_expired()
        for algorithm in Config.signing_algorithms():
            self.active(algorithm)
        published = self.rows.find(self.rows.db.status.in_([KEY_ACTIVE, KEY_RETIRING]))
        return {"keys": [json.loads(key.public_jwk) for key in published]}


class SignedInUser:
    """A user signed in to the framework, and when that session began."""

    def __init__(self, user_id: str, auth_time: datetime) -> None:
        self.user_id = user_id
        self.auth_time = auth_time


class AuthorizationServer:
    """Every verb of the authorization server, over the app's registry."""

    def __init__(self, model_registry: Any) -> None:
        self.model_registry = model_registry
        self.clients = _Rows(model_registry, OauthClientModel)
        self.requests = _Rows(model_registry, OauthAuthorizationRequestModel)
        self.codes = _Rows(model_registry, OauthAuthorizationCodeModel)
        self.tokens = _Rows(model_registry, OauthTokenModel)
        self.grants = _Rows(model_registry, OauthGrantModel)
        self.keys = SigningKeys(model_registry)

    # -- clients ------------------------------------------------------------

    def client(self, client_id: Optional[str]) -> Optional[OauthClientModel]:
        """The enabled client ``client_id`` names."""
        if not client_id:
            return None
        found: Optional[OauthClientModel] = self.clients.first(
            self.clients.db.client_id == client_id,
            self.clients.db.is_enabled == True,  # noqa: E712
        )
        return found

    def authenticate_client(
        self, parameters: Mapping[str, str], authorization: Optional[str]
    ) -> OauthClientModel:
        """The client a token, introspection or revocation request comes
        from, authenticated by the one method it registered (RFC 6749
        §2.3, RFC 9700 §2.5). Anything else is ``invalid_client``."""
        basic = parse_basic_credentials(authorization)
        challenge = {"WWW-Authenticate": 'Basic realm="oauth"'} if basic else {}
        body_id = parameters.get("client_id")
        body_secret = parameters.get("client_secret")
        if basic is not None:
            if body_secret is not None:
                raise invalid_request("a client authenticates with one method")
            client_id, secret = basic
            if body_id is not None and body_id != client_id:
                raise invalid_request("client_id disagrees with the credentials")
            method = AUTH_METHOD_BASIC
        elif body_secret is not None:
            client_id, secret, method = body_id or "", body_secret, AUTH_METHOD_POST
        else:
            client_id, secret, method = body_id or "", "", AUTH_METHOD_NONE
        refused = OAuthError(
            "invalid_client", "client authentication failed", 401, challenge
        )
        client = self.client(client_id)
        if client is None or client.token_endpoint_auth_method != method:
            raise refused
        if method in CONFIDENTIAL_AUTH_METHODS and not (
            client.client_secret_hash
            and digest_matches(secret, client.client_secret_hash)
        ):
            raise refused
        return client

    def _registered_resources(self) -> List[str]:
        return [
            c.resource_uri
            for c in self.clients.find(
                self.clients.db.resource_uri.isnot(None),
                self.clients.db.is_enabled == True,  # noqa: E712
            )
        ]

    # -- users --------------------------------------------------------------

    def user(self, user_id: str) -> Optional[UserModel]:
        """The user, while their account is active."""
        rows = _Rows(self.model_registry, UserModel)
        found: Optional[UserModel] = rows.first(rows.db.id == user_id)
        return found if found is not None and found.active is not False else None

    def signed_in(self, authorization: Optional[str]) -> Optional[SignedInUser]:
        """The framework user a bearer session token names, and when that
        session signed in; None without a live session."""
        from zephyrex.extensions.auth_session.BLL_Session import SessionModel
        from zephyrex.logic.BLL_Auth import UserManager

        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return None
        try:
            payload = UserManager._decode_jwt(token.strip())
            UserManager._enforce_session_not_revoked(payload, self.model_registry)
        except (jwt.InvalidTokenError, HTTPException):
            return None
        user_id = str(payload["sub"])
        sessions = _Rows(self.model_registry, SessionModel)
        session = sessions.first(
            sessions.db.session_key == payload["jti"],
            sessions.db.user_id == user_id,
        )
        if session is None or session.created_at is None or self.user(user_id) is None:
            return None
        return SignedInUser(user_id, ensure_utc(session.created_at))

    def _grant(self, user_id: str, client_id: str) -> Optional[OauthGrantModel]:
        found: Optional[OauthGrantModel] = self.grants.first(
            self.grants.db.user_id == user_id, self.grants.db.client_id == client_id
        )
        return found

    def _consented(self, user_id: str, client_id: str, scopes: List[str]) -> bool:
        grant = self._grant(user_id, client_id)
        return grant is not None and set(scopes) <= set(grant.scopes.split())

    # -- authorization endpoint --------------------------------------------

    def authorize(
        self, query: Sequence[Tuple[str, str]], authorization: Optional[str]
    ) -> Response:
        """``GET /authorize`` (RFC 6749 §4.1.1, OIDC Core §3.1.2.1)."""
        seen: Dict[str, str] = {}
        resources: List[str] = []
        repeated: List[str] = []
        for name, value in query:
            if name == RESOURCE_PARAMETER:
                resources.append(value)
            elif name in AUTHORIZE_PARAMETERS:
                if name in seen:
                    repeated.append(name)
                seen[name] = value
        try:
            parameters = string_parameters(seen)
        except OAuthError as error:
            return self._refuse_here(error)
        if "client_id" in repeated or "redirect_uri" in repeated:
            return self._refuse_here(
                invalid_request("client_id or redirect_uri repeats")
            )
        client = self.client(parameters.get("client_id"))
        if client is None:
            return self._refuse_here(invalid_request("unknown client_id"))
        redirect_uri = parameters.get("redirect_uri")
        if not redirect_uri or not redirect_uri_registered(
            json_list(client.redirect_uris), redirect_uri
        ):
            return self._refuse_here(
                invalid_request("redirect_uri is not registered for this client")
            )
        state = parameters.get("state")
        try:
            if repeated:
                raise invalid_request(f"repeated parameters: {sorted(set(repeated))}")
            request = self._validated(client, parameters, resources)
            if request["prompt"] == PROMPT_NONE:
                code = self._silently(client, redirect_uri, request, authorization)
                return self._redirect(redirect_uri, state=state, code=code)
        except OAuthError as error:
            return self._redirect(redirect_uri, state=state, error=error)
        stored = self.requests.create(
            client_id=client.client_id,
            redirect_uri=redirect_uri,
            scopes=join_scope(request["scopes"]),
            state=state,
            nonce=request["nonce"],
            code_challenge=request["code_challenge"],
            prompt=request["prompt"],
            max_age=request["max_age"],
            resources=json.dumps(request["resources"]),
            expires_at=now_utc() + timedelta(seconds=Config.request_ttl()),
        )
        return RedirectResponse(
            with_query(Config.consent_url(), {"request_id": stored.id}),
            status_code=302,
            headers=dict(NO_STORE_HEADERS),
        )

    def _validated(
        self,
        client: OauthClientModel,
        parameters: Mapping[str, str],
        resources: List[str],
    ) -> Dict[str, Any]:
        if "request" in parameters:
            raise OAuthError(
                "request_not_supported", "request objects are not supported"
            )
        if "request_uri" in parameters:
            raise OAuthError(
                "request_uri_not_supported", "request_uri is not supported"
            )
        response_type = parameters.get("response_type")
        if response_type is None:
            raise invalid_request("response_type is required")
        if response_type != "code":
            raise OAuthError(
                "unsupported_response_type",
                "only the authorization code flow (response_type=code) is offered",
            )
        if parameters.get("response_mode", "query") != "query":
            raise invalid_request("response_mode is query")
        scopes = parse_scope(parameters.get("scope"))
        if not scopes:
            raise OAuthError("invalid_scope", "scope is required")
        allowed = set(client.allowed_scopes.split())
        if not set(scopes) <= allowed:
            raise OAuthError(
                "invalid_scope",
                f"the client may not request {sorted(set(scopes) - allowed)}",
            )
        challenge = parameters.get("code_challenge")
        if not challenge:
            raise invalid_request("code_challenge is required (PKCE)")
        if parameters.get("code_challenge_method") != PKCE_METHOD:
            raise invalid_request("code_challenge_method is S256")
        if not is_s256_challenge(challenge):
            raise invalid_request("code_challenge is a base64url SHA-256")
        prompts = (parameters.get("prompt") or "").split()
        if any(p not in PROMPTS for p in prompts) or (
            PROMPT_NONE in prompts and len(prompts) > 1
        ):
            raise invalid_request("prompt is none, or login and/or consent")
        max_age: Optional[int] = None
        if "max_age" in parameters:
            if not parameters["max_age"].isdigit():
                raise invalid_request("max_age is a non-negative integer")
            max_age = int(parameters["max_age"])
        registered = self._registered_resources()
        for resource in resources:
            if resource not in registered:
                raise OAuthError("invalid_target", "an unknown resource was requested")
        return {
            "scopes": scopes,
            "nonce": parameters.get("nonce"),
            "code_challenge": challenge,
            "prompt": " ".join(prompts) or None,
            "max_age": max_age,
            "resources": list(dict.fromkeys(resources)),
        }

    def _silently(
        self,
        client: OauthClientModel,
        redirect_uri: str,
        request: Dict[str, Any],
        authorization: Optional[str],
    ) -> str:
        """``prompt=none``: a code for a signed-in user who already consented,
        else ``login_required`` or ``consent_required`` (OIDC Core §3.1.2.6)."""
        signed_in = self.signed_in(authorization)
        if signed_in is None or self._too_old(signed_in, request["max_age"]):
            raise OAuthError("login_required", "the user is not signed in")
        if not self._consented(signed_in.user_id, client.client_id, request["scopes"]):
            raise OAuthError("consent_required", "the user has not consented")
        return self._issue_code(
            client_id=client.client_id,
            user_id=signed_in.user_id,
            redirect_uri=redirect_uri,
            scopes=request["scopes"],
            nonce=request["nonce"],
            code_challenge=request["code_challenge"],
            resources=request["resources"],
            auth_time=signed_in.auth_time,
        )

    @staticmethod
    def _too_old(signed_in: SignedInUser, max_age: Optional[int]) -> bool:
        if max_age is None:
            return False
        return (now_utc() - signed_in.auth_time).total_seconds() > max_age

    @staticmethod
    def _refuse_here(error: OAuthError) -> Response:
        """An authorization error that must not be redirected: the client or
        the redirect URI is not known (RFC 6749 §4.1.2.1)."""
        return JSONResponse(
            error.body(), status_code=400, headers=dict(NO_STORE_HEADERS)
        )

    def _redirect(
        self,
        redirect_uri: str,
        *,
        state: Optional[str],
        error: Optional[OAuthError] = None,
        code: Optional[str] = None,
    ) -> Response:
        return RedirectResponse(
            self.redirect_target(redirect_uri, state=state, error=error, code=code),
            status_code=302,
            headers=dict(NO_STORE_HEADERS),
        )

    @staticmethod
    def redirect_target(
        redirect_uri: str,
        *,
        state: Optional[str],
        error: Optional[OAuthError] = None,
        code: Optional[str] = None,
    ) -> str:
        """The authorization response (RFC 6749 §4.1.2), with ``iss``
        (RFC 9207)."""
        params: Dict[str, Optional[str]] = {}
        if error is not None:
            params["error"] = error.error
            params["error_description"] = error.description or None
        else:
            params["code"] = code
        params["state"] = state
        params["iss"] = Config.issuer()
        return with_query(redirect_uri, params)

    def _issue_code(
        self,
        *,
        client_id: str,
        user_id: str,
        redirect_uri: str,
        scopes: List[str],
        nonce: Optional[str],
        code_challenge: str,
        resources: List[str],
        auth_time: datetime,
    ) -> str:
        raw = mint_secret(CODE_PREFIX)
        self.codes.create(
            code_hash=digest(raw),
            client_id=client_id,
            user_id=user_id,
            redirect_uri=redirect_uri,
            scopes=join_scope(scopes),
            nonce=nonce,
            code_challenge=code_challenge,
            resources=json.dumps(resources),
            auth_time=auth_time,
            expires_at=now_utc() + timedelta(seconds=Config.code_ttl()),
        )
        return raw

    # -- consent (the UI's side of the authorization endpoint) -------------

    def _open_request(self, request_id: str) -> OauthAuthorizationRequestModel:
        found: Optional[OauthAuthorizationRequestModel] = self.requests.first(
            self.requests.db.id == request_id,
            self.requests.db.decided_at.is_(None),
        )
        if found is None or _expired(found.expires_at):
            raise HTTPException(
                status_code=404, detail="No pending authorization request"
            )
        return found

    def _request_client(
        self, request: OauthAuthorizationRequestModel
    ) -> OauthClientModel:
        client = self.client(request.client_id)
        if client is None:
            raise HTTPException(status_code=404, detail="The client is gone")
        return client

    def pending(self, request_id: str, user_id: str) -> Dict[str, Any]:
        """What the consent screen shows for a pending request."""
        request = self._open_request(request_id)
        client = self._request_client(request)
        grant = self._grant(user_id, client.client_id)
        return {
            "request_id": request.id,
            "client_id": client.client_id,
            "client_name": client.name,
            "redirect_uri": request.redirect_uri,
            "scopes": request.scopes.split(),
            "granted_scopes": grant.scopes.split() if grant else [],
            "prompt": request.prompt.split() if request.prompt else [],
            "max_age": request.max_age,
            "expires_at": ensure_utc(request.expires_at),
        }

    def decide(
        self,
        request_id: str,
        signed_in: SignedInUser,
        approve: bool,
        scopes: Optional[List[str]],
    ) -> str:
        """Record the signed-in user's decision on a pending request, and
        answer with where to send the browser: the client's redirect URI
        with a code, or with ``access_denied``."""
        request = self._open_request(request_id)
        client = self._request_client(request)
        requested = request.scopes.split()
        granted = requested if scopes is None else list(dict.fromkeys(scopes))
        if approve:
            prompts = (request.prompt or "").split()
            fresh = PROMPT_LOGIN not in prompts or signed_in.auth_time >= ensure_utc(
                request.created_at
            )
            if not fresh or self._too_old(signed_in, request.max_age):
                raise HTTPException(
                    status_code=401,
                    detail="login_required: sign in again to approve this request",
                )
            if not granted or not set(granted) <= set(requested):
                raise HTTPException(
                    status_code=400,
                    detail="scopes are a non-empty subset of those requested",
                )
        if not self.requests.claim(request.id, "decided_at"):
            raise HTTPException(
                status_code=404, detail="No pending authorization request"
            )
        self.requests.update(request.id, user_id=signed_in.user_id)
        if not approve:
            return self.redirect_target(
                request.redirect_uri,
                state=request.state,
                error=OAuthError("access_denied", "the user denied the request"),
            )
        OauthGrantManager(
            requester_id=signed_in.user_id, model_registry=self.model_registry
        ).consent(client, granted)
        code = self._issue_code(
            client_id=client.client_id,
            user_id=signed_in.user_id,
            redirect_uri=request.redirect_uri,
            scopes=granted,
            nonce=request.nonce,
            code_challenge=request.code_challenge,
            resources=json_list(request.resources),
            auth_time=signed_in.auth_time,
        )
        return self.redirect_target(
            request.redirect_uri, state=request.state, code=code
        )

    # -- token endpoint -----------------------------------------------------

    def token(
        self, raw_parameters: Mapping[str, object], authorization: Optional[str]
    ) -> Dict[str, Any]:
        """``POST /token`` (RFC 6749 §4.1.3, §6)."""
        parameters = string_parameters(raw_parameters)
        client = self.authenticate_client(parameters, authorization)
        grant_type = parameters.get("grant_type")
        if grant_type == "authorization_code":
            return self._redeem_code(client, parameters)
        if grant_type == "refresh_token":
            return self._refresh(client, parameters)
        if grant_type is None:
            raise invalid_request("grant_type is required")
        raise OAuthError(
            "unsupported_grant_type",
            "only authorization_code and refresh_token are offered",
        )

    def _redeem_code(
        self, client: OauthClientModel, parameters: Mapping[str, str]
    ) -> Dict[str, Any]:
        raw_code = parameters.get("code")
        redirect_uri = parameters.get("redirect_uri")
        if not raw_code or not redirect_uri:
            raise invalid_request("code and redirect_uri are required")
        code: Optional[OauthAuthorizationCodeModel] = self.codes.first(
            self.codes.db.code_hash == digest(raw_code)
        )
        if code is None or code.client_id != client.client_id:
            raise _invalid_grant("the authorization code is not valid")
        # Claimed before it is checked: a code gets one attempt.
        if not self.codes.claim(code.id, "used_at"):
            revoke_tokens(self.model_registry, family_id=code.id)
            raise _invalid_grant("the authorization code was already used")
        if _expired(code.expires_at):
            raise _invalid_grant("the authorization code has expired")
        if code.redirect_uri != redirect_uri:
            raise _invalid_grant("redirect_uri differs from the authorization request")
        if not pkce_verifies(parameters.get("code_verifier"), code.code_challenge):
            raise _invalid_grant("code_verifier does not match the code_challenge")
        scopes = code.scopes.split()
        resources = json_list(code.resources)
        audience = self._audience(resources, parameters)
        user = self._consenting_user(code.user_id, client, scopes)
        return self._issue_tokens(
            client=client,
            user=user,
            scopes=scopes,
            audience=audience,
            family_id=code.id,
            auth_time=ensure_utc(code.auth_time),
            nonce=code.nonce,
            refresh_expires_at=now_utc()
            + timedelta(seconds=Config.refresh_token_ttl()),
            refresh_scopes=scopes,
            refresh_resources=resources,
        )

    def _audience(
        self, authorized: List[str], parameters: Mapping[str, str]
    ) -> List[str]:
        """The access token's audience: the resource the client names at the
        token endpoint (RFC 8707 §2.2), among those it was authorized for;
        else all of those; else this issuer."""
        requested = parameters.get(RESOURCE_PARAMETER)
        if requested is not None:
            if requested not in authorized:
                raise OAuthError("invalid_target", "that resource was not authorized")
            return [requested]
        return authorized or [Config.issuer()]

    def _consenting_user(
        self, user_id: str, client: OauthClientModel, scopes: List[str]
    ) -> UserModel:
        user = self.user(user_id)
        if user is None or not self._consented(user_id, client.client_id, scopes):
            raise _invalid_grant("the authorization is no longer in force")
        return user

    def _refresh(
        self, client: OauthClientModel, parameters: Mapping[str, str]
    ) -> Dict[str, Any]:
        raw_token = parameters.get("refresh_token")
        if not raw_token:
            raise invalid_request("refresh_token is required")
        token: Optional[OauthTokenModel] = self.tokens.first(
            self.tokens.db.token_hash == digest(raw_token),
            self.tokens.db.token_type == REFRESH_TOKEN,
        )
        if token is None or token.client_id != client.client_id:
            raise _invalid_grant("the refresh token is not valid")
        if token.revoked_at is not None:
            raise _invalid_grant("the refresh token was revoked")
        if not self.tokens.claim(token.id, "used_at"):
            # A rotated-out refresh token came back: someone holds a copy.
            revoke_tokens(self.model_registry, family_id=token.family_id)
            raise _invalid_grant("the refresh token was already used")
        if _expired(token.expires_at):
            raise _invalid_grant("the refresh token has expired")
        authorized = token.scopes.split()
        scopes = parse_scope(parameters.get("scope")) or authorized
        if not set(scopes) <= set(authorized):
            raise OAuthError("invalid_scope", "a refresh cannot widen the scope")
        audience = self._audience(json_list(token.audience), parameters)
        user = self._consenting_user(token.user_id, client, scopes)
        return self._issue_tokens(
            client=client,
            user=user,
            scopes=scopes,
            audience=audience,
            family_id=token.family_id,
            auth_time=ensure_utc(token.auth_time),
            nonce=None,
            refresh_expires_at=ensure_utc(token.expires_at),
            refresh_scopes=authorized,
            refresh_resources=json_list(token.audience),
        )

    def _issue_tokens(
        self,
        *,
        client: OauthClientModel,
        user: UserModel,
        scopes: List[str],
        audience: List[str],
        family_id: str,
        auth_time: datetime,
        nonce: Optional[str],
        refresh_expires_at: datetime,
        refresh_scopes: List[str],
        refresh_resources: List[str],
    ) -> Dict[str, Any]:
        """A new access token for ``scopes`` and ``audience``, the next
        refresh token of ``family_id`` (which keeps the scope and the
        resources the user authorized, and the family's expiry), and an ID
        token when ``openid`` was granted."""
        access_ttl = Config.access_token_ttl()
        access = mint_secret(ACCESS_PREFIX)
        refresh = mint_secret(REFRESH_PREFIX)
        response: Dict[str, Any] = {
            "access_token": access,
            "token_type": TOKEN_TYPE_BEARER,
            "expires_in": access_ttl,
            "refresh_token": refresh,
            "scope": join_scope(scopes),
        }
        # Signed first: a token that cannot be answered is never stored.
        if SCOPE_OPENID in scopes:
            response["id_token"] = self._id_token(
                client, user, auth_time, nonce, access
            )
        now = now_utc()
        common = {
            "client_id": client.client_id,
            "user_id": user.id,
            "family_id": family_id,
            "auth_time": auth_time,
        }
        self.tokens.create(
            token_hash=digest(access),
            token_type=ACCESS_TOKEN,
            scopes=join_scope(scopes),
            audience=json.dumps(audience),
            expires_at=now + timedelta(seconds=access_ttl),
            **common,
        )
        self.tokens.create(
            token_hash=digest(refresh),
            token_type=REFRESH_TOKEN,
            scopes=join_scope(refresh_scopes),
            audience=json.dumps(refresh_resources),
            expires_at=refresh_expires_at,
            **common,
        )
        return response

    def _id_token(
        self,
        client: OauthClientModel,
        user: UserModel,
        auth_time: datetime,
        nonce: Optional[str],
        access_token: str,
    ) -> str:
        """OIDC Core §2: the identity claims; the profile and email claims
        are UserInfo's (§5.4)."""
        issued = now_utc()
        claims: Dict[str, Any] = {
            "iss": Config.issuer(),
            "sub": user.id,
            "aud": client.client_id,
            "iat": _timestamp(issued),
            "exp": _timestamp(issued) + Config.id_token_ttl(),
            "auth_time": _timestamp(auth_time),
            "at_hash": half_hash(access_token),
        }
        if nonce:
            claims["nonce"] = nonce
        return self.keys.sign(claims, client.id_token_signed_response_alg)

    # -- introspection and revocation --------------------------------------

    def _live_token(self, raw_token: str) -> Optional[OauthTokenModel]:
        token: Optional[OauthTokenModel] = self.tokens.first(
            self.tokens.db.token_hash == digest(raw_token)
        )
        if (
            token is None
            or token.revoked_at is not None
            or _expired(token.expires_at)
            or (token.token_type == REFRESH_TOKEN and token.used_at is not None)
            or self.client(token.client_id) is None
            or self.user(token.user_id) is None
        ):
            return None
        return token

    def introspect(
        self, raw_parameters: Mapping[str, object], authorization: Optional[str]
    ) -> Dict[str, Any]:
        """``POST /introspect`` (RFC 7662). The caller is a confidential
        client: the token's holder, or (for an access token) the resource
        server the token is for. To anyone else the token is inactive."""
        parameters = string_parameters(raw_parameters)
        caller = self.authenticate_client(parameters, authorization)
        if not caller.is_confidential:
            raise OAuthError(
                "invalid_client", "introspection is for confidential clients", 401
            )
        raw_token = parameters.get("token")
        if not raw_token:
            raise invalid_request("token is required")
        token = self._live_token(raw_token)
        if token is None:
            return {"active": False}
        audience = json_list(token.audience)
        holder = caller.client_id == token.client_id
        if token.token_type == ACCESS_TOKEN:
            entitled = holder or (
                caller.resource_uri is not None and caller.resource_uri in audience
            )
        else:
            entitled = holder
        if not entitled:
            return {"active": False}
        answer: Dict[str, Any] = {
            "active": True,
            "scope": token.scopes,
            "client_id": token.client_id,
            "sub": token.user_id,
            "iss": Config.issuer(),
            "exp": _timestamp(token.expires_at),
            "iat": _timestamp(token.created_at or token.auth_time),
        }
        if token.token_type == ACCESS_TOKEN:
            answer["aud"] = audience
            answer["token_type"] = TOKEN_TYPE_BEARER
        return answer

    def revoke(
        self, raw_parameters: Mapping[str, object], authorization: Optional[str]
    ) -> None:
        """``POST /revoke`` (RFC 7009). A refresh token takes its whole
        family with it (§2.1). A token the client does not hold, or one that
        does not exist, is left alone and answered the same way."""
        parameters = string_parameters(raw_parameters)
        client = self.authenticate_client(parameters, authorization)
        raw_token = parameters.get("token")
        if not raw_token:
            raise invalid_request("token is required")
        token: Optional[OauthTokenModel] = self.tokens.first(
            self.tokens.db.token_hash == digest(raw_token)
        )
        if token is None or token.client_id != client.client_id:
            return
        if token.token_type == REFRESH_TOKEN:
            revoke_tokens(self.model_registry, family_id=token.family_id)
        else:
            revoke_tokens(self.model_registry, id=token.id)

    # -- UserInfo -----------------------------------------------------------

    def userinfo(self, authorization: Optional[str]) -> Dict[str, Any]:
        """OIDC Core §5.3: the claims the access token's scope grants. The
        token is a bearer access token for this issuer, with ``openid``."""
        scheme, _, raw_token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not raw_token.strip():
            raise OAuthError(
                "invalid_token",
                "a bearer access token is required",
                401,
                {"WWW-Authenticate": 'Bearer realm="userinfo"'},
            )
        token = self._live_token(raw_token.strip())
        if token is None or token.token_type != ACCESS_TOKEN:
            raise _bearer_refusal("invalid_token", "the access token is not valid", 401)
        if Config.issuer() not in json_list(token.audience):
            raise _bearer_refusal(
                "invalid_token", "the token is for another audience", 401
            )
        scopes = token.scopes.split()
        if SCOPE_OPENID not in scopes:
            raise _bearer_refusal(
                "insufficient_scope", "the openid scope is required", 403
            )
        user = self.user(token.user_id)
        if user is None:
            raise _bearer_refusal("invalid_token", "the access token is not valid", 401)
        return self.claims(user, scopes)

    @staticmethod
    def claims(user: UserModel, scopes: List[str]) -> Dict[str, Any]:
        """The standard claims (OIDC Core §5.1) the scopes reach. The
        framework does not record whether an email address was verified, so
        ``email_verified`` is not asserted."""
        claims: Dict[str, Any] = {"sub": user.id}
        if SCOPE_PROFILE in scopes:
            full_name = " ".join(n for n in (user.first_name, user.last_name) if n)
            updated = user.updated_at or user.created_at
            claims.update(
                {
                    "name": user.display_name or full_name or None,
                    "given_name": user.first_name,
                    "family_name": user.last_name,
                    "preferred_username": user.username,
                    "zoneinfo": user.timezone,
                    "locale": user.language,
                    "updated_at": _timestamp(updated) if updated else None,
                }
            )
        if SCOPE_EMAIL in scopes:
            claims["email"] = user.email
        return {name: value for name, value in claims.items() if value is not None}

    # -- discovery ----------------------------------------------------------

    def metadata(self) -> Dict[str, Any]:
        """OpenID Connect Discovery §3 / RFC 8414 §2 metadata."""
        auth_methods = [AUTH_METHOD_BASIC, AUTH_METHOD_POST]
        return {
            "issuer": Config.issuer(),
            "authorization_endpoint": Config.endpoint("/v1/oauth2/authorize"),
            "token_endpoint": Config.endpoint("/v1/oauth2/token"),
            "userinfo_endpoint": Config.endpoint("/v1/oauth2/userinfo"),
            "jwks_uri": Config.endpoint("/v1/oauth2/jwks"),
            "introspection_endpoint": Config.endpoint("/v1/oauth2/introspect"),
            "revocation_endpoint": Config.endpoint("/v1/oauth2/revoke"),
            "scopes_supported": sorted(supported_scopes(self.model_registry)),
            "response_types_supported": ["code"],
            "response_modes_supported": ["query"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": list(Config.signing_algorithms()),
            "token_endpoint_auth_methods_supported": [*auth_methods, AUTH_METHOD_NONE],
            "introspection_endpoint_auth_methods_supported": auth_methods,
            "revocation_endpoint_auth_methods_supported": [
                *auth_methods,
                AUTH_METHOD_NONE,
            ],
            "code_challenge_methods_supported": [PKCE_METHOD],
            "prompt_values_supported": list(PROMPTS),
            "claims_supported": [
                "sub",
                "iss",
                "aud",
                "exp",
                "iat",
                "auth_time",
                "nonce",
                "at_hash",
                "name",
                "given_name",
                "family_name",
                "preferred_username",
                "zoneinfo",
                "locale",
                "updated_at",
                "email",
            ],
            "claims_parameter_supported": False,
            "request_parameter_supported": False,
            "request_uri_parameter_supported": False,
            "authorization_response_iss_parameter_supported": True,
        }
