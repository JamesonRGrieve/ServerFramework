# Public Primitives Contract

This document is the canonical enumeration of every primitive an extension author may rely on as stable. Symbols not listed here are internal and may change without notice; importing them is at the consumer's own risk.

The contract is enforced by a CI manifest: a JSON file alongside this document declares every public symbol, and CI fails when (a) a public symbol is added without a manifest entry, or (b) a manifest entry has no corresponding documented contract. The `serverframework.__all__` re-export list and this document must agree at all times.

The typed-signature half is generated from docstrings and type annotations against the committed manifest. The invariant and recommended-usage sections are hand-written and reviewed on every public-API change.

## Document Structure

Each entry below carries:

- **Signature** — the typed Python signature including all keyword arguments, defaults, and return type.
- **Invariants** — properties the framework guarantees at runtime.
- **Author obligations** — properties the extension author must uphold.
- **Recommended usage** — the canonical pattern; deviations are at the author's risk.
- **See also** — cross-references to deeper documentation.

## Provider System

### `AbstractStaticProvider`
Base class for all providers. See `extensions/PRV.Patterns.md` for the architectural summary.

### `AbstractProviderInstance`
The bonded-instance contract. Concrete provider classes declare `_instance: ClassVar[Optional[AbstractProviderInstance]] = None`; `bond_instance` is typed `(cls, ProviderInstanceModel) -> AbstractProviderInstance`. Required methods: `__init__(self, instance: ProviderInstanceModel)`, `validate_credentials() -> bool`, `close()`, plus extension-specific abstract methods on subclasses. See `extensions/PRV.Patterns.md § Provider Cross-Cutting Concerns`.

### `RotationManager`
Failover-only rotation across a single linear chain of provider instances. Constructor: `(model_registry, requester_id, target_id=None, target_team_id=None, parent=None)`. `rotate(operation, *args, **kwargs, routing_hint: Optional[RoutingHint]=None) -> Any` consumes `RotationPolicy` from the calling provider. See `extensions/PRV.Patterns.md`.

### `ProviderInstanceModel`
Persisted credential-and-configuration record for a bonded provider. Carries `api_key: CredentialRef`, `auth_strategy_name: str`, `scope: Literal["root", "system", "team", "user"]`, `region: Optional[ResidencyRegion]`. See `logic/BLL.Patterns.md § Provider Scope and Quota`.

### `RotationPolicy`
Per-provider failure-classification and retry/backoff policy. Carries `transient_max_retries`, `transient_base_ms` / `max_ms` / `jitter`, `rate_limit_base_ms` / `max_ms`, `auth_cooldown_seconds`, `header_parser`. See `extensions/PRV.Patterns.md`.

### `RateLimit`
`RateLimit(rps: int, burst: int)`. Backed by `DistributedCounter`. See `extensions/PRV.Patterns.md`.

### `DegradationPolicy`
`DegradationPolicy(mode: DegradationMode, outbox_retention_days: int, outbox_max_attempts: int)`. Modes: `FAIL_FAST`, `QUEUE_AND_RETRY`, `SILENT_DROP`. See `extensions/PRV.Patterns.md`.

### `CostModel` (Protocol)
`(request, response) -> Decimal`. Built-ins: `ConstantCostModel`, `TokenBasedCostModel`, `PercentOfAmountCostModel`, `FreeCostModel`. See `extensions/PRV.Patterns.md`.

### `RetentionPolicy`
`RetentionPolicy(window: str, archive_to: str, legal_hold: Optional[str])`. Window suffixes: `s`/`m`/`h`/`d`/`w`/`mo`/`y` plus literal `"forever"`. Presets: GDPR, HIPAA, SOX, short-lived, forever. See `logic/SVC.Patterns.md § Audit Log Retention`.

### `BaseExternalError` and subclasses
`TransientExternalError`, `RateLimitExternalError`, `AuthExternalError`, `InvalidInputExternalError`, `PermanentExternalError`. Every `*_via_provider` raises one of these on failure. See `extensions/PRV.Patterns.md` and `extensions/PRV.External.md`.

## Auth Strategies

### `AuthStrategy`
`AuthStrategy.headers_for(requester) -> dict`, `params_for(requester) -> dict`, `body_modifier(requester, body) -> body`, `refresh_if_needed()`. Built-ins: `APIKeyAuth`, `OAuth2Auth`, `AWSSigV4Auth`, `JWTBearerAuth`, `BasicAuth`, `MTLSAuth`. Registered via `AuthStrategyRegistry`. See `extensions/PRV.Patterns.md`.

## External Federation Translators

### `FieldMapping`
Built-ins: `Rename`, `Compose`, `Decompose`, `DotPath`, `UnitConvert`, `EnumRemap`, `TimestampConvert`, `Custom`. See `extensions/PRV.External.md`.

### `AbstractPaginator`
Subclasses: `OffsetPaginator`, `CursorPaginator`, `PageTokenPaginator`, `LinkHeaderPaginator`. See `extensions/PRV.External.md`.

### `AbstractQueryDSLTranslator`
Subclasses: `StripeSearchTranslator`, `SOQLTranslator`, `GraphQLFilterTranslator`, `MongoStyleTranslator`, `KeyValueTranslator`, `IMAPSearchTranslator`. See `extensions/PRV.External.md`.

## Decorators

