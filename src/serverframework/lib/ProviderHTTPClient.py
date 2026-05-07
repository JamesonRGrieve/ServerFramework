"""
Shared outbound HTTP client for providers (Item 31).

Wraps `httpx.AsyncClient` (and `httpx.Client` for sync providers) and
applies the cross-cutting outbound pipeline in this order:

1. trace propagation (W3C `traceparent` from a context-var if present)
2. authentication strategy invocation (`AuthStrategy.headers_for/params_for/body_modifier`)
3. idempotency-key injection (`Idempotency-Key` header when supplied)
4. rate-limit token acquisition (delegates to a `TokenBucket` if attached)
5. timeout (per-call `deadline_ms` overrides the policy default)
6. send via `httpx`
7. response-class detection -> typed `BaseExternalError` subclass
8. `Retry-After` header parsing
9. log redaction (`Credentials.redact`) on emitted error/info messages

Connection pooling is shared per `ClientPolicy` key (so providers with
identical policy share the same underlying `httpx.AsyncClient`).

For SDK-wrapped providers, the SDK MUST accept a custom transport;
otherwise this client only covers the raw-HTTP code path. Document the
SDK-specific limitations in the provider's `PRV.X.md`.
"""

from __future__ import annotations

import contextvars
import ipaddress
import logging
import os
import socket
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple, Type
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

# Item 1's typed errors live in extensions.ExternalErrors. If Batch A's
# file isn't on the import path yet, fall back to local stubs so this
# module is independently importable.
try:
    from serverframework.extensions.ExternalErrors import (
        AuthExternalError,
        BaseExternalError,
        InvalidInputExternalError,
        PermanentExternalError,
        RateLimitExternalError,
        TransientExternalError,
    )
except ImportError:  # pragma: no cover - safety net during phased rollout

    class BaseExternalError(Exception):
        def __init__(self, message: str = "", **_: Any) -> None:
            super().__init__(message)

    class TransientExternalError(BaseExternalError):
        pass

    class AuthExternalError(BaseExternalError):
        pass

    class InvalidInputExternalError(BaseExternalError):
        pass

    class RateLimitExternalError(BaseExternalError):
        def __init__(self, message: str = "", *, retry_after_seconds: Optional[float] = None, **kw: Any) -> None:
            super().__init__(message, **kw)
            self.retry_after_seconds = retry_after_seconds

    class PermanentExternalError(BaseExternalError):
        pass


from serverframework.extensions.AuthStrategy import AuthStrategy
from serverframework.extensions.RateLimit import TokenBucket, parse_retry_after

try:
    from serverframework.lib.Credentials import redact as _redact_secret
except ImportError:  # pragma: no cover

    def _redact_secret(text: str) -> str:
        return text


# ----- SSRF guard -----------------------------------------------------------
#
# H-2 — outbound HTTP must refuse internal/loopback/link-local destinations
# unless the operator explicitly allowlists them. The default deny-list
# covers RFC1918 private space, loopback, link-local (incl. cloud IMDS at
# 169.254.169.254), CGNAT, multicast, the IPv4 broadcast, and the IPv6
# equivalents. Operators expand the allowlist via ``EGRESS_ALLOWED_HOSTS``
# (comma-separated host[:port] entries — exact host match) when a provider
# legitimately addresses an internal endpoint (mirrored upstream, in-cluster
# proxy, etc.).
#
# The default policy also restricts the URL scheme to ``http``/``https``;
# ``file://``, ``gopher://``, etc. would otherwise let an attacker who
# influences a provider URL pivot into local files or smuggle SMTP via
# gopher.


_SSRF_DEFAULT_DISABLED_ENV = "DISABLE_SSRF_GUARD"

