# Framework Architecture

This FastAPI-based server framework provides a comprehensive, production-ready foundation for building scalable API applications with a unique Pydantic-first approach that eliminates dual schema maintenance while providing enterprise-grade features.

## Core Architecture

### Layered Design
The framework follows a strict layered architecture with clear separation of concerns:

- **Library Layer (`lib/`)**: Foundation utilities for configuration, dependencies, model management, and logging
- **Database Layer (`DB_*.py`)**: SQLAlchemy models automatically generated from Pydantic schemas with declarative base isolation
- **Business Logic Layer (`BLL_*.py`)**: Pydantic-first schema design with comprehensive CRUD operations and hook support
- **Endpoint Layer (abstracted via `RouterMixin`)**: FastAPI routers are automatically generated from BLL managers that inherit `RouterMixin`. While `EP_*.py` files exist (primarily for endpoint tests), the actual endpoint logic is abstracted into the BLL layer. Managers configure their endpoints through class variables like `prefix`, `tags`, and `auth_type`.
- **Extension System (`EXT_*.py`)**: Modular plugin architecture with auto-discovery and isolated migrations
- **Provider System (`PRV_*.py`)**: External API integration with failover support and rotation capabilities

### Revolutionary Patterns
- **Pydantic-First Schema Design**: Business models defined in Pydantic automatically generate SQLAlchemy database models, eliminating dual schema maintenance and ensuring single source of truth
- **RORO Pattern**: All methods follow "Receive Object, Return Object" for consistency and type safety
- **Comprehensive Hook System**: Type-safe before/after method hooks with priority ordering enable cross-cutting concerns
- **Model Registry Pattern**: Isolated model management allowing multiple applications with different model sets
- **Automatic Router Generation**: BLL managers with RouterMixin eliminate manual endpoint creation
- **Extension Isolation**: Each extension maintains independent migrations and configuration

### ModelRegistry Lifecycle

The `ModelRegistry` is the central hub for model management and database access:

1. **Creation**: ModelRegistry is instantiated during app startup in `app.py`
2. **Storage**: Stored on `app.state.model_registry` for global access
3. **Injection**: Injected into endpoint handlers via FastAPI dependencies
4. **Manager Access**: BLL managers receive it as their first constructor parameter (`model_registry`)

```python
# App startup
model_registry = ModelRegistry(app_instance=app, database_manager=db_manager)
app.state.model_registry = model_registry

# Endpoint handler receives via dependency injection
async def endpoint_handler(model_registry = Depends(get_model_registry)):
    manager = UserManager(model_registry, requester_id=user_id)
    return manager.get(id=target_id)

# Manager usage
class UserManager(AbstractBLLManager, RouterMixin):
    def __init__(self, model_registry, requester_id: str, **kwargs):
        # model_registry provides database access via model_registry.DB
        # and model transformations via model_registry.apply(Model)
```

### Common Patterns Across Layers

These patterns are used consistently across Database, Business Logic, and Endpoint layers. See layer-specific pattern documentation for implementation details.

#### CRUD Model Pattern
All entities follow a standard model structure:
- **Base Model**: Core entity fields with full schema
- **Create Model**: Fields required/allowed during creation (subset of base)
- **Update Model**: Fields allowed during updates (subset of base, all optional)
- **Search Model**: Fields available for filtering and search operations

#### Error Handling Pattern
Standard error responses across all layers:
- **400**: Validation errors, malformed requests
- **401**: Authentication required or invalid
- **403**: Insufficient permissions for operation
- **404**: Resource not found
- **409**: Resource conflict (duplicate, constraint violation)
- **500**: Internal server errors

#### Environment Configuration Pattern
Extensions and services use consistent configuration:
- Static `_env` dictionary declares required variables
- Automatic registration with framework's configuration system
- Type-safe access through `env()` helper
- Optional defaults and validation

## Database Layer

### Management
- **DatabaseManager**: Thread-safe connection pooling. SQLite is the production-ready default — the only backend with a passing engine-config test. PostgreSQL/MariaDB/MSSQL/Vector engine-config branches exist and are wired through `init_engine_config`, but the Postgres path requires driver pinning (asyncpg/psycopg) and CI provisioning of a live Postgres before the multi-DB claim is honest. Postgres-specific primitives (Row-Level Security, advisory locks, distributed counters) sit on top of the Postgres path landing first.
- **Declarative Base Isolation**: Each DatabaseManager instance maintains separate declarative base
- **Migration System**: Alembic-based with core and extension-specific migrations, automatic dependency resolution

