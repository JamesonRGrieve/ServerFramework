# DatabaseMemory Extension

This document describes the **DatabaseMemory** extension implementation —
the framework's protocol-family extension for in-memory data stores.

> **Extension Architecture**: For general extension patterns, architecture, and concepts, see [EXT.Patterns.md](../EXT.Patterns.md).

## Naming

The extension is named **database_memory** (lowercase, snake-case) to
mirror the existing `database` extension's SQL-protocol-family naming.

This is a protocol-family name, not a product name. Concrete in-memory
backends ship as sibling providers under this extension:

| Provider                       | Wire protocol family    | Status      |
|--------------------------------|-------------------------|-------------|
| `PRV_Valkey`                   | Valkey/Redis            | Default reference implementation |
| `PRV_Fake_DatabaseMemory`      | (in-process)            | Test seam   |
| `PRV_Memcached` *(future)*     | Memcached               | Open slot   |
| `PRV_DragonflyDB` *(future)*   | Valkey/Redis            | Open slot (alternate Redis-protocol implementation) |
| `PRV_KeyDB` *(future)*         | Valkey/Redis            | Open slot   |
| `PRV_Garnet` *(future)*        | Valkey/Redis (subset)   | Open slot   |

A future provider (e.g., `PRV_Memcached`) lands as a sibling to
`PRV_Valkey` without any change to `EXT_DatabaseMemory` itself or to any
other framework code. Item 23/26 contracts are unchanged by the
restructure.

## Why an extension?

This is the framework's **canonical home for in-memory-store clients**.
Any layer that needs an in-memory store — the cross-process EventBus
streams transport (Item 42), the multi-process inbound rate-limit
counter (Item 71), the production `DistributedCounter` Redis backend
(Item 69), future application caches — consumes this extension's
bonded provider rather than importing a backend SDK directly. This is
the same pattern the `database` extension uses: one extension owns
the connection-pool lifecycle, and every consumer shares one set of
connections per provider instance.

Consequences:

- The framework's core (`logic/EventBus.py`, `lib/InboundSecurity.py`)
  does not import `redis`, `pymemcache`, or any other backend SDK.
  Deployments without this extension installed still work; they fall
  back to in-memory implementations.
- Multiple framework subsystems share one connection pool per provider
  instance.