# Networks rejected by default for outbound traffic.
_BLOCKED_V4_NETWORKS: Tuple[ipaddress.IPv4Network, ...] = tuple(
    ipaddress.IPv4Network(net)
    for net in (
        "0.0.0.0/8",        # current network / "this host"
        "10.0.0.0/8",       # RFC1918
        "127.0.0.0/8",      # loopback
        "169.254.0.0/16",   # link-local (IMDS lives here)
        "172.16.0.0/12",    # RFC1918
        "192.0.0.0/24",     # IETF protocol assignments
        "192.0.2.0/24",     # TEST-NET-1
        "192.168.0.0/16",   # RFC1918
        "198.18.0.0/15",    # benchmarking
        "198.51.100.0/24",  # TEST-NET-2
        "203.0.113.0/24",   # TEST-NET-3
        "100.64.0.0/10",    # CGNAT
        "224.0.0.0/4",      # multicast
        "240.0.0.0/4",      # reserved
        "255.255.255.255/32",  # broadcast
    )
)
_BLOCKED_V6_NETWORKS: Tuple[ipaddress.IPv6Network, ...] = tuple(
    ipaddress.IPv6Network(net)
    for net in (
        "::1/128",          # loopback
        "::/128",           # unspecified
        "::ffff:0:0/96",    # IPv4-mapped (covers v4 blocks via mapping)
        "fc00::/7",         # ULA
        "fe80::/10",        # link-local
        "ff00::/8",         # multicast
        "100::/64",         # RFC 6666 discard prefix
    )
)


class SSRFGuardError(ValueError):
    """Raised when an outbound URL targets a blocked destination (H-2)."""


def _allowed_hosts() -> List[str]:
    raw = (os.environ.get("EGRESS_ALLOWED_HOSTS") or "").strip()
    if not raw:
        return []
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def _ip_is_blocked(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv4Address):
        for net in _BLOCKED_V4_NETWORKS:
            if ip in net:
                return True
        return False
    for net in _BLOCKED_V6_NETWORKS:
        if ip in net:
            return True
    return False


def validate_outbound_url(url: str) -> None:
    """Refuse outbound requests to internal / disallowed destinations.

    Validates scheme is http/https, parses the host, and (if the host
    is a literal IP or resolves to one) checks every resolved address
    against the blocked-network set. ``EGRESS_ALLOWED_HOSTS`` (env)
    short-circuits the check on exact host match. Setting
    ``DISABLE_SSRF_GUARD=1`` disables the whole guard; this is the
    documented escape hatch for tests and deployments where a network
    egress proxy already enforces the policy upstream.

    Raises :class:`SSRFGuardError` on rejection so the caller's
    ``except`` branches stay typed (the framework's outbound paths
    surface this as ``InvalidInputExternalError``).
    """
    if os.environ.get(_SSRF_DEFAULT_DISABLED_ENV, "").lower() in ("1", "true", "yes"):
        return
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise SSRFGuardError(
            f"Refusing outbound URL {url!r}: scheme {scheme!r} not in {{http, https}}"
        )
    host = (parsed.hostname or "").lower()
    if not host:
        raise SSRFGuardError(f"Refusing outbound URL {url!r}: missing host")
    port = parsed.port
    allow_key_with_port = f"{host}:{port}" if port else host
    allow = _allowed_hosts()
    if host in allow or allow_key_with_port in allow:
        return

    # Literal-IP fast path.
    try:
        ipaddress.ip_address(host)
        if _ip_is_blocked(host):
            raise SSRFGuardError(
                f"Refusing outbound URL {url!r}: literal IP {host} in "
                "blocked network range"
            )
        return
    except ValueError:
        pass

    # Hostname path — resolve and reject if any resolved address is blocked.
    # Best-effort: if resolution fails, fall through and let httpx raise.
    try:
        infos = socket.getaddrinfo(host, port or 80, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return
    for info in infos:
        sockaddr = info[4]
        addr = sockaddr[0]
        if _ip_is_blocked(addr):
            raise SSRFGuardError(
                f"Refusing outbound URL {url!r}: host {host!r} resolves to "
                f"blocked address {addr}"
            )


# ----- Trace context --------------------------------------------------------

# W3C traceparent context var. Populated by request middleware when present.
_traceparent: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "traceparent", default=None
)


