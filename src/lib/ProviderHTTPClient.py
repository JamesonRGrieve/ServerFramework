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
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple, Type

import httpx
from pydantic import BaseModel

# Item 1's typed errors live in extensions.ExternalErrors. If Batch A's
# file isn't on the import path yet, fall back to local stubs so this
# module is independently importable.
try:
    from extensions.ExternalErrors import (
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


from extensions.AuthStrategy import AuthStrategy
from extensions.RateLimit import TokenBucket, parse_retry_after

try:
    from lib.Credentials import redact as _redact_secret
except ImportError:  # pragma: no cover

    def _redact_secret(text: str) -> str:
        return text


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
    }
    if policy.egress_proxy:
        kwargs["proxy"] = policy.egress_proxy
    return httpx.AsyncClient(**kwargs)


def _build_sync_client(policy: ClientPolicy) -> httpx.Client:
    kwargs: Dict[str, Any] = {
        "timeout": policy.timeout,
        "verify": policy.tls_verify,
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
]
