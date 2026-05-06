"""Valkey extension — FOSS in-memory data structure store with API parity to Redis.

Owns the framework's connection management for any Valkey-protocol-compatible
backend: Valkey itself (the FOSS continuation of Redis), real Redis (still
protocol-compatible), or any drop-in replacement that speaks the wire protocol.

This extension is the canonical home for Valkey-protocol clients in the
framework. Other layers that need Valkey — the cross-process EventBus
streams transport, the multi-process inbound rate-limit counter, future
caches, future distributed counters — consume this extension's bonded
provider rather than importing the redis SDK directly. Same pattern the
``database`` extension uses for SQL backends: a root provider for system
functionality, with the door open for per-team/per-user instances.

Provider abilities:
    - ``key_value``: GET/SET/DEL/EXPIRE
    - ``streams``: XADD/XREADGROUP/XACK (consumer groups)
    - ``pubsub``: PUBLISH/SUBSCRIBE
    - ``counter``: INCRBY/DECRBY (atomic)

The shared ``BrokerTransport`` for the EventBus's Redis-Streams-shape
adapter is built via :meth:`AbstractValkeyProvider.build_streams_transport`.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Optional, Set, Type

from serverframework.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    AbstractStaticProvider,
)
from serverframework.lib.Dependencies import Dependencies, PIP_Dependency
from serverframework.lib.Logging import logger


class AbstractValkeyProvider(AbstractStaticProvider):
    """Abstract base class for Valkey-protocol-compatible service providers.

    Concrete providers (``PRV_Valkey``, plus any future provider that
    speaks the same protocol) inherit from this and implement the
    ``connect()`` / ``build_streams_transport()`` contract.

    Per Item 19 the framework's system-internal callers reach this provider
    via ``EXT_Valkey.root`` (a root-scoped provider instance); user-context
    callers can attach team-scoped or user-scoped instances when an
    application needs per-tenant Valkey databases (e.g., per-team session
    stores). The default deployment uses one root instance for all
    framework-internal usage.
    """

    extension: ClassVar[Optional[Type[AbstractStaticExtension]]] = None

    name: str = ""
    friendly_name: str = ""
    description: str = ""

    _abilities: Set[str] = {
        "key_value",
        "streams",
        "pubsub",
        "counter",
    }

    _env: Dict[str, Any] = {
        "VALKEY_URL": "redis://localhost:6379/0",
        "VALKEY_USERNAME": "",
        "VALKEY_PASSWORD": "",
        "VALKEY_TLS": "false",
    }

    @classmethod
    def connect(cls, instance: Any) -> Any:
        """Return a Valkey-protocol async client for ``instance``.

        Subclasses override to wire the client of their choice. The
        default implementation lazily imports ``redis.asyncio`` (the
        canonical Python client for the Valkey wire protocol) and
        returns a connection pool keyed by URL.

        ``instance`` is a ``ProviderInstanceModel`` whose ``api_key``
        field carries the URL when present; otherwise ``VALKEY_URL``
        is read from the environment.
        """
        raise NotImplementedError

    @classmethod
    def build_streams_transport(cls, instance: Any, consumer_group: str = "serverframework") -> Any:
        """Return a ``BrokerTransport`` over Valkey Streams for the EventBus.

        Subclasses construct their backend-specific transport. The
        framework's EventBus consumes whatever shape this returns via
        the ``BrokerTransport`` ABC contract (``send`` / ``subscribe`` /
        ``close``). See ``logic/EventBus.py``.
        """
        raise NotImplementedError


class EXT_Valkey(AbstractStaticExtension):
    """Valkey extension — FOSS Redis-protocol client management.

    Exposes Valkey/Redis-protocol abilities to the framework and to other
    extensions that need a key-value store, streams-based broker, or
    distributed counter. The extension owns connection-pool lifecycle so
    every consumer (EventBus, rate-limiter, cache) shares one set of
    connections per provider instance.

    Naming note: the extension is named "valkey" (the FOSS continuation
    of Redis after Redis Inc. relicensed under BSL/SSPL) per the
    framework's "FOSS api-parity naming" policy. The wire protocol is
    identical to Redis, so deployments using Redis Inc.'s commercial
    distribution work with this extension unmodified.
    """

    name: str = "valkey"
    friendly_name: str = "Valkey (Redis-protocol)"
    version: str = "1.0.0"
    description: str = (
        "Valkey extension — FOSS Redis-protocol client. Owns connection "
        "management for the framework's key-value, streams, pubsub, and "
        "counter abilities; consumed by the EventBus, the inbound rate "
        "limiter, and any application-level cache."
    )

    _providers: List[Type] = []

    _env: Dict[str, Any] = {
        "VALKEY_URL": "redis://localhost:6379/0",
        "VALKEY_USERNAME": "",
        "VALKEY_PASSWORD": "",
        "VALKEY_TLS": "false",
    }

    _abilities: Set[str] = {
        "key_value",
        "streams",
        "pubsub",
        "counter",
        "broker_transport",
    }

    dependencies = Dependencies(
        [
            PIP_Dependency(
                name="redis",
                friendly_name="redis-py (Valkey/Redis client)",
                optional=False,
                semver=">=4.2.0",
                reason=(
                    "Canonical Python client for the Valkey/Redis wire "
                    "protocol. >=4.2 ships the asyncio submodule used "
                    "by the EventBus streams transport."
                ),
            )
        ]
    )

    @classmethod
    def get_root_instance(cls) -> Any:
        """Return the root-scoped Valkey provider instance for system use.

        Per Item 19, root-scoped instances are framework-internal only —
        users cannot reach them via the user-context resolver. This is
        the entry point the EventBus and inbound rate limiter call to
        bond a connection.
        """
        from serverframework.extensions.valkey.PRV_Valkey import PRV_Valkey

        return PRV_Valkey