def set_traceparent(traceparent: Optional[str]) -> contextvars.Token:
    """Set the current W3C traceparent on the context. Returns the
    contextvar Token so callers can `reset()` to restore the previous value."""
    return _traceparent.set(traceparent)


def get_traceparent() -> Optional[str]:
    return _traceparent.get()


# ----- Policy ---------------------------------------------------------------


@dataclass(frozen=True)
class ClientPolicy:
    """Per-provider HTTP client policy. Used as the pool key."""

    timeout: float = 30.0
    max_retries: int = 3
    base_backoff_ms: int = 100
    max_backoff_ms: int = 5000
    jitter: float = 0.1
    tls_verify: bool = True
    egress_proxy: Optional[str] = None


# ----- Pooled client cache --------------------------------------------------

_shared_clients: Dict[Tuple, httpx.AsyncClient] = {}
_shared_sync_clients: Dict[Tuple, httpx.Client] = {}


def _policy_pool_key(policy: ClientPolicy) -> Tuple:
    return (
        policy.timeout,
        policy.tls_verify,
        policy.egress_proxy,
    )


def _build_async_client(policy: ClientPolicy) -> httpx.AsyncClient:
    kwargs: Dict[str, Any] = {
        "timeout": policy.timeout,
        "verify": policy.tls_verify,
        # H-2 — disable transparent redirect-following. Each redirect
        # would need its own SSRF check; httpx's default of 20 hops is a
        # large attack surface. Providers that need redirects should
        # opt in per call by setting ``follow_redirects=True`` AND
        # ensuring the destination passes ``validate_outbound_url``.
        "follow_redirects": False,
    }
    if policy.egress_proxy:
        kwargs["proxy"] = policy.egress_proxy
    return httpx.AsyncClient(**kwargs)


def _build_sync_client(policy: ClientPolicy) -> httpx.Client:
    kwargs: Dict[str, Any] = {
        "timeout": policy.timeout,
        "verify": policy.tls_verify,
        "follow_redirects": False,
    }
    if policy.egress_proxy:
        kwargs["proxy"] = policy.egress_proxy
    return httpx.Client(**kwargs)


def get_async_client(policy: ClientPolicy) -> httpx.AsyncClient:
    key = _policy_pool_key(policy)
    client = _shared_clients.get(key)
    if client is None:
        client = _build_async_client(policy)
        _shared_clients[key] = client
    return client


def get_sync_client(policy: ClientPolicy) -> httpx.Client:
    key = _policy_pool_key(policy)
    client = _shared_sync_clients.get(key)
    if client is None:
        client = _build_sync_client(policy)
        _shared_sync_clients[key] = client
    return client


# ----- Response classification ---------------------------------------------


def _classify_response(response: httpx.Response, provider: Optional[str]) -> None:
    """Raise the appropriate `BaseExternalError` subclass for non-2xx."""
    status = response.status_code
    if 200 <= status < 300:
        return
    detail = _safe_response_text(response)
    detail = _redact_secret(detail)
    kw = dict(provider=provider, upstream_status=status, upstream_payload=detail)
    if status == 429:
        retry_after = parse_retry_after(response.headers.get("Retry-After"))
        raise RateLimitExternalError(
            f"Rate limited (status {status})",
            retry_after_seconds=retry_after,
            **kw,
        )
    if status in (401, 403):
        raise AuthExternalError(f"Auth failure (status {status})", **kw)
    if 400 <= status < 500:
        raise InvalidInputExternalError(f"Client error (status {status})", **kw)
    if 500 <= status < 600:
        raise TransientExternalError(f"Server error (status {status})", **kw)
    raise PermanentExternalError(f"Unhandled status {status}", **kw)


