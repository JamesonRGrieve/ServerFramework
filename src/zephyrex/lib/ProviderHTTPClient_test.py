"""Unit tests for the shared provider HTTP client (Item 31)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from zephyrex.extensions.AuthStrategy import APIKeyAuth
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    RateLimitExternalError,
    TransientExternalError,
)
from zephyrex.extensions.RateLimit import RateLimit, TokenBucket
from zephyrex.lib.ProviderHTTPClient import (
    ClientPolicy,
    ProviderHTTPClient,
    ProviderHTTPClientSync,
    _shared_clients,
    _shared_sync_clients,
    _shared_unbound_clients,
    get_async_client,
    get_sync_client,
    get_traceparent,
    set_traceparent,
)


@pytest.fixture(autouse=True)
def _clear_pool():
    _shared_clients.clear()
    _shared_unbound_clients.clear()
    _shared_sync_clients.clear()
    yield
    _shared_clients.clear()
    _shared_unbound_clients.clear()
    _shared_sync_clients.clear()


def _patch_client_with_handler(monkeypatch, handler, sync: bool = False):
    """Replace pool builders so requests route through `httpx.MockTransport`."""
    transport_factory = httpx.MockTransport if not sync else httpx.MockTransport
    if not sync:

        def _build_async(policy):
            return httpx.AsyncClient(
                transport=transport_factory(handler), timeout=policy.timeout
            )

        monkeypatch.setattr(
            "zephyrex.lib.ProviderHTTPClient._build_async_client", _build_async
        )
    else:

        def _build_sync(policy):
            return httpx.Client(
                transport=transport_factory(handler), timeout=policy.timeout
            )

        monkeypatch.setattr(
            "zephyrex.lib.ProviderHTTPClient._build_sync_client", _build_sync
        )


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
async def test_requests_name_zephyrex_and_the_deployments_source(monkeypatch):
    """Wikimedia and others refuse anonymous clients: every request says
    what is calling, unless the caller names itself."""
    from zephyrex.lib.Environment import env

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("User-Agent"))
        return httpx.Response(200, json={})

    _patch_client_with_handler(monkeypatch, handler)
    c = ProviderHTTPClient()
    await c.get("https://api.example/test")
    await c.get("https://api.example/test", headers={"User-Agent": "custom/1"})
    assert seen[0].startswith("zephyrex/")
    assert seen[0].endswith(f"({env('APP_REPOSITORY')})")
    assert seen[1] == "custom/1"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_failure_names_the_host_never_the_path_or_query(monkeypatch):
    """A Telegram bot token is a path segment and keys travel as query
    parameters: neither may reach an error message (or a log)."""
    from zephyrex.extensions.ExternalErrors import TransientExternalError

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    _patch_client_with_handler(monkeypatch, handler)
    with pytest.raises(TransientExternalError) as raised:
        await ProviderHTTPClient().get(
            "https://api.example/bot123:SECRET/sendMessage?key=SECRET2"
        )
    assert "https://api.example" in raised.value.message
    assert "SECRET" not in raised.value.message


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
        from zephyrex.lib.ProviderHTTPClient import _traceparent

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
    bucket = TokenBucket(RateLimit(rps=0.01, burst=1))
    bucket.try_acquire()  # drain the only token — refill takes 100s
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
def test_each_event_loop_gets_its_own_client():
    """An httpx.AsyncClient is bound to the loop it first sends on; sharing
    one across loops failed with "Event loop is closed" (every async test
    runs in a loop of its own)."""
    import asyncio

    async def pooled():
        return get_async_client(ClientPolicy())

    first = asyncio.run(pooled())
    second = asyncio.run(pooled())
    assert first is not second

    async def twice():
        return get_async_client(ClientPolicy()), get_async_client(ClientPolicy())

    a, b = asyncio.run(twice())
    assert a is b


@pytest.mark.unit
def test_pool_separates_clients_for_distinct_policy():
    a = get_async_client(ClientPolicy(timeout=10.0))
    b = get_async_client(ClientPolicy(timeout=20.0))
    assert a is not b
