"""DatabaseMemory extension — in-memory data store family (Valkey, Redis, Memcached, KeyDB, DragonflyDB, Garnet).

Owns the framework's connection management for **any in-memory data store
backend**, regardless of wire protocol. The extension is named for the
*protocol family* ("database_memory") not for any specific concrete
product, so that Memcached, Valkey, KeyDB, DragonflyDB, and Garnet land
as siblings under one extension rather than each forking the namespace.

This restores parity with the existing ``database`` extension shape:
``EXT_Database`` is the SQL protocol family, with ``PRV_SQLite``,
``PRV_InfluxDB``, etc. as concrete providers underneath. ``EXT_DatabaseMemory``
follows the same rule for in-memory stores.

Provider abilities (the canonical contract — concrete providers may
declare a subset via the ``Capability`` flag mechanism if their backend
does not implement every ability):

    - ``key_value``: GET/SET/DEL/EXPIRE
    - ``streams``: XADD/XREADGROUP/XACK (consumer groups)
    - ``pubsub``: PUBLISH/SUBSCRIBE
    - ``counter``: INCRBY/DECRBY (atomic)

Other framework layers — the cross-process EventBus streams transport
(Item 42), the multi-process inbound rate-limit counter (Item 71), the
``DistributedCounter`` Redis backend (Item 69) — consume this extension's
bonded provider instead of importing a backend-specific SDK directly.
The ``BrokerTransport`` for the EventBus's streams-shape adapter is
built via :meth:`AbstractDatabaseMemoryProvider.build_streams_transport`.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Optional, Set, Type

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    AbstractStaticProvider,
)
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency


class AbstractDatabaseMemoryProvider(AbstractStaticProvider):
    """Abstract base class for in-memory data store providers.

    Concrete providers (``PRV_Valkey``, the future ``PRV_Memcached``,
    ``PRV_DragonflyDB``, ``PRV_KeyDB``, ``PRV_Garnet``, etc.) inherit
    from this and implement the ``connect()`` / ``build_streams_transport()``
    contract.

    Per Item 19 the framework's system-internal callers reach this
    provider via ``EXT_DatabaseMemory.get_root_instance()`` (a
    root-scoped provider instance); user-context callers can attach
    team-scoped or user-scoped instances when an application needs
    per-tenant in-memory databases (e.g., per-team session stores).
    The default deployment uses one root instance for all
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
        "DATABASE_MEMORY_URL": "redis://localhost:6379/0",
        "DATABASE_MEMORY_USERNAME": "",
        "DATABASE_MEMORY_PASSWORD": "",
        "DATABASE_MEMORY_TLS": "false",
    }

    @classmethod
    def connect(cls, instance: Any) -> Any:
        """Return a connected async client for ``instance``.

        Subclasses override to wire the client of their choice. ``instance``
        is a ``ProviderInstanceModel`` whose ``api_key`` field carries the
        connection URL when present; otherwise the deployment-level env
        var (``DATABASE_MEMORY_URL`` for protocol-family default, or a
        provider-specific override) is consulted.
        """
        raise NotImplementedError

    @classmethod
    def build_streams_transport(
        cls, instance: Any, consumer_group: str = "zephyrex"
    ) -> Any:
        """Return a ``BrokerTransport`` over the backend's streams primitive.

        Subclasses construct their backend-specific transport. The
        framework's EventBus consumes whatever shape this returns via
        the ``BrokerTransport`` ABC contract (``send`` / ``subscribe`` /
        ``close``). See ``logic/EventBus.py``. Backends that lack a
        streams primitive (e.g., classic Memcached) raise
        ``NotImplementedError`` from this method; the EventBus then
        cannot bind to that backend, but the rest of the abilities
        (``key_value``, ``counter``) remain usable.
        """
        raise NotImplementedError


class EXT_DatabaseMemory(AbstractStaticExtension):
    """DatabaseMemory extension — in-memory data store family.

    Exposes in-memory-store abilities to the framework and to other
    extensions. Owns connection-pool lifecycle so every consumer
    (EventBus, rate-limiter, cache) shares one set of connections per
    provider instance.

    Naming policy: the extension is named "database_memory" — the
    protocol family — to mirror the existing ``database`` extension's
    SQL-family naming. The concrete in-memory backends (Valkey, Redis,
    Memcached, KeyDB, DragonflyDB, Garnet) live underneath as siblings.
    """

    name: str = "database_memory"
    friendly_name: str = "Database (In-Memory)"
    version: str = "1.0.0"
    description: str = (
        "DatabaseMemory extension — in-memory data store family. Owns "
        "connection management for the framework's key-value, streams, "
        "pubsub, and counter abilities; consumed by the EventBus, the "
        "inbound rate limiter, and any application-level cache. "
        "Concrete backends ship as sibling providers (PRV_Valkey, the "
        "default reference implementation; PRV_Memcached, PRV_DragonflyDB, "
        "PRV_KeyDB, PRV_Garnet as future additions)."
    )

    _providers: List[Type] = []

    _env: Dict[str, Any] = {
        "DATABASE_MEMORY_URL": "redis://localhost:6379/0",
        "DATABASE_MEMORY_USERNAME": "",
        "DATABASE_MEMORY_PASSWORD": "",
        "DATABASE_MEMORY_TLS": "false",
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
                    "protocol — the default concrete backend (PRV_Valkey). "
                    ">=4.2 ships the asyncio submodule used by the EventBus "
                    "streams transport. Memcached / DragonflyDB / KeyDB "
                    "providers declare their own client deps when they land."
                ),
            )
        ]
    )

    @classmethod
    def get_root_instance(cls) -> Any:
        """Return the root-scoped in-memory-store provider for system use.

        Per Item 19, root-scoped instances are framework-internal only —
        users cannot reach them via the user-context resolver. This is
        the entry point the EventBus and inbound rate limiter call to
        bond a connection. The default returns ``PRV_Valkey``; deployments
        that prefer a different in-memory backend override this method
        in a subclass or rebind the registry entry.
        """
        from zephyrex.extensions.database_memory.PRV_Valkey import PRV_Valkey

        return PRV_Valkey