def _safe_response_text(response: httpx.Response) -> str:
    try:
        return response.text[:2048]
    except Exception:
        return "<unreadable response body>"


def _maybe_parse_model(
    response: httpx.Response, model: Optional[Type[BaseModel]]
) -> Any:
    if model is None:
        try:
            return response.json()
        except Exception:
            return response.text
    return model.model_validate(response.json())


# ----- Client ---------------------------------------------------------------


# Item 33 — set of provider classes for which the framework has already
# emitted the "version-without-header" warning. Keyed on the bound provider
# class so the warning fires exactly once per (provider, version) pair.
_versioning_warned: set = set()


def _maybe_warn_version_without_header(provider: Any) -> None:
    """Emit a one-shot warning when a bound provider declares
    `external_api_version` but no `external_api_version_header`.

    The version is then assumed to be consumed by the SDK (e.g. Stripe's
    Python SDK reads `stripe.api_version`) rather than as a wire header.
    """
    if provider is None:
        return
    version = getattr(provider, "external_api_version", None)
    header = getattr(provider, "external_api_version_header", None)
    if not version or header:
        return
    key = (provider, version)
    if key in _versioning_warned:
        return
    _versioning_warned.add(key)
    name = getattr(provider, "__name__", provider.__class__.__name__)
    logging.getLogger("ProviderHTTP").warning(
        "Provider %s declares external_api_version=%r without "
        "external_api_version_header; the version will not be injected "
        "as an outbound header (SDK-consumed only).",
        name,
        version,
    )