### Permissions
- **Role-Based Access Control**: Team-scoped role hierarchies with granular resource permissions
- **SQL-Level Filtering**: Permission enforcement at database query level for security
- **Dynamic Authorization**: Context-aware permission validation with inheritance patterns

### Patterns
- **Entity Mixins**: Reusable model components for common patterns (timestamps, soft delete, etc.)
- **Seeding System**: Intelligent seed data with automatic dependency resolution and dynamic discovery
- **Search Architecture**: Flexible search transformation patterns with custom filtering

## Business Logic Layer

### Core Abstractions
- **AbstractBLLManager**: Base class providing standardized CRUD operations with hook support
- **AbstractService**: Background service lifecycle management with database access
- **Model Mixins**: Reusable Pydantic model components for common entity patterns

### Schema Design
- **Pydantic-First**: Business models defined in Pydantic automatically generate SQLAlchemy database models
- **Three-Model Pattern**: Entity (core), Reference (relationships), Network (API schemas) models per entity
- **Automatic Binding**: ModelRegistry system handles Pydantic-SQLAlchemy integration

### Authentication
- **JWT-Based**: Token authentication with root API key for system entities
- **User Management**: Complete user lifecycle with team/role management
- **Invitation System**: Team invitation workflow with role assignment

### Service Layer
- **Background Services**: Long-running services with lifecycle management and error handling
- **Configuration**: Environment-based service configuration with validation
- **Database Integration**: Service-level database access patterns with transaction management

## Endpoint Layer

### REST Patterns
- **AbstractEPRouter**: Automatic CRUD endpoint generation from BLL managers
- **Authentication Types**: Flexible authentication strategies (JWT, API key, optional)
- **Nested Resources**: Support for hierarchical resource relationships
- **Example Generation**: Automatic API documentation examples using field pattern recognition

### GraphQL Integration
- **Automatic Mapping**: Pydantic models automatically converted to GraphQL schemas
- **Dynamic Schema**: Runtime schema generation with type safety
- **Real-Time Subscriptions**: end-to-end subscription delivery depends on the `StreamingService` flavor, the cross-process event bus, and the GraphQL composition contract. Until those land in the deployment, Strawberry subscriptions declared on a `RouterMixin` manager are not wired to deliver events through this server.
- **Unified Queries**: Single endpoint for complex data retrieval patterns

## Extension System

### Architecture
- **Static Classes**: Extensions implemented as static/abstract classes without instantiation
- **Auto-Discovery**: Filesystem-based discovery of extensions and providers
- **Modular Installation**: Extensions can be enabled/disabled via environment variables
- **Independent Migrations**: Each extension maintains its own migration path

### Provider Rotation
- **External API Management**: Unified interface for external service integration
- **Failover Support**: Automatic provider rotation on failure — rotation is a failover mechanism only, not a load distributor.
- **AbstractExternalModel**: Standardized external API integration patterns
- **Configuration Management**: Provider-specific configuration with validation
- **Load balancing is delegated to L7 infrastructure** (HAProxy or equivalent). Round-robin, weighted, latency-based, and percentage-canary routing are explicitly out of scope; the rotation chain implements failover only. The lone carve-out is session stickiness — pinning an LLM conversation or other application-level session to one upstream remains rotation's concern because L7 cannot see the application key.

### Core Extensions
- **auth_mfa**: Multi-factor authentication (TOTP, email, SMS)
- **email**: SendGrid integration with template support
- **payment**: Stripe payment processing with subscription management
- **database**: Multi-database support with natural language querying

### Localization (`src/Localization.py`)
- **Locale-aware metadata**: `docs.<locale>.json` per-locale dictionaries supply translated table comments, column comments, singular/plural nouns, relationship names, and Swagger descriptions for every entity.
- **`@localized_model` decorator**: applies the active locale's metadata to a SQLAlchemy declarative class — derives `__tablename__`, fills `Column.comment`, rewrites `back_populates`, and lets foreign keys target the locale-derived table name.
- **Module-level helpers** (`relationship`, `foreign_key`): drop-in replacements for the SQLAlchemy originals that consult the locale.
- **Singleton** (`Localization()`): process-global locale state. Per-request locale switching is currently out of scope (would live on top of `RequestContext`). See [`lib/LIB.Localization.md`](./lib/LIB.Localization.md) for the architectural summary and public-symbol contract.

