# SPDX-License-Identifier: AGPL-3.0-or-later
"""Signing in with another identity provider, as an OAuth 2.0 client.

The authorization code grant with PKCE (RFC 6749, RFC 7636), as the OAuth
2.0 Security BCP (RFC 9700) has it, and OpenID Connect on top of it (OIDC
Core 1.0, Discovery 1.0):

- every authorization request carries an S256 code challenge, and the code
  is exchanged server side with its verifier;
- an OpenID provider is found from its issuer through
  ``/.well-known/openid-configuration``. Its ID token is verified against
  the provider's JWKS (cached; an unknown key id fetches the set again, at
  most once a minute), and its ``iss``, ``aud``/``azp``, ``exp``, ``iat``
  and ``nonce`` checked. The account is the token's ``sub``; the userinfo
  endpoint only fills in profile fields;
- a provider without ID tokens (GitHub, Login with Amazon) is a plain
  OAuth 2.0 provider: its user API, over TLS with the access token the
  PKCE-bound exchange returned, names the account.

Every endpoint is ``https`` (plain ``http`` only to the loopback host, as
RFC 8252 allows a native client), and every request goes through the
provider's SSRF-guarded ``http()`` client.

An abstract provider lives here rather than in a ``PRV_`` module: every
class in a ``PRV_`` module is taken for a concrete provider.
"""

import base64
import hashlib
import hmac
import time
from abc import abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Set, Tuple
from urllib.parse import quote, urlencode, urlparse

import jwt

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticProvider,
    InstanceSetting,
)
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    PermanentExternalError,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

# Clock skew tolerated between this server and an identity provider.
CLOCK_SKEW_SECONDS = 60
# An ID token is minted by the code exchange this server just made; one
# issued longer ago than this is refused.
MAX_ID_TOKEN_AGE_SECONDS = 600
DISCOVERY_TTL_SECONDS = 3600
JWKS_TTL_SECONDS = 3600
# An ID token naming a key the cached set lacks fetches the set again (the
# provider rotated its keys), but at most this often: a forged key id must
# not turn this server into a fetching loop against the provider.
JWKS_REFETCH_INTERVAL_SECONDS = 60
HTTP_TIMEOUT_SECONDS = 15.0

# Signature algorithms an ID token may use: public-key ones only. ``none``
# and the HMAC family (keyed with the client secret) are refused.
ASYMMETRIC_ALGORITHMS: Tuple[str, ...] = (
    "RS256",
    "RS384",
    "RS512",
    "PS256",
    "PS384",
    "PS512",
    "ES256",
    "ES384",
    "ES512",
    "EdDSA",
)
_KEY_TYPE_OF: Mapping[str, str] = {"RS": "RSA", "PS": "RSA", "ES": "EC", "Ed": "OKP"}
_HASH_OF: Mapping[str, Any] = {
    "256": hashlib.sha256,
    "384": hashlib.sha384,
    "512": hashlib.sha512,
}
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_DISCOVERY_PATH = "/.well-known/openid-configuration"
_TRUE_STRINGS = frozenset({"true"})


@dataclass(frozen=True)
class Endpoints:
    """Where a provider is reached, and what its metadata promises."""

    authorize: str
    token: str
    userinfo: Optional[str] = None
    issuer: Optional[str] = None
    jwks_uri: Optional[str] = None
    signing_algorithms: Tuple[str, ...] = ("RS256",)
    token_auth_methods: Tuple[str, ...] = ("client_secret_post",)
    # RFC 9207: the provider names itself in every authorization response.
    issuer_in_response: bool = False


@dataclass(frozen=True)
class TokenSet:
    """A token endpoint's answer."""

    access_token: str
    refresh_token: Optional[str] = None
    expires_at: Optional[datetime] = None
    id_token: Optional[str] = None
    scope: Optional[str] = None


@dataclass(frozen=True)
class Identity:
    """The account a provider vouched for: ``subject`` is its stable id at
    the provider; ``email_verified`` is True only when the provider says it
    checked the mailbox is the user's."""

    subject: str
    email: Optional[str] = None
    email_verified: bool = False
    issuer: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    display_name: Optional[str] = None
    username: Optional[str] = None
    picture: Optional[str] = None


@dataclass(frozen=True)
class _PublishedKey:
    """One key of a provider's JWKS: the JWK as published, and the key."""

    jwk: Mapping[str, Any]
    key: jwt.PyJWK


@dataclass
class _KeySet:
    keys: List[_PublishedKey]
    fetched_at: float
    refetched_at: float = field(default=float("-inf"))


_DISCOVERED: Dict[str, Tuple[float, Endpoints]] = {}
_KEY_SETS: Dict[str, _KeySet] = {}


def text(value: Any) -> Optional[str]:
    """``value`` when it is a non-empty string."""
    return value if isinstance(value, str) and value.strip() else None


def asserted_true(value: Any) -> bool:
    """A claim that is ``true``, as a JSON boolean or (as some providers
    send it) the string."""
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.strip().lower() in _TRUE_STRINGS


def client_settings(
    client_id_env: str, client_secret_env: str, default_scopes: str
) -> Tuple[InstanceSetting, ...]:
    """The settings every client registration has. The secret is
    write-only: stored encrypted, never returned."""
    return (
        InstanceSetting(
            "client_id", "The client id registered at the provider", client_id_env
        ),
        InstanceSetting(
            "client_secret",
            "The client secret (none for a public client)",
            client_secret_env,
            secret=True,
        ),
        InstanceSetting(
            "scopes", "Space-separated scopes to request", default=default_scopes
        ),
        InstanceSetting(
            "redirect_uris",
            "Comma-separated redirect URIs registered at the provider, matched "
            "exactly (default: <APP_URI>/user/close/<provider>)",
            "OAUTH_CONSUMER_REDIRECT_URIS",
        ),
    )


def issuer_setting(
    issuer_env: Optional[str], default: Optional[str] = None
) -> InstanceSetting:
    return InstanceSetting(
        "issuer",
        "The provider's issuer URL; its metadata is read from "
        "<issuer>/.well-known/openid-configuration",
        issuer_env,
        default=default,
    )


def pkce_challenge(verifier: str) -> str:
    """The S256 code challenge of ``verifier`` (RFC 7636 §4.2)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def require_tls(url: str, what: str, provider: str) -> str:
    """``url`` when it is ``https`` (or ``http`` to the loopback host)."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host and (
        parsed.scheme == "https"
        or (parsed.scheme == "http" and host in _LOOPBACK_HOSTS)
    ):
        return url
    raise PermanentExternalError(
        f"{provider}: the {what} must be an https URL", provider=provider
    )


def _token_hash(value: str, algorithm: str) -> Optional[str]:
    """The ``at_hash`` of ``value`` under an ID token's ``algorithm``: the
    left half of its digest, base64url (OIDC Core §3.1.3.6)."""
    digest_of = hashlib.sha512 if algorithm == "EdDSA" else _HASH_OF.get(algorithm[-3:])
    if digest_of is None:
        return None
    digest = digest_of(value.encode("ascii")).digest()
    half = digest[: len(digest) // 2]
    return base64.urlsafe_b64encode(half).rstrip(b"=").decode("ascii")


def _key_fits(published: _PublishedKey, kid: Optional[str], algorithm: str) -> bool:
    """A signing key of the algorithm's type, published for it (or for any
    algorithm), with the id the token names."""
    jwk = published.jwk
    return (
        jwk.get("use") in (None, "sig")
        and jwk.get("alg") in (None, algorithm)
        and jwk.get("kty") == _KEY_TYPE_OF.get(algorithm[:2])
        and (kid is None or jwk.get("kid") == kid)
    )


def _select_key(
    keys: List[_PublishedKey], kid: Optional[str], algorithm: str
) -> Optional[jwt.PyJWK]:
    """The one key that fits; none when no key or several do (a token
    without a key id is accepted only from a provider with one key)."""
    fitting = [
        published.key for published in keys if _key_fits(published, kid, algorithm)
    ]
    return fitting[0] if len(fitting) == 1 else None


def _parse_key_set(document: Any) -> List[_PublishedKey]:
    if not isinstance(document, dict) or not isinstance(document.get("keys"), list):
        return []
    keys: List[_PublishedKey] = []
    for jwk in document["keys"]:
        if not isinstance(jwk, dict):
            continue
        try:
            keys.append(_PublishedKey(jwk=jwk, key=jwt.PyJWK(jwk)))
        except (jwt.PyJWKError, jwt.InvalidKeyError):
            continue
    return keys


class AbstractIdentityProvider(AbstractStaticProvider):
    """An OAuth 2.0 provider users sign in with; each instance is one
    client registration at it.

    ``public_name`` is what a client may call the provider by when this
    server has one instance of it configured (``google``); an instance is
    always also reachable by its own name.
    """

    name: ClassVar[str] = ""
    public_name: ClassVar[str] = ""
    friendly_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    # "oidc" for an OpenID provider, "oauth2" for a plain one.
    kind: ClassVar[str] = "oauth2"
    default_scopes: ClassVar[str] = ""
    _abilities: ClassVar[Set[str]] = set()
    _env: ClassVar[Dict[str, Any]] = {}
    http_timeout_seconds: ClassVar[float] = HTTP_TIMEOUT_SECONDS

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    def services(cls) -> List[str]:
        return ["oauth_consumer"]

    @classmethod
    def client_id(cls, instance: ProviderInstanceModel) -> str:
        found = text(cls.setting(instance, "client_id"))
        if found is None:
            raise PermanentExternalError(
                f"{cls.friendly_name}: no client id is configured", provider=cls.name
            )
        return found.strip()

    @classmethod
    def is_configured_instance(cls, instance: ProviderInstanceModel) -> bool:
        """Whether ``instance`` names enough to sign anyone in."""
        return text(cls.setting(instance, "client_id")) is not None

    @classmethod
    def scopes(cls, instance: ProviderInstanceModel) -> str:
        return " ".join((cls.setting(instance, "scopes") or cls.default_scopes).split())

    @classmethod
    def redirect_uris(cls, instance: ProviderInstanceModel) -> List[str]:
        """The configured redirect URIs; empty when none is."""
        raw = cls.setting(instance, "redirect_uris") or ""
        return [uri.strip() for uri in raw.split(",") if uri.strip()]

    @classmethod
    def authorize_params(cls, instance: ProviderInstanceModel) -> Dict[str, str]:
        """Provider-specific parameters added to every authorization request."""
        return {}

    @classmethod
    @abstractmethod
    async def endpoints(cls, instance: ProviderInstanceModel) -> Endpoints:
        """Where ``instance``'s provider is reached."""

    @classmethod
    @abstractmethod
    async def identity(
        cls,
        instance: ProviderInstanceModel,
        endpoints: Endpoints,
        tokens: TokenSet,
        nonce: str,
    ) -> Identity:
        """The account ``tokens`` were issued for."""

    @classmethod
    def authorization_url(
        cls,
        instance: ProviderInstanceModel,
        endpoints: Endpoints,
        *,
        redirect_uri: str,
        state: str,
        nonce: str,
        code_verifier: str,
    ) -> str:
        """Where to send the browser: the code flow, with the S256 challenge
        of ``code_verifier`` (and, to an OpenID provider, the nonce)."""
        params = {
            **cls.authorize_params(instance),
            "response_type": "code",
            "client_id": cls.client_id(instance),
            "redirect_uri": redirect_uri,
            "scope": cls.scopes(instance),
            "state": state,
            "code_challenge": pkce_challenge(code_verifier),
            "code_challenge_method": "S256",
        }
        if cls.kind == "oidc":
            params["nonce"] = nonce
        separator = "&" if urlparse(endpoints.authorize).query else "?"
        return f"{endpoints.authorize}{separator}{urlencode(params)}"

    @classmethod
    async def exchange_code(
        cls,
        instance: ProviderInstanceModel,
        endpoints: Endpoints,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> TokenSet:
        return await cls.token_request(
            instance,
            endpoints,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            },
        )

    @classmethod
    async def refresh(
        cls, instance: ProviderInstanceModel, endpoints: Endpoints, refresh_token: str
    ) -> TokenSet:
        return await cls.token_request(
            instance,
            endpoints,
            {"grant_type": "refresh_token", "refresh_token": refresh_token},
        )

    @classmethod
    async def token_request(
        cls,
        instance: ProviderInstanceModel,
        endpoints: Endpoints,
        grant: Dict[str, str],
    ) -> TokenSet:
        """POST ``grant`` to the token endpoint, authenticated as the client:
        HTTP Basic when the provider offers it (RFC 6749 §2.3.1), else in
        the form; a public client sends only its id."""
        client_id = cls.client_id(instance)
        secret = text(cls.setting(instance, "client_secret"))
        form = {**grant, "client_id": client_id}
        headers = {"Accept": "application/json"}
        if secret is not None:
            if "client_secret_basic" in endpoints.token_auth_methods:
                pair = f"{quote(client_id, safe='')}:{quote(secret, safe='')}"
                encoded = base64.b64encode(pair.encode()).decode("ascii")
                headers["Authorization"] = f"Basic {encoded}"
            else:
                form["client_secret"] = secret
        url = require_tls(endpoints.token, "token endpoint", cls.name)
        answer = await cls.http().post(url, data=form, headers=headers)
        return cls.parse_tokens(answer)

    @classmethod
    def parse_tokens(cls, answer: Any) -> TokenSet:
        if not isinstance(answer, dict):
            raise InvalidInputExternalError(
                f"{cls.friendly_name}: the token endpoint answered no JSON object",
                provider=cls.name,
            )
        # GitHub reports a refused code with 200 and an ``error`` member.
        if "error" in answer or not text(answer.get("access_token")):
            raise InvalidInputExternalError(
                f"{cls.friendly_name}: the token endpoint refused the grant "
                f"({str(answer.get('error') or 'no access token')[:64]})",
                provider=cls.name,
            )
        token_type = text(answer.get("token_type"))
        if token_type is not None and token_type.lower() != "bearer":
            raise InvalidInputExternalError(
                f"{cls.friendly_name}: unsupported token type", provider=cls.name
            )
        expires_in = answer.get("expires_in")
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
            if isinstance(expires_in, (int, float)) and expires_in > 0
            else None
        )
        return TokenSet(
            access_token=str(answer["access_token"]),
            refresh_token=text(answer.get("refresh_token")),
            expires_at=expires_at,
            id_token=text(answer.get("id_token")),
            scope=text(answer.get("scope")),
        )

    @classmethod
    async def api_json(cls, url: str, access_token: str, what: str) -> Any:
        """GET a provider API ``url`` with the access token, over TLS."""
        return await cls.get_json(
            require_tls(url, what, cls.name),
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
        )


class AbstractOIDCProvider(AbstractIdentityProvider):
    """An OpenID provider, found from its issuer."""

    kind: ClassVar[str] = "oidc"
    default_scopes: ClassVar[str] = "openid email profile"

    @classmethod
    def issuer(cls, instance: ProviderInstanceModel) -> str:
        found = text(cls.setting(instance, "issuer"))
        if found is None:
            raise PermanentExternalError(
                f"{cls.friendly_name}: no issuer is configured", provider=cls.name
            )
        return found.strip()

    @classmethod
    def is_configured_instance(cls, instance: ProviderInstanceModel) -> bool:
        return super().is_configured_instance(instance) and (
            text(cls.setting(instance, "issuer")) is not None
        )

    @classmethod
    def discovery_url(cls, instance: ProviderInstanceModel) -> str:
        return cls.issuer(instance).rstrip("/") + _DISCOVERY_PATH

    @classmethod
    def metadata_issuer(
        cls, instance: ProviderInstanceModel, metadata: Dict[str, Any]
    ) -> str:
        """The issuer the metadata names, which must be the configured one
        (OIDC Discovery §4.3; a trailing slash aside, since the metadata
        was found by stripping it). ID tokens must carry it exactly."""
        named = text(metadata.get("issuer"))
        configured = cls.issuer(instance)
        if named is None or named.rstrip("/") != configured.rstrip("/"):
            raise PermanentExternalError(
                f"{cls.friendly_name}: the provider's metadata names another issuer",
                provider=cls.name,
            )
        return named

    @classmethod
    def accepted_issuers(
        cls,
        instance: ProviderInstanceModel,
        endpoints: Endpoints,
        claims: Dict[str, Any],
    ) -> List[str]:
        """The ``iss`` values an ID token may carry; ``claims`` are its
        unverified claims (a multi-tenant issuer is templated on one)."""
        return [endpoints.issuer] if endpoints.issuer else []

    @classmethod
    def email_verified(cls, claims: Dict[str, Any]) -> bool:
        return asserted_true(claims.get("email_verified"))

    @classmethod
    async def endpoints(cls, instance: ProviderInstanceModel) -> Endpoints:
        url = require_tls(cls.discovery_url(instance), "issuer", cls.name)
        cached = _DISCOVERED.get(url)
        if cached is not None and time.monotonic() - cached[0] < DISCOVERY_TTL_SECONDS:
            return cached[1]
        metadata = await cls.get_json(url, headers={"Accept": "application/json"})
        if not isinstance(metadata, dict):
            raise PermanentExternalError(
                f"{cls.friendly_name}: the provider's metadata is not a JSON object",
                provider=cls.name,
            )
        endpoints = cls.read_metadata(instance, metadata)
        _DISCOVERED[url] = (time.monotonic(), endpoints)
        return endpoints

    @classmethod
    def read_metadata(
        cls, instance: ProviderInstanceModel, metadata: Dict[str, Any]
    ) -> Endpoints:
        issuer = cls.metadata_issuer(instance, metadata)
        challenge_methods = metadata.get("code_challenge_methods_supported")
        if isinstance(challenge_methods, list) and "S256" not in challenge_methods:
            raise PermanentExternalError(
                f"{cls.friendly_name}: the provider does not support PKCE with S256",
                provider=cls.name,
            )
        required = {}
        for key, what in (
            ("authorization_endpoint", "authorization endpoint"),
            ("token_endpoint", "token endpoint"),
            ("jwks_uri", "JWKS URI"),
        ):
            value = text(metadata.get(key))
            if value is None:
                raise PermanentExternalError(
                    f"{cls.friendly_name}: the provider's metadata has no {what}",
                    provider=cls.name,
                )
            required[key] = require_tls(value, what, cls.name)
        userinfo = text(metadata.get("userinfo_endpoint"))
        algorithms = metadata.get("id_token_signing_alg_values_supported")
        methods = metadata.get("token_endpoint_auth_methods_supported")
        return Endpoints(
            authorize=required["authorization_endpoint"],
            token=required["token_endpoint"],
            jwks_uri=required["jwks_uri"],
            userinfo=(
                require_tls(userinfo, "userinfo endpoint", cls.name)
                if userinfo
                else None
            ),
            issuer=issuer,
            signing_algorithms=tuple(
                a for a in (algorithms or ["RS256"]) if a in ASYMMETRIC_ALGORITHMS
            ),
            # The default when unadvertised is client_secret_basic.
            token_auth_methods=tuple(methods or ["client_secret_basic"]),
            issuer_in_response=metadata.get(
                "authorization_response_iss_parameter_supported"
            )
            is True,
        )

    @classmethod
    async def signing_key(
        cls, jwks_uri: str, kid: Optional[str], algorithm: str
    ) -> Optional[jwt.PyJWK]:
        """The provider's key for ``kid``, from the cached set; a key id the
        set lacks fetches it again (key rotation), at most once a minute."""
        now = time.monotonic()
        cached = _KEY_SETS.get(jwks_uri)
        if cached is None or now - cached.fetched_at >= JWKS_TTL_SECONDS:
            cached = await cls._fetch_key_set(jwks_uri, cached)
        key = _select_key(cached.keys, kid, algorithm)
        if key is None and now - cached.refetched_at >= JWKS_REFETCH_INTERVAL_SECONDS:
            cached = await cls._fetch_key_set(jwks_uri, cached)
            cached.refetched_at = now
            key = _select_key(cached.keys, kid, algorithm)
        return key

    @classmethod
    async def _fetch_key_set(
        cls, jwks_uri: str, previous: Optional[_KeySet]
    ) -> _KeySet:
        document = await cls.get_json(
            require_tls(jwks_uri, "JWKS URI", cls.name),
            headers={"Accept": "application/json"},
        )
        fetched = _KeySet(
            keys=_parse_key_set(document),
            fetched_at=time.monotonic(),
            refetched_at=previous.refetched_at if previous else float("-inf"),
        )
        _KEY_SETS[jwks_uri] = fetched
        return fetched

    @classmethod
    def _refuse(cls, reason: str) -> InvalidInputExternalError:
        return InvalidInputExternalError(
            f"{cls.friendly_name}: invalid ID token ({reason})", provider=cls.name
        )

    @classmethod
    async def verify_id_token(
        cls,
        instance: ProviderInstanceModel,
        endpoints: Endpoints,
        id_token: str,
        *,
        nonce: str,
        access_token: Optional[str],
    ) -> Dict[str, Any]:
        """The verified claims of ``id_token`` (OIDC Core §3.1.3.7)."""
        try:
            header = jwt.get_unverified_header(id_token)
            unverified = jwt.decode(id_token, options={"verify_signature": False})
        except jwt.PyJWTError:
            raise cls._refuse("malformed") from None
        algorithm = header.get("alg")
        if (
            not isinstance(algorithm, str)
            or algorithm not in endpoints.signing_algorithms
        ):
            raise cls._refuse("signature algorithm not allowed")
        if endpoints.jwks_uri is None:
            raise cls._refuse("the provider publishes no keys")
        kid = header.get("kid") if isinstance(header.get("kid"), str) else None
        key = await cls.signing_key(endpoints.jwks_uri, kid, algorithm)
        if key is None:
            raise cls._refuse("signed with an unknown key")
        client_id = cls.client_id(instance)
        try:
            claims: Dict[str, Any] = jwt.decode(
                id_token,
                key=key.key,
                algorithms=[algorithm],
                audience=client_id,
                issuer=cls.accepted_issuers(instance, endpoints, unverified),
                leeway=CLOCK_SKEW_SECONDS,
                options={"require": ["iss", "sub", "aud", "exp", "iat"]},
            )
        except jwt.PyJWTError as exc:
            raise cls._refuse(type(exc).__name__) from None
        audience = claims["aud"]
        if (
            isinstance(audience, list)
            and len(audience) > 1
            and claims.get("azp") != client_id
        ):
            raise cls._refuse("issued to another party")
        if "azp" in claims and claims["azp"] != client_id:
            raise cls._refuse("issued to another party")
        issued_at = claims["iat"]
        if not isinstance(issued_at, (int, float)) or (
            time.time() - issued_at > MAX_ID_TOKEN_AGE_SECONDS + CLOCK_SKEW_SECONDS
        ):
            raise cls._refuse("issued too long ago")
        if not hmac.compare_digest(str(claims.get("nonce") or ""), nonce):
            raise cls._refuse("nonce mismatch")
        if text(claims.get("sub")) is None:
            raise cls._refuse("no subject")
        at_hash = claims.get("at_hash")
        if at_hash is not None and access_token is not None:
            expected = _token_hash(access_token, algorithm)
            if expected is None or not hmac.compare_digest(str(at_hash), expected):
                raise cls._refuse("access token hash mismatch")
        return claims

    @classmethod
    async def profile(
        cls,
        endpoints: Endpoints,
        access_token: str,
        subject: str,
    ) -> Dict[str, Any]:
        """The userinfo answer, for profile fields only; an answer for
        another subject is not used (OIDC Core §5.3.2)."""
        if endpoints.userinfo is None:
            return {}
        answer = await cls.api_json(
            endpoints.userinfo, access_token, "userinfo endpoint"
        )
        if not isinstance(answer, dict) or answer.get("sub") != subject:
            return {}
        return answer

    @classmethod
    async def identity(
        cls,
        instance: ProviderInstanceModel,
        endpoints: Endpoints,
        tokens: TokenSet,
        nonce: str,
    ) -> Identity:
        if tokens.id_token is None:
            raise cls._refuse("the provider returned none")
        claims = await cls.verify_id_token(
            instance,
            endpoints,
            tokens.id_token,
            nonce=nonce,
            access_token=tokens.access_token,
        )
        subject = str(claims["sub"])
        found = {
            **(await cls.profile(endpoints, tokens.access_token, subject)),
            **claims,
        }
        email = text(claims.get("email"))
        return Identity(
            subject=subject,
            email=email.strip().lower() if email else None,
            email_verified=email is not None and cls.email_verified(claims),
            issuer=str(claims["iss"]),
            first_name=text(found.get("given_name")),
            last_name=text(found.get("family_name")),
            display_name=text(found.get("name")),
            username=text(found.get("preferred_username")),
            picture=text(found.get("picture")),
        )