class ProviderHTTPClient:
    """Async HTTP client wrapping `httpx.AsyncClient` with the provider
    cross-cutting pipeline.

    Construct one per provider instance. The underlying `httpx.AsyncClient`
    is pooled across all `ProviderHTTPClient` instances that share the same
    `ClientPolicy` (modulo the proxy/TLS keys).

    `provider` is the bound provider class (subclass of
    `AbstractStaticProvider`). When supplied, the client reads cross-cutting
    ClassVars off it — currently `external_api_version` and
    `external_api_version_header` (Item 33) for header injection.
    """

    def __init__(
        self,
        policy: Optional[ClientPolicy] = None,
        auth_strategy: Optional[AuthStrategy] = None,
        rate_limit: Optional[TokenBucket] = None,
        provider_name: Optional[str] = None,
        provider: Optional[Any] = None,
    ) -> None:
        self.policy = policy or ClientPolicy()
        self.auth_strategy = auth_strategy
        self.rate_limit = rate_limit
        self.provider_name = provider_name
        self.provider = provider
        self._logger = logging.getLogger(f"ProviderHTTP.{provider_name or 'default'}")
        _maybe_warn_version_without_header(provider)

    # --- Pipeline helpers ---

    def _build_headers(
        self,
        extra_headers: Optional[Mapping[str, str]],
        idempotency_key: Optional[str],
        requester_id: Optional[str],
    ) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        tp = get_traceparent()
        if tp:
            headers["traceparent"] = tp
        if self.auth_strategy is not None:
            self.auth_strategy.refresh_if_needed()
            headers.update(self.auth_strategy.headers_for(requester_id))
        # Item 33 — upstream API version pinning. Same pipeline step as
        # auth strategy header injection: when the bound provider declares
        # both a version and a header name, inject as a wire header.
        if self.provider is not None:
            version = getattr(self.provider, "external_api_version", None)
            header_name = getattr(self.provider, "external_api_version_header", None)
            if version and header_name:
                headers[header_name] = version
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def _build_params(
        self, extra_params: Optional[Mapping[str, Any]], requester_id: Optional[str]
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if self.auth_strategy is not None:
            params.update(self.auth_strategy.params_for(requester_id))
        if extra_params:
            params.update(extra_params)
        return params

    def _maybe_modify_body(
        self, body: Any, requester_id: Optional[str]
    ) -> Any:
        if self.auth_strategy is None or body is None:
            return body
        return self.auth_strategy.body_modifier(requester_id, body)

    def _acquire_rate_token(self, deadline_ms: Optional[int]) -> None:
        if self.rate_limit is None:
            return
        timeout = (deadline_ms / 1000.0) if deadline_ms else None
        if not self.rate_limit.acquire_blocking(timeout=timeout):
            raise RateLimitExternalError(
                "Local rate-limit acquire timeout",
                provider=self.provider_name,
            )

    def _resolve_timeout(self, deadline_ms: Optional[int]) -> float:
        # Item 47 — when no per-call deadline_ms is supplied, fall back to
        # the inbound request's remaining budget so cross-layer budgets
        # cooperate instead of stacking. A `DeadlineExceededError` raised
        # by the per-call check propagates up to the inbound middleware,
        # which surfaces it as 504.
        if deadline_ms is None:
            try:
                from serverframework.lib.RequestContext import (
                    DeadlineExceededError,
                    remaining_deadline_ms,
                )

                request_remaining = remaining_deadline_ms()
                if request_remaining is not None:
                    if request_remaining <= 0:
                        raise DeadlineExceededError(
                            elapsed_ms=0, layer="provider_http"
                        )
                    deadline_ms = request_remaining
            except ImportError:  # pragma: no cover - test isolation
                pass
        if deadline_ms is not None:
            return max(0.001, deadline_ms / 1000.0)
        return self.policy.timeout

    # --- Public verb methods (async) ---

    async def request(
        self,
        method: str,
        url: str,
        *,
        model: Optional[Type[BaseModel]] = None,
        idempotency_key: Optional[str] = None,
        deadline_ms: Optional[int] = None,
        requester_id: Optional[str] = None,
        json: Any = None,
        data: Any = None,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        **kwargs: Any,
    ) -> Any:
        # H-2 — refuse outbound to internal/loopback/link-local before we
        # spend pool slots, time budget, or auth headers on the request.
        try:
            validate_outbound_url(url)
        except SSRFGuardError as exc:
            raise InvalidInputExternalError(
                f"Outbound URL refused by SSRF guard: {exc}",
                provider=self.provider_name,
            )
        merged_headers = self._build_headers(headers, idempotency_key, requester_id)
        merged_params = self._build_params(params, requester_id)
        modified_json = self._maybe_modify_body(json, requester_id)
        modified_data = self._maybe_modify_body(data, requester_id) if json is None else data
        self._acquire_rate_token(deadline_ms)
        client = get_async_client(self.policy)
        try:
            response = await client.request(
                method,
                url,
                headers=merged_headers,
                params=merged_params or None,
                json=modified_json,
                data=modified_data,
                timeout=self._resolve_timeout(deadline_ms),
                **kwargs,
            )
        except httpx.TimeoutException as exc:
            raise TransientExternalError(
                f"Timeout calling {method} {url}",
                provider=self.provider_name,
                cause=exc,
            )
        except httpx.RequestError as exc:
            raise TransientExternalError(
                f"Network error calling {method} {url}: {exc!s}",
                provider=self.provider_name,
                cause=exc,
            )
        _classify_response(response, self.provider_name)
        return _maybe_parse_model(response, model)

    async def get(self, url: str, **kw: Any) -> Any:
        return await self.request("GET", url, **kw)

    async def post(self, url: str, **kw: Any) -> Any:
        return await self.request("POST", url, **kw)

    async def put(self, url: str, **kw: Any) -> Any:
        return await self.request("PUT", url, **kw)

    async def patch(self, url: str, **kw: Any) -> Any:
        return await self.request("PATCH", url, **kw)

    async def delete(self, url: str, **kw: Any) -> Any:
        return await self.request("DELETE", url, **kw)


class ProviderHTTPClientSync:
    """Synchronous variant of `ProviderHTTPClient` wrapping `httpx.Client`."""

    def __init__(
        self,
        policy: Optional[ClientPolicy] = None,
        auth_strategy: Optional[AuthStrategy] = None,
        rate_limit: Optional[TokenBucket] = None,
        provider_name: Optional[str] = None,
        provider: Optional[Any] = None,
    ) -> None:
        self.policy = policy or ClientPolicy()
        self.auth_strategy = auth_strategy
        self.rate_limit = rate_limit
        self.provider_name = provider_name
        self.provider = provider
        self._logger = logging.getLogger(f"ProviderHTTP.{provider_name or 'default'}")
        self._async_helper = ProviderHTTPClient(
            policy=policy,
            auth_strategy=auth_strategy,
            rate_limit=rate_limit,
            provider_name=provider_name,
            provider=provider,
        )

    def _build_headers(self, *a: Any, **k: Any) -> Dict[str, str]:
        return self._async_helper._build_headers(*a, **k)

    def _build_params(self, *a: Any, **k: Any) -> Dict[str, Any]:
        return self._async_helper._build_params(*a, **k)

    def _maybe_modify_body(self, *a: Any, **k: Any) -> Any:
        return self._async_helper._maybe_modify_body(*a, **k)

    def _acquire_rate_token(self, deadline_ms: Optional[int]) -> None:
        self._async_helper._acquire_rate_token(deadline_ms)

    def _resolve_timeout(self, deadline_ms: Optional[int]) -> float:
        return self._async_helper._resolve_timeout(deadline_ms)

    def request(
        self,
        method: str,
        url: str,
        *,
        model: Optional[Type[BaseModel]] = None,
        idempotency_key: Optional[str] = None,
        deadline_ms: Optional[int] = None,
        requester_id: Optional[str] = None,
        json: Any = None,
        data: Any = None,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        **kwargs: Any,
    ) -> Any:
        # H-2 — see ProviderHTTPClient.request for rationale.
        try:
            validate_outbound_url(url)
        except SSRFGuardError as exc:
            raise InvalidInputExternalError(
                f"Outbound URL refused by SSRF guard: {exc}",
                provider=self.provider_name,
            )
        merged_headers = self._build_headers(headers, idempotency_key, requester_id)
        merged_params = self._build_params(params, requester_id)
        modified_json = self._maybe_modify_body(json, requester_id)
        modified_data = self._maybe_modify_body(data, requester_id) if json is None else data
        self._acquire_rate_token(deadline_ms)
        client = get_sync_client(self.policy)
        try:
            response = client.request(
                method,
                url,
                headers=merged_headers,
                params=merged_params or None,
                json=modified_json,
                data=modified_data,
                timeout=self._resolve_timeout(deadline_ms),
                **kwargs,
            )
        except httpx.TimeoutException as exc:
            raise TransientExternalError(
                f"Timeout calling {method} {url}",
                provider=self.provider_name,
                cause=exc,
            )
        except httpx.RequestError as exc:
            raise TransientExternalError(
                f"Network error calling {method} {url}: {exc!s}",
                provider=self.provider_name,
                cause=exc,
            )
        _classify_response(response, self.provider_name)
        return _maybe_parse_model(response, model)

    def get(self, url: str, **kw: Any) -> Any:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw: Any) -> Any:
        return self.request("POST", url, **kw)

    def put(self, url: str, **kw: Any) -> Any:
        return self.request("PUT", url, **kw)

    def patch(self, url: str, **kw: Any) -> Any:
        return self.request("PATCH", url, **kw)

    def delete(self, url: str, **kw: Any) -> Any:
        return self.request("DELETE", url, **kw)


__all__ = [
    "ClientPolicy",
    "ProviderHTTPClient",
    "ProviderHTTPClientSync",
    "set_traceparent",
    "get_traceparent",
    "get_async_client",
    "get_sync_client",
    "validate_outbound_url",
    "SSRFGuardError",
]