### `@webhook_handler(EXT, provider, event)`
Registers a static method as a webhook handler. The mounted path is `/webhook/{extension}/{provider}/{event}`. See `extensions/PRV.External.md`.

### `@idempotent`
Decorates mutating `*_via_provider` methods so the rotation system reuses the original idempotency key on retries. See `extensions/PRV.Patterns.md`.

### `@mirror_on_create(local, external, link_field)`, `@mirror_on_update`, `@mirror_on_delete`
Lifecycle decorators producing the local-create + upstream-create + ID-write-back saga via the outbox. See `extensions/PRV.External.md`.

### `@extension_model`
Field injection into core models. See `extensions/EXT.Patterns.md § Field Injection Collision Detection`.

### `@custom_route(method, path, input, output, expose_in)`
Custom REST/GraphQL/SDK route declared on a `RouterMixin` subclass. See `endpoints/EP.Abstraction.md § Custom Routes`.

### `@hook_bll(target, timing, priority, blocking, before, after)`
Hook registration. Default `blocking=True` for BEFORE, `blocking=False` for AFTER. See `logic/BLL.Hooks.md`.

### `@on_event(EventModel)`
Cross-process event-bus subscription handler. See `logic/BLL.Hooks.md § Cross-Process Event Bus`.

### `@read_only`
Marks a BLL method as safe to route to a replica. See `database/DB.Patterns.md § Read-Replica Routing`.

## Hook System

### `HookContext[P, R]`
Generic over the target's `ParamSpec` and return type. `context.kwargs` is typed as the keyword arguments of the target; `context.result: R | None` is typed as the return value. `context.kwarg("user_id")` returns the field's typed value. See `logic/BLL.Hooks.md`.

## Authentication

### `PermissionDef`
`PermissionDef(name, description, implies, sensitive, user_grantable, system_only)`. Canonical permission-name shape `{extension}.{resource}.{action}[:{qualifier}]` doubles as the OAuth scope. `AbstractStaticExtension.get_permissions() -> List[PermissionDef]`. See `logic/BLL.Authentication.md`.

### `OneTimeTokenMixin`
Reusable model mixin: `code_hash`, `code_salt`, `expires_at`, `is_used`, `used_at`, `created_ip`, `verify(submitted_code) -> bool`, `mark_used()`. Backs magic-link, QR-pairing, recovery codes, invitation codes. See `logic/BLL.Authentication.md`.

### `PasswordlessGrantRegistry`
Registry of `(grant_type, validator)` pairs consumed by `UserManager.login_via_grant`. See `logic/BLL.Authentication.md`.

### `CrossDeviceGrant`
Grant kind whose validation requires another authenticated requester to approve. Validator signature `(pairing_request, approver_session) -> UserModel`. See `logic/BLL.Authentication.md`.

## Concurrency Primitives

### `acquire_lock(name, timeout=None) -> AdvisoryLock`
Cross-process serialization. Backends: Postgres `pg_advisory_lock` (default), Redis Redlock. See `lib/LIB.Scalability.md`.

### `DistributedCounter(key, limit, period_key)`
Multi-process atomic counter. Operations: `try_consume(amount) -> bool`, `release(amount)`, `reset(period_key)`. See `lib/LIB.Scalability.md`.

## Service Flavors

### `PerpetualService`, `ScheduledService`, `QueueConsumerService`, `StreamingService`
Four flavors sharing a common lifecycle (`start`, `stop`, `pause`, `resume`, `health`). `StreamingService` has `ConsumerService` and `ProducerService` sub-flavors. See `logic/SVC.Patterns.md`.

## Request Context

### `RequestContext`
Request-scoped state with `correlation_id`, `traceparent`, `deadline: Optional[datetime]`, `read_only: bool`. Set by inbound middleware, consumed by every layer that performs I/O. See `lib/LIB.RequestContext.md`.

### `DeadlineExceededError`
Surfaces as HTTP 504 with the elapsed budget and the layer that ran out. See `lib/LIB.RequestContext.md`.

## Error Reporting

### `ErrorReporter` (ABC)
`report(exception, context)`. Adapters: Sentry, Rollbar, no-op. See `lib/LIB.Logging.md`.

## Multi-Tenancy and Compliance

### `TenantScopedMixin`
`TenantScopedMixin.with_keys("team_id")`, `TenantScopedMixin.with_keys("org_id", "team_id")`. Postgres RLS enforced at the query-compile layer. See `database/DB.Permissions.md`.

### `Quota`
Unified per-user / per-team / per-user-within-team quota table. See `logic/BLL.Patterns.md § Provider Scope and Quota`.

### `pii: PIIClass` field annotation
`direct_identifier`, `pseudonymous`, `sensitive`, `derived`, `none`. Drives right-to-erasure orchestration. See `Framework.md § PII classification and right-to-erasure`.

### `BackupClass`
`critical`, `recoverable`, `ephemeral`. See `database/DB.Management.md § Backup, Restore, Point-in-Time Recovery`.

## SDK

### `ResourceConfig`
Hand-authored handler configuration for non-CRUD resources. Generated handlers from `RouterMixin` are the default; hand-authored handlers are the exception. See `sdk/SDK.Patterns.md`.

## Public API Surface

`serverframework.__all__` is the committed public surface. Anything not listed there is internal. The `serverframework.types` module re-exports the Pydantic models that consumers need to type-hint against (`UserModel.Create`, `SessionModel`, etc.) so type-imports do not pull from internal modules.