- Per-team and per-user instances are supported (Item 19's scope model).
  Most deployments use a single root-scoped instance for system
  functionality; the door is open for per-tenant in-memory databases
  when an application needs them.

## Architecture

### Extension class

```python
class EXT_DatabaseMemory(AbstractStaticExtension):
    name = "database_memory"
    version = "1.0.0"
    description = "In-memory data store family — connection management for key_value, streams, pubsub, counter abilities"

    _abilities = {
        "key_value",        # GET/SET/DEL/EXPIRE
        "streams",          # XADD/XREADGROUP/XACK
        "pubsub",           # PUBLISH/SUBSCRIBE
        "counter",          # INCRBY/DECRBY (atomic)
        "broker_transport", # BrokerTransport for the EventBus
    }

    _env = {
        "DATABASE_MEMORY_URL":      "redis://localhost:6379/0",
        "DATABASE_MEMORY_USERNAME": "",
        "DATABASE_MEMORY_PASSWORD": "",
        "DATABASE_MEMORY_TLS":      "false",
    }
```

### Abstract provider class

```python
class AbstractDatabaseMemoryProvider(AbstractStaticProvider):
    _abilities = {"key_value", "streams", "pubsub", "counter"}

    @classmethod
    def connect(cls, instance) -> Any: ...

    @classmethod
    def build_streams_transport(cls, instance, consumer_group="serverframework") -> Any: ...
```

A backend that does not implement every ability declares which
abilities it supports via the existing `Capability` flag mechanism
(Item 95's pattern). Memcached, for example, exposes `key_value` and
`counter` but raises `NotImplementedError` from
`build_streams_transport` because the protocol has no streams primitive.

### Provider abilities

Every provider declares the same ability vocabulary so the framework
can pick a backend by ability without hard-coding the provider name:

- **`key_value`** — `client.set(k, v)` / `client.get(k)` / `client.delete(k)` / `client.expire(k, seconds)`
- **`streams`** — `transport.send(topic, payload)` / `transport.subscribe(topic, handler)` / `transport.close()` via `provider.build_streams_transport(instance)`
- **`pubsub`** — `client.publish(channel, payload)` / `client.subscribe(channel)` (consumed only by application code; the EventBus uses streams instead for at-least-once delivery)
- **`counter`** — `client.incrby(k, n)` / `client.decrby(k, n)` (used by Item 71's multi-process rate limiter and by Item 69's `DistributedCounter` backend)

## Streams as the EventBus transport

`AbstractDatabaseMemoryProvider.build_streams_transport(instance, consumer_group="serverframework")` returns a `BrokerTransport`-shaped object the framework's EventBus consumes. The transport conforms to the `serverframework.logic.EventBus.BrokerTransport` ABC structurally:

```python
class BrokerTransport(ABC):
    async def send(self, topic: str, payload: bytes) -> None: ...
    async def subscribe(self, topic, handler) -> None: ...
    async def close(self) -> None: ...
```

For the Valkey/Redis-protocol backend (`PRV_Valkey`), each EventBus
topic maps to a Valkey stream key. A consumer group on the topic
provides at-least-once delivery across consumer replicas. The producer
side uses `XADD`; the consumer side uses `XREADGROUP` in a loop with
explicit `XACK` after handler success.

To wire the EventBus to the in-memory store:

```python
from serverframework.extensions.database_memory.EXT_DatabaseMemory import EXT_DatabaseMemory
from serverframework.logic.EventBus import RedisStreamsEventBus, set_event_bus

# Bond a root provider instance (Item 19); for system use only.
provider = EXT_DatabaseMemory.get_root_instance()  # PRV_Valkey by default
instance = bond_root_database_memory_instance()  # constructs a ProviderInstanceModel
transport = provider.build_streams_transport(instance, consumer_group="serverframework")

set_event_bus(RedisStreamsEventBus(transport=transport, dlq_topic="events.dlq"))
```

The EventBus class name retains "RedisStreams" because that is the
wire-protocol feature it consumes; the *extension* name changed to
`database_memory` per Item 98 to reflect that the family of in-memory
backends is broader than any one product.

## Tests

`PRV_Fake_DatabaseMemory` is the test seam. Every test that exercises
an EventBus pubsub path uses the fake transport — the EventBus's
behavior under publish/subscribe is identical between the fake and any
real provider because both implement the same `BrokerTransport` contract.

Tests against the real `PRV_Valkey` are gated behind the `external_api`
pytest marker (per Item 15) and run only when a `DATABASE_MEMORY_URL`
(or legacy `VALKEY_URL`) pointing at a sandbox instance is configured.

## Cross-references

- **Item 19** — provider scope (root for system, team/user for per-tenant).
- **Item 23** — collision detection (the extension-namespace rename is observed by the registry).
- **Item 24** — migration ownership (file-path detection picks up the renamed directory; this extension currently owns no tables).
- **Item 26** — `AbstractProviderInstance` contract (`AbstractDatabaseMemoryProvider` retains the abstract-provider shape).
- **Item 42** — EventBus consumes the streams transport from this extension.
- **Item 43** — `PRV_Abstract_Cache` complements this for higher-level cache APIs; a future cache provider can build atop this extension's `key_value` ability.
- **Item 69** — `DistributedCounter`'s production Redis backend can build atop this extension's `counter` ability.
- **Item 71** — the inbound rate limiter's multi-process counter backend wires through this extension when production deployments need cross-worker consistency.
- **Item 98** — this restructure (Valkey-as-extension → Valkey-as-provider-under-DatabaseMemory).
