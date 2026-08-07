"""Tests for the DatabaseMemory extension.

Covers:
    - Extension metadata + ability declarations
    - Provider URL resolution (instance.api_key vs env vs default,
      including the legacy VALKEY_URL fallback)
    - PRV_Fake_DatabaseMemory publish/subscribe semantics (the no-broker test seam)
    - PRV_Fake_DatabaseMemory end-to-end through `RedisStreamsEventBus`
    - PRV_Valkey raises a clear error when redis-py is missing
    - Item 98: restructure validates against the EXT_Database precedent
      (extensions are named for protocol families, providers for backends)

Real PRV_Valkey integration tests against a sandbox instance run under
the `external_api` marker (Item 15) and are auto-xfailed when
`DATABASE_MEMORY_URL` (or `VALKEY_URL`) is not configured.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest
from pydantic import BaseModel

from zephyrex.extensions.database_memory.EXT_DatabaseMemory import (
    AbstractDatabaseMemoryProvider,
    EXT_DatabaseMemory,
)
from zephyrex.extensions.database_memory.PRV_Fake_DatabaseMemory import (
    PRV_Fake_DatabaseMemory,
    _FakeDatabaseMemoryStreamsTransport,
)
from zephyrex.extensions.database_memory.PRV_Valkey import PRV_Valkey
from zephyrex.logic.EventBus import (
    BrokerTransport,
    InMemoryBrokerTransport,
    RedisStreamsEventBus,
)


class _UserSignedUp(BaseModel):
    user_id: str
    email: str


class _FakeInstance:
    """Stand-in for `ProviderInstanceModel` for unit tests; no DB."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key


# ---------------------------------------------------------------------------
# Extension metadata — Item 98 protocol-family naming
# ---------------------------------------------------------------------------


def test_extension_name_is_database_memory():
    """Item 98: the extension is named for the protocol family
    (in-memory data stores), not for any one product."""
    assert EXT_DatabaseMemory.name == "database_memory"
    assert EXT_DatabaseMemory.friendly_name == "Database (In-Memory)"


def test_extension_declares_canonical_abilities():
    """The four core abilities the framework relies on, plus the
    broker-transport ability the EventBus consumes."""
    abilities = EXT_DatabaseMemory._abilities
    assert "key_value" in abilities
    assert "streams" in abilities
    assert "pubsub" in abilities
    assert "counter" in abilities
    assert "broker_transport" in abilities


def test_extension_declares_redis_py_dependency():
    """`Dependencies` is a `List[Dependency]` subclass — iterate directly.

    redis-py is the dep for the default reference backend (PRV_Valkey);
    future Memcached / DragonflyDB providers add their own client deps
    when they land."""
    names = {d.name for d in EXT_DatabaseMemory.dependencies}
    assert "redis" in names


def test_get_root_instance_returns_concrete_provider():
    """Item 19 + Item 98: the default root provider is PRV_Valkey
    (the reference Valkey/Redis-protocol implementation)."""
    assert EXT_DatabaseMemory.get_root_instance() is PRV_Valkey


def test_prv_valkey_subclasses_database_memory_abstract():
    """Item 98: PRV_Valkey is a concrete provider under the
    AbstractDatabaseMemoryProvider family (not the deprecated
    AbstractValkeyProvider)."""
    assert issubclass(PRV_Valkey, AbstractDatabaseMemoryProvider)


def test_fake_subclasses_database_memory_abstract():
    """Item 98: the protocol-agnostic fake lives at the family level."""
    assert issubclass(PRV_Fake_DatabaseMemory, AbstractDatabaseMemoryProvider)


# ---------------------------------------------------------------------------
# PRV_Valkey URL resolution
# ---------------------------------------------------------------------------


def test_resolve_url_prefers_instance_api_key():
    instance = _FakeInstance(api_key="redis://specific-host:6380/2")
    assert PRV_Valkey._resolve_url(instance) == "redis://specific-host:6380/2"


def test_resolve_url_falls_back_to_database_memory_env(monkeypatch):
    """Item 98: the canonical env name is DATABASE_MEMORY_URL (matching
    the protocol-family extension name)."""
    monkeypatch.delenv("VALKEY_URL", raising=False)
    monkeypatch.setenv("DATABASE_MEMORY_URL", "redis://from-env-canonical:6379/0")
    instance = _FakeInstance(api_key=None)
    assert PRV_Valkey._resolve_url(instance) == "redis://from-env-canonical:6379/0"


def test_resolve_url_legacy_valkey_env_still_honored(monkeypatch):
    """Item 98: deployments that pre-dated the rename keep working —
    the legacy VALKEY_URL env var is consulted when the canonical
    DATABASE_MEMORY_URL is not set."""
    monkeypatch.delenv("DATABASE_MEMORY_URL", raising=False)
    monkeypatch.setenv("VALKEY_URL", "redis://legacy-env:6379/0")
    instance = _FakeInstance(api_key=None)
    assert PRV_Valkey._resolve_url(instance) == "redis://legacy-env:6379/0"


def test_resolve_url_canonical_wins_over_legacy(monkeypatch):
    """Item 98: when both env vars are set, the canonical name wins."""
    monkeypatch.setenv("DATABASE_MEMORY_URL", "redis://canonical:6379/0")
    monkeypatch.setenv("VALKEY_URL", "redis://legacy:6379/0")
    instance = _FakeInstance(api_key=None)
    assert PRV_Valkey._resolve_url(instance) == "redis://canonical:6379/0"