## Framework Design Philosophy

### Extension-Only Implementation Pattern
**Core principle:** All custom implementations go in `extensions/` directory only. This enables conflict-free framework updates.

**Benefits:**
- Zero-conflict updates when merging framework changes
- Clean separation between framework and custom code
- Modular, isolated extensions

**Rules:**
- ✅ Create new folders in `extensions/`
- ✅ Use `@extension_model` to extend existing models
- ✅ Register hooks in extensions
- ✅ Extension-specific migrations in `extensions/{name}/migrations/`
- ❌ Avoid modifying `src/lib/`, `src/database/`, `src/logic/`, `src/endpoints/`
- ❌ Avoid modifying core migrations

**Structure:**
```
extensions/my_feature/
├── EXT_MyFeature.py       # Extension definition
├── BLL_MyModel.py         # Business logic + Pydantic models
├── EP_MyEndpoints.py      # API endpoints (or use RouterMixin)
├── PRV_MyProvider.py      # External service provider
└── versions/001_initial.py  # Migrations
```

**Update Strategy:**
1. Fork/branch framework for your implementation
2. Keep all custom code in `extensions/` only
3. Merge framework updates into your fork
4. Result: Zero conflicts if only `extensions/` modified

## Development Principles

### Code Organization
- **Extension-Only Development**: All implementations in `extensions/` directory for conflict-free updates
- **UUID Primary Keys**: Consistent UUID usage across all entities
- **Relative Imports**: All imports relative to `src/` directory
- **Early Error Handling**: Fail fast with FastAPI HTTPExceptions at database layer
- **No Mocking**: Real functionality testing without mocks

### Performance
- **Connection Pooling**: Database connection management for scalability
- **Parallel Testing**: Concurrent test execution with isolation
- **Lazy Loading**: On-demand component loading and initialization
- **Caching Strategies**: Built-in caching for frequently accessed data

### Security
- **Permission Enforcement**: SQL-level permission filtering
- **JWT Security**: Secure token-based authentication
- **Input Validation**: Comprehensive Pydantic validation
- **SQL Injection Prevention**: SQLAlchemy ORM protection

## Library Foundation

### Configuration Management
- **Type-Safe Settings**: Pydantic-based AppSettings with environment variable validation
- **Runtime Registration**: Extensions can register configuration variables dynamically
- **Domain Extraction**: Robust URI/email parsing for multi-format domain handling
- **Inflection Engine**: Consistent naming transformations across the framework

### Dependency System
- **Multi-Platform Support**: System package management across APT, Homebrew, WinGet, Chocolatey, and Snap
- **Python Package Management**: PIP dependency handling with version constraint validation
- **Extension Dependencies**: Automatic loading order resolution with circular dependency detection
- **Requirements.txt Integration**: Validation against existing requirements with conflict detection

### Model Utilities
- **Introspection System**: Comprehensive Pydantic model analysis with relationship discovery
- **Forward Reference Resolution**: String type annotation resolution across modules
- **Schema Generation**: Recursive schema creation with circular reference handling
- **NetworkMixin**: Dynamic REST API model generation from Pydantic models

### Logging Infrastructure
- **Custom Log Levels**: Extended hierarchy including VERBOSE and SQL levels
- **Environment Configuration**: LOG_LEVEL driven output control
- **Structured Output**: Consistent formatting across all framework components
- **Performance Optimization**: Minimal overhead with level-based filtering

## Framework Benefits

### Developer Productivity
- **Minimal Boilerplate**: Automatic generation of database models, API endpoints, and documentation
- **Type Safety**: End-to-end type checking from API to database through Pydantic
- **Declarative Configuration**: Define behavior through class attributes and decorators
- **Comprehensive Testing**: Abstract test classes provide complete coverage patterns

### Scalability & Performance
- **Connection Pooling**: Efficient database connection management
- **Lazy Loading**: On-demand component initialization
- **Parallel Testing**: Concurrent test execution with isolation
- **Caching Strategies**: Built-in caching at multiple levels

### Maintainability
- **Single Source of Truth**: Pydantic models drive entire stack
- **Clear Separation**: Layered architecture with defined responsibilities
- **Extension Isolation**: Plugins can be added/removed without core changes
- **Comprehensive Documentation**: Integrated with code generation

This framework represents a paradigm shift in API development, eliminating repetitive tasks while maintaining flexibility for complex requirements through its innovative Pydantic-first approach and comprehensive automation capabilities.

## Cross-Cutting Concerns

These primitives wrap every layer; layer-specific docs hold the details.

### Distributed tracing and metrics
W3C `traceparent` and `baggage` propagate from inbound request through `RequestContext`, into `RotationManager.rotate`, into the shared outbound HTTP client, and out to the upstream. ASGI middleware reads the `traceparent` header at ingress, sets it on the `_traceparent` contextvar via `set_traceparent`, and resets it after the response. `RotationManager.rotate` opens one span per attempt tagged with provider name and attempt index, so a multi-provider rotation appears as a parent span with N attempt-children. Span naming follows OTel HTTP-client semantic conventions (`http.method`, `http.url.template`, `http.response.status_code`, `peer.service`). The framework emits a fixed metric set: latency histogram per `(provider, ability)`, success/error rate, rotation-attempt counter, per-provider health gauge, idempotency-key cache hit rate. Backends are pluggable — Prometheus, OpenTelemetry, Statsd — with a no-op default. Metric labels are bounded; user IDs and other unbounded values do not appear in tags.

### Deadline budget propagation
`RequestContext.deadline: Optional[datetime]` is set by inbound middleware from the request's `X-Request-Timeout-Ms` header (relative integer milliseconds; gRPC-style relative timeouts avoid clock-skew issues) or from a configurable default. The framework converts to an absolute internal deadline at ingress. Every component that performs I/O — rotation, shared HTTP client, database session, event-bus publish — reads the deadline before scheduling work, computes the remaining budget, and either adjusts its own timeout to fit or raises `DeadlineExceededError` immediately if the budget is exhausted. `DeadlineExceededError` surfaces as HTTP 504 with a typed message identifying the elapsed budget and the layer that ran out. When an operation enrolls in the outbox via `queue_and_retry` degradation, the original deadline is satisfied at that point and the outbox entry receives a fresh deadline driven by the operation's own retry policy.

### Structured application logging
Every log line carries a `correlation_id` populated from `RequestContext.correlation_id` — set by inbound middleware, derived from the `traceparent` header when present, otherwise minted. `correlation_id` propagates across `asyncio.to_thread` and `asyncio.create_task` via a context-var copying helper the framework supplies (`contextvars.copy_context().run(fn)` for `to_thread`, a wrapper around `create_task` that snapshots context). Log levels: `DEBUG` (developer-only), `INFO` (operational milestones), `WARNING` (degraded behavior; dashboard signal), `ERROR` (operator should be alerted; request failed but process continues), `CRITICAL` (process-level failure; framework startup-validation failures). Application-log retention defaults to 30 days, configurable per deployment, independent of audit-log retention. A pluggable `ErrorReporter` ABC accepts `report(exception, context)` — framework-shipped adapters cover Sentry, Rollbar, and a no-op default. Every uncaught exception in a request handler, every `blocking=True` hook failure, every `failed`-state service crash routes through this surface in addition to being logged. No `print()` calls exist in non-test source.

### Inbound surface hardening
Production deployments declare CORS allowed origins via `APP_CORS_ALLOWED_ORIGINS`; the framework refuses to start in `APP_ENV=production` if origins are wildcarded, and the `allow_credentials=True` path is rejected when origins are `*` (RFC compliance). The `@rate_limit("100/min", scope="ip")` decorator on `RouterMixin` methods, custom routes, and webhook endpoints enforces per-(scope) request rate using the distributed-counter primitive. Supported scopes: `ip`, `user`, `tenant`, `(ip, endpoint)`, `(user, endpoint)`. Defaults: auth endpoints 10/min by IP, mutating endpoints 60/min by user, read endpoints 600/min by user. `LockoutPolicy(failures_per_window, window, lockout)` on auth flows triggers a per-(actor, flow) lockout after the configured failures; lockout state lives in a small DB table and is observable via an admin endpoint. A pluggable `AnomalyDetector` ABC accepts `report_failure(actor, flow)` and is the integration point for captcha (hCaptcha, reCAPTCHA), step-up MFA, and SIEM alerting.

