"""Unit tests for the shared provider HTTP client (Item 31)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from serverframework.extensions.AuthStrategy import APIKeyAuth
from serverframework.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    RateLimitExternalError,
    TransientExternalError,
)
from serverframework.extensions.RateLimit import RateLimit, TokenBucket
from serverframework.lib.ProviderHTTPClient import (
    ClientPolicy,
    ProviderHTTPClient,
    ProviderHTTPClientSync,
    _shared_clients,
    _shared_sync_clients,
    get_async_client,
    get_sync_client,
    get_traceparent,
    set_traceparent,
)


@pytest.fixture(autouse=True)
def _clear_pool():
    _shared_clients.clear()
    _shared_sync_clients.clear()
    yield
    _shared_clients.clear()
    _shared_sync_clients.clear()


def _patch_client_with_handler(monkeypatch, handler, sync: bool = False):
    """Replace pool builders so requests route through `httpx.MockTransport`."""
    transport_factory = (
        httpx.MockTransport if not sync else httpx.MockTransport
    )
    if not sync:
        def _build_async(policy):
            return httpx.AsyncClient(transport=transport_factory(handler), timeout=policy.timeout)
        monkeypatch.setattr("serverframework.lib.ProviderHTTPClient._build_async_client", _build_async)
    else:
        def _build_sync(policy):
            return httpx.Client(transport=transport_factory(handler), timeout=policy.timeout)
        monkeypatch.setattr("serverframework.lib.ProviderHTTPClient._build_sync_client", _build_sync)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_returns_json_dict(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"hello": "world"})
    _patch_client_with_handler(monkeypatch, handler)
    c = ProviderHTTPClient()
    out = await c.get("https://api.example/test")
    assert out == {"hello": "world"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_auth_strategy_headers_injected(monkeypatch):
    captured = {}
    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={})
    _patch_client_with_handler(monkeypatch, handler)
    c = ProviderHTTPClient(auth_strategy=APIKeyAuth("k123"))
    await c.get("https://api.example/test")
    assert captured["auth"] == "Bearer k123"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_idempotency_header_injected(monkeypatch):
    captured = {}
    def handler(request: httpx.Request) -> httpx.Response:
        captured["idem"] = request.headers.get("Idempotency-Key")
        return httpx.Response(200, json={})
    _patch_client_with_handler(monkeypatch, handler)
    c = ProviderHTTPClient()
    await c.post("https://api.example/test", idempotency_key="key-abc")
    assert captured["idem"] == "key-abc"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_traceparent_propagated(monkeypatch):
    captured = {}
    def handler(request: httpx.Request) -> httpx.Response:
        captured["tp"] = request.headers.get("traceparent")
        return httpx.Response(200, json={})
    _patch_client_with_handler(monkeypatch, handler)
    token = set_traceparent("00-abc-def-01")
    try:
        c = ProviderHTTPClient()
        await c.get("https://api.example/test")
        assert captured["tp"] == "00-abc-def-01"
    finally:
        import contextvars
        # Reset the contextvar
        from serverframework.lib.ProviderHTTPClient import _traceparent
        _traceparent.reset(token)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_5xx_raises_transient(monkeypatch):
    _patch_client_with_handler(monkeypatch, lambda r: httpx.Response(503, text="boom"))
    c = ProviderHTTPClient(provider_name="test")
    with pytest.raises(TransientExternalError):
        await c.get("https://api.example/x")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_429_raises_rate_limit_with_retry_after(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "12"}, text="slow down")
    _patch_client_with_handler(monkeypatch, handler)
    c = ProviderHTTPClient(provider_name="test")
    with pytest.raises(RateLimitExternalError) as ei:
        await c.get("https://api.example/x")
    assert ei.value.retry_after_seconds == 12


@pytest.mark.unit
@pytest.mark.asyncio
async def test_401_raises_auth(monkeypatch):
    _patch_client_with_handler(monkeypatch, lambda r: httpx.Response(401))
    c = ProviderHTTPClient(provider_name="test")
    with pytest.raises(AuthExternalError):
        await c.get("https://api.example/x")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_400_raises_invalid_input(monkeypatch):
    _patch_client_with_handler(monkeypatch, lambda r: httpx.Response(400, text="bad"))
    c = ProviderHTTPClient(provider_name="test")
    with pytest.raises(InvalidInputExternalError):
        await c.get("https://api.example/x")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rate_limit_token_blocks(monkeypatch):
    _patch_client_with_handler(monkeypatch, lambda r: httpx.Response(200, json={}))
    bucket = TokenBucket(RateLimit(rps=1, burst=1))
    bucket.try_acquire()  # drain the only token
    c = ProviderHTTPClient(rate_limit=bucket, provider_name="test")
    with pytest.raises(RateLimitExternalError):
        await c.get("https://api.example/x", deadline_ms=10)


@pytest.mark.unit
def test_sync_client_works(monkeypatch):
    _patch_client_with_handler(
        monkeypatch, lambda r: httpx.Response(200, json={"k": "v"}), sync=True
    )
    c = ProviderHTTPClientSync()
    assert c.get("https://api.example/x") == {"k": "v"}


@pytest.mark.unit
def test_pool_reuses_clients_for_same_policy():
    a = get_async_client(ClientPolicy())
    b = get_async_client(ClientPolicy())
    assert a is b
    s1 = get_sync_client(ClientPolicy())
    s2 = get_sync_client(ClientPolicy())
    assert s1 is s2


@pytest.mark.unit
def test_pool_separates_clients_for_distinct_policy():
    a = get_async_client(ClientPolicy(timeout=10.0))
    b = get_async_client(ClientPolicy(timeout=20.0))
    assert a is not b
