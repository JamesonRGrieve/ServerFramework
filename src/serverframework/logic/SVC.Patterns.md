# Service Layer Patterns

This document outlines service-specific patterns and conventions.

> **Common Patterns**: For CRUD model patterns, error handling, and configuration patterns shared across layers, see [Framework.md](../Framework.md#common-patterns-across-layers).

## Core Service Architecture

### AbstractService Base Class

The AbstractService provides a standardized foundation for background services with lifecycle management, error handling, and resource management.

```python
class MyService(AbstractService):
    def __init__(
        self,
        requester_id: str,
        db: Optional[Session] = None,
        interval_seconds: int = 60,
        max_failures: int = 3,
        retry_delay_seconds: int = 5,
        service_id: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(
            requester_id=requester_id,
            db=db,
            interval_seconds=interval_seconds,
            max_failures=max_failures,
            retry_delay_seconds=retry_delay_seconds,
            service_id=service_id,
            **kwargs,
        )

    def _configure_service(self, **kwargs) -> None:
        """Override for service-specific initialization"""
        self.custom_setting = kwargs.get("custom_setting", "default")
        self.external_client = SomeExternalClient()
        # DatabaseManager is available as self.db_manager

    async def update(self) -> None:
        """Main service logic - runs periodically"""
        # Implement service-specific logic
        pass

    def cleanup(self) -> None:
        """Override for service-specific cleanup"""
        super().cleanup()
        if hasattr(self, 'external_client'):
            self.external_client.close()
```

## Service Execution Model and Lifecycle Contract

This section pins the contract every service flavor (perpetual, scheduled, queue-consumer, streaming) inherits. Service authors write against these guarantees; the supervisor honors them.

### Execution model: asyncio
- **Services are coroutines** running on the framework's main event loop, or on a dedicated event loop in a worker process for CPU-heavy workloads. The choice is made by the supervisor, not the service.
- **Blocking I/O is forbidden inside a service handler.** Use `asyncio.to_thread(fn, …)` for unavoidable blocking calls. The default `asyncio.to_thread` thread pool is unbounded and a footgun under load — the framework caps it via a configurable thread-pool size (default 32; per-deployment override via `SVC_THREAD_POOL_SIZE`).
- **Cross-thread context.** When using `asyncio.to_thread`, propagate `RequestContext` (deadline budget, `correlation_id`) via `contextvars.copy_context().run(fn)` so logs and deadlines do not silently lose context at the thread boundary.

### Cancellation: cooperative, with documented drain
- **Stop signals.** Calling `service.stop()` sets the cancellation flag and sends `asyncio.CancelledError` into the running coroutine on the next `await` point.
- **Drain period.** After cancellation, the service has a documented drain window (default **30 seconds**, configurable per service via the `drain_timeout_seconds` ClassVar) to finish in-flight work — flush an open transaction, ack a queue message, write the last outbox row.
- **Forced cancellation.** If the service exceeds the drain window, the supervisor force-cancels and emits a logged warning naming the service and the elapsed drain time.
- **Pause / resume.** `pause()` stops scheduling new work but lets in-flight work complete normally. `resume()` re-enables scheduling. Neither raises `CancelledError`.

### Outbox interaction
The `OutboxDrainService` must complete in-flight outbox entries before exiting, or the framework will produce duplicate sends after a restart. Outbox-style services declare a longer drain window (typical 60–120 seconds) to honor this; the default 30-second drain is too aggressive for outbox patterns.

### Hot-reload interaction
- The supervisor attempts a clean stop-and-restart of every running service on a manifest-driven extension install/uninstall.
- Services that cannot survive a reload — for example a stateful streaming consumer with a costly re-handshake — opt out via `reloadable: ClassVar[bool] = False`. They fall back to "process restart only" semantics for their lifecycle: an extension install elsewhere in the codebase logs a warning and leaves the service running on the old code; the operator restarts the process to pick up changes.

### Restart policy
Each service declares its restart policy via a ClassVar:

```python
class MyService(QueueConsumerService):
    restart_policy: ClassVar[RestartPolicy] = RestartPolicy.ON_FAILURE
    backoff_initial_seconds: ClassVar[float] = 1.0
    backoff_max_seconds: ClassVar[float] = 60.0
    backoff_jitter: ClassVar[float] = 0.1
    crash_window_seconds: ClassVar[int] = 300  # 5 min
    crash_threshold: ClassVar[int] = 5
    drain_timeout_seconds: ClassVar[int] = 30
    reloadable: ClassVar[bool] = True
```

| `RestartPolicy` | When                                            |
| --------------- | ----------------------------------------------- |
| `ALWAYS`        | restart on every exit, including clean exits    |
| `ON_FAILURE`    | restart only on non-zero exit / unhandled exception (default for queue / streaming) |
| `NEVER`         | one-shot — never restart                        |

Backoff between restarts is exponential with jitter, capped at `backoff_max_seconds`.

### Restart-storm protection: the `failed` state
A service that has crashed `crash_threshold` times within `crash_window_seconds` is held in the `failed` state and **does not auto-restart**. Restart-storming against a misconfigured upstream is the single most common operational footgun for this kind of supervisor; the framework refuses to participate.

Recovery is a deliberate operator action:
- **Admin endpoint.** `POST /admin/services/{name}/reset` resets the crash counter and re-enables the service. Requires admin authentication.
- **CLI equivalent.** `python -m serverframework services reset <name>`.
- The admin reset emits an audit event (subject to the configured `RetentionPolicy`) so the operator action is traceable.

### Health surface
Every service exposes:

```python
def get_health_status(self) -> Dict[str, Any]:
    return {
        "service_id": self.service_id,
        "state": "running" | "paused" | "draining" | "failed" | "stopped",
        "restart_count": self.restart_count,
        "last_restart_at": self.last_restart_at,
        "last_run_at": self.metrics.get("last_run_time"),
        "drain_remaining_seconds": self.drain_remaining_seconds,  # only when draining
        ...
    }
```

The supervisor aggregates this into the framework's `/healthz` and `/readyz` operational probes.

## Service Lifecycle Patterns

### Initialization Pattern
```python
def _configure_service(self, **kwargs) -> None:
    """Configure service-specific settings during initialization"""
    # Parse service-specific parameters
    self.batch_size = kwargs.get("batch_size", 100)
    self.external_api_key = kwargs.get("api_key")
    
    # Initialize external clients
    if self.external_api_key:
        self.api_client = ExternalAPIClient(self.external_api_key)
    
    # Setup internal state
    self.last_processed_id = None
    self.metrics = {"processed": 0, "errors": 0}
    
    logger.debug(f"Configured {self.__class__.__name__} with batch_size={self.batch_size}")
```

### Main Logic Pattern
```python
async def update(self) -> None:
    """Implement the main service logic"""
    logger.debug(f"{self.__class__.__name__} executing update...")
    
    try:
        # 1. Get work items from database
        items = await self._get_pending_items()
        
        if not items:
            logger.debug("No pending items to process")
            return
        
        # 2. Process items in batches
        for batch in self._batch_items(items, self.batch_size):
            await self._process_batch(batch)
        
        # 3. Update metrics
        self.metrics["processed"] += len(items)
        
        logger.debug(f"Processed {len(items)} items successfully")
        
    except Exception as e:
        self.metrics["errors"] += 1
        logger.error(f"Error in {self.__class__.__name__}: {str(e)}")
        raise  # Re-raise to trigger failure handling
```

### Cleanup Pattern
```python
def cleanup(self) -> None:
    """Cleanup resources when service shuts down"""
    super().cleanup()  # Always call parent cleanup
    
    logger.debug(f"Cleaning up {self.__class__.__name__}")
    
    # Close external connections
    if hasattr(self, 'api_client'):
        self.api_client.close()
    
    # Save final state if needed
    if hasattr(self, 'metrics'):
        logger.debug(f"Final metrics: {self.metrics}")
    
    # Clear references
    self.api_client = None
    self.metrics = None
```

## Database Access Patterns

### Session Management
```python
async def update(self) -> None:
    """Use the db property for database access"""
    # The db property provides an active session via self.db_manager
    items = self.db.query(MyModel).filter(
        MyModel.status == "pending"
    ).all()
    
    for item in items:
        # Process item
        await self._process_item(item)
        
        # Update status in database
        item.status = "completed"
        item.updated_at = datetime.now(timezone.utc)
    
    # Commit changes
    self.db.commit()
```

### Error Handling with Database
```python
async def update(self) -> None:
    """Handle database errors gracefully"""
    try:
        # Start transaction
        items = self.db.query(MyModel).filter(
            MyModel.status == "pending"
        ).limit(self.batch_size).all()
        
        for item in items:
            try:
                await self._process_item(item)
                item.status = "completed"
            except Exception as e:
                # Mark individual item as failed
                item.status = "failed"
                item.error_message = str(e)
                logger.error(f"Failed to process item {item.id}: {e}")
        
        # Commit batch updates
        self.db.commit()
        
    except Exception as e:
        # Rollback on database errors
        self.db.rollback()
        raise
```

## Error Handling Patterns

### Graceful Error Recovery
```python
async def update(self) -> None:
    """Implement graceful error handling"""
    try:
        # Main processing logic
        await self._process_work()
        
    except ConnectionError as e:
        # Handle recoverable errors
        logger.warning(f"Connection error in {self.__class__.__name__}: {e}")
        # Let the framework handle retry
        raise
        
    except ValueError as e:
        # Handle configuration errors (non-recoverable)
        logger.error(f"Configuration error in {self.__class__.__name__}: {e}")
        self.stop()  # Stop service for manual intervention
        
    except Exception as e:
        # Handle unexpected errors
        logger.error(f"Unexpected error in {self.__class__.__name__}: {e}")
        # Re-raise to trigger failure counting
        raise
```

### Custom Failure Handling
```python
def _handle_failure(self, error: Exception) -> bool:
    """Override for custom failure handling"""
    # Call parent failure handling first
    should_retry = super()._handle_failure(error)
    
    # Custom logic based on error type
    if isinstance(error, RateLimitError):
        logger.warning("Rate limit hit, increasing retry delay")
        self.retry_delay_seconds = min(self.retry_delay_seconds * 2, 300)
        return True
    
    if isinstance(error, AuthenticationError):
        logger.error("Authentication failed, stopping service")
        return False  # Don't retry authentication errors
    
    return should_retry
```

## Service Registration Patterns

### Single Service Registration
```python
# Create and register a service
service = MyService(
    requester_id=env("SYSTEM_ID"),
    interval_seconds=30,
    max_failures=5,
    custom_setting="value"
)

ServiceRegistry.register("my_service", service)
service.start()
```

### Bulk Service Management
```python
def initialize_services():
    """Initialize all application services"""
    services = [
        ("data_processor", DataProcessingService(
            requester_id=env("SYSTEM_ID"),
            interval_seconds=60
        )),
        ("email_sender", EmailService(
            requester_id=env("SYSTEM_ID"),
            interval_seconds=30
        )),
        ("cleanup_service", CleanupService(
            requester_id=env("SYSTEM_ID"),
            interval_seconds=3600  # Run hourly
        ))
    ]
    
    for service_id, service in services:
        ServiceRegistry.register(service_id, service)
    
    # Start all services
    ServiceRegistry.start_all()

def shutdown_services():
    """Gracefully shutdown all services"""
    ServiceRegistry.stop_all()
    ServiceRegistry.cleanup_all()
```

## Async Service Loop Patterns

### Manual Service Loop
```python
async def run_service_manually():
    """Run a service loop manually for testing"""
    service = MyService(requester_id=env("SYSTEM_ID"))
    service.start()
    
    try:
        await service.run_service_loop()
    except KeyboardInterrupt:
        logger.debug("Service interrupted by user")
    finally:
        service.cleanup()
```

### Background Task Integration
```python
import asyncio

async def start_background_services():
    """Start services as background tasks"""
    services = [
        MyService(requester_id=env("SYSTEM_ID"), service_id="service_1"),
        AnotherService(requester_id=env("SYSTEM_ID"), service_id="service_2")
    ]
    
    tasks = []
    for service in services:
        service.start()
        task = asyncio.create_task(service.run_service_loop())
        tasks.append(task)
    
    try:
        # Run all services concurrently
        await asyncio.gather(*tasks)
    except Exception as e:
        logger.error(f"Service error: {e}")
    finally:
        # Cleanup all services
        for service in services:
            service.cleanup()
```

## Configuration Patterns

### Environment-Based Configuration
```python
def _configure_service(self, **kwargs) -> None:
    """Configure service from environment variables"""
    self.api_endpoint = env("EXTERNAL_API_ENDPOINT")
    self.api_key = env("EXTERNAL_API_KEY")
    self.batch_size = int(env("PROCESSING_BATCH_SIZE", "100"))
    self.enable_notifications = env("ENABLE_NOTIFICATIONS", "false").lower() == "true"
    
    if not self.api_endpoint or not self.api_key:
        raise ValueError("Missing required API configuration")
```

### Dynamic Configuration
```python
def _configure_service(self, **kwargs) -> None:
    """Configure service with dynamic settings"""
    # Get configuration from database
    config = self.db.query(ServiceConfig).filter(
        ServiceConfig.service_name == self.__class__.__name__
    ).first()
    
    if config:
        self.interval_seconds = config.interval_seconds
        self.batch_size = config.batch_size
        self.enabled_features = config.features.split(",")
    else:
        # Use defaults
        self.batch_size = 100
        self.enabled_features = []
```

## Monitoring and Metrics Patterns

### Basic Metrics Collection
```python
def _configure_service(self, **kwargs) -> None:
    """Initialize metrics tracking"""
    super()._configure_service(**kwargs)
    self.metrics = {
        "items_processed": 0,
        "errors_count": 0,
        "last_run_duration": 0,
        "last_run_time": None
    }

async def update(self) -> None:
    """Track execution metrics"""
    start_time = time.time()
    self.metrics["last_run_time"] = datetime.now(timezone.utc)
    
    try:
        # Main processing logic
        processed_count = await self._process_items()
        self.metrics["items_processed"] += processed_count
        
    except Exception as e:
        self.metrics["errors_count"] += 1
        raise
    finally:
        self.metrics["last_run_duration"] = time.time() - start_time
```

### Health Check Pattern
```python
def get_health_status(self) -> Dict[str, Any]:
    """Return service health information"""
    return {
        "service_id": self.service_id,
        "running": self.running,
        "paused": self.paused,
        "failures": self.failures,
        "max_failures": self.max_failures,
        "last_run": self.metrics.get("last_run_time"),
        "total_processed": self.metrics.get("items_processed", 0),
        "error_count": self.metrics.get("errors_count", 0)
    }

# Global health check for all services
def get_all_service_health():
    """Get health status for all registered services"""
    health_data = {}
    for service_id in ServiceRegistry.list():
        service = ServiceRegistry.get(service_id)
        if hasattr(service, 'get_health_status'):
            health_data[service_id] = service.get_health_status()
    return health_data
```

## Testing Patterns

### Mock Service for Testing
```python
class MockMyService(MyService):
    """Mock version for testing"""
    
    def _configure_service(self, **kwargs) -> None:
        """Override to avoid external dependencies"""
        self.batch_size = kwargs.get("batch_size", 10)
        self.api_client = Mock()  # Mock external client
        self.test_data = kwargs.get("test_data", [])
    
    async def _get_pending_items(self):
        """Return test data instead of database query"""
        return self.test_data
    
    async def _process_item(self, item):
        """Mock processing"""
        await asyncio.sleep(0.01)  # Simulate work
        return f"processed_{item}"

# Test usage
async def test_service():
    service = MockMyService(
        requester_id="test_user",
        interval_seconds=0.1,
        test_data=["item1", "item2", "item3"]
    )
    
    service.start()
    
    # Run one update cycle
    await service.update()
    
    assert service.metrics["items_processed"] == 3
    service.cleanup()
```

## Best Practices

### Service Design
1. **Single Responsibility** - Each service should have one clear purpose
2. **Idempotency** - Services should be safe to run multiple times
3. **Graceful Degradation** - Handle external service failures gracefully
4. **Resource Management** - Always clean up resources in cleanup()
5. **Configuration** - Make services configurable through _configure_service()

### Error Handling
1. **Specific Exceptions** - Catch specific exception types when possible
2. **Logging** - Log errors with appropriate detail levels
3. **Recovery** - Implement appropriate retry and recovery strategies
4. **Monitoring** - Track error rates and patterns

### Performance
1. **Batch Processing** - Process items in batches when possible
2. **Rate Limiting** - Respect external API rate limits
3. **Memory Management** - Avoid memory leaks in long-running services
4. **Database Efficiency** - Use efficient queries and proper indexing

### Monitoring
1. **Metrics** - Track key performance indicators
2. **Health Checks** - Implement service health endpoints
3. **Alerting** - Set up alerts for service failures
4. **Logging** - Use structured logging for better observability 

## Service Flavors

Four service flavors share a common lifecycle (`start`, `stop`, `pause`, `resume`, `health`). All four are discoverable as `SVC_*.py` files; all four can declare `@hook_bll`-style triggers; all four participate in graceful shutdown with documented draining behavior.

- **`PerpetualService`** — perpetual time-based loop with a sleep interval. The default flavor. Useful for thirty-second agentic loops, background reconciliation passes.
- **`ScheduledService`** — cron expression or fixed-interval execution. Useful for periodic syncs, daily reports, billing-cycle resets, retention archival, backup snapshots.
- **`QueueConsumerService`** — pulls from a queue (Redis, SQS, Postgres-as-queue) with backoff, visibility-timeout semantics, and dead-letter handling. Useful for outbox workers, compensating actions, deferred webhook processing.
- **`StreamingService`** — long-lived connection-oriented work, with `ConsumerStreamingService` (websocket subscriber, SSE listener, Kafka consumer) and `ProducerStreamingService` (long-lived outbound stream) sub-flavors. See **Streaming Services (Item 13)** below.

State that must persist across restarts (cron last-run-at, queue cursors, stream subscription tokens) lives in a small per-service state store and is the responsibility of the framework, not the service author.

## Streaming Services (Item 13)

`StreamingService` completes the broadened service contract from Item 28 with three additions on top of the connect/iter/on_message/disconnect skeleton: a typed handler registry that mirrors the inbound webhook contract from Item 5, cursor / subscription-token persistence via an injectable `state_store`, and graceful drain on stop.

### Subclass shape

```python
class StripeEventsStream(ConsumerStreamingService):
    extension_name = "payment"
    provider_name = "stripe"

    async def connect(self):
        return await stripe_client.events.subscribe(starting_after=self.cursor)

    async def iter_messages(self, connection):
        async for raw in connection:
            yield raw

    def classify(self, message):
        return message["type"], message["data"], {"x-id": message["id"]}, message["id"]
```

The base class's default `on_message` invokes `classify(message)` -> `fan_out(...)` which dispatches to the streaming handler registry. Subclasses that need full lifecycle control override `on_message` directly.

### Handler registry

```python
from serverframework.logic.AbstractService import streaming_handler, StreamingMessageContext

@streaming_handler(EXT_Payment, provider="stripe", event="customer.updated")
async def on_customer_updated(ctx: StreamingMessageContext) -> None:
    # ctx.payload, ctx.headers, ctx.event_name, ctx.cursor, ctx.service_id
    ...
```

Lookup is by `(extension_name, provider_name, event_name)`. If no event-specific handler is registered, the wildcard `(extension, provider, None)` handler is invoked. Re-registration overwrites with a warning. Handlers may be sync or async; async handlers are awaited.

The Stripe events firehose and the Stripe webhook deliver the same canonical event into the same downstream chain — `Webhook.WebhookContext` and `StreamingMessageContext` carry equivalent fields, so a downstream `@hook_bll` or business handler does not need to know which transport delivered the event.

### Cursor persistence

`state_store` is an injectable `Callable[[service_id], cursor]` / `Callable[[service_id, value=cursor], None]` (matching the `ScheduledService` shape). On construction the service loads the last-acknowledged cursor; after each successful `fan_out` the cursor is persisted. Subclasses consume `self.cursor` to resume from the last position on reconnect. Failures inside `state_store` log a warning and continue — cursor persistence is durable but not transactional with the upstream stream.

### Cross-process fan-out

When `event_bus` is provided (any object with an `async publish(payload)` method, e.g. an Item 42 `AbstractEventBus`), the canonical payload is published after successful handler dispatch so out-of-process subscribers see the event. The handler registry path is in-process; `event_bus` is the cross-process upgrade.

### Reconnection + graceful drain

Reconnection backoff is exponential with jitter (±25%), capped at `reconnect_max_seconds` (default 60s, initial 1s). On stop:

- `stop()` flips `running=False`; the loop exits its iteration.
- `await stop_and_drain()` waits up to `drain_period_seconds` (default 30s) for the in-flight `on_message` to finish, then cancels it on timeout with a logged warning.
- `disconnect()` runs in the loop's `finally` block regardless of how the loop exits.

The `_drained` event is set when no message is in flight, so `stop_and_drain()` returns immediately when the service is idle.

### Acceptance per Item 13

A `StreamingService` author declares an upstream subscriber, has the framework manage connection lifecycle, reconnect automatically on transient failures, and route received events into the existing handler chain on the corresponding `*Manager` classes — all without writing connection-management or backoff code.

## Execution Model

Services are asyncio coroutines running on the framework's main event loop, or on dedicated event loops in worker processes for CPU-heavy workloads. Cancellation is cooperative — services receive `asyncio.CancelledError` on stop and have a documented drain period (default thirty seconds, configurable per service) to finish in-flight work before the process exits. A service that exceeds the drain period is forcibly cancelled with a logged warning.

Blocking I/O inside service handlers requires `asyncio.to_thread` with a configurable thread-pool size cap (the default `asyncio.to_thread` pool is unbounded and a footgun under load). The drain-period semantics interact with the outbox: the `OutboxDrainService` must complete in-flight outbox entries before exiting, or the framework risks producing duplicate sends after a restart.

The supervisor restart policy is configurable per service: `always` (restart on any exit), `on_failure` (restart only on non-zero exit / unhandled exception), `never` (one-shot). Backoff between restarts is exponential with jitter. A service that has crashed N times within a window is held in a `failed` state with an admin-action requirement — no restart-storming. The framework exposes an admin endpoint (`POST /admin/services/{name}/reset`) and an equivalent CLI command that an operator runs after diagnosing the underlying cause; the service does not auto-recover from `failed` until reset.

## Per-Tenant Fairness

Two complementary mechanisms prevent a noisy tenant from starving the queue:

- **Per-tenant fair queuing.** The queue-consumer service partitions work by tenant key (typically `team_id`) and drains partitions in round-robin or weighted-fair order. A single tenant's backlog is bounded above by its own throughput; a tenant with no submitted jobs is not penalized for another tenant's backlog. The consumer declares a `tenant_key_resolver: Callable[[Job], str]` and the framework's scheduler enforces fairness via Weighted Fair Queuing (virtual-time scheduling) at the worker level — there is no proliferation of database queues.
- **Priority lanes.** Jobs declare a priority class — `high` (transactional, user-blocking; password resets, MFA), `normal` (default; marketing emails, eventually-consistent fan-out), `low` (batch; nightly reconciliation, archival sweeps). Within a tenant's partition, lanes drain in priority order; across tenants, fair-share enforces equal treatment within each lane. A high-priority job from any tenant runs before a low-priority job from any tenant; among same-lane jobs, fairness applies.

Optional preemption (cancelling a low-priority job mid-execution to free a worker for a high-priority job) is offered but disabled by default. Cross-process consumers coordinate via the `DistributedCounter` primitive to avoid one process unfairly draining one tenant. Metrics: `queue_wait_seconds{tenant_id, lane}` histogram.

## Audit Log Retention

Each audit event class declares a retention window via `retention: ClassVar[RetentionPolicy]`. The policy carries a window (`30d`, `1y`, `7y`, `forever`), an archival target (S3, GCS, on-disk, none), and a `legal_hold: Optional[str]` field for operations that put a class on indefinite hold pending a regulatory action.

A scheduled `RetentionService` (a `ScheduledService`) runs nightly: events past their window are first archived to the configured target as compressed, integrity-checked artifacts, then purged from the live audit table. The archival step is non-skippable for events with non-`none` archival targets — the purge step refuses to run if archival did not succeed for any event in the batch. Archives are written in a stable consumer-friendly format (JSONL or Parquet) so a regulator can be handed an artifact that a third-party tool can read without framework knowledge.

The audit subsystem itself emits an audit event for each retention pass: how many events were archived, how many purged, the cryptographic digest of the archived artifact. This is the audit-of-the-audit and is itself subject to its own (typically `forever`) retention policy. Together, archival digests and the retention-pass audit trail give a regulator-defensible chain.

Convenience presets cover GDPR (`1y` archived), HIPAA (`6y` archived), SOX (`7y` archived), short-lived (`30d` no-archive), and forever (indefinite, archived — used for the audit-of-the-audit). Legal hold is a runtime override that prevents purge regardless of retention window; releasing a hold requires a separate audit event and an admin-level operation.