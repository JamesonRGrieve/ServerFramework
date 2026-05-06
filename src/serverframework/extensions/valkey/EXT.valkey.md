# Valkey Extension

This document describes the **Valkey** extension implementation.

> **Extension Architecture**: For general extension patterns, architecture, and concepts, see [EXT.Patterns.md](../EXT.Patterns.md).

## Naming

The extension is named **valkey** (lowercase) per the framework's "FOSS api-parity naming" policy. **Valkey** is the Linux Foundation–stewarded continuation of Redis after Redis Inc. relicensed under BSL/SSPL in 2024. The wire protocol is identical to Redis, so deployments using Redis OSS (≤ 7.2), Redis Inc.'s commercial distribution, or any other Valkey-protocol-compatible backend (KeyDB, DragonflyDB) work with this extension unmodified.

The Python client used is `redis-py` — the canonical implementation of the Valkey/Redis wire protocol. The package name on PyPI is still `redis` (a 4.2+ release adds the `redis.asyncio` submodule that the EventBus streams transport uses).

## Why an extension?

This is the framework's **canonical home for Valkey-protocol clients**. Any layer that needs Valkey — the cross-process EventBus streams transport, the multi-process inbound rate-limit counter, future application caches, future distributed counters — consumes this extension's bonded provider rather than importing the redis SDK directly. This is the same pattern the `database` extension uses: one extension owns the connection-pool lifecycle, and every consumer shares one set of connections per provider instance.

Consequences:

- The framework's core (`lib/EventBus.py`, `lib/InboundSecurity.py`) does not import `redis`. Deployments without the Valkey extension installed still work; they fall back to in-memory implementations.
- Multiple framework subsystems share one Valkey connection pool per provider instance.
- Per-team and per-user Valkey instances are supported (Item 19's scope model). Most deployments use a single root-scoped instance for system functionality; the door is open for per-tenant Valkey databases when an application needs them.

## Architecture

### Extension class

```python
class EXT_Valkey(AbstractStaticExtension):
    name = "valkey"
    version = "1.0.0"
    description = "Valkey/Redis-protocol client management"

    _abilities = {
        "key_value",        # GET/SET/DEL/EXPIRE
        "streams",          # XADD/XREADGROUP/XACK
        "pubsub",           # PUBLISH/SUBSCRIBE
        "counter",          # INCRBY/DECRBY (atomic)
        "broker_transport", # BrokerTransport for the EventBus
    }

    _env = {
        "VALKEY_URL":      "redis://localhost:6379/0",
        "VALKEY_USERNAME": "",
        "VALKEY_PASSWORD": "",
        "VALKEY_TLS":      "false",
    }
```

### Providers

| Provider          | Use case                          | Notes                                                 |
|-------------------|-----------------------------------|-------------------------------------------------------|
| `PRV_Valkey`      | Production                        | Wraps `redis.asyncio.from_url(...)`. Connection pools cached per URL. |
| `PRV_Fake_Valkey` | Tests / offline CI                | In-process; same `BrokerTransport` semantics, no SDK. |

### Provider abilities

Every provider declares the same ability vocabulary so the framework can pick a backend by ability without hard-coding the provider name:

- **`key_value`** — `client.set(k, v)` / `client.get(k)` / `client.delete(k)` / `client.expire(k, seconds)`
- **`streams`** — `transport.send(topic, payload)` / `transport.subscribe(topic, handler)` / `transport.close()` via `provider.build_streams_transport(instance)`
- **`pubsub`** — `client.publish(channel, payload)` / `client.subscribe(channel)` (consumed only by application code; the EventBus uses streams instead for at-least-once delivery)
- **`counter`** — `client.incrby(k, n)` / `client.decrby(k, n)` (used by Item 71's multi-process rate limiter and by Item 69's `DistributedCounter` Redis backend)

## Streams as the EventBus transport

`AbstractValkeyProvider.build_streams_transport(instance, consumer_group="serverframework")` returns a `BrokerTransport`-shaped object the framework's EventBus consumes. The transport conforms to the `serverframework.logic.EventBus.BrokerTransport` ABC structurally:

```python
class BrokerTransport(ABC):
    async def send(self, topic: str, payload: bytes) -> None: ...
    async def subscribe(self, topic, handler) -> None: ...
    async def close(self) -> None: ...
```

Each EventBus topic maps to a Valkey stream key. A consumer group on the topic provides at-least-once delivery across consumer replicas. The producer side uses `XADD`; the consumer side uses `XREADGROUP` in a loop with explicit `XACK` after handler success.

To wire the EventBus to Valkey:

```python
from serverframework.extensions.valkey.EXT_Valkey import EXT_Valkey
from serverframework.logic.EventBus import RedisStreamsEventBus, set_event_bus

# Bond a root provider instance (Item 19); for system use only.
provider = EXT_Valkey.get_root_instance()
instance = bond_root_valkey_instance()  # constructs a ProviderInstanceModel
transport = provider.build_streams_transport(instance, consumer_group="serverframework")

set_event_bus(RedisStreamsEventBus(transport=transport, dlq_topic="events.dlq"))
```

## Tests

`PRV_Fake_Valkey` is the test seam. Every test that exercises an EventBus pubsub path uses the fake transport — the EventBus's behavior under publish/subscribe is identical between the fake and the real provider because both implement the same `BrokerTransport` contract.

Tests against the real `PRV_Valkey` are gated behind the `external_api` pytest marker (per Item 15) and run only when a `VALKEY_URL` pointing at a sandbox Valkey instance is configured.

## Cross-references

- **Item 19** — provider scope (root for system, team/user for per-tenant).
- **Item 42** — EventBus consumes the streams transport from this extension.
- **Item 43** — `PRV_Abstract_Cache` complements this for higher-level cache APIs; a future cache provider can build atop `EXT_Valkey`'s `key_value` ability.
- **Item 69** — `DistributedCounter`'s production Redis backend can build atop `EXT_Valkey`'s `counter` ability.
- **Item 71** — the inbound rate limiter's multi-process counter backend wires through this extension when production deployments need cross-worker consistency.