### Operational probes and admin surface
`/healthz` returns 200 if the process is up. `/readyz` returns 200 only if the database is reachable, the credential store is reachable, and every Critical-tier provider's `health_check` is `OK` — with a hysteresis window (default 60s) so a flapping provider does not cycle the deployment in and out of the load balancer. Kubernetes liveness probes target `/healthz`; readiness probes target `/readyz`. Every emitted metric has an associated `Alert` declaration tied to a runbook URL: `Alert(metric="provider_silent_drop_total", threshold=0, window="5m", runbook="docs/runbooks/silent-drop.md")`. The framework ships a generated `alerts.yaml` for Prometheus AlertManager. Every typed framework error carries `runbook_url: ClassVar[Optional[str]]` that the structured logger emits. `/admin/dlq` lists dead-letter-queue entries with filtering by extension, ability, error class, and timestamp; bulk-replay and bulk-discard actions are typed and audit-logged. The same surface lists `failed`-state services with the reset action.

### Backup, restore, point-in-time recovery
Each table declares `backup_class: ClassVar[BackupClass]`: `critical` (data loss unacceptable; nightly snapshots plus continuous WAL archiving), `recoverable` (recoverable from upstream federation; nightly snapshots only), `ephemeral` (cache, session, sticky-routing state; excluded from backups). A scheduled `BackupService` runs nightly and drives the underlying engine's snapshot/dump command (`pg_dump` for Postgres) into a configured `BackupTarget` (the object-storage abstract; S3/GCS/local-filesystem). PITR is supported for engines with WAL streaming via a separate continuous-archive job. A monthly automated CI job restores the latest backup into a scratch DB, runs a smoke test, and discards. RTO/RPO targets are declared per deployment and tracked as `backup_age_seconds` and `last_successful_restore_drill_age_seconds` metrics. Outbox entries past their deadline are marked DLQ on restore rather than re-fired; quota counters are restored as-of backup time with the documented over-count window.

### Zero-downtime migrations
Every migration is split into **expand** (add new structure, leave old in place; both old and new versions of the application run cleanly against the post-expand DB) and **contract** (remove old structure only after the new version has fully rolled out and the old version has been retired). Invariants the framework enforces: NOT NULL columns added by `@extension_model` injection must declare a default; column drops are gated by a `removed_in: str` declaration that the migration generator turns into a separate contract migration in a later release; FK additions are split into "add column with FK" (expand) and "set NOT NULL on the FK" (contract). A startup check rejects a migration that violates these invariants.

### PII classification and right-to-erasure
A `pii: PIIClass` annotation on Pydantic fields declares classification: `direct_identifier` (name, email, phone), `pseudonymous` (user_id, session_key — replaceable with a sentinel), `sensitive` (SSN, financial, health), `derived` (model output that may have absorbed PII), `none` (default). The annotation is enumerable, so a per-deployment PII inventory falls out of the model graph. `AbstractStaticExtension.erase_user(user_id)` is an optional method extensions implement to delete or pseudonymize the user's data within their domain; the central `UserManager.erase()` orchestrates the call across every installed extension in dependency order (reverse of migration ordering), is idempotent, and is itself audit-logged via a special `erasure_event` class. Audit-log conflict resolution is field-level: the audit event keeps its outline (timestamp, action, outcome) but PII fields within the event are redacted to a sentinel on erasure, with a per-event-class `pii_redactable: bool` defaulting True so compliance-mandated full-fidelity retention can opt out. The `erasure_event` class is itself `pii_redactable=False` and `retention="forever"`. A symmetric `export_user(user_id) -> dict` hook produces the user's data in a portable shape (JSON or CSV) for data-portability requests.

### Cross-region deployment topology
The supported topology is active-passive multi-region: a primary region runs the framework against a primary DB, secondary regions run read-only replicas for read-heavy workloads, and failover is operator-driven (DNS or load-balancer cutover) with documented RTO/RPO from the backup contract. Active-active is out of scope for the current major version. Active-active would require: cross-region consensus or per-region partitioning for atomic quota decrement, per-region sharding of the outbox, a global session-affinity layer for sticky-session routing, cross-region credential cache invalidation, and globally-replicated counter storage. Mixing active-active with the current framework risks silent over-counting on quota, duplicate sends from the outbox, and routing inconsistency on stickiness — none of which produce immediate errors but all of which produce financial or correctness drift. The residency primitive routes user traffic to the in-jurisdiction region's primary; cross-region writes go to the user's home region.