"""Tests for the Valkey extension.

Covers:
    - Extension metadata + ability declarations
    - Provider URL resolution (instance.api_key vs env vs default)
    - PRV_Fake_Valkey publish/subscribe semantics (the no-broker test seam)
    - PRV_Fake_Valkey end-to-end through `RedisStreamsEventBus`
    - PRV_Valkey raises a clear error when redis-py is missing

Real PRV_Valkey integration tests against a sandbox Valkey instance run
under the `external_api` marker (Item 15) and are auto-xfailed when
`VALKEY_URL` is not configured.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest
from pydantic import BaseModel

from serverframework.extensions.valkey.EXT_Valkey import (
    AbstractValkeyProvider,
    EXT_Valkey,
)
from serverframework.extensions.valkey.PRV_Fake_Valkey import (
    PRV_Fake_Valkey,
    _FakeValkeyStreamsTransport,
)
from serverframework.extensions.valkey.PRV_Valkey import PRV_Valkey
from serverframework.logic.EventBus import (
    BrokerTransport,
    InMemoryBrokerTransport,
    RedisStreamsEventBus,
)


# ---------------------------------------------------------------------------
# Test event class
# ---------------------------------------------------------------------------


class _UserSignedUp(BaseModel):
    user_id: str
    email: str


class _FakeInstance:
    """Stand-in for `ProviderInstanceModel` for unit tests; no DB."""

    def __init__(self, api_key: str = None) -> None:
        self.api_key = api_key


# ---------------------------------------------------------------------------
# Extension metadata
# ---------------------------------------------------------------------------


def test_extension_name_is_valkey():
    """Naming policy: FOSS api-parity. Valkey is the FOSS continuation
    of Redis post-license-change; the extension is named after the
    project, not the wire protocol."""
    assert EXT_Valkey.name == "valkey"
    assert EXT_Valkey.friendly_name == "Valkey (Redis-protocol)"


def test_extension_declares_canonical_abilities():
    """The four core abilities the framework relies on, plus the
    broker-transport ability the EventBus consumes."""
    abilities = EXT_Valkey._abilities
    assert "key_value" in abilities
    assert "streams" in abilities
    assert "pubsub" in abilities
    assert "counter" in abilities
    assert "broker_transport" in abilities


def test_extension_declares_redis_py_dependency():
    """`Dependencies` is a `List[Dependency]` subclass — iterate directly."""
    names = {d.name for d in EXT_Valkey.dependencies}
    assert "redis" in names


def test_get_root_instance_returns_concrete_provider():
    """Item 19: root-scoped provider for system functionality. The
    framework's EventBus and rate-limiter call this to bond a connection."""
    assert EXT_Valkey.get_root_instance() is PRV_Valkey


# ---------------------------------------------------------------------------
# PRV_Valkey URL resolution
# ---------------------------------------------------------------------------


def test_resolve_url_prefers_instance_api_key():
    instance = _FakeInstance(api_key="redis://specific-host:6380/2")
    assert PRV_Valkey._resolve_url(instance) == "redis://specific-host:6380/2"


def test_resolve_url_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("VALKEY_URL", "redis://from-env:6379/0")
    instance = _FakeInstance(api_key=None)
    assert PRV_Valkey._resolve_url(instance) == "redis://from-env:6379/0"


def test_resolve_url_default_when_unset(monkeypatch):
    monkeypatch.delenv("VALKEY_URL", raising=False)
    instance = _FakeInstance(api_key=None)
    # Default is the localhost dev URL.
    url = PRV_Valkey._resolve_url(instance)
    assert url.startswith("redis://localhost:6379")


def test_prv_valkey_connect_raises_clear_error_when_redis_missing(monkeypatch):
    """When `redis-py` is not on the path, `PRV_Valkey.connect` raises
    a RuntimeError pointing at the dependency manager. The framework's
    extension loader installs missing pip deps before the provider is
    exercised at runtime; this guards against import-time crashes."""
    # Stash redis if present, replace with a sentinel that imports as None.
    original = sys.modules.get("redis")
    original_async = sys.modules.get("redis.asyncio")
    sys.modules["redis"] = None  # type: ignore[assignment]
    sys.modules["redis.asyncio"] = None  # type: ignore[assignment]

    instance = _FakeInstance(api_key="redis://no-host/0")
    # Must clear cached connection from earlier tests.
    PRV_Valkey._connections.clear()
    try:
        with pytest.raises(RuntimeError, match="redis>=4.2"):
            PRV_Valkey.connect(instance)
    finally:
        for k, v in (("redis", original), ("redis.asyncio", original_async)):
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        PRV_Valkey._connections.clear()


# ---------------------------------------------------------------------------
# Fake provider: streams semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_streams_transport_round_trip():
    instance = _FakeInstance()
    transport = PRV_Fake_Valkey.build_streams_transport(instance)
    assert isinstance(transport, _FakeValkeyStreamsTransport)

    received: list = []

    async def handler(payload: bytes) -> None:
        received.append(payload)

    await transport.subscribe("topic-a", handler)
    await transport.send("topic-a", b"hello")
    assert received == [b"hello"]
    await transport.close()


@pytest.mark.asyncio
async def test_fake_streams_transport_isolates_topics():
    instance = _FakeInstance()
    transport = PRV_Fake_Valkey.build_streams_transport(instance)

    a_received: list = []
    b_received: list = []

    async def a(payload: bytes) -> None:
        a_received.append(payload)

    async def b(payload: bytes) -> None:
        b_received.append(payload)

    await transport.subscribe("topic-a", a)
    await transport.subscribe("topic-b", b)
    await transport.send("topic-a", b"to-a")

    assert a_received == [b"to-a"]
    assert b_received == []
    await transport.close()


@pytest.mark.asyncio
async def test_fake_streams_transport_swallows_handler_errors():
    """Cross-process broker semantics: a subscriber failure must not
    take the publisher down. The fake transport mirrors the contract
    so tests verify the right behavior without standing up Valkey."""
    instance = _FakeInstance()
    transport = PRV_Fake_Valkey.build_streams_transport(instance)

    async def bad(payload: bytes) -> None:
        raise RuntimeError("subscriber blew up")

    await transport.subscribe("topic", bad)
    # Must not raise.
    await transport.send("topic", b"payload")
    await transport.close()


@pytest.mark.asyncio
async def test_fake_transport_close_blocks_further_send():
    instance = _FakeInstance()
    transport = PRV_Fake_Valkey.build_streams_transport(instance)
    await transport.close()
    with pytest.raises(RuntimeError, match="closed"):
        await transport.send("topic", b"x")


@pytest.mark.asyncio
async def test_fake_transport_close_blocks_further_subscribe():
    instance = _FakeInstance()
    transport = PRV_Fake_Valkey.build_streams_transport(instance)
    await transport.close()

    async def h(p: bytes) -> None:
        pass

    with pytest.raises(RuntimeError, match="closed"):
        await transport.subscribe("topic", h)


# ---------------------------------------------------------------------------
# End-to-end: RedisStreamsEventBus consuming the Valkey transport
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_bus_consumes_valkey_transport_end_to_end():
    """`RedisStreamsEventBus(transport=PRV_Fake_Valkey.build_streams_transport(...))`
    publishes through the fake transport and dispatches into the
    subscribed handler. Verifies the EventBus duck-types correctly
    against the extension-supplied transport without any redis SDK
    on the framework's path."""
    instance = _FakeInstance()
    transport = PRV_Fake_Valkey.build_streams_transport(instance)
    bus = RedisStreamsEventBus(transport=transport)

    received: list = []

    async def handler(event: _UserSignedUp) -> None:
        received.append(event.user_id)

    bus.subscribe(_UserSignedUp, handler)
    # Subscribe schedules an async transport.subscribe call; give it a tick.
    await asyncio.sleep(0)
    await bus.publish(_UserSignedUp(user_id="u-42", email="x@x"))

    assert received == ["u-42"]
    await bus.close()


def test_streams_transport_conforms_to_broker_transport_contract():
    """Structural typing: the extension-supplied transport exposes the
    same surface as `BrokerTransport`. No nominal subclass relationship
    is required (the EventBus duck-types), but every method must exist."""
    instance = _FakeInstance()
    transport = PRV_Fake_Valkey.build_streams_transport(instance)
    for method in ("send", "subscribe", "close"):
        assert hasattr(transport, method)
        assert callable(getattr(transport, method))


# ---------------------------------------------------------------------------
# Sandbox-credential gated tests against a real Valkey (Item 15)
# ---------------------------------------------------------------------------


@pytest.mark.external_api
@pytest.mark.skipif(
    not os.environ.get("VALKEY_URL"),
    reason="Requires VALKEY_URL pointing at a sandbox Valkey instance",
)
def test_real_prv_valkey_against_sandbox():
    """End-to-end against a real Valkey/Redis instance. Auto-xfailed in
    branches without the credential per Item 15."""
    instance = _FakeInstance(api_key=os.environ["VALKEY_URL"])
    PRV_Valkey._connections.clear()
    client = PRV_Valkey.connect(instance)
    assert client is not None
    PRV_Valkey._connections.clear()
