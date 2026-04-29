"""
Pluggable authentication strategies for provider instances (Item 10).

Strategies decouple `bond_instance` from credential shape. They expose
four hooks: `headers_for`, `params_for`, `body_modifier`, `refresh_if_needed`.
The shared HTTP client (`ProviderHTTPClient`, Item 31) invokes them
in order on every outbound call.

Strategies must be **thread-safe**: they are bound to provider instances
and may be invoked concurrently by parallel rotation calls.

Built-in strategies register themselves at import time via
`AuthStrategyRegistry.register(name, factory)`. Extensions (e.g., the
external OAuth extension) extend the registry the same way.
"""

from __future__ import annotations

import base64
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable, ClassVar, Dict, Optional


# ----- ABC + concrete strategies -------------------------------------------


class AuthStrategy(ABC):
    """Base class for all authentication strategies.

    All concrete strategies MUST be safe to invoke concurrently from
    multiple threads. Mutating internal state (token refresh, lease
    rotation) MUST hold an internal lock.
    """

    @abstractmethod
    def headers_for(self, requester_id: Optional[str] = None) -> Dict[str, str]:
        """Return headers to merge into the outbound request."""

    @abstractmethod
    def params_for(self, requester_id: Optional[str] = None) -> Dict[str, Any]:
        """Return query params to merge into the outbound request."""

    @abstractmethod
    def body_modifier(self, requester_id: Optional[str], body: Any) -> Any:
        """Return a (possibly modified) body, e.g. for signed payloads."""

    @abstractmethod
    def refresh_if_needed(self) -> None:
        """Refresh the strategy's credential material if stale. Idempotent."""


class APIKeyAuth(AuthStrategy):
    """Static API-key authentication via a request header."""

    def __init__(
        self,
        api_key: str,
        header_name: str = "Authorization",
        header_prefix: str = "Bearer ",
    ) -> None:
        self._api_key = api_key
        self._header_name = header_name
        self._header_prefix = header_prefix

    def headers_for(self, requester_id: Optional[str] = None) -> Dict[str, str]:
        return {self._header_name: f"{self._header_prefix}{self._api_key}"}

    def params_for(self, requester_id: Optional[str] = None) -> Dict[str, Any]:
        return {}

    def body_modifier(self, requester_id: Optional[str], body: Any) -> Any:
        return body

    def refresh_if_needed(self) -> None:
        return None


class OAuth2Auth(AuthStrategy):
    """OAuth2 bearer-token authentication with optional refresh.

    `refresh_if_needed` is a stub that the external OAuth extension may
    override; the base class only checks the `expires_at` timestamp.
    """

    def __init__(
        self,
        access_token: str,
        refresh_token: Optional[str] = None,
        token_url: Optional[str] = None,
        expires_at: Optional[datetime] = None,
    ) -> None:
        self._lock = RLock()
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._token_url = token_url
        self._expires_at = expires_at

    def headers_for(self, requester_id: Optional[str] = None) -> Dict[str, str]:
        with self._lock:
            return {"Authorization": f"Bearer {self._access_token}"}

    def params_for(self, requester_id: Optional[str] = None) -> Dict[str, Any]:
        return {}

    def body_modifier(self, requester_id: Optional[str], body: Any) -> Any:
        return body

    def refresh_if_needed(self) -> None:
        with self._lock:
            if not self._expires_at:
                return
            if datetime.now(timezone.utc) < self._expires_at:
                return
            # Base impl can't actually refresh; the OAuth extension overrides.
            return None

    def update_token(
        self, access_token: str, expires_at: Optional[datetime] = None
    ) -> None:
        """Hook used by the OAuth extension after refresh."""
        with self._lock:
            self._access_token = access_token
            self._expires_at = expires_at


class JWTBearerAuth(AuthStrategy):
    """Static JWT bearer-token authentication (no refresh)."""

    def __init__(self, token: str) -> None:
        self._token = token

    def headers_for(self, requester_id: Optional[str] = None) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def params_for(self, requester_id: Optional[str] = None) -> Dict[str, Any]:
        return {}

    def body_modifier(self, requester_id: Optional[str], body: Any) -> Any:
        return body

    def refresh_if_needed(self) -> None:
        return None


class BasicAuth(AuthStrategy):
    """HTTP Basic authentication."""

    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password

    def headers_for(self, requester_id: Optional[str] = None) -> Dict[str, str]:
        token = base64.b64encode(
            f"{self._username}:{self._password}".encode("utf-8")
        ).decode("ascii")
        return {"Authorization": f"Basic {token}"}

    def params_for(self, requester_id: Optional[str] = None) -> Dict[str, Any]:
        return {}

    def body_modifier(self, requester_id: Optional[str], body: Any) -> Any:
        return body

    def refresh_if_needed(self) -> None:
        return None


class MTLSAuth(AuthStrategy):
    """mTLS authentication.

    Stores cert/key paths; the shared HTTP client reads them off the
    strategy when configuring the underlying transport. `headers_for`
    returns no headers — TLS auth is transport-level.
    """

    def __init__(self, cert_path: str, key_path: str) -> None:
        self.cert_path = cert_path
        self.key_path = key_path

    def headers_for(self, requester_id: Optional[str] = None) -> Dict[str, str]:
        return {}

    def params_for(self, requester_id: Optional[str] = None) -> Dict[str, Any]:
        return {}

    def body_modifier(self, requester_id: Optional[str], body: Any) -> Any:
        return body

    def refresh_if_needed(self) -> None:
        return None


class AWSSigV4Auth(AuthStrategy):
    """AWS Signature V4 authentication.

    The actual signing is stubbed — the contract is what matters for the
    framework. A real implementation either uses `botocore.auth.SigV4Auth`
    or an equivalent. Producers of `headers_for` MUST sign the request as
    of the call time; this stub returns an `X-Amz-*` placeholder.
    """

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        region: str,
        service: str,
    ) -> None:
        self.access_key = access_key
        self._secret_key = secret_key
        self.region = region
        self.service = service

    def headers_for(self, requester_id: Optional[str] = None) -> Dict[str, str]:
        # Stub: real implementation signs the canonical request.
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return {
            "X-Amz-Date": ts,
            "Authorization": (
                f"AWS4-HMAC-SHA256 Credential={self.access_key}/"
                f"{ts[:8]}/{self.region}/{self.service}/aws4_request, "
                "SignedHeaders=host;x-amz-date, Signature=STUB"
            ),
        }

    def params_for(self, requester_id: Optional[str] = None) -> Dict[str, Any]:
        return {}

    def body_modifier(self, requester_id: Optional[str], body: Any) -> Any:
        return body

    def refresh_if_needed(self) -> None:
        return None


# ----- Registry -------------------------------------------------------------


class AuthStrategyRegistry:
    """Global registry of auth-strategy factories keyed by name.

    Built-in strategies register themselves at module import time.
    Extensions register additional strategies (e.g., `OAuth2Auth_Refreshable`)
    at extension-load time. Lookups are thread-safe.
    """

    _factories: ClassVar[Dict[str, Callable[[Dict[str, Any]], AuthStrategy]]] = {}
    _lock: ClassVar[RLock] = RLock()

    @classmethod
    def register(cls, name: str, factory: Callable[[Dict[str, Any]], AuthStrategy]) -> None:
        with cls._lock:
            cls._factories[name] = factory

    @classmethod
    def get(cls, name: str, payload: Dict[str, Any]) -> AuthStrategy:
        with cls._lock:
            factory = cls._factories.get(name)
        if factory is None:
            raise KeyError(f"No auth strategy registered as {name!r}")
        return factory(payload)

    @classmethod
    def names(cls) -> list:
        with cls._lock:
            return sorted(cls._factories.keys())

    @classmethod
    def unregister(cls, name: str) -> None:
        """Test helper. Production code never calls this."""
        with cls._lock:
            cls._factories.pop(name, None)


def _api_key_factory(payload: Dict[str, Any]) -> AuthStrategy:
    return APIKeyAuth(
        api_key=payload["api_key"],
        header_name=payload.get("header_name", "Authorization"),
        header_prefix=payload.get("header_prefix", "Bearer "),
    )


def _oauth2_factory(payload: Dict[str, Any]) -> AuthStrategy:
    return OAuth2Auth(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token"),
        token_url=payload.get("token_url"),
        expires_at=payload.get("expires_at"),
    )


def _jwt_factory(payload: Dict[str, Any]) -> AuthStrategy:
    return JWTBearerAuth(token=payload["token"])


def _basic_factory(payload: Dict[str, Any]) -> AuthStrategy:
    return BasicAuth(username=payload["username"], password=payload["password"])


def _mtls_factory(payload: Dict[str, Any]) -> AuthStrategy:
    return MTLSAuth(cert_path=payload["cert_path"], key_path=payload["key_path"])


def _aws_sigv4_factory(payload: Dict[str, Any]) -> AuthStrategy:
    return AWSSigV4Auth(
        access_key=payload["access_key"],
        secret_key=payload["secret_key"],
        region=payload["region"],
        service=payload["service"],
    )


AuthStrategyRegistry.register("api_key", _api_key_factory)
AuthStrategyRegistry.register("oauth2", _oauth2_factory)
AuthStrategyRegistry.register("jwt_bearer", _jwt_factory)
AuthStrategyRegistry.register("basic", _basic_factory)
AuthStrategyRegistry.register("mtls", _mtls_factory)
AuthStrategyRegistry.register("aws_sigv4", _aws_sigv4_factory)


__all__ = [
    "AuthStrategy",
    "APIKeyAuth",
    "OAuth2Auth",
    "JWTBearerAuth",
    "BasicAuth",
    "MTLSAuth",
    "AWSSigV4Auth",
    "AuthStrategyRegistry",
]