def test_resolve_url_default_when_unset(monkeypatch):
    monkeypatch.delenv("DATABASE_MEMORY_URL", raising=False)
    monkeypatch.delenv("VALKEY_URL", raising=False)
    instance = _FakeInstance(api_key=None)
    url = PRV_Valkey._resolve_url(instance)
    assert url.startswith("redis://localhost:6379")


def test_prv_valkey_connect_raises_clear_error_when_redis_missing(monkeypatch):
    """When `redis-py` is not on the path, `PRV_Valkey.connect` raises
    a RuntimeError pointing at the dependency manager. The framework's
    extension loader installs missing pip deps before the provider is
    exercised at runtime; this guards against import-time crashes."""
    original = sys.modules.get("redis")
    original_async = sys.modules.get("redis.asyncio")
    sys.modules["redis"] = None  # type: ignore[assignment]
    sys.modules["redis.asyncio"] = None  # type: ignore[assignment]

    instance = _FakeInstance(api_key="redis://no-host/0")
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
    transport = PRV_Fake_DatabaseMemory.build_streams_transport(instance)
    assert isinstance(transport, _FakeDatabaseMemoryStreamsTransport)

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
    transport = PRV_Fake_DatabaseMemory.build_streams_transport(instance)

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
    so tests verify the right behavior without standing up a backend."""
    instance = _FakeInstance()
    transport = PRV_Fake_DatabaseMemory.build_streams_transport(instance)

    async def bad(payload: bytes) -> None:
        raise RuntimeError("subscriber blew up")

    await transport.subscribe("topic", bad)
    await transport.send("topic", b"payload")
    await transport.close()


@pytest.mark.asyncio
async def test_fake_transport_close_blocks_further_send():
    instance = _FakeInstance()
    transport = PRV_Fake_DatabaseMemory.build_streams_transport(instance)
    await transport.close()
    with pytest.raises(RuntimeError, match="closed"):
        await transport.send("topic", b"x")


@pytest.mark.asyncio
async def test_fake_transport_close_blocks_further_subscribe():
    instance = _FakeInstance()
    transport = PRV_Fake_DatabaseMemory.build_streams_transport(instance)
    await transport.close()

    async def h(p: bytes) -> None:
        pass

    with pytest.raises(RuntimeError, match="closed"):
        await transport.subscribe("topic", h)


# ---------------------------------------------------------------------------
# End-to-end: RedisStreamsEventBus consuming the DatabaseMemory transport
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_bus_consumes_database_memory_transport_end_to_end():
    """`RedisStreamsEventBus(transport=PRV_Fake_DatabaseMemory.build_streams_transport(...))`
    publishes through the fake transport and dispatches into the
    subscribed handler. Verifies the EventBus duck-types correctly
    against the extension-supplied transport without any redis SDK
    on the framework's path."""
    instance = _FakeInstance()
    transport = PRV_Fake_DatabaseMemory.build_streams_transport(instance)
    bus = RedisStreamsEventBus(transport=transport)

    received: list = []

    async def handler(event: _UserSignedUp) -> None:
        received.append(event.user_id)

    bus.subscribe(_UserSignedUp, handler)
    await asyncio.sleep(0)
    await bus.publish(_UserSignedUp(user_id="u-42", email="x@x"))

    assert received == ["u-42"]
    await bus.close()


def test_streams_transport_conforms_to_broker_transport_contract():
    """Structural typing: the extension-supplied transport exposes the
    same surface as `BrokerTransport`. No nominal subclass relationship
    is required (the EventBus duck-types), but every method must exist."""
    instance = _FakeInstance()
    transport = PRV_Fake_DatabaseMemory.build_streams_transport(instance)
    for method in ("send", "subscribe", "close"):
        assert hasattr(transport, method)
        assert callable(getattr(transport, method))


# ---------------------------------------------------------------------------
# Item 98 acceptance: the old valkey package is gone
# ---------------------------------------------------------------------------


def test_old_valkey_extension_directory_removed():
    """Item 98 acceptance criterion: a fresh checkout has no
    extensions/valkey/ directory. The framework has not yet shipped
    to PyPI (Item 86) so no compat shim is required."""
    import pathlib

    from zephyrex.extensions.database_memory import EXT_DatabaseMemory as _self

    extensions_dir = pathlib.Path(_self.__file__).parent.parent
    assert not (extensions_dir / "valkey").exists(), (
        "extensions/valkey/ should be removed by the Item 98 restructure"
    )


def test_old_valkey_imports_no_longer_resolvable():
    """Item 98 acceptance criterion: the old import paths are gone.
    No back-compat shim is needed because the framework has not shipped
    to PyPI yet."""
    import importlib

    with pytest.raises(ImportError):
        importlib.import_module("zephyrex.extensions.valkey.EXT_Valkey")


# ---------------------------------------------------------------------------
# Sandbox-credential gated tests against a real Valkey (Item 15)
# ---------------------------------------------------------------------------


@pytest.mark.external_api
@pytest.mark.skipif(
    not (os.environ.get("DATABASE_MEMORY_URL") or os.environ.get("VALKEY_URL")),
    reason="Requires DATABASE_MEMORY_URL (or legacy VALKEY_URL) pointing at a sandbox instance",
)
def test_real_prv_valkey_against_sandbox():
    """End-to-end against a real Valkey/Redis instance. Auto-xfailed in
    branches without the credential per Item 15."""
    url = os.environ.get("DATABASE_MEMORY_URL") or os.environ["VALKEY_URL"]
    instance = _FakeInstance(api_key=url)
    PRV_Valkey._connections.clear()
    client = PRV_Valkey.connect(instance)
    assert client is not None
    PRV_Valkey._connections.clear()
