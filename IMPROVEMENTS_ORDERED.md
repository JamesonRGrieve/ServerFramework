# Framework Improvements — Dependency-Ordered & Grouped

This document is a re-arrangement of `IMPROVEMENTS.md`. Every piece of content from the source is preserved; the items have been regrouped by theme and ordered so that dependencies precede dependents, and so that closely-related items sit together. Original item numbers (1–59) are retained for traceability against the source document.

## Executive Summary

This document captures the design gaps identified during the review of the framework's documentation against its stated goal: **extensible into any backend application without touching the core, with minimal supplementary code, while preserving full strong typing, documentation, and test coverage.**

The review found thirty discrete items requiring attention. Seven of those are critical-path: until they are resolved, every provider or extension we write will leak provider-specific compensating code into our codebase and will eventually force us to modify the core to recover. The remainder are tractable in parallel once the critical-path items are landed, and several are already partly implemented and only require either documentation, enforcement, or completion.

A subsequent independent documentation audit surfaced additional gaps that the first thirty items did not cover. They are added below as Items 31–52. Several extend or refine earlier items; cross-references are noted in each entry.

A subsequent gap-spotting pass surfaced infrastructure primitives the framework needs that the first two rounds did not call out, plus the two authentication doors-open items the user explicitly requested. Items 53–57 are the gap items; items 58–59 are the auth extensions, each captured as a single line item that specifies *both* the framework provisions required to leave the door open and the extension implementation that walks through it.

A fourth-round audit reconciled the document against the live codebase and added Items 60–87. Items 60–68 are the former pip-package items P1–P9, redistributed by topic across the existing groups (Group 21 is dissolved). Items 69–87 are gaps surfaced by reading framework docs and source against the prior items: distribution primitives, inbound surface hardening, operational resilience, compliance, code-hygiene sweep, and verified code-vs-doc divergences. Affected earlier items also carry **Refinement after audit:** subsections that resolve the conflicts the fourth-round pass found (idempotency-key ownership, credential resolution layering, the Item 14↔Item 35 outbox dependency, hot-reload's known-broken Python semantics, SDK generation vs. SDK packaging, RLS multi-tenant hierarchy, grant-issued session freshness, JWS algorithm picker). GitHub issue #38 (blocking hooks) is now cross-referenced from Item 22; GitHub issue #10 (BLL fields/includes coverage) became Item 87. Closed-by-verification: GitHub issues #12, #24, #25, #26 — the targeted tests now pass against the current branch (combined run: 658 passed, 145 skipped, 0 failed).

Each item below is scoped as a deliverable. The intent is that this document becomes the working backlog for the framework hardening effort that precedes provider authorship.

### Severity legend

- **Critical** — must land before provider work begins; gating.
- **High** — required for a clean v1 of the federation story; should land in the same milestone as the critical items.
- **Medium** — quality / completeness of a system that already mostly works.
- **Low** — polish, ergonomics, or developer experience.
- **Deferred** — explicitly out of scope; documented here so we don't relitigate.

### Critical-path summary

The eight gating items that must land before provider authorship are:

1. ~~Item 1 — Unified result contract for external provider calls.~~ ✅
2. ~~Item 2 — Failure classification and rotation policy.~~ ✅
3. ~~Item 4 — Idempotency primitive for external operations.~~ ✅
4. ~~Item 5 — Inbound webhook handler infrastructure.~~ ✅
5. ~~Item 10 — Pluggable authentication strategies on provider instances.~~ ✅
6. ~~Item 14 — Mirror-on-create lifecycle primitive.~~ ✅
7. ~~Item 26 — Explicit `AbstractProviderInstance` contract.~~ ✅
8. ~~Item 70 — Manager constructor contract consistency (RotationManager violates the documented `model_registry`-first signature; every provider author derives from a moving target until this is fixed).~~ ✅

A separate critical-path applies to the public PyPI release of the package (the items in former Group 21, plus the supply-chain hardening surfaced by the fourth-round audit):

1. ~~Item 60 — Rename top-level packages under a single namespace.~~ ✅
2. ~~Item 61 — Out-of-tree extension import support.~~ ✅
3. ~~Item 86 — Supply-chain hygiene (SBOM, signed releases, pinned hashes, vuln scanning).~~ ✅

Everything else can iterate without retrofit once these are in place.

---

# Group 1 — Foundation: Typed Errors and Result Contracts

The single contract every external call and every cross-cutting policy depends on. Item 1 establishes the typed exception hierarchy that downstream items (rotation policy, idempotency, bulk endpoints, GraphQL federation) consume.

## ~~Item 1~~ — ~~Unified result contract for external provider calls~~ ✅ DONE

**Severity:** Critical
**Scope:** `AbstractExternalModel`, `AbstractExternalManager`, `AbstractExternalAPIClient`, every `*_via_provider` method authored hereafter.
**Owner area:** Extensions / external federation core.

**Purpose.** Today the static `*_via_provider` methods return a `{"success": bool, "data": ..., "error": ...}` dictionary while the surrounding `AbstractBLLManager` plumbing — hooks, routers, GraphQL resolvers — speaks in raised exceptions and typed return values. The translation between the two contracts is performed in one place inside the example client and is implicit everywhere else. A provider author who returns a dict from an unexpected code path, or who raises instead of returning a dict, will silently corrupt hook state, produce 200-OK responses on failed operations, or bypass our error envelope entirely.

**Current state.** `AbstractExternalAPIClient.create` shows the translation pattern; `get`, `list`, `update`, `delete`, batch, and search do not. Navigation property resolution does not. Field-mapping helpers do not. The contract is described only as prose.

**Target state.** A single canonical contract: `*_via_provider` methods raise typed exceptions on failure and return the unwrapped external payload on success. The dict envelope, if retained at all, lives only inside the rotation system as an internal data carrier and never escapes to caller code. A `BaseExternalError` hierarchy mirrors the failure classes defined in Item 2 (`TransientExternalError`, `AuthExternalError`, `InvalidInputExternalError`, `RateLimitExternalError`, `PermanentExternalError`).

**Implementation notes.** Update `AbstractExternalAPIClient` to be the only translator from rotation-internal envelope to caller-facing exception. Add a metaclass or `__init_subclass__` hook on `AbstractExternalModel` that rejects subclasses whose `*_via_provider` methods are typed as returning `dict`. Update the documentation example in `PRV.External.md` to show raise-style. Migrate the existing payment / email / database extension reference docs to the new style.

**Acceptance criteria.** A provider author can write a `*_via_provider` that raises `TransientExternalError` and have it correctly propagate through rotation, hooks, and the FastAPI / Strawberry layers without writing any envelope-handling code. A static check fails the build if a new provider returns `dict` from a typed external method.

**Dependencies.** Blocks Items 2, 4, 12, 16. Independent of all other items.

---

# Group 2 — Provider Instance & Authentication Contract

The base shape of a bonded provider, the auth strategies that sit on top of it, and the typed settings/abilities that pin its surface at type-check time. These are critical-path because every concrete provider derives from them.

## ~~Item 26~~ — ~~Explicit `AbstractProviderInstance` contract~~ ✅ DONE

**Severity:** Critical
**Scope:** `AbstractProviderInstance`, `bond_instance` typing, all existing and future provider authors.
**Owner area:** Provider system.

**Purpose.** Provider instance classes today are described as "what `bond_instance` returns." Provider methods access `self._instance.something()` without any typed contract specifying what `_instance` is or what methods it must expose. The required interface is implicit, discovered by trial and error, and undocumented in code. Every provider author rediscovers it on their own and may end up implementing different shapes that the framework cannot rely on uniformly.

**Current state.** `AbstractProviderInstance` exists as a near-empty base class. The bound `_instance` attribute is referenced in examples but never declared on the provider class. There is no typed contract enforcing the shape.

**Target state.** Promote `AbstractProviderInstance` to a real contract. Make it either an `abc.ABC` with required abstract methods or a `typing.Protocol` capturing the canonical shape. The contract specifies: `__init__(self, instance: ProviderInstanceModel)` so bonding is uniform; `validate_credentials() -> bool` for self-test before first use; `close()` for cleanup of any held connections; and any extension-specific abstract methods (e.g., `AbstractPaymentProviderInstance` requires `create_charge`, `refund`, etc.).

The provider class declares `_instance: ClassVar[Optional[AbstractProviderInstance]] = None` as a typed attribute, and `bond_instance` is typed as `(cls, ProviderInstanceModel) -> AbstractProviderInstance` so static type checking catches contract violations. A `mypy`-or-equivalent gate enforces the typing in CI.

**Implementation notes.** Existing provider examples in the documentation are migrated to declare the typed attribute and to inherit from the appropriate `AbstractProviderInstance` subclass. The `_instance` attribute is set during bonding in a single canonical code path (a base-class helper), so authors do not write the assignment themselves.

**Acceptance criteria.** A provider author writing a new provider knows exactly which methods their `*ProviderInstance` class must implement, the static type checker enforces it, and forgotten methods produce a clear error rather than a runtime `AttributeError`. The `_instance` attribute on every provider class is typed and resolves correctly under static analysis.

**Dependencies.** Cross-references Items 10 (auth strategies live alongside the bonded instance) and 70 (the provider-instance contract is one half of the manager-constructor contract).

**Refinement after audit.** The bonded-instance shape is one half of the contract; the manager constructor signature is the other half and is independently broken in the codebase (see Item 70). Both must land before provider authorship begins. Item 70 is a sibling Critical-path item, not a sub-issue of this one.

---

## ~~Item 70~~ — ~~Manager constructor contract consistency~~ ✅ DONE

**Severity:** Critical
**Scope:** `RotationManager.__init__` and any other manager whose constructor diverges from the documented `model_registry`-first signature.
**Owner area:** BLL / provider rotation.

**Purpose.** `Framework.md:32` and `BLL.Patterns.md:24-26` both specify that BLL managers receive `model_registry` as their first constructor parameter. Every other manager honors this. `RotationManager.__init__` at `src/logic/BLL_Providers.py:991` puts `requester_id` first and `model_registry` last (and optional). This is a quiet contract violation that breaks the documented authoring pattern: an extension author following `BLL.Patterns.md` will write `RotationManager(model_registry, requester_id=...)` and get a `TypeError` at runtime, or worse, will silently bind `model_registry` to the `requester_id` slot. Provider authorship cannot proceed against a moving manager-constructor target.

**Current state.** `RotationManager.__init__(self, requester_id, target_id=None, target_team_id=None, model_registry=None)`. Documented contract is `(model_registry, requester_id, target_id=None, target_team_id=None, parent=None)`.

**Target state.** `RotationManager.__init__` is rewritten to match the documented contract. Every BLL manager (existing and future) is verified at startup to match the contract via a class-decorator-style check that walks `inspect.signature(cls.__init__)` and asserts the first parameter is `model_registry`. The check fails fast at import time with a clear error naming the offending class.

**Implementation notes.** The fix is a one-method rewrite plus call-site updates wherever `RotationManager(...)` is instantiated. The startup check pattern is similar to Item 23's collision detection — a registry-finalize pass that walks every manager class and validates the constructor. Items 26 (provider instance contract) and 70 (manager constructor) together pin the two halves of the manager-and-its-bonded-instance contract; both must land before provider authorship.

**Acceptance criteria.** `RotationManager(model_registry=..., requester_id=...)` works without raising; `RotationManager(requester_id=..., model_registry=...)` continues to work via keyword-only call but emits a deprecation warning. The startup check catches any new manager whose constructor drifts from the contract. Provider authorship can rely on the documented signature without runtime surprises.

**Dependencies.** Cross-references Item 26 (provider instance contract — sibling Critical-path item).

---

## ~~Item 10~~ — ~~Pluggable authentication strategies on provider instances~~ ✅ DONE

**Severity:** Critical
**Scope:** `AbstractStaticProvider.bond_instance`, `ProviderInstanceModel`, new `AuthStrategy` ABC and concrete subclasses; integration point for the OAuth extension.
**Owner area:** Provider system.

**Purpose.** Today `bond_instance` assumes static credentials — typically an API key — extracted from `ProviderInstanceModel`. Real upstream APIs require a wide range of authentication shapes: OAuth2 with refresh, AWS SigV4 request signing, JWT-bearer with rotation, mTLS, short-lived STS-style tokens, GitHub-app installation tokens, and per-end-user delegated credentials (Stripe Connect, Google Workspace impersonation). The user has confirmed that an OAuth extension exists in a separate repository and is expected to plug into this system; the framework must accept the plug-in, not bake API-key assumptions into the bonding layer.

**Current state.** `bond_instance` examples show direct extraction of `api_key` and a few settings. Multi-step auth flows (token refresh, request signing, credential exchange) have no documented home.

**Target state.** Define `AuthStrategy` as an abstract base class with the contract `headers_for(requester) -> dict`, `params_for(requester) -> dict`, `body_modifier(requester, body) -> body`, and `refresh_if_needed()`. Ship concrete subclasses: `APIKeyAuth`, `OAuth2Auth` (consumed and extended by the OAuth extension), `AWSSigV4Auth`, `JWTBearerAuth`, `MTLSAuth`. `ProviderInstanceModel` carries an `auth_strategy_name: str` and an opaque credentials blob that the strategy interprets. `bond_instance` looks up the strategy by name from a registry, hydrates it from the blob, and the bonded instance routes every outbound call through the strategy's headers/params/body hooks.

**Implementation notes.** The strategy registry is populated by the framework for built-in strategies and extended at runtime by extensions (the OAuth extension contributes `OAuth2Auth` with refresh handling). A single provider may declare a default strategy and accept overrides per provider instance, so a Stripe provider class can default to `APIKeyAuth` and have a per-instance override to `OAuth2Auth` for Stripe Connect users. Strategies must be reloadable so that the OAuth extension can hot-swap a refreshed token without re-bonding the entire instance.

**Acceptance criteria.** A Stripe Connect integration installable as an extension can register an `OAuth2Auth` strategy, attach it to per-user provider instances, and have outbound calls signed correctly without modifying the Stripe provider class. Adding a new authentication scheme is a matter of contributing a new `AuthStrategy` subclass; no changes are required in `AbstractStaticProvider` or in any existing provider.

**Dependencies.** Anticipates the external OAuth extension. Cross-references Items 32 (credential vault — strategies dereference `CredentialRef` rather than reading raw secrets) and 50 (sandbox/live discriminator applies inside `CredentialRef.resolve`, not inside the strategy).

**Refinement after audit.** Items 10, 32, and 50 each touch credential resolution; the canonical layering is now pinned: `AuthStrategy.headers_for(requester)` calls `CredentialRef.resolve(env=APP_ENV)` (Item 32), which dispatches in the documented OpenBao → env → encrypted-DB tier order. Item 50's paired-name (`{PROVIDER}_API_KEY_TEST`/`_LIVE`) discriminator applies **only** when the resolved tier is the env-var fallback; OpenBao paths embed the environment at storage time so the discriminator does not double-fire. The shared HTTP client (Item 31) does not select credentials — it routes through whatever the `AuthStrategy` returned. This eliminates the "two layers fighting over the same logic" condition the audit surfaced.

**Status: ✅ DONE.** Framework primitives shipped earlier: `AuthStrategy` ABC + six concrete subclasses (`APIKeyAuth`, `OAuth2Auth`, `JWTBearerAuth`, `BasicAuth`, `MTLSAuth`, `AWSSigV4Auth`) in `src/serverframework/extensions/AuthStrategy.py:29-320`; `AuthStrategyRegistry`; `default_auth_strategy_name` ClassVar consulted in `bond_instance`. Closed by adding the per-instance override field on `ProviderInstanceModel` itself: `auth_strategy_name: Optional[str]` Pydantic field at `src/serverframework/logic/BLL_Providers.py:617-630`, exposed in `Create`, `Update`, `Search` inner classes; Alembic migration `d4e5f6a7b8c9_provider_instance_auth_strategy_name.py` adds the nullable column; integration test `test_build_auth_strategy_reads_field_from_real_model_instance` in `extensions/AbstractExtensionProvider_contract_test.py:217+` verifies that a `ProviderInstanceModel.Create(... auth_strategy_name="basic")` against a provider class whose default is `"api_key"` correctly resolves to `BasicAuth`. The Stripe Connect per-user override path now works end-to-end without touching the Stripe provider class.

---

## ~~Item 37~~ — ~~Typed `ProviderSettings` and ability declarations~~ ✅ DONE

**Severity:** High
**Scope:** `AbstractStaticProvider`, abstract-provider per-extension subclasses, `_env`, `_abilities`.
**Owner area:** Provider system / type safety.

**Purpose.** Provider configuration today is stringly-typed in three ways simultaneously. Settings are accessed via `get_setting("base_url")` returning untyped values. Environment variables are declared in `_env: Dict[str, Any]` with no schema. Abilities are declared as `_abilities: Set[str]` with no information about input types, return types, or the contract they fulfill. All three are runtime surprises waiting to happen, and all three undermine the framework's stated commitment to end-to-end strong typing — particularly at the federation boundary where mistakes cost real money.

**Current state.** `_env: Dict[str, Any]`, `_abilities: Set[str]`, `get_setting("name", default)`, all stringly-typed.

**Target state.** Each abstract provider declares `Settings` as an inner Pydantic model class capturing every required and optional setting with typed fields, defaults, validators, and `Secret` markers for sensitive values. Concrete providers declare a subclass when they add settings beyond the abstract's minimum. `_env` is replaced by an `EnvSchema` Pydantic model with the same shape — typed names, defaults, required flags, secret flags. The framework validates env-var presence and types at startup, surfacing a clear error rather than failing at first use.

Abilities are declared as typed callables on the abstract provider class. Each ability has a Pydantic-validated input model, a typed return, and clear semantics. Concrete providers either implement or omit each ability, and the framework knows at import time which providers fulfill which abilities, surfacing a clear error if a concrete provider's signature drifts from the abstract's contract. The string-set abilities registry is derived from the typed declarations rather than being a separate source of truth.

**Implementation notes.** `Secret`-typed fields integrate with Item 32's redaction. The `EnvSchema` pulls double duty as the documentation source for required configuration — the auto-generated provider docs derive from it. Concrete providers' `Settings` extend the abstract's via Pydantic inheritance, so the type checker enforces compatibility. Pydantic field-override inheritance has known gotchas (a subclass loosening a required field to optional, or narrowing a type, will type-check but break invariants); the framework runs strict-mode validation and a startup test that walks every concrete provider's `Settings` and verifies the inheritance chain is non-narrowing on required fields.

**Acceptance criteria.** A new provider author writes `class Settings(AbstractStripeSettings.Settings): webhook_endpoint: str` and the framework validates this at startup; missing required settings produce a clear error that names them; the type checker catches a concrete provider whose ability signature drifts from the abstract.

**Dependencies.** Cross-references Items 26 (provider instance contract), 32 (secret marking).

---

# Group 3 — Service Layer Foundation

The service interface is broadened before any item that schedules background work, drains an outbox, runs a streaming consumer, or schedules compensating actions can land. Item 28 sets the surface; Item 44 pins the execution model.

## ~~Item 28~~ — ~~Broaden the service interface beyond perpetual time-based loops~~ ✅ DONE

**Severity:** Medium
**Scope:** Existing service interface (currently shaped for perpetual time-based tasks), new `ScheduledService`, `QueueConsumerService`, and `StreamingService` flavors, shared lifecycle.
**Owner area:** Services.

**Purpose.** The user has confirmed that the current service interface was designed for perpetual time-based tasks — a thirty-second agentic loop, for example — and needs broadening to support a wider range of background work patterns. Streaming consumers (Item 13), saga compensators (Item 14), webhook event-handlers that schedule heavy work for later (Item 5), and outbox workers all need a service contract that fits their shape rather than being shoehorned into the perpetual-loop pattern.

**Current state.** Service interface accommodates one shape: a perpetual loop with a sleep interval.

**Target state.** Four service flavors sharing a common lifecycle (`start`, `stop`, `pause`, `resume`, `health`, all with documented semantics):

- **`PerpetualService`** — the existing thirty-second-loop shape. Default flavor.
- **`ScheduledService`** — cron expression or fixed-interval execution. Useful for periodic syncs, daily reports, billing-cycle resets.
- **`QueueConsumerService`** — pulls from a queue (Redis, SQS, Postgres-as-queue) with backoff, visibility-timeout semantics, and dead-letter handling. Useful for outbox workers, compensating actions, deferred webhook processing.
- **`StreamingService`** — long-lived connection-oriented work. Consumer flavor (websocket subscriber, SSE listener) and Producer flavor (long-lived outbound stream). See Item 13 for the federation-specific use cases.

All four are discoverable as `SVC_*.py` files. All four can declare `@hook_bll`-style triggers. All four participate in graceful shutdown with documented draining behavior.

**Implementation notes.** The lifecycle methods carry their existing semantics from `PerpetualService`. New flavors implement the same lifecycle surface so that the framework's controller (and operators) treat services uniformly regardless of the underlying pattern. State that must persist across restarts (cron last-run-at, queue cursors, stream subscription tokens) lives in a small per-service state store and is the responsibility of the framework, not the service author. The `SVC.Test.md` pattern is updated to cover all four flavors.

**Acceptance criteria.** A `QueueConsumerService` author can declare a queue source, an event handler, and a backoff policy without writing connection or polling code. A `ScheduledService` runs on its cron expression and survives process restarts without missed-run gaps (or with documented gap behavior for misfires). A `StreamingService` reconnects automatically on transient failures.

**Dependencies.** Blocks Items 13, 14. Blocks the deferred-work paths from Item 5.

---

## ~~Item 44~~ — ~~Service layer async and lifecycle semantics~~ ✅ DONE

**Severity:** Medium
**Scope:** `SVC.Patterns.md`, all service flavors from Item 28, the framework's process lifecycle.
**Owner area:** Services.

**Purpose.** Item 28 broadens the service interface into perpetual, scheduled, queue-consumer, and streaming flavors. It does not pin the underlying execution model — are services asyncio coroutines? Threads? Subprocesses? How are they cancelled on shutdown? Do they survive a code reload (Item 20)? What is the supervisor's restart policy for a service that crashes? These questions matter most for webhook listeners, queue consumers, schedulers, and streaming services — exactly the flavors Item 28 added.

**Current state.** Lifecycle methods (start, stop, pause, resume) are documented but their underlying execution model is not pinned.

**Target state.** Pin asyncio as the model. Services are coroutines running on the framework's main event loop, or on dedicated event loops in worker processes for CPU-heavy workloads. Cancellation is cooperative — services receive `asyncio.CancelledError` on stop and have a documented drain period (default thirty seconds, configurable per service) to finish in-flight work before the process exits. A service that exceeds the drain period is forcibly cancelled with a logged warning. Code reload via Item 20 attempts a clean stop-and-restart of every running service; services that cannot survive a reload (e.g. stateful streaming consumers with long re-handshake costs) opt out via a class flag and fall back to "process restart only" semantics for that service.

The supervisor restart policy is configurable per service: `always` (restart on any exit), `on_failure` (restart only on non-zero exit / unhandled exception), `never` (one-shot). Backoff between restarts is exponential with jitter. A service that has crashed N times within a window is held in a `failed` state with an admin-action requirement, rather than restart-storming.

**Implementation notes.** The asyncio choice rules out blocking I/O inside service handlers — document the use of `asyncio.to_thread` for unavoidable blocking calls, with a configurable thread-pool size cap (the default `asyncio.to_thread` pool is unbounded and a footgun under load). The drain-period semantics interact with Item 14's outbox draining: the OutboxDrainService must complete in-flight outbox entries before exiting, or the framework risks producing duplicate sends after a restart. The `failed`-state admin-action requirement is concrete: the framework exposes an admin endpoint (`POST /admin/services/{name}/reset`) and an equivalent CLI command that an operator runs after diagnosing the underlying cause; the service does not auto-recover from `failed` until reset.

**Acceptance criteria.** A service author writing a `QueueConsumerService` can rely on documented cancellation semantics; a long-running operation receives `CancelledError` cleanly on stop; the drain period is honored. A service crash triggers the configured restart policy with the configured backoff. Item 20's hot reload triggers a clean stop-and-restart for every reloadable service.

**Dependencies.** Refines Item 28. Cross-references Items 14, 20.

---

# Group 4 — Rotation System: Failure Handling, Rate Limits, Health, Stickiness

The rotation system's policy surface, from the typed-error consumer (Item 2) through rate limits (17) and liveness (27) to application-level stickiness (51). Item 3 documents what we deliberately delegate to L7.

## ~~Item 2~~ — ~~Failure classification and rotation policy~~ ✅ DONE

**Severity:** Critical
**Scope:** `RotationManager`, `AbstractStaticProvider`, every external provider authored hereafter.
**Owner area:** Provider rotation system.

**Purpose.** The current rotation system rotates on any exception. This is incorrect. A 400-class error from the upstream is a request defect that will fail identically against every provider; rotating burns the chain on first request. A 429 signals rate-limiting and demands backoff against the same provider, not rotation away from it. A 401 signals an auth defect that should mark the provider unhealthy without consuming the retry budget. Only 5xx-class transient errors should trigger rotation in the failover sense.

**Current state.** `rotate()` catches a bare `Exception` and advances. There is no failure taxonomy and no per-provider policy.

**Target state.** A `RotationPolicy` dataclass declared per provider with sensible defaults. The policy specifies what each failure class does:

- 4xx-shaped failures are caught at the request-homogenization layer **before** the call reaches a provider, surfaced as `InvalidInputExternalError`, and never trigger rotation.
- 5xx-shaped failures raise `TransientExternalError`, are retried per the provider's backoff policy, and on exhaustion advance to the next provider in the chain.
- 429-shaped failures raise `RateLimitExternalError`, honor the upstream `Retry-After` header (or the provider's parser equivalent for non-standard headers), and back off against the same provider with exponential delay and jitter — they do not advance the chain.
- Auth-shaped failures (401 / 403 with auth-failure semantics) raise `AuthExternalError`, mark the provider unhealthy for a cool-down window, and advance to the next provider without consuming the retry budget.

**Implementation notes.** The `RotationPolicy` carries `transient_max_retries`, `transient_base_ms`, `transient_max_ms`, `transient_jitter`, `rate_limit_base_ms`, `rate_limit_max_ms`, `auth_cooldown_seconds`, and a per-provider `header_parser` callable for extracting `Retry-After` semantics. Per Item 3 we are not implementing load balancing inside rotation — that is HAProxy's job — so the policy concerns failover only.

**Acceptance criteria.** A provider author can declare `rotation_policy = RotationPolicy(...)` on their provider class and have all four failure classes routed correctly without writing any retry or backoff code. A 4xx-shaped upstream failure surfaces as a 400-class response to our caller without any rotation attempt logged. A 429 storm against one provider does not propagate to its neighbors in the chain.

**Dependencies.** Depends on Item 1 (typed error hierarchy). Independent otherwise.

---

## ~~Item 17~~ — ~~Rate-limit and quota awareness per provider~~ ✅ DONE

**Severity:** Medium
**Scope:** Per-provider `RateLimit`, token-bucket implementation, response-header parsers, integration into rotation.
**Owner area:** Provider rotation system.

**Purpose.** Without rate-limit awareness the rotation system will, on a sustained-rate workload, drive a provider to its rate limit, observe the 429 responses, and rotate to the backup — which it will then drive to the same rate limit. This is rate-limit storming. The correct behavior is to queue, throttle, and respect the upstream's `Retry-After` (or equivalent) signal against the same provider rather than rotating away from it.

**Current state.** No rate-limit primitives. Rotation responds to 429 like any other failure.

**Target state.** Each provider declares an optional `rate_limit: ClassVar[Optional[RateLimit]]` carrying the steady-state requests-per-second and burst capacity. The framework maintains a per-provider token bucket; outbound calls acquire a token before issuing and block (with timeout) when the bucket is empty. Response headers honor `Retry-After`, `X-RateLimit-Reset`, and provider-specific equivalents via a per-provider header parser. When a rate-limit signal is observed, the rotation system pauses that provider for the indicated window rather than rotating.

**Implementation notes.** The token bucket is in-process for single-instance deployments; for multi-instance deployments, point the rate-limit accounting at a shared store (Redis). Per-provider concurrency caps complement rate limits and are configured similarly. Document the interaction with quota tracking (Item 19): rate limits constrain wire-level throughput, quotas constrain logical-billing usage — they are independent dimensions and both apply.

**Acceptance criteria.** A provider with `rate_limit = RateLimit(rps=100, burst=20)` saturating at 100 requests per second never produces a 429 under steady load. When a 429 does occur (e.g., from a different consumer of the same upstream key), the provider is paused for the upstream-indicated window and resumes automatically. No rotation is triggered by rate-limit signals.

**Dependencies.** Depends on Items 2 (rotation policy hosts the rate-limit branches) and 69 (token-bucket counter is implemented atop the shared distributed-counter primitive). Cross-references Item 19 (quota tracking).

**Refinement after audit.** The token bucket is one consumer of the new `DistributedCounter` primitive (Item 69). The original prose "for multi-instance deployments, point the rate-limit accounting at a shared store (Redis)" is preserved but the *mechanism* is no longer per-item bespoke; both Item 17 and Item 19 (quota) and Item 57 (per-tenant fairness virtual-time scheduler) use the same primitive with the same `INCR ... WHERE counter < limit RETURNING` semantics.

---

## ~~Item 27~~ — ~~Separate `is_configured` from `health_check`~~ ✅ DONE

**Severity:** Low
**Scope:** `AbstractStaticProvider`, rotation system health gates.
**Owner area:** Provider system.

**Purpose.** The current `is_configured` method returns `True` when no required environment variable is empty. That is a useful sanity check at startup but does not distinguish "credentials are present" from "credentials are real and the upstream is reachable." A provider with stale credentials passes `is_configured` and then fails on every actual call. The rotation system needs a notion of liveness it can trust, separate from pure configuration completeness.

**Current state.** `is_configured` exists and is naive. There is no health-check primitive.

**Target state.** Two distinct methods: `is_configured() -> bool` continues to mean "all required env vars are present" and is used for startup checks and admin-level readiness reporting. A new `health_check() -> HealthStatus` performs a live, lightweight call to the upstream (a no-op endpoint, a credential-validating call, an unauthenticated ping) and returns a structured status: `OK`, `DEGRADED`, `DOWN`, with a timestamp and a human-readable detail. Health-check results are cached with a configurable TTL (default 60 seconds) so they do not hammer the upstream. The rotation system consults `health_check` to skip providers that are currently `DOWN` rather than discovering it on the next real call.

**Implementation notes.** Health-check failure does not raise; it returns `DOWN` with the failure detail. The cache is per-provider-instance, not per-class. Some upstreams have rate-limited or expensive health endpoints; document the per-provider TTL recommendations.

**Acceptance criteria.** A provider with valid env vars but stale credentials returns `is_configured == True` and `health_check == DOWN` after the first failed validation. The rotation system skips `DOWN` providers without consuming a retry slot.

**Dependencies.** Cross-references Item 2 (rotation policy).

---

## ~~Item 51~~ — ~~Sticky-session routing in rotation~~ ✅ DONE

**Severity:** Low
**Scope:** `RotationManager`, per-call routing hints.
**Owner area:** Provider rotation.

**Purpose.** Per Item 3, load balancing across providers is delegated to L7 infrastructure (HAProxy). Per the same delegation, **percentage-canary routing is also HAProxy's job** — HAProxy can route a configured percentage of traffic to a canary backend as the first hop and observe its health independently. There is no reason to duplicate that machinery inside the application's rotation system.

What L7 cannot solve is **session stickiness driven by application-level context**. An LLM chat conversation should pin successive messages to the same upstream provider so the upstream's session-state (KV cache, conversation memory, partial outputs) stays coherent — but L7 cannot see the conversation id, only the connection. Stickiness therefore remains the rotation system's concern; canary does not.

**Current state.** Linear failover only. No application-level stickiness primitive.

**Target state.** `RotationManager.rotate` accepts an optional routing hint of the form `RoutingHint(stickiness_key: Optional[str])`. When a stickiness key is present, the rotation system pins repeated calls within that key to the first provider that succeeded for it, until that provider becomes unhealthy or the sticky-session TTL expires; on advancement the next call recomputes the pin against the surviving chain. Default behavior is unchanged (linear failover) when no key is supplied.

**Implementation notes.** Sticky session state lives in a small in-memory cache keyed by `(stickiness_key, ability)` with a configurable TTL. Multi-process deployments either accept session-affinity-loss on cross-process requests (acceptable for many use cases) or back the cache with Redis. The framework does not implement canary routing; deployments wanting canary semantics configure HAProxy (or equivalent) per Item 3, and rolling forward a canary to "first in chain" remains a deployment-level concern, not an application one.

**Acceptance criteria.** An LLM chat session with `stickiness_key="conv_123"` routes its successive messages to the same provider until that provider fails or the TTL expires, at which point it advances normally. The rotation system contains no canary code; canary semantics are documented as L7-delegated.

**Dependencies.** Cross-references Items 2, 3.

---

## ~~Item 3~~ — ~~Load balancing among providers~~ ✅ DONE

**Severity:** Deferred (by design)
**Scope:** Documentation only.
**Owner area:** Infrastructure documentation.

**Purpose.** Capture the architectural decision that load balancing across active-active providers is not a concern of the application-level rotation system. Production deployments place HAProxy or an equivalent L7 load balancer in front of provider endpoints when load distribution is desired. The rotation system is a failover mechanism, not a load distributor, and conflating the two complicates failure semantics.

**Current state.** The rotation chain is linear, single-parent. The documentation does not state explicitly that load balancing is out of scope.

**Target state.** A short note in `PRV.Patterns.md` and `Framework.md` that load balancing is delegated to L7 infrastructure and that the rotation chain implements failover only. No code change.

**Implementation notes.** Add a paragraph to the rotation-system documentation. Cross-reference HAProxy or equivalent. Note that round-robin, weighted, and latency-based routing are explicitly out of scope for the framework.

**Acceptance criteria.** Documentation reflects the design decision; future contributors do not propose load-balancing features inside rotation.

**Dependencies.** None.

---

# Group 5 — Credentials, Vault, Sandbox/Live Discrimination

How secret material enters the framework, where it lives at rest, how it is redacted in logs, and how test/live shapes are kept apart. These wrap into the auth strategies of Group 2 and feed the shared HTTP client of Group 6.

## ~~Item 32~~ — ~~Credential vault with OpenBao as default~~ ✅ DONE

**Severity:** High
**Scope:** Credential storage for `ProviderInstanceModel.api_key` and any other sensitive provider settings; logging layer redaction; admin API for credential rotation.
**Owner area:** Security / configuration.

**Purpose.** Provider credentials today live as plaintext columns in the database. The documentation rule "never log API keys" is a guideline, not a primitive — there is no enforcement. Production deployments will require encryption at rest, secret rotation, audit-on-read, and log redaction enforced by the framework rather than by author discipline. The user has specified that **OpenBao** (the open-source HashiCorp Vault fork) is the framework's default credential store, with a documented fallback chain for environments where OpenBao is not configured.

**Current state.** `api_key` is a plain DB column. Settings flagged secret-in-prose are stored in plaintext. No rotation contract. No audit. No enforced redaction.

**Target state.** A three-tier credential resolution order, in priority:

1. **OpenBao** — the default credential store. Provider instance credential references resolve through OpenBao's API. Credentials are referenced by path/key, not stored in our database. Reads are audited by OpenBao. Rotation is performed by OpenBao policies; the framework re-resolves on token-renewal cycles.
2. **Environment variables** — fallback when OpenBao is not configured (single-process developer setups, simple deployments). Conventional names per provider.
3. **Encrypted database column** — fallback when neither OpenBao nor env vars are configured. Encryption uses framework-managed Fernet (or KMS-backed equivalent) with key rotation supported. Reads are audited via the existing audit log infrastructure.

The `ProviderInstanceModel.api_key` field becomes a `CredentialRef` rather than a plaintext string — a typed reference that resolves through whichever tier is configured. Logging layer integration enforces redaction: any value matching a known credential pattern, or any value tagged `Secret[T]` in Pydantic, is replaced with `***` before reaching log output. Stack traces and error envelopes are similarly scrubbed.

**Implementation notes.** OpenBao integration uses the standard Vault API — token-based or app-role authentication, lease renewal, secret-version pinning. The fallback chain is configured by environment (`CREDENTIAL_STORE=openbao|env|database`). Document a migration path from plaintext columns to encrypted: a one-time script reads existing plaintext, writes to OpenBao (or encrypts in place), updates the reference. Audit-on-read includes the requester id, the credential reference, the calling provider, and the outcome.

Credentials resolve per-request, not per-bond, so rotation in the credential store takes effect on the next outbound call rather than the next bonding pass. SDK instances that cache the credential at construction (e.g. `stripe.api_key = ...` set once) must be re-bonded on lease renewal; the framework provides a `re_bind_on_rotation` declarative flag for these cases. For the encrypted-column fallback, framework-managed Fernet is the documented default for self-managed key material; envelope-encryption-with-KMS (AWS KMS, GCP KMS, or equivalent) is the recommended cloud variant and ships as a configurable `EncryptionBackend`.

**Credential cache-bust on rejection.** When an upstream returns an authentication failure (401, 403 with auth-failure semantics, or a provider-specific equivalent surfaced as `AuthExternalError` per Item 2), the framework invalidates the cached credential and forces a fresh resolution from the source tier. If the re-resolved credential is byte-identical to the rejected one, the credential is marked actually-bad rather than stale-cached: the provider transitions to `DOWN` (Item 27), an admin alert is emitted, and rotation per Item 2's auth policy advances to the next provider without further attempts on this credential until an operator intervenes or the source tier reports a new version. This prevents tight-loop re-attempts against a known-bad credential while preserving the rotation-on-actual-rotation property when the credential genuinely changed in OpenBao between attempts.

**Acceptance criteria.** Starting the framework with OpenBao configured resolves all provider credentials through OpenBao; without it, env vars take over; without those, the encrypted-column fallback is used. Logging output never contains a plaintext credential. A credential rotation in OpenBao is reflected in subsequent provider calls within one renewal cycle without a framework restart. An upstream auth rejection invalidates the cached credential and triggers a re-resolve; a re-resolved-identical credential marks the provider actually-bad and halts retry against it.

**Dependencies.** Independent. Cross-references Items 10 (auth strategies consume credentials through this layer per the canonical layering) and 50 (the env-var tier honors the paired-name discriminator).

**Refinement after audit.** Two additions. **(a) JWS/JWE algorithm picker.** Fernet is fine for symmetric encryption at rest of the encrypted-column fallback; it is *not* a JWS/JWE primitive, and Items 18 (OAuth tokens) and 58 (magic-link bearer tokens) need one. PyJWT is the framework's signed-token library; the algorithm registry is a versioned artifact (`JWS_ALG_VERSION=v1`); the rotation procedure for "we are moving from HS256 to EdDSA" is documented as: register both algorithms, sign new tokens with the new algorithm, accept either for verification during a documented overlap window, retire the old algorithm at the end of the window. **(b) Credential resolution layering.** This item is the canonical owner of credential resolution; Items 10 and 50 layer atop it per their refinements. The shared HTTP client (Item 31) does not select credentials.

---

## ~~Item 50~~ — ~~Sandbox versus live credential split convention~~ ✅ DONE

**Severity:** Low
**Scope:** Provider configuration, `_env` schema (Item 37), shared HTTP client (Item 31).
**Owner area:** Provider system / configuration.

**Purpose.** Most external APIs distinguish test mode from live mode (Stripe's `sk_test_*` versus `sk_live_*`, SendGrid's sandbox flag, Twilio's test credentials). Today there is no documented convention for naming the paired environment variables, no per-environment selection mechanism, and no enforcement that a deployment marked `production` will refuse test credentials and vice versa. Every provider author rolls their own scheme.

**Current state.** No sandbox/live convention.

**Target state.** A canonical convention: paired env vars are named `{PROVIDER}_API_KEY_TEST` and `{PROVIDER}_API_KEY_LIVE` (and similarly for any other secret), with selection driven by a top-level `APP_ENV` discriminator (`development`, `staging`, `production`). The framework's typed `EnvSchema` (Item 37) declares which variables are paired and the shared HTTP client (Item 31) selects the right value at request time. Production deployments refuse to start if any provider resolves to a `_TEST_` credential; development deployments warn (but do not refuse) on `_LIVE_` credentials.

**Implementation notes.** Some upstreams use a single key with a server-side test/live flag rather than paired keys; document that the convention is opt-out for those providers and the discriminator is provider-specific. The headers injected per environment (e.g. `Stripe-Account` for Connect, sandbox flags) are likewise declarative on the provider rather than imperative inside `bond_instance`. `APP_ENV` is the default discriminator but providers can override via `environment_source: ClassVar[str]` (e.g. `STRIPE_ENV`) for deployments that genuinely need per-provider environment selection — staging processes that hit production-Stripe-test-mode plus staging-DB are common enough to warrant the escape hatch.

**Acceptance criteria.** A staging deployment uses `_TEST_` credentials automatically; a production deployment fails to start if any `_TEST_` credential is configured for an in-use provider; the documentation describes the convention in one place.

**Dependencies.** Cross-references Items 31, 37, 32.

**Refinement after audit.** The discriminator runs **inside** `CredentialRef.resolve` (Item 32), not inside the shared HTTP client (Item 31), and only when the resolved tier is the env-var fallback. OpenBao paths embed the environment in the storage path itself (`secret/data/{env}/{provider}/api_key`) so the discriminator does not double-fire on resolves that hit OpenBao. The shared HTTP client receives a fully-resolved credential from the auth strategy and is unaware of the test-vs-live distinction.

---

## ~~Item 65~~ — ~~Lazy environment variable lookup (formerly P6)~~ ✅ DONE

**Severity:** Medium
**Scope:** `app.py` (`instance`, `create_registry_with_db_manager`), every other call site that consumes `env(...)` in a default argument expression.
**Owner area:** Configuration / packaging.

**Purpose.** Several places in `app.py` invoke `env(...)` in default-argument expressions, e.g. `def instance(db_prefix: str = "", extensions: str = env("APP_EXTENSIONS")):`. Default arguments evaluate at module-import time, so the value of `APP_EXTENSIONS` is captured the moment `app.py` is first imported. That is fine when `python app.py` sets up env first, but it breaks the "import the package, then configure it" flow that the façade enables — by the time the consumer sets `os.environ["APP_EXTENSIONS"]` and calls `serverframework.run()`, the default has already been baked. The façade's `run()` works around this by setting env *before* importing `app`, but anyone calling `instance()` directly is exposed.

**Current state.** `default=env(...)` patterns in `app.py` and elsewhere capture configuration at import time.

**Target state.** Replace `default=env(...)` patterns with `default=None` plus an in-body fallback: `def instance(db_prefix: str = "", extensions: Optional[str] = None): if extensions is None: extensions = env("APP_EXTENSIONS"); ...`. Every call site that wants the env-var default reads it inside the function body, after the caller has had the chance to set the environment.

**Implementation notes.** Sites to audit: `app.py` — `instance`, `create_registry_with_db_manager`. Anywhere else `grep -n "= env(" src/**/*.py` turns up at function signatures. This is the kind of change that is easy to do wrong: if `extensions=""` and `extensions=None` are meant to behave differently (one means "no extensions," the other means "use the env default"), preserve that distinction; pick one sentinel and document it.

**Acceptance criteria.** A consumer can `import serverframework; os.environ["APP_EXTENSIONS"] = "payment"; serverframework.run()` and have the payment extension load. Calling `instance(extensions=None)` reads the current env; calling `instance(extensions="")` produces the empty-extensions behavior unambiguously.

**Dependencies.** Independent. Cross-references Item 60 (the façade's restructured `__init__.py` removes the `sys.path` workaround once this lands).

---

# Group 6 — Shared Outbound HTTP, Versioning, Tracing, Deadlines

The single client every provider routes outbound calls through, and the cross-cutting concerns that hang off it: upstream API version pinning, distributed tracing, and end-to-end deadline budgets.

## ~~Item 31~~ — ~~Shared outbound HTTP client primitive~~ ✅ DONE

**Severity:** High
**Scope:** New `ProviderHTTPClient`, integration into every provider that performs outbound HTTP, refactor of existing example providers.
**Owner area:** Provider system / external federation core.

**Purpose.** Today every provider initializes its own SDK or HTTP client inside `bond_instance`. There is no shared layer for cross-cutting outbound concerns — timeout policy, retry policy, idempotency-key injection (Item 4), 429 handling (Item 17), trace context propagation (Item 34), credential redaction in logs (Item 32), authentication strategy invocation (Item 10), sandbox/live discriminator (Item 50). Each provider author re-implements these, inconsistently, and the resulting drift is the single largest source of duplicated provider code in the framework's existing example extensions.

**Current state.** Each provider holds its own SDK or constructs its own `requests`/`httpx` client. No shared policy layer.

**Target state.** A single `ProviderHTTPClient` that bonded provider instances either wrap (when an upstream SDK is the natural interface) or use directly (when the provider speaks raw HTTP). The client exposes a method-per-verb interface and applies, in order: trace propagation, authentication strategy, idempotency-key injection (for marked methods), rate-limit token acquisition, timeout, send, response-class detection, header parsing for `Retry-After`-style signals, log redaction. Providers that wrap an SDK route the SDK's HTTP layer through this client (most modern SDKs allow injection of a custom transport); providers that hit raw HTTP use it directly. Configuration is per-provider, layered defaults from the framework with provider overrides.

**Implementation notes.** The client is typed: request bodies and responses are bound to Pydantic models when given, and untyped passthrough is supported but discouraged. Connection pooling is shared across all providers in the same process. TLS configuration (cipher suites, certificate pinning) is exposed as policy. Egress proxy support is exposed as policy. For providers wrapping an upstream SDK, full cross-cutting coverage depends on the SDK exposing a transport hook; SDKs that bind credentials at instance construction without a re-resolution callback, or that monkey-patch their own transport, get partial coverage. The framework documents per-SDK which concerns are applicable so authors do not assume universal coverage.

**Acceptance criteria.** A new provider hitting raw HTTP writes its outbound calls through `self.http.post(url, model=...)` and inherits trace propagation, retry, rate limiting, idempotency keying, log redaction, and auth-strategy headers without writing any of those concerns itself. A provider wrapping an upstream SDK (e.g. `stripe`) configures the SDK to route through the shared client and gets the same behavior, modulo SDK-specific limitations documented in the provider's `PRV.X.md`.

**Dependencies.** Cross-references Items 2, 4, 10, 17, 32, 34, 50.

---

## ~~Item 33~~ — ~~Upstream API version pinning per provider~~ ✅ DONE

**Severity:** Medium
**Scope:** `AbstractStaticProvider`, paired Pydantic external DTOs replacing string-based `field_mappings` for compile-time validation.
**Owner area:** External federation.

**Purpose.** Stripe and most modern SaaS APIs version their wire format. Pinning the upstream version is the difference between "we pick up upstream changes when we are ready" and "an upstream silent change breaks production on a Tuesday." Today providers do not declare which upstream version they target. Combined with stringly-typed `field_mappings: Dict[str, str]`, this means there is no compile-time guarantee that mapped fields exist on either side, and no deliberate path for upgrading from one upstream version to the next.

**Current state.** No version pin. `field_mappings` is a string-to-string dict.

**Target state.** Each provider declares `external_api_version: ClassVar[Optional[str]]`. The shared HTTP client (Item 31) injects this as the appropriate header (`Stripe-Version`, `X-Api-Version`, etc.) per provider. Field mappings (Item 6) are upgraded from string keys to references to fields on a paired Pydantic external DTO that mirrors the upstream's schema for the pinned version, so renames or removed fields are caught at type-check time. When the framework supports a new upstream version, the provider's external DTOs are updated and the version pin advanced in a single coordinated change.

**Implementation notes.** The contract-testing snapshots from Item 11 are pinned to the same version, so drift detection compares like-versus-like. Document a recommended cadence for upstream version review and upgrade. For upstreams without a formal version (e.g. internal partner APIs), the version field is null and the framework relies on Item 11 to detect drift. The paired external DTOs cover the fields the framework actually uses, not the entire upstream surface — for upstreams the size of Stripe, mirroring the full schema is impractical and unnecessary. Adding a new upstream field to a DTO is a single-line addition gated by Item 11's drift snapshot.

**Acceptance criteria.** A provider declaring `external_api_version = "2024-06-20"` produces correctly-versioned outbound headers; type checks fail if a `field_mapping` references a field absent from the paired external DTO; advancing the version pin and updating the DTOs is a single reviewable change.

**Dependencies.** Cross-references Items 6 (field-mapping pipeline), 11 (drift snapshots), 31 (shared HTTP client injects the header).

---

## ~~Item 34~~ — ~~Distributed tracing and provider-call metrics~~ ✅ DONE

**Severity:** Medium
**Scope:** `RotationManager.rotate`, shared HTTP client (Item 31), `RequestContext`, metrics emission layer.
**Owner area:** Observability.

**Purpose.** A single user request traverses API → BLL → rotation → bonded SDK → outbound HTTP. Diagnosing latency or failures along that path requires correlated traces across all layers. Metrics on rotation behavior (how often we rotated, which provider succeeded, how long each attempt took) are essential for capacity planning and for catching upstream degradation before it becomes user-visible. Today none of this is documented.

**Current state.** `meta_logging` extension exists for structured logs; no trace context propagation; no provider-call metrics.

**Target state.** W3C `traceparent` and `baggage` headers propagate from the inbound request through `RequestContext`, into `RotationManager.rotate`, into the shared HTTP client (Item 31), and out to the upstream. The framework emits a fixed set of provider-call metrics: latency histogram per `(provider, ability)`, success/error rate, rotation-attempt counter (how many providers we walked before success), per-provider health gauge (consumed from Item 27's `health_check`), idempotency-key cache hit rate. Metrics emission is pluggable — Prometheus, OpenTelemetry, Statsd backends — with a no-op default for environments that do not collect.

**Implementation notes.** Trace context lives on `RequestContext` and is set by an ASGI middleware on inbound requests. The shared HTTP client reads it from the active context. `RotationManager.rotate` opens a span per attempt, tagged with the provider name and the attempt index, so a multi-provider rotation appears as a parent span with N attempt-children. Document the span naming convention; recommended convention is to follow OTel HTTP-client semantic conventions (`http.method`, `http.url.template`, `http.response.status_code`, `peer.service` as the upstream provider name) so backends like Jaeger, Honeycomb, and Datadog give correlated UX out of the box. Metric labels are bounded — provider names and ability names form a small cardinality set; do not add unbounded labels (such as user id) to metric tags.

**Acceptance criteria.** A request tagged with a `traceparent` produces a coherent trace from inbound API through outbound provider call in the configured tracing backend. Provider-call metrics are visible in the configured metrics backend with consistent label names. A simulated rotation with three failed attempts and one success appears as four attempt-spans nested under one parent.

**Dependencies.** Cross-references Items 27, 31. Independent of other items.

**Status: ✅ DONE.** All four pieces of the contract have landed: (a) `RotationManager.rotate` opens a parent rotation span tagged with `target_id` + `ability` and a child span per attempt tagged with `provider` + `attempt_index` (`src/serverframework/logic/BLL_Providers.py`); (b) pluggable metrics emission ships via `lib/Metrics.py` with `MetricsBackend` ABC and concrete `NoopMetricsBackend` (default), `InMemoryMetricsBackend`, `PrometheusMetricsBackend`, `OpenTelemetryMetricsBackend`; (c) per-attempt latency histograms, success/error counters, rotation-attempt counter, chain-exhaustion counter, per-provider health gauge (consumes Item 27's `health_check`), and idempotency-key cache hit-rate metric all emit through `_safe_metric` so telemetry never derails rotation; (d) backend selection is via `set_metrics_backend(...)` with no-op default. Tests: `lib/Metrics_test.py` (existing), `lib/Metrics_health_gauge_test.py` (new), `logic/BLL_Providers_idempotency_metric_test.py` (new). Closed by verification.

---

## ~~Item 47~~ — ~~Deadline budget propagation through `RequestContext`~~ ✅ DONE

**Severity:** Medium
**Scope:** `RequestContext`, `RotationManager`, shared HTTP client (Item 31), middleware chain.
**Owner area:** Operational resilience.

**Purpose.** A user request traverses API → BLL → rotation → SDK → outbound HTTP. Each layer applies its own timeout, with no shared deadline budget. A request with a five-second outer deadline can spend the first four seconds in the BLL and then start a four-second outbound HTTP call, blowing through the user-visible deadline by three seconds. A shared budget threaded through `RequestContext` lets each downstream layer see how much time remains and shrink its own timeout accordingly.

**Current state.** Per-layer timeouts. No shared budget.

**Target state.** `RequestContext` carries a `deadline: Optional[datetime]` set by the inbound middleware from the request's deadline header (or computed from a configurable default). Every framework component that performs I/O — the rotation system, the shared HTTP client, the database session, the event bus publish — reads the deadline before scheduling work, computes the remaining budget, and either adjusts its own timeout to fit or raises `DeadlineExceededError` immediately if the budget is already exhausted. A typed `DeadlineExceededError` surfaces as HTTP 504 to the client with a clear message.

**Wire format.** The header is `X-Request-Timeout-Ms`, a relative integer (milliseconds remaining). Relative is preferred over absolute (`X-Deadline-At`) because clock skew between client and server makes absolute deadlines fragile, and gRPC-style relative timeouts are the convention most clients already understand. The framework converts to an absolute internal deadline at ingress so all subsequent measurements use a single time source.

**Implementation notes.** Deadline propagation interacts with the outbox (Item 35) in a subtle way: when an operation enrolls in `queue_and_retry` (Item 48) and returns 202, the original request's deadline is satisfied at that point — the SLA shifts from synchronous to asynchronous. The outbox entry therefore receives a *fresh* deadline (driven by the operation's own retry policy), not the original request's deadline. The trace context (Item 34) records the budget consumed at each span and the deadline reset on outbox enrollment, so post-mortem analysis can identify which layer ate the deadline and where the SLA boundary shifted.

**Acceptance criteria.** A request with a one-second deadline that spends nine hundred milliseconds in the BLL produces a `DeadlineExceededError` rather than starting a one-second outbound HTTP call. The error surfaces as 504 with a typed message identifying the elapsed budget and the layer that ran out.

**Dependencies.** Cross-references Items 31, 34, 35.

---

## ~~Item 85~~ — ~~Structured application logging contract~~ ✅ DONE

**Severity:** Medium
**Scope:** `lib/Logging.py`, `meta_logging` extension, `RequestContext`, asyncio context propagation, pluggable error-reporter.
**Owner area:** Observability.

**Purpose.** Item 34 covers distributed tracing and provider-call metrics; Item 56 covers *audit*-log retention. Application logs — the structured-log stream that operators search during a production incident — have no documented retention, no documented correlation-ID propagation across `asyncio.to_thread` boundaries (Item 44 names `to_thread` but does not pin the log-context behavior across the boundary), no error-reporting integration, and no committed log-level taxonomy. The first production incident produces traces, audit events, and a trickle of unstructured stderr lines that cannot be correlated.

**Current state.** `lib/Logging.py` exists; `meta_logging` extension exists for structured audit events. No correlation-ID story across asyncio boundaries. No pluggable error-reporter. Ad-hoc `print()` statements scattered through the codebase (43 instances at audit time — see Item 73) bypass the logging layer entirely.

**Target state.** Every log line carries a `correlation_id` populated from `RequestContext.correlation_id` (set by inbound middleware, derived from the `traceparent` header per Item 34 when present, otherwise minted). `correlation_id` propagates across `asyncio.to_thread` and `asyncio.create_task` via a context-var copying helper that the framework supplies; service authors do not write the propagation themselves. Log levels follow a documented taxonomy: `DEBUG` (developer-only), `INFO` (operational milestones), `WARNING` (degraded behavior; operator should see this in dashboards), `ERROR` (operator should be alerted; the request failed but the process continues), `CRITICAL` (process-level failure; reserved for the framework's own startup-validation failures). Application-log retention defaults to 30 days, configurable per deployment, and is independent of audit-log retention from Item 56.

A pluggable `ErrorReporter` ABC accepts `report(exception, context)` calls; framework-shipped adapters cover Sentry, Rollbar, and a no-op default. Every uncaught exception in a request handler, every `blocking=True` hook failure, every `failed`-state service crash (Item 44) is reported through this surface in addition to being logged.

**Implementation notes.** The 43 `print()` calls audited at `lib/Pydantic.py:1212`, `lib/Pydantic2FastAPI.py:2257-2290`, `app.py:379-404` are removed in the same change that lands this item — they are a symptom of the missing logging contract. The `correlation_id` propagation across asyncio boundaries uses `contextvars.copy_context().run(fn)` for `asyncio.to_thread` and a wrapper around `asyncio.create_task` that snapshots context. This is documented in `lib/LIB.RequestContext.md`. The error-reporter integration ties into the `meta_logging` extension's hook surface so a single hook can both audit and report.

**Acceptance criteria.** A log line emitted from inside an `asyncio.to_thread` call inside a `QueueConsumerService` (Item 28) handler reaches the structured-log backend with the originating request's `correlation_id` attached. A `Sentry`-configured deployment receives an error report on every uncaught request-handler exception, with the `correlation_id` and `RequestContext` snapshot attached. No `print()` calls remain in non-test source.

**Dependencies.** Cross-references Items 34 (trace context is the upstream of `correlation_id`), 44 (asyncio service lifecycle), 56 (audit-log retention is independent), 73 (the `print()` cleanup happens here).

---

# Group 7 — Idempotency, Outbox, Lifecycle Mirroring, Locks, Degradation

The reliability core: making mutating calls safely retryable, persisting work that did not finish in the foreground, mirroring local entities to upstream counterparts, serializing critical sections, and choosing the right shape of failure when rotation is exhausted.

## ~~Item 4~~ — ~~Idempotency primitive for external operations~~ ✅ DONE

**Severity:** Critical
**Scope:** `AbstractExternalManager`, `RotationManager`, every external provider performing mutating operations.
**Owner area:** Extensions / external federation core.

**Purpose.** Mutating external operations must be safely retryable. When the rotation system retries a transient failure, the first attempt may have actually succeeded but the response was lost in transit. Without an idempotency key on the wire, the retry double-acts: a second charge is issued, a second customer is created, a second email is sent. Stripe, Square, Adyen, and most modern APIs accept an `Idempotency-Key` header for exactly this reason. The framework must provide this primitive rather than asking each provider author to reinvent it.

**Current state.** No idempotency support. Retries are unsafe for any non-idempotent upstream operation.

**Target state.** `AbstractExternalManager` exposes an `idempotency_key(operation, args) -> str` method with a default implementation that hashes the requester id, operation name, and a canonicalized representation of the arguments. Subclasses override when an upstream demands a particular key shape. The bonded provider instance is given a hook to inject the key into the outgoing request — typically as a header, sometimes as a body field — depending on the upstream's contract. The rotation system caches the most recently emitted key per `(provider, operation, key)` tuple for the rotation duration so that a retry inside the same logical operation reuses the key rather than minting a new one.

**Implementation notes.** Idempotency is a property of mutating operations only; reads do not require it. Mark `*_via_provider` methods with a class-level `@idempotent` decorator so the rotation system knows whether to mint a key. The default canonicalization should be stable across Python invocations: sorted-keys JSON of primitives, with `Decimal` and `datetime` rendered in ISO form. Document the key lifetime and recommend providers expire entries after the rotation budget is exhausted to avoid unbounded memory growth.

**Acceptance criteria.** A provider author writing `create_charge_via_provider` annotates it `@idempotent` and the framework guarantees that any retry performed by the rotation system will carry the same key as the original attempt. Replaying a request from the client side with the same logical inputs produces the same key and the upstream returns the prior result rather than creating a duplicate.

**Dependencies.** Depends on Items 1 and 2 (typed errors and rotation policy). Cross-references Item 35 (outbox owns durable storage of the key per the refinement below).

**Refinement after audit.** The original target state described an in-memory cache "per `(provider, operation, key)` tuple for the rotation duration." That cache is correct as a write-through optimization but is not the canonical store: a retry that crosses a process restart must reuse the original key, and an in-memory cache cannot satisfy that. The canonical owner of the idempotency key is the **outbox row from Item 35** when the operation is enrolled in the outbox (typical for mutating calls), and the **request envelope** when the call is purely synchronous and never enrolls. The key is minted by the BLL caller at the mutation boundary — *not* by the rotation system — so it is written into the outbox row and the local row in the same transaction; the rotation system reads the key from the request context and never mints. The in-memory cache survives only as a same-process retry-budget optimization. This unifies Item 4 and Item 35 around one durable artifact and eliminates the "different lifetimes / different storage tiers" problem the audit surfaced.

---

## ~~Item 35~~ — ~~Outbox, dead-letter queue, and reconciliation primitive~~ ✅ DONE

**Severity:** Medium
**Scope:** New `outbox` and `dlq` tables, background drain service, reconciliation job pattern.
**Owner area:** External federation reliability.

**Purpose.** Today a fully exhausted rotation raises HTTP 500 and the request is lost. For fire-and-forget operations (audit shipping, non-critical notifications) this is unacceptable; for mutating operations whose local state has already been committed (a charge attempt that failed mid-rotation after the local order was created) this is dangerous. The framework needs a generic outbox/DLQ primitive that the federation system uses to guarantee at-least-once delivery and to surface unrecoverable failures for human attention.

This item is distinct from the outbox pattern referenced in Item 14, which uses an outbox specifically for mirror-on-create. Item 35 generalizes the primitive so any mutation can be deferred and retried.

**Current state.** No outbox. No DLQ. No reconciliation pattern.

**Target state.** Two tables: `outbox` records pending outbound operations with the operation type, the target provider/ability, the payload, the idempotency key, the deadline, and the retry count. `dlq` records exhausted-retry operations with the same metadata plus the final error and the operator-action status. A background `OutboxDrainService` (built on Item 28's `QueueConsumerService` flavor) reads pending outbox entries, attempts the operation through `RotationManager`, marks them complete on success, retries with backoff on transient failure, and moves to DLQ on exhausted retries.

A BLL mutation that wishes to use the outbox writes to its local table and to `outbox` in the same transaction (the "transactional outbox" pattern), guaranteeing local commit and pending external operation are atomic with each other. The drain service runs at a configurable cadence; reconciliation jobs sweep periodically to catch outbox entries stuck in flight (e.g. after a worker crash) and to verify external state matches local state for the long-tail of operations where neither system has perfect knowledge.

**Implementation notes.** The outbox table carries a unique-per-domain natural key so duplicate outbox entries from retried logic are absorbed; for idempotency-driven mutations the natural key is the idempotency key from Item 4, unifying the two systems and avoiding parallel dedup mechanisms. DLQ entries surface in an admin endpoint with replay and discard actions. Reconciliation is a per-extension concern; the framework provides the scheduling and the comparison primitive, but extensions implement the diff-and-converge logic for their domain.

**Acceptance criteria.** A user-create that needs to mirror to Stripe writes to `users` and `outbox` in one transaction; the drain service successfully creates the Stripe customer and clears the outbox entry; if Stripe is exhausted-failed, the entry moves to DLQ and an admin can replay it after fixing the underlying issue. A reconciliation job for the payment extension can detect a Stripe customer that exists upstream but is missing locally (or vice versa) and trigger the appropriate compensating action.

**Dependencies.** Depends on Item 28. Blocks Item 14.

**Refinement after audit.** This item is promoted from **Medium** to **High** and resequenced to land **before** Item 14. The original framing made Item 14 (Critical) depend only on Item 28 and treated the outbox as an in-implementation choice the author made — but Item 14's acceptance criteria assumes the outbox infrastructure exists ("a simulated external failure produces the documented compensating action automatically"). Without Item 35, Item 14 must ship its own mini-outbox that Item 35 then subsumes — duplicated implementation effort and a forced data migration. Item 35 now also owns the canonical idempotency-key store per the refinement on Item 4. The Critical-path summary continues to list Item 14 (not Item 35) as the gating item because Item 35 is upgraded to a hard prerequisite; the substantive blocker is the same.

---

## ~~Item 14~~ — ~~Mirror-on-create lifecycle primitive~~ ✅ DONE

**Severity:** Critical
**Scope:** New `@mirror_on_create` decorator, BEFORE-COMMIT hook integration, saga / outbox documentation.
**Owner area:** Extensions / external federation core.

**Purpose.** When a local `User` is created, who creates the corresponding Stripe customer, when, and how is the external ID written back into the local record atomically? The framework currently does not document this lifecycle. Every provider author will reinvent it, and they will get the failure modes wrong: orphaned external entities when the local commit rolls back; orphaned local rows pointing to nonexistent external entities when the external call fails after the local commit succeeded; inconsistent state when both succeed but the ID write-back fails. These are classic distributed-transaction failures and they are not safely solvable by ad-hoc code per provider.

**Current state.** Documented prose alludes to navigation via `external_payment_id` but does not specify how that ID arrives in the field, who writes it, or how partial failures are recovered.

**Target state.** A first-class `@mirror_on_create(local=UserModel, external=Stripe_CustomerModel, link_field="external_payment_id")` decorator that registers a BEFORE-COMMIT hook on the local manager. The hook creates the external entity, captures the returned ID, writes it into the link field, and ties the local commit to the external success. For providers that support transactional semantics (rare), a true two-phase commit is offered. For the common case (no upstream transactions), the framework offers two patterns and lets the author choose:

- **Roll-forward saga:** local create runs first, external create runs after a successful local commit, ID is written back in a separate commit. Failure of the external call schedules a compensating delete of the local row via a background service (Item 28). Documented as eventually consistent.
- **Outbox pattern:** local create writes both the row and an outbox entry in the same transaction; a background worker reads the outbox, performs the external create, and writes back the ID. Documented as the recommended default for non-trivial cases.

Symmetric `@mirror_on_update` and `@mirror_on_delete` decorators handle the corresponding lifecycle events.

**Implementation notes.** The compensating-delete service is provided by the framework, not authored per-provider. The outbox table is shared infrastructure. Authors choose the pattern declaratively; they do not write the orchestration code. The link field is updated within a row-level lock to prevent races with concurrent updates of the same local record. Failure scenarios are exhaustively documented with the failure mode, the resulting state, and the recovery path.

**Acceptance criteria.** A provider author writing `@mirror_on_create(local=UserModel, external=Stripe_CustomerModel, link_field="external_payment_id")` gets correct atomic-ish behavior across local commit, external create, and ID write-back without writing any saga orchestration code. A simulated external failure produces the documented compensating action automatically.

**Dependencies.** Depends on Items 28 (background service for compensating actions) and 35 (outbox is the canonical implementation of the recommended pattern; see Item 35's refinement).

**Refinement after audit.** The "Outbox pattern" sub-bullet is no longer one of two co-equal options the author chooses — it is the **default**. The roll-forward saga is retained only for the narrow case where the upstream cannot survive at-least-once delivery and the local rollback is cheaper than reconciliation. Item 35 owns the outbox table, the drain service, and the idempotency-key column (per Item 4's refinement); Item 14 contributes only the `@mirror_on_*` decorator and the BEFORE-COMMIT hook integration. The link-field write-back uses Item 53's `AdvisoryLock` primitive for serialization, not an ad-hoc row-level lock. The simulated-failure acceptance criterion is now testable end-to-end against Item 35's drain service rather than against a per-Item-14 mini-orchestrator.

---

## ~~Item 53~~ — ~~Advisory locking primitive~~ ✅ DONE

**Severity:** Medium
**Scope:** New `AdvisoryLock` abstraction, Postgres advisory-lock backend, Redis-based fallback, integration with quota decrement, outbox claim, and other critical sections.
**Owner area:** Database / concurrency.

**Purpose.** Several existing items reference the need to serialize concurrent operations on a logical resource: Item 19's atomic quota decrement, Item 35's outbox claim (one drain worker per entry), Item 14's row-level lock during link-field write-back, and an unspecified set of singleton-resource updates in extensions. Today the framework does not provide a canonical advisory-lock primitive, so each of these reaches for a different mechanism — Postgres `SELECT ... FOR UPDATE`, application-level `threading.Lock`, ad-hoc `UPDATE ... WHERE` patterns. The result is incorrect under concurrency in subtle ways: the application lock does not serialize across processes; the row lock holds a transaction open longer than necessary; the conditional update misses concurrent readers. A single canonical primitive closes the gap and pulls every critical-section caller onto one well-tested implementation.

**Current state.** No framework primitive. Each caller rolls their own locking.

**Target state.** `acquire_lock(name: str, timeout: Optional[float] = None) -> AdvisoryLock` is the canonical call, with `AdvisoryLock` usable as a context manager (`async with acquire_lock("outbox.claim:{entry_id}"): ...`). Two backends ship: the default uses Postgres's `pg_advisory_lock` family (transaction-scoped or session-scoped per declaration); the Redis backend uses a Redlock-style implementation for deployments that prefer to keep the database load down. Lock identifiers are namespaced by extension. The outbox drain (Item 35), the quota decrement (Item 19), and the link-field write-back (Item 14) all migrate to use this primitive rather than reinventing.

**Implementation notes.** Postgres advisory locks come in two flavors — session-level and transaction-level. The framework default is transaction-level for safety (auto-released on commit/rollback), with session-level available for explicit cross-transaction locks. The Redis backend uses a fencing token to detect lock-holder failure mid-operation. Lock acquisition has a configurable timeout and raises `LockTimeoutError` on exhaustion rather than blocking forever. The framework instruments lock acquisition with metrics (`advisory_lock_wait_seconds{name}`, `advisory_lock_held_seconds{name}`) so contention is visible to operators.

**Acceptance criteria.** A BLL author writing a critical section calls `async with acquire_lock("payment.subscription_renew:{user_id}"): ...` and gets cross-process serialization without choosing a backend or writing lock-management code. The outbox drain claims an entry under this primitive and never produces concurrent processing of the same entry. Lock-wait metrics are visible in the standard observability backends (Item 34).

**Dependencies.** Cross-references Items 14, 19, 34, 35.

---

## ~~Item 69~~ — ~~Distributed counter primitive~~ ✅ DONE

**Severity:** High
**Scope:** New `DistributedCounter` abstraction; Postgres-backed and Redis-backed adapters; integration with token bucket (Item 17), atomic quota decrement (Item 19), per-tenant fairness virtual-time scheduler (Item 57).
**Owner area:** Database / concurrency.

**Purpose.** Items 17, 19, and 57 each independently need a multi-process atomic counter with `INCR ... WHERE counter < limit RETURNING` semantics. Item 17 calls it a token bucket; Item 19 calls it `Quota.consumed` with `UPDATE ... WHERE consumed < limit RETURNING`; Item 57 calls it a virtual-time scheduler that tracks per-tenant work per consumer. Without a shared primitive each of the three reinvents the wheel — three different storage tiers, three different correctness proofs, three different failure modes under contention. The audit surfaced this as "three items independently invent we-need-a-shared-counter."

**Current state.** No framework primitive. Item 17 names "Redis" as the multi-instance answer in prose; Item 19 specifies `UPDATE ... WHERE ... RETURNING` semantics inline; Item 57 specifies WFQ in prose without a counter store.

**Target state.** `DistributedCounter(key, limit, period_key)` with three operations: `try_consume(amount) -> bool` (returns False if limit would be exceeded; atomic), `release(amount)` (credits an amount back; used by Item 43's pre-estimate/post-true-up), `reset(period_key)` (rolls to a new period). Two backends ship: the default uses Postgres `UPDATE ... WHERE consumed + ? <= limit RETURNING` against a `distributed_counter` table; the Redis backend uses Lua-scripted INCRBY-with-bound for deployments that prefer to keep the database load down. The primitive is the canonical mechanism for Items 17, 19, 57; each migrates to consume it rather than reinventing.

**Implementation notes.** The Postgres backend is the documented default because the framework already requires Postgres for RLS (Item 55) and advisory locks (Item 53). The Redis backend uses a small Lua script (atomic `GET`/`INCRBY` with rollback on overage) to avoid the `INCR-then-DECR` race that kills naive Redis counters. Per-counter metrics (`counter_consumed_ratio{name}`, `counter_overrun_total{name}`) tie into Item 34's tracing. The audit log captures every `try_consume` failure with the requester, the counter name, and the requested amount.

**Acceptance criteria.** A `RateLimit(rps=100, burst=20)` provider (Item 17) saturating at exactly 100 RPS across a 4-process deployment never exceeds the global rate. A `Quota` (Item 19) decrement under concurrent contention from 10 processes never permits the limit to be exceeded; the failing decrements raise `QuotaExhaustedError`. The Postgres and Redis backends produce identical correctness behavior under a stress test with 1000 concurrent consumers.

**Dependencies.** Independent. Consumed by Items 17, 19, 57.

---

## ~~Item 48~~ — ~~Graceful degradation contract~~ ✅ DONE

**Severity:** Medium
**Scope:** Provider declarations, rotation system, outbox (Item 35).
**Owner area:** Operational resilience.

**Purpose.** When a non-critical provider exhausts its rotation chain (e.g. the email provider is fully down), the right behavior depends on the operation. For a transactional email tied to a user signup, failing fast and surfacing the error is correct. For a marketing send, queuing for later delivery is correct. The framework today has no documented decision point for this — every provider author makes the choice ad hoc, and the choice is invisible to operators trying to understand why a request succeeded with degraded behavior versus failing outright.

**Current state.** Exhausted rotation raises HTTP 500. No degradation contract.

**Target state.** Each provider declares a `degradation_policy: ClassVar[DegradationPolicy]` per ability or per operation. Three modes: `fail_fast` (the current behavior; raise on exhaustion), `queue_and_retry` (write to outbox, return 202, drain via background service), `silent_drop` (log and return success; only valid for genuinely fire-and-forget operations like analytics). The default is `fail_fast` for correctness; opt-in to the others requires explicit declaration so degradation is visible in code review.

The `queue_and_retry` mode integrates with Item 35's outbox: the request returns immediately with a 202 and a tracking id; the outbox drain service performs the actual operation in the background; the client polls the tracking id for completion or subscribes to a webhook for the resolution.

**Implementation notes.** The choice between `fail_fast` and `queue_and_retry` is part of the API contract — clients calling a `queue_and_retry` operation must know to handle 202 responses, so the choice is reflected in the OpenAPI documentation, the SDK, and the GraphQL surface. Switching an operation between modes is a breaking change to its API and follows normal versioning (Item 39). **Silent-drop observability:** the `silent_drop` mode is dangerous (it returns success on actual failure) and easy to misuse; the framework emits a mandatory metric (`provider_silent_drop_total{provider, ability}`) on every silent-drop occurrence so operators can dashboard the rate and catch unintentional silent failures. A non-zero rate on an operation an operator did not expect to be silent-drop is the alert signal for misconfiguration.

**Acceptance criteria.** A provider author declaring `degradation_policy = QueueAndRetry()` on a `send_email` ability produces a 202 response on rotation exhaustion with a tracking id, with the operation completing once the upstream recovers. The same author declaring `degradation_policy = FailFast()` produces a 500 on exhaustion, the existing behavior.

**Dependencies.** Cross-references Item 35 (outbox), Item 39 (versioning).

**Status: REST + Outbox tracking + GraphQL conversion complete.** All four pieces have landed: (a) rotation-exhaustion dispatch in `RotationManager.rotate` consults `degradation_policy` and branches into outbox enrollment via `OutboxEntry` returning a `QueuedForRetry(tracking_id=...)` sentinel for `QUEUE_AND_RETRY`, or increments `_silent_drop_counter`+logs+emits `provider_silent_drop_total` and returns a `SilentDropped(...)` sentinel for `SILENT_DROP`; (b) the 202-tracking-id endpoint shape lives in `lib/Pydantic2FastAPI.py::_render_degradation_sentinel` which converts `QueuedForRetry` → HTTP 202 `{"status":"accepted","tracking_id":...}` and `SilentDropped` → HTTP 200 `{"status":"silent_dropped",...}`, plus a new `endpoints/EP_Outbox.py` exposing `GET /v1/outbox/{tracking_id}` for tracking-id polling; (c) OpenAPI annotations via `_degradation_responses_annotation(manager_class)` flag `QUEUE_AND_RETRY` operations with the 202 response model `QueuedForRetryModel`; (d) the Strawberry/GraphQL conversion is now in `lib/Pydantic2Strawberry.py` — typed `QueuedForRetryGQL` and `SilentDroppedGQL` Strawberry types, `render_degradation_sentinel_gql` converter, and a `degradation_aware` resolver decorator that converts both sync and async returns. Schema authors expose the union arm by declaring the resolver's return type as `Annotated[Union[Payload, QueuedForRetryGQL, SilentDroppedGQL], strawberry.union(...)]`. Tests: `lib/Pydantic2FastAPI_degradation_test.py` (7), `endpoints/EP_Outbox_test.py` (5), `lib/Pydantic2Strawberry_degradation_test.py` (8 — replaces the previous xfail placeholder).

---

# Group 8 — External Federation: Data Translation (Fields, Pagination, Search, Bulk, N+1)

The data-shape translation layer between our internal contracts and any external API. These items share the same pattern (a typed translator abstraction with provider-nominated implementations) and travel together.

## ~~Item 6~~ — ~~Field-mapping pipeline beyond 1:1 renames~~ ✅ DONE

**Severity:** High
**Scope:** `AbstractExternalModel.field_mappings`, `to_external_format`, `from_external_format`.
**Owner area:** Extensions / external federation core.

**Purpose.** The current `field_mappings: Dict[str, str]` only supports renaming a field from internal name to external name. Real external APIs require richer transformations: composite fields where internal `full_name` decomposes into external `first_name` plus `last_name`; nested unwrapping where `address.line1` is a dotted path; unit conversion where Stripe stores cents and we store `Decimal` dollars; enum remapping; timestamp format conversion; restructuring lists into id-keyed dictionaries. The example payment provider documentation already shows ad-hoc unit conversion inside `create_payment`, which is precisely the leak we want to prevent — that logic should live declaratively on the model, not procedurally inside provider methods.

**Current state.** `field_mappings: Dict[str, str]` performs rename only. Anything more complex is hand-coded inside `*_via_provider` methods and is invisible to the rest of the framework.

**Target state.** Replace `field_mappings: Dict[str, str]` with `field_mappings: List[FieldMapping]`, where `FieldMapping` is a typed transformer carrying the operation kind and its parameters. Built-in mappings ship with the framework: `Rename(internal, external)`, `Compose(externals=[...], internal, fn)`, `Decompose(external, internals=[...], fn)`, `DotPath(internal, external_path)`, `UnitConvert(internal, external, factor)` with helpers like `CentsToDecimal`, `EnumRemap(internal, external, mapping)`, `TimestampConvert(internal, external, format)`, and a `Custom(fn_to, fn_from)` escape hatch for everything else. `to_external_format` and `from_external_format` derive from this list mechanically and become non-overridable in normal cases.

**Implementation notes.** The mapping list must be reversible — every transformation declared for outbound (`to_external`) is matched by its inverse for inbound (`from_external`) so that round-trip integrity is preserved. The `Custom` escape hatch is the only one that requires the author to write both directions. Provide a runtime check at provider class load time that the mappings round-trip correctly against a Pydantic-generated example.

**Acceptance criteria.** A provider author declaring `field_mappings = [CentsToDecimal("price", "amount"), Compose(["first_name", "last_name"], "full_name", " ".join)]` gets correct two-way conversion without writing any conversion code in `*_via_provider` methods. Round-trip tests run automatically as part of the provider's class-level test suite.

**Dependencies.** Independent.

---

## ~~Item 7~~ — ~~Pagination homogenization~~ ✅ DONE

**Severity:** High
**Scope:** `AbstractExternalAPIClient.list`, `AbstractExternalManager.list`, `to_external_query_format`, every external provider supporting list operations.
**Owner area:** Extensions / external federation core.

**Purpose.** Internal list endpoints speak offset and limit. External APIs vary widely: Stripe uses cursor pagination via `starting_after`; SendGrid uses page tokens; Salesforce returns `nextRecordsUrl`; GitHub returns `Link` headers; BigQuery returns page tokens; some legacy APIs use offset. Without a homogenization layer, every provider author must hand-translate between our offset/limit contract and the upstream's cursor scheme, and clients calling our list endpoints over external data will paginate incorrectly because the offset semantics are silently lost.

The user has confirmed: this is **not** backed by a new database table. Pagination state is round-tripped through an opaque token carried by the client. The framework remains stateless with respect to pagination cursors.

**Current state.** `to_external_query_format` shows an `offset → starting_after` example only; no abstraction; the internal list contract assumes offset semantics universally.

**Target state.** Define `AbstractPaginator` with concrete subclasses `OffsetPaginator`, `CursorPaginator`, `PageTokenPaginator`, `LinkHeaderPaginator`. Internal list contracts continue to expose offset and limit to clients; the paginator translates to and from the provider style and round-trips opaque cursor state in a `next_token` field on `ResponsePlural`. The `next_token` is a base64-encoded JSON envelope `{provider_cursor, page_size, query_hash}`, where `query_hash` is a tripwire — if the client sends a `next_token` whose embedded query hash does not match the new query parameters, the framework returns `400 invalid_pagination` rather than producing misaligned results.

For cursor-only providers, arbitrary jump-to-offset (e.g., "give me page 100 directly without walking 1–99") is not supported and not solvable in general; document it as such. The typed `Pagination` model exposes `supports_random_access: bool` so clients can adapt their UI to the limitation.

**Implementation notes.** No database table. The `next_token` is opaque to clients and self-describing to the framework. Each paginator subclass owns a single contract: convert the internal offset/limit request to upstream parameters on the way out, and convert the upstream response cursor back to a `next_token` on the way back. Providers nominate their paginator via a class variable, e.g. `paginator: ClassVar[Type[AbstractPaginator]] = CursorPaginator`. The cross-cutting `LIST` test harness (Item 29) exercises both styles.

**Acceptance criteria.** A provider author declaring `paginator = CursorPaginator` gets correct list pagination over Stripe-style upstreams without writing any cursor-handling code. Clients calling our list endpoints receive consistent offset/limit + `next_token` responses regardless of the underlying provider's pagination style. A query-hash mismatch is rejected with a typed error.

**Dependencies.** Cross-references Item 29 (unskip pagination tests in core list endpoints).

---

## ~~Item 8~~ — ~~Search DSL translation~~ ✅ DONE

**Severity:** High
**Scope:** `AbstractExternalAPIClient.search`, `AbstractExternalManager.search`, search transformers; every external provider supporting search.
**Owner area:** Extensions / external federation core.

**Purpose.** Internal search uses typed search models — `StringSearchModel`, `NumericalSearchModel`, `DateSearchModel`, `BooleanSearchModel` — each carrying typed operators. External APIs all express search differently: Stripe filter strings, SendGrid filter expressions, Salesforce SOQL, GraphQL filter objects, MongoDB-style operator dicts, simple key-value query parameters. Without a translation layer, every provider author must hand-roll the conversion from our typed search model to the provider's DSL, and the conversion logic is invisible to hooks, tests, and OpenAPI introspection.

This problem mirrors Item 7 in shape and is solved with the same pattern: a translator abstraction with provider-specific implementations.

**Current state.** External managers may register custom search transformers, but the translation from typed search models to upstream DSLs is undefined. `to_external_query_format` handles direct field renaming only.

**Target state.** Define `AbstractQueryDSLTranslator` that consumes the typed search models and emits the provider's DSL. Ship reference implementations: `StripeSearchTranslator`, `SOQLTranslator`, `GraphQLFilterTranslator`, `MongoStyleTranslator`, `KeyValueTranslator`. External managers nominate one via `query_translator: ClassVar[Type[AbstractQueryDSLTranslator]]`. The translator has full access to the search model fields, the operators (`inc`, `exact`, `gt`, `lt`, etc.), and the cross-field combinators (`AND`, `OR`).

**Implementation notes.** The translator is purely outbound — it takes a search model, returns a provider-shaped query payload. The inverse direction (parsing a provider's stored query back into our search model) is not required and is not in scope. A translator may declare unsupported operators via a `supported_operators` class set; the framework rejects the search at request time with a typed error rather than silently dropping the operator.

**Acceptance criteria.** A provider author declaring `query_translator = StripeSearchTranslator` can call `manager.search(name={"inc": "Premium"}, is_active=True)` and have it translate to a correct Stripe filter string without writing translation code. A search using an unsupported operator surfaces a clear error naming the operator and the provider.

**Dependencies.** Independent. Same shape as Item 7.

---

## ~~Item 12~~ — ~~Bulk endpoint expression for upstream APIs that support them~~ ✅ DONE

**Severity:** Medium
**Scope:** `AbstractExternalManager.batch_*`, optional `batch_*_via_provider` slots.
**Owner area:** Extensions / external federation core.

**Purpose.** Many upstream APIs offer dramatically more efficient bulk endpoints than serially calling their single-resource endpoints. Stripe's batch API, SendGrid's bulk send, BigQuery's streaming insert, and Salesforce's Composite API all reduce round-trip count and cost by an order of magnitude or more. Today `AbstractExternalManager.batch_create` and `batch_update` loop through individual `*_via_provider` calls because there is no slot for a true bulk implementation. For a provider that must batch ten thousand emails per minute, this is the difference between practical and impractical.

**Current state.** Batch operations on external managers loop over single-resource provider methods.

**Target state.** Optional `batch_create_via_provider`, `batch_update_via_provider`, `batch_delete_via_provider` slots on `AbstractExternalModel`. When present, `AbstractExternalManager.batch_*` delegates to the bulk implementation; when absent, it falls back to the loop. The bulk methods return a per-item success-or-error structure so that partial failures are recoverable — a thousand-item batch where seventeen items fail must surface those seventeen failures with their individual error details, not collapse to a single batch-level error.

**Implementation notes.** The per-item error structure mirrors the typed exception hierarchy from Item 1: each failed item carries one of the typed external errors and the framework's downstream consumers (hooks, response envelopes) handle them as if they had been raised individually. The bulk path also participates in the idempotency primitive (Item 4) — the batch-level idempotency key is derived from the per-item keys, and replay produces the same result.

**Acceptance criteria.** A SendGrid provider author can implement `batch_create_via_provider` for bulk send, send a thousand emails in one upstream call, and have seventeen rejected addresses surface as seventeen individual `InvalidInputExternalError` results rather than a single batch failure.

**Dependencies.** Depends on Items 1, 4.

---

## ~~Item 9~~ — ~~N+1 prevention through `include` for external navigation~~ ✅ DONE

**Severity:** High
**Scope:** External navigation properties (`create_external_reference_model`), the `include` query parameter handling, batched resolvers.
**Owner area:** Extensions / external federation core.

**Purpose.** External navigation properties are powerful — `user.stripe_customer` resolves to a real Stripe customer record — but the naive resolver triggers one upstream call per access. Iterating over a list of users and accessing `user.stripe_customer` on each performs N upstream calls. The framework already has the `include` parameter for exactly this kind of eager-loading control; we need to wire it through the external navigation path so that opting into navigation expansion produces one batched upstream call instead of N individual ones.

**Current state.** Navigation properties resolve lazily on first access with per-instance caching. There is no batched resolver and no documented behavior when `include` is omitted.

**Target state.** Navigation properties on external references behave according to the `include` parameter. When the field is named in `include`, the framework collects the external IDs across the result set and makes a single upstream call (e.g., `list_via_provider(ids=[...])`) to fetch all referents in one round trip. When the field is not in `include`, accessing it returns `None` or, if the consumer prefers strict behavior, raises a typed `NavigationNotIncludedError` rather than silently issuing the upstream call.

**Implementation notes.** The framework already has machinery to plumb `include` through endpoints and resolvers; the work is to extend it through the external reference factory. The batched resolver is a per-request cache keyed by `(external_model, set_of_ids)`. For providers whose upstream supports list-by-id, this is one call; for providers that do not, we fall back to issuing concurrent individual calls bounded by the provider's rate limit (Item 17). The strict-vs-lenient behavior on omitted `include` is configurable per deployment with a sensible default; we recommend strict in development and lenient in production.

**Acceptance criteria.** A list endpoint over fifty users, called with `include=stripe_customer`, performs at most one upstream Stripe call rather than fifty. A list endpoint without `include` does not issue any upstream calls until and unless `user.stripe_customer` is accessed (or raises if strict-mode is enabled).

**Dependencies.** Cross-references Item 17 (rate limiting).

---

# Group 9 — Schema Drift Detection & External API Test Contract

How we keep our view of an upstream's wire format honest, and how the no-mock pillar reconciles with the external-call reality.

## ~~Item 11~~ — ~~Schema drift and contract testing~~ ✅ DONE

**Severity:** Medium
**Scope:** Per-external-provider schema snapshot files, CI diff job, optional cron-driven refresh.
**Owner area:** External federation testing.

**Purpose.** External APIs change beneath us. New fields appear, existing fields are deprecated, error envelopes shift, enum values are added. Without a contract-testing discipline, the first signal we get of a breaking upstream change is a production incident. The federation system needs an active early-warning mechanism so that schema drift is detected at CI time, not at runtime.

**Current state.** No schema snapshots, no contract tests, no drift detection.

**Target state.** Each external provider that targets an upstream with a published OpenAPI specification declares the spec URL via a class variable: `openapi_url: ClassVar[Optional[str]]`. A canonical snapshot of that spec lives in the repository under `src/extensions/{name}/contracts/{provider}.openapi.json`. A CI job periodically fetches the live spec, runs a structural diff against the committed snapshot, and fails on breaking changes (removed fields, narrowed types, removed enum values). Non-breaking diffs (added fields, widened types) produce a warning and a pull request that updates the snapshot.

For providers whose upstreams do not publish a machine-readable spec, the snapshot is generated from real recorded responses — one captured response per `*_via_provider` method, taken against a sandbox account, normalized to remove instance-specific identifiers. The CI diff uses structural comparison rather than literal equality.

**Implementation notes.** The OpenAPI fetcher must handle authentication for spec URLs that require it (GitHub's spec, for example). The diff tool should be a well-known one — `oasdiff` for OpenAPI, a custom structural diff for response samples. The cron cadence is per-provider; high-velocity upstreams (Stripe) get daily, slow-moving upstreams (legacy SOAP wrappers) get weekly. Document a clear escalation path when CI fails: triage the diff, decide whether to absorb it as a non-breaking change or to update the provider, and update the snapshot.

**Acceptance criteria.** A breaking change in Stripe's OpenAPI spec produces a CI failure on the next run with a clear diff summary. The snapshot files are reviewable artifacts in pull requests.

**Dependencies.** Cross-references Item 15 (test contract).

---

## ~~Item 15~~ — ~~External API test contract reconciled with the no-mock pillar~~ ✅ DONE

**Severity:** High
**Scope:** Pytest markers, fixture scaffolding for external sandbox credentials, CI configuration.
**Owner area:** Testing infrastructure.

**Purpose.** The framework's most-emphasized testing principle is no mocks: real implementations, real databases, real server connections, no exceptions. The external federation documentation, however, instructs authors to "mock external API calls in tests using provider rotation patterns." Both cannot be true. The user has clarified the intent: tests that exercise real upstream calls run against sandbox or test-mode credentials when they are present, and are programmatically marked expected-to-fail when they are not. A small, separate set of smoke tests covers the no-keys case explicitly — verifying that configuration-failure paths surface correctly. There are no mocks anywhere.

**Current state.** Contradiction between two documented testing philosophies. No standardized pytest markers. No documented fixture for sandbox credentials.

**Target state.** Two pytest markers govern external-API tests. `@pytest.mark.external_api(provider="stripe")` marks a test that requires real (sandbox) credentials; if the credentials are absent, the test is automatically xfailed with a clear skip reason naming the missing credentials; if they are present, the test runs end-to-end against the sandbox. `@pytest.mark.external_smoke` marks a test that deliberately runs without credentials and asserts that the framework's configuration-failure paths surface correctly. Real tests use sandbox or test-mode credentials issued by the upstream specifically for this purpose; production credentials are never permitted in the test environment.

The documented "mock the rotation system" example in `PRV.External.md` is removed in favor of the marker-driven approach. The framework optionally supports `PRV_Fake_*` providers as an opt-in for offline CI, but the default and recommended path is sandbox credentials.

**Implementation notes.** The xfail reason names the specific environment variables that, if set, would unblock the test. CI in branches without secrets runs the smoke set only; CI in protected branches with secrets runs both. Document a sandbox credential management policy: who owns the keys, how they rotate, how they are scoped to test-only operations.

**Acceptance criteria.** Running the test suite without any external credentials configured produces xfail-pass results for all `external_api`-marked tests with a clear reason. Running the same suite with sandbox credentials configured produces real-pass results from real upstream calls. No test in the suite uses a mock for an external API.

**Dependencies.** Cross-references Item 11 (schema-drift snapshots produced from real recorded responses).

---

## ~~Item 72~~ — ~~Remove BLL/extension test mocks that violate the no-mock pillar~~ ✅ DONE

**Severity:** High
**Scope:** `extensions/database/EXT_Database_test.py`, `extensions/AbstractExtensionProvider_test.py`, any other BLL or extension test file that uses `unittest.mock`.
**Owner area:** Testing infrastructure.

**Purpose.** `AGENTS.md` is unambiguous: "Mock business logic or integration tests" is forbidden — "No mocking of BLL managers, endpoint handlers, or extension functionality — use real database instances and real server connections instead. Unit testing of pure utility functions (SQL filter generation, permission calculations) may use mocks for isolation." Item 15 reconciles the no-mock pillar with external-API testing via sandbox credentials. The same pillar applies to *every* BLL and extension test, and is currently violated. Tests that mock the very surface they are exercising do not catch the bugs that real wiring would surface, and they pass green while the real surface is broken.

**Current state.** Verified violations: `extensions/database/EXT_Database_test.py:7,225-271` imports `AsyncMock`, `MagicMock`, `@patch` and uses `AsyncMock(return_value="SQL executed successfully")` to mock `EXT_Database.root.rotate` across at least five tests. `extensions/AbstractExtensionProvider_test.py:729` patches `importlib.import_module`. The pattern is "mock the rotation system" — the same example `Item 15` calls out as removed from `PRV.External.md`, but with no enforcement against the BLL/extension test files where it currently exists.

**Target state.** Every test under `src/extensions/` and `src/logic/` that imports from `unittest.mock` is rewritten to use a real implementation: real SQLite/Postgres for database tests, real `EXT_Database.root.rotate` against a `PRV_Fake_Database` (or sandbox credentials per Item 15), real `importlib` resolution against a fixture extension. Tests that genuinely test a pure utility function may retain their mocks but are tagged `@pytest.mark.unit` and live alongside the function they test. A linter rule (`flake8`-plugin or `ruff`-plugin or grep-in-CI) rejects new `from unittest.mock import` lines under `src/extensions/` and `src/logic/`.

**Implementation notes.** Replacing the database-extension mocks with a real `PRV_Fake_Database` (Item 15's recommended offline-CI pattern for external providers) covers most of the violations. The `importlib.import_module` patch in `AbstractExtensionProvider_test.py` is replaced by a fixture-extension under `tests/fixtures/extensions/test_extension/` that the test loads through the real registry. The CI rule is the enforcement: it is one regex away and pays for itself the first time it catches a regression.

**Acceptance criteria.** No file under `src/extensions/` or `src/logic/` imports from `unittest.mock` outside of `@pytest.mark.unit`-tagged tests of pure utility functions. The CI lint rule rejects new violations. The previously-mocking tests pass against real implementations.

**Dependencies.** Cross-references Item 15 (the no-mock pillar's external-API counterpart). Independent of other items.

---

# Group 10 — Inbound Eventing: Webhooks and Streaming

The bidirectional half of the federation story. Item 5 is the typed inbound mount; Item 13 is the long-lived connection counterpart. Both fan into the same hook bus that internal mutations fire.

## ~~Item 5~~ — ~~Inbound webhook handler infrastructure~~ ✅ DONE

**Severity:** Critical
**Scope:** New endpoint mount, new decorator, new registry; every external provider that emits webhooks.
**Owner area:** Extensions / external federation core.

**Purpose.** The federation story is currently outbound-only: we call upstream providers. Real federations are bidirectional. Stripe pushes `customer.updated`, `invoice.payment_failed`, `charge.refunded`. SendGrid pushes bounce, deferral, and click events. Twilio pushes delivery receipts. GitHub pushes repository events. Without an inbound primitive, the framework's claim that "an external resource looks like a local row" breaks the moment that resource changes outside our process — our cached representation diverges, our hooks never fire on external state changes, and downstream consumers see stale data.

A previous iteration of the framework had a `/webhook` endpoint backed by a global dict that providers and extensions wrote into. That mechanism was lost during refactoring and needs to be restored as a proper, typed system.

**Current state.** No inbound webhook support exists in the documented framework. Extensions cannot receive upstream events.

**Target state.** A canonical mount at `/webhook/{extension}/{provider}` and `/webhook/{extension}/{provider}/{event}` registered automatically when an extension or provider declares an inbound handler. A `@webhook_handler(EXT_Payment, provider="stripe", event="customer.updated")` decorator registers static methods into a typed registry. Providers expose `verify_signature(headers, body) -> bool` as a required method on `AbstractStaticProvider` for any provider that registers webhook handlers; signature verification is mandatory and the request is rejected before dispatch if it fails. Inbound events fan into the same hook bus that internal mutations fire, so an external `customer.updated` triggers the AFTER-update hook chain on `Stripe_CustomerManager` exactly as if the change had been originated locally.

**Implementation notes.** The webhook mount must be public (no authentication middleware) but performs cryptographic verification per provider. Replay protection is per-provider (timestamp window, nonce cache) and is part of the provider's `verify_signature`. Event routing uses the event field after signature verification; an unrecognized event logs a warning and returns 200 (rejecting with non-2xx tells the upstream to retry, which we don't want for events we deliberately ignore). Handlers run in the request thread for fast events and may schedule to a background service (Item 28) for heavy events. The handler signature receives a `WebhookContext` with the parsed payload, the originating provider instance, and the requester resolution chain (most webhooks resolve to a system requester for audit purposes).

**Acceptance criteria.** A provider author registers a handler for `customer.updated`, an upstream test event hits our `/webhook/payment/stripe/customer.updated` mount, signature verification passes, the handler runs, and the AFTER-update hook chain on `Stripe_CustomerManager` fires with the new state. A signature failure produces a 401 without invoking the handler. An unrecognized event produces a 200 with a logged warning.

**Dependencies.** Independent.

---

## ~~Item 13~~ — ~~Streaming, websocket, SSE, and long-poll support~~ ✅ DONE

**Severity:** Medium
**Scope:** Service interface broadening (see Item 28), new `StreamingService` abstraction, integration with the hook bus.
**Owner area:** Services + external federation.

**Purpose.** Real-time integrations — Stripe's events firehose, Slack RTM, Kafka-style consumers, websocket-driven chat upstreams — do not fit the request/response shape of `*_via_provider`. They are long-lived, asynchronous, and stateful. The framework's current service interface, designed for perpetual time-based loops (a thirty-second agentic loop, for example), is the correct conceptual home but must be broadened to accommodate connection-oriented work.

**Resolution.** `StreamingService` (and `ConsumerStreamingService` / `ProducerStreamingService` aliases) in `src/serverframework/logic/AbstractService.py` already provided the connect/iter/on_message/disconnect skeleton with exponential-backoff-with-jitter reconnect. Item 13's completion adds the three pieces that were missing: a typed `streaming_handler(extension, provider, event)` registry mirroring Item 5's `webhook_handler` so the same canonical event flows through whether it arrives via webhook or streaming firehose; `state_store`-backed cursor / subscription-token persistence (loaded on `__init__`, persisted after each successful `fan_out`); and `stop_and_drain()` with a `_drained: asyncio.Event` that waits up to `drain_period_seconds` for in-flight `on_message` to finish before cancelling. Cross-process fan-out is opt-in via an injected `event_bus` argument (Item 42 `AbstractEventBus`-shaped).

**Acceptance criteria — met.** A `StreamingService` author declares `extension_name` / `provider_name` on the subclass, overrides `classify(message)`, and writes connection plumbing only for the upstream-specific protocol. Reconnection, drain, cursor persistence, and handler dispatch are framework-owned. Tests in `src/serverframework/logic/AbstractService_streaming_test.py` cover registry registration, wildcard fallback, async/sync handler dispatch, cursor load+persist, event-bus publish, the `extension_name`/`provider_name` requirement, default `on_message` -> `classify` -> `fan_out` integration, drain-within-deadline, and drain-timeout cancellation.

**Dependencies.** Depends on Item 28 (broadened service interface, ✅). Cross-references Item 5 (shared `webhook_handler` registry shape, ✅) and Item 42 (cross-process event bus, ✅).

---

# Group 11 — Endpoints, Routing, SDK Generation

The auto-generated REST surface, custom routes that participate in the same generation pipeline, version side-by-side, and the SDK that derives from all of them.

## ~~Item 29~~ — ~~Unskip pagination, filtering, and search-pagination tests in core endpoints~~ ✅ DONE

**Severity:** Medium
**Scope:** `EP.Test.md` skip list, the core list and search endpoints, the auto-generated test matrix.
**Owner area:** Endpoints / testing.

**Purpose.** `EP.Test.md` currently lists three skipped tests: `test_GET_200_list_pagination`, `test_POST_200_search_pagination`, `test_GET_200_filter`. The corresponding behavior is documented as "not yet implemented." The user has confirmed these should all be unskipped, with the underlying behavior implemented. Pagination and filtering are core list-endpoint expectations; an extension author depending on a list endpoint that does not paginate or filter is forced to either reimplement the missing behavior in their own endpoint (touching the core conceptually) or to live with the limitation.

**Current state.** Three tests skipped. Behavior not implemented.

**Target state.** Pagination, filtering, and search-pagination are implemented in the auto-generated CRUD layer for every `RouterMixin`-tagged manager. The skipped tests are unskipped and pass. The implementation is wired through the same pagination abstraction defined in Item 7, so internal and external pagination share one model and one set of tests.

**Implementation notes.** Filtering uses the existing typed search models, including the operator vocabulary that Item 8 will need to translate for external providers. Pagination uses the offset/limit + `next_token` pattern from Item 7. Field selection (`fields` parameter) continues to work alongside the new pagination and filtering parameters.

**Acceptance criteria.** All three skipped tests pass with real behavior. Every list endpoint produced by `RouterMixin` supports pagination, filtering, and search-pagination uniformly. The OpenAPI surface reflects the new parameters.

**Dependencies.** Cross-references Item 7 (shared pagination abstraction) and Item 8 (search transformer translation reuses the typed search models).

---

## ~~Item 39~~ — ~~Per-resource endpoint versioning~~ ✅ DONE

**Severity:** Medium
**Scope:** `RouterMixin`, route generation, OpenAPI generation, SDK generation (Item 25).
**Owner area:** Endpoints.

**Purpose.** Endpoint paths today are hardcoded `/v1/<resource>`. There is no documented mechanism for promoting a resource to `/v2/`, no side-by-side dual-version routing, no deprecation contract. Any non-trivial framework will eventually need to ship a v2 of some resources while v1 remains supported; the current shape gives no path to that without core changes — which is the situation the framework most wants to avoid.

**Current state.** `prefix: ClassVar[str] = "/v1/<resource>"` is hand-set per manager. No version field. No per-version routing.

**Target state.** `RouterMixin` exposes `version: ClassVar[str] = "v1"` (default) and the prefix is computed from the version plus the resource name. Multiple managers may register the same resource at different versions; both versions route concurrently, both appear in OpenAPI, both are present in the generated SDK as version-suffixed methods. A `deprecated_in: ClassVar[Optional[str]]` and `sunset_in: ClassVar[Optional[str]]` carry the deprecation contract — the framework adds `Deprecation` and `Sunset` HTTP headers automatically and emits a logged warning per-call after the deprecation date.

**Implementation notes.** Versions are alphanumeric tokens (`v1`, `v2`, `v2beta`, `v3rc1`); ordering for "latest" is lexicographic with documented quirks for prereleases. The SDK generator (Item 25) emits versioned method names. REST gets path-versioned routes; GraphQL gets field-level `@deprecated` and `@sunset` directives by default, since real GraphQL evolution is field-level deprecation plus additive change rather than wholesale type renaming. Full type-version namespacing in GraphQL is opt-in for breaking renames where field-level deprecation cannot express the change. Persisted-query interaction: a persisted query bound to v1 continues to resolve against v1 types after v2 ships, so persisted-query stores must record the version they were registered against.

**Acceptance criteria.** A `UserManagerV2(AbstractBLLManager, RouterMixin)` declared alongside the existing `UserManager` produces a `/v2/user` route concurrently with `/v1/user`, both versions in OpenAPI, both versions in the SDK. Sunsetting `/v1/` emits the documented headers and logs.

**Dependencies.** Cross-references Item 25 (SDK generation).

**Status: ✅ DONE.** All four contract pieces are in place: (a) `RouterMixin.version` / `deprecated_in` / `sunset_in` ClassVars; (b) route-prefix derivation honors `version`; (c) SDK generator emits version-suffixed class names and method names per `sdk/SDKGenerator.py::_version_suffix_for` and `_method_version_suffix_for` (e.g., `UserV2SDK_generated.list_v2(...)`), with metadata comments `# version:` / `# deprecated_in:` / `# sunset_in:` in the emitted file header; (d) GraphQL field-level deprecation/sunset via `lib/Pydantic2Strawberry.py::_versioned_field` which sets `deprecation_reason` from `deprecated_in` and attaches a `@sunset` schema directive (custom `Sunset(date=...)`) when `sunset_in` is set. Tests: `sdk/SDKGenerator_test.py` (existing), `lib/Pydantic2Strawberry_versioning_test.py` (32 tests, all passing). Closed by verification.

---

## ~~Item 40~~ — ~~Custom-route contract with SDK, GraphQL, and test parity~~ ✅ DONE

**Status: ✅ DONE.** REST + GraphQL + SDK + test scaffolding all shipped. REST surface: `@custom_route` decorator captures HTTP method/path/input/output/auth/tags/`expose_in`; auto-generated REST routes, OpenAPI surface, and SDK methods all derive from it. GraphQL half: `register_custom_routes_to_graphql` walks `@custom_route` methods on every `RouterMixin`-tagged manager during schema build (`GraphQLManager._register_custom_routes_for_manager`) and registers each as a `FieldContribution` against the Item 46 contribution registry. HTTP `GET` → query, anything else → mutation, with explicit `graphql_kind="query"` / `"mutation"` overrides. Routes whose `expose_in` excludes `GRAPHQL` are skipped. The resolver wraps the bound method, validates the input against the spec's `input_model`, coerces dict returns into the `output_model`, and is registered idempotently per `(manager_cls, method_name)` so schema rebuilds don't re-emit duplicate fields. **Test scaffolding generation (now landed):** `lib/CustomRoute.py::generate_test_scaffold(manager_cls)` emits baseline test source code per `@custom_route` method — auth (unauthenticated → 401), validation (malformed body → 422; POST/PUT/PATCH only), happy path (typed input → typed output). Path params (`{id}` style) are substituted with deterministic scaffold values. Body fields are populated with deterministic defaults (`scaffold-{name}` for str, `1` for int, `1.0` for float, etc.) so the scaffold is byte-stable across regenerations per Item 25's deterministic-regeneration contract. `write_test_scaffold(manager_cls, scaffold_dir, *, overwrite=False)` writes to `{ManagerName}_custom_routes_scaffold_test.py`; the protective default is `overwrite=False` so codegen passes do not clobber author-edited scaffolds. `has_existing_scaffold(...)` lets callers check before regenerating. Tests: `lib/CustomRoute_test.py` (51 total: existing 34 + 17 new covering scaffold emission for POST/GET/DELETE routes, validation-test skip for non-body methods, path-param substitution, scaffold-value population, byte-stability, empty-class no-op, header omission via `include_header=False`, write_test_scaffold lifecycle including overwrite protection).

**Severity:** Medium
**Scope:** `@custom_route` decorator (referenced but undefined), SDK generator (Item 25), GraphQL schema generator, auto-generated test scaffolds.
**Owner area:** Endpoints.

**Purpose.** `RouterMixin` auto-generates eight CRUD-shaped routes per resource. Many real endpoints are not CRUD — RPC-style action endpoints (`/v1/user/{id}/promote`), file uploads, batch imports, streaming, custom search shapes. Today these are added via `@custom_route` (referenced in the documentation as a decorator), but the contract for how custom routes participate in the rest of the framework is undefined. Most importantly, a custom route silently bypasses the auto-generated SDK, the auto-generated GraphQL surface, and the auto-generated test scaffold, which means every author writing a custom route must hand-author corresponding SDK methods, GraphQL fields, and tests — duplicating work that the framework was meant to do automatically.

**Current state.** `@custom_route` is referenced. Its contract for SDK, GraphQL, and test parity is not documented.

**Target state.** `@custom_route` is a typed decorator that captures everything the framework needs to extend the auto-generated surface beyond CRUD. The decorator declares: HTTP method, path (relative to the manager's prefix), input model (Pydantic), output model (Pydantic), authentication type, OpenAPI tags, and an optional `expose_in` set controlling whether the route appears in REST only, GraphQL only, SDK only, or all three. The SDK generator (Item 25) emits a method per custom route. The GraphQL generator emits a field (mutation by default, query for safe operations) per custom route. The test scaffolder generates a baseline test for each custom route with the standard auth, validation, and happy-path checks.

For genuinely RPC-shaped routes (no clear resource), the decorator can be applied to a free-standing class derived from a new `AbstractActionEndpoint` rather than to a `RouterMixin` subclass; the same generators handle it.

**Implementation notes.** The streaming case (Item 13) and the webhook case (Item 5) are handled by their own decorators (e.g. `@webhook_handler`, `@streaming_route`) rather than by `@custom_route`, but they share the same SDK/GraphQL/test integration story. Custom routes must declare typed inputs and outputs — untyped routes are rejected at registration to preserve the framework's typing guarantees. The GraphQL operation kind is inferred from the HTTP method by default — `GET` → query, anything else → mutation — with an explicit `graphql_kind` override for cases where the inference is wrong (a `POST` that is genuinely read-only, for example). Subscriptions are not produced from `@custom_route`; they require a stream output type and use the streaming decorator from Item 13.

**Acceptance criteria.** A custom route declared via `@custom_route(method="POST", path="/promote", input=PromoteRequest, output=PromoteResponse)` produces a corresponding SDK method, a GraphQL mutation, and a generated baseline test, without the author writing any of those concerns. A custom route lacking typed input/output is rejected at registration with a clear error.

**Dependencies.** Cross-references Items 5, 13, 25.

---

## ~~Item 25~~ — ~~Generated SDK handlers from BLL `RouterMixin`~~ ✅ DONE

**Severity:** Medium
**Scope:** New SDK generator, existing `SDK_Auth.py`, `SDK_Providers.py`, `SDK_Extensions.py` (potentially regenerated), the `AbstractSDKHandler` contract.
**Owner area:** SDK.

**Purpose.** Inspection of the SDK directory confirms that handlers are hand-written today: `UserSDK`, `TeamSDK`, and the rest each declare their `ResourceConfig` blocks manually. This is configuration-driven, but it is not generation-driven — every new BLL manager that declares `RouterMixin` must be matched by a hand-written SDK handler. The same Pydantic-and-`RouterMixin` source already powers REST routing, OpenAPI schemas, and the GraphQL surface; the SDK should derive from it equally. As-is, the SDK will silently drift behind extensions, and the framework's promise of "minimal supplementary code" leaks an SDK-handler-per-resource tax onto every extension author.

**Current state.** SDK handlers are hand-authored using `ResourceConfig`. New BLL managers require new SDK files.

**Target state.** A generator walks the registry of `RouterMixin`-tagged managers and emits an `SDKHandler` per resource at build time (or at runtime, with cached output). The generated handlers are deterministic and overwrite-safe — regeneration produces the same file, byte for byte. Hand-authored handlers remain for non-CRUD operations (login, logout, install_extension, etc.) and for resources that need behavior beyond the mechanical CRUD shape; these are clearly distinguished and live in separate files. The generator runs as part of the SDK build and is gated by CI.

**Implementation notes.** The generator should use the same source-of-truth as the OpenAPI generator and the REST router generator, so that all three are derived from one introspection pass over the registry. Field selection and pagination support, search transformers, and authentication overrides flow into the generated handlers automatically. The generated SDK is published per the existing `SDK.Publish.md` process. Authors writing custom handlers extend or override the generated ones via subclassing.

**Acceptance criteria.** Adding a new `RouterMixin`-decorated manager to an extension produces a corresponding SDK handler with full CRUD, search, and batch support, without any SDK code being written by the author. The generated SDK is byte-stable across regenerations.

**Dependencies.** Cross-references Item 64 (the SDK ships as a separate package; this generator is the canonical mechanism for emitting per-extension handlers into that package).

**Refinement after audit.** Item 64 (formerly P5) and this item described two different SDK-emission mechanisms — introspection of the in-process `RouterMixin` registry here, vs. cross-package Python entry-points in Item 64 — and Item 64 left the choice as an "open question." The choice is now closed in favor of introspection: Item 64's separate package ships the generator plus a build step that walks the consumer's installed-extension registry (the same registry Item 23/24/49 build) and emits handler files into the package. Entry points are not the discovery mechanism. This collapses the open question and aligns the SDK build with how the OpenAPI and GraphQL surfaces are generated from the same registry pass.

---

## Item 64 — Split SDK into its own pip package (formerly P5)

**Severity:** High
**Scope:** Move `src/sdk/` to its own package; new `pyproject.toml` for `serverframework-sdk`; build-time integration with Item 25's introspection generator; remove `sdk*` from the server's package include list.
**Owner area:** SDK / packaging.

**Purpose.** The SDK is a separate ship by design: it should be deployed by the server based on what extensions are loaded, not bundled with the server. Today it lives under `src/sdk/` and is included in the server's wheel, which couples SDK release cadence to server release cadence and forces consumers who only need typed REST clients to drag in fastapi, sqlalchemy, alembic, and the rest of the server's dependencies. The user's stated mental model is: "the SDK would be a separate ship that the server itself could deploy based on its extensions."

**Current state.** `src/sdk/` is included in the server's wheel. No separate package metadata. Handlers are hand-authored (see Item 25 for the generator that replaces them).

**Target state.** Move `src/sdk/` to its own package (still in this repo for now — monorepo with two `pyproject.toml` files is fine). Give it a `pyproject.toml` with `name = "serverframework-sdk"`. The extension-discovery mechanism is **Item 25's introspection generator** — at build time, the SDK package walks the consumer's installed-extension registry and emits per-resource handler files; entry points are not used for discovery (per Item 25's refinement). Remove `sdk*` from the server's `[tool.setuptools.packages.find]` include list once the split lands.

**Implementation notes.** The SDK package depends on the server's introspection-friendly subset (the `RouterMixin` registry, the Pydantic model export, the OpenAPI schema generation), but not on FastAPI's runtime or SQLAlchemy. Carve out a small "shared types" module (likely under Item 68's `serverframework.types`) that both the server and the SDK depend on, so that the SDK does not pull the full server install. The build step that emits handlers runs in the consumer's environment after `pip install serverframework[ext_payment]` so the generated handlers reflect the actually-installed extensions.

**Acceptance criteria.** `pip install serverframework-sdk` succeeds without installing fastapi or sqlalchemy. The generated handlers cover every `RouterMixin`-tagged manager in the consumer's installed extensions. SDK release cadence is decoupled from server release cadence (independent version numbers in the two `pyproject.toml` files).

**Dependencies.** Depends on Items 25 (introspection generator is the discovery mechanism) and 60 (rename moves `src/sdk/` out from under `serverframework/`).

---

## ~~Item 87~~ — ~~BLL-level field-selection and include test coverage (GitHub #10)~~ ✅ DONE

**Severity:** Medium
**Scope:** New abstract test cases under `src/logic/AbstractBLLTest.py` covering `load_only` and related-entity loading at the BLL layer, plus per-manager test files that consume them.
**Owner area:** Testing infrastructure / BLL.
**Tracking:** GitHub issue #10.

**Purpose.** GitHub #10 ("BLL Tests for Fields/Includes Need Written") notes that the framework tests the `fields` and `includes` query parameters at the EP layer (and they pass against the current branch — issues #12, #24, #25, #26 are closed-by-verification per the executive summary), but the BLL-layer logic that produces `load_only` SQL and lazy-loads related entities is not exercised abstractly. A drift in the BLL machinery that still passes through to a correct EP response (e.g., over-fetching fields and discarding them at serialization) ships green; only a slow-test or a database-load regression catches it later.

**Current state.** Verified at audit time: no test under `src/logic/` matches the pattern `def test_load_only` / `def test_includes` / `def test_fields` / `def test_field_selection`. EP tests pass; BLL tests do not exist for this surface.

**Target state.** `AbstractBLLTest` gains a fixture set and abstract test methods that, for each `RouterMixin`-tagged manager, exercise: `manager.get(id, fields=[...])` produces SQL with the correct `load_only` columns; `manager.list(includes=[...])` triggers exactly one query per included relation (joinedload/selectinload, not a per-row N+1); `manager.search(..., fields=[...])` honors the field set; combinations of `fields` + `includes` produce the expected SQL surface. Each per-manager test file inherits these abstract methods and binds them to its concrete manager. The tests assert on the generated SQL via SQLAlchemy's compiled-statement introspection, not on the response shape (which the EP tests already cover).

**Implementation notes.** SQL-shape assertions are brittle if written against literal query strings; use `sqlalchemy.event.listen` for `before_cursor_execute` to capture the rendered statements and assert against parsed AST or `inspect()`-driven column sets. The `joinedload`-vs-`selectinload` choice is per-relation and must be honored by the BLL — Item 9's batched resolver is the EP-side counterpart. This item closes GitHub #10; it does not invent new functionality, only adds the missing test coverage.

**Acceptance criteria.** Every concrete BLL manager has BLL-level tests for `load_only`, `includes`, and combined `fields`+`includes` against its model. A regression in the BLL field-selection layer is caught at the BLL test boundary, not at the EP boundary. GitHub #10 is closed.

**Dependencies.** Independent. Cross-references Item 9 (batched include resolver) and Item 41 (typed hook context — the test helpers benefit from the typed signature once Item 41 lands).

**Status: ABSTRACT MATRIX COMPLETE; per-manager binding inherited automatically.** `AbstractBLLTest` (`src/serverframework/logic/AbstractBLLTest.py:18`) keeps the original primitives `test_field_selection_pushes_load_only` and `test_field_selection_empty_list_skips_load_only`, and now also exposes the three matrix methods Item 87 promised: `test_load_only` (exercises `get`/`list`/`search` in one matrix and asserts a `load_only` option or `fields=` kwarg propagates to the DB layer for each), `test_includes` (asserts the BLL pushes a Load-family option — joinedload/selectinload/nested Load — for `include=[...]` so N+1 is prevented at the DB boundary), and `test_fields` (the combined `fields`+`includes` matrix asserting both options coexist on the DB call). Each method uses the existing `_capture_db_options` snapshot pattern so they execute without a populated test DB. Per-manager test files inherit `AbstractBLLTest` and bind these methods automatically — every concrete BLL manager test class participating in the CRUD ladder now has the matrix without per-class wiring. Tests: `logic/AbstractBLLTest_test.py` (20 new) directly covers the helper functions (`_candidate_relationship_names`, `_captured_includes_evidence`, `_captured_load_only_evidence`, `_capture_db_options`, `_is_load_option`) so a regression in the abstract layer surfaces without spinning up the full ladder. Closes GitHub #10.

---

# Group 12 — GraphQL: External Federation and Internal Composition

Item 16 covers federating *external* GraphQL upstreams into our schema. Item 46 covers how *our own* extensions compose into a single merged schema. They share machinery (selection-set push-down, batched resolvers, type-name conflict resolution) and travel together.

## ~~Item 16~~ — ~~Real GraphQL federation, not RPC wrapping~~ ✅ DONE

**Severity:** High
**Scope:** New `AbstractGraphQLProvider`, new `MergedSchemaRegistry`, schema introspection and stitching pipeline, generated resolvers, gateway integration. Extended in implementation to also cover REST upstream federation and bidirectional projection (REST→GQL via Pydantic lift, GQL→REST via FastAPI route generation) so the framework's "an external resource looks like a local row" claim holds for either inbound channel regardless of upstream wire format.

**Status.** Landed in `lib/Federation_GQL.py` (GraphQL upstream federation: Apollo v2 / stitching / namespaced styles, `SchemaTransformer` pipeline, `MergedSchemaRegistry`, `BatchedFieldResolver`, per-request `ResponseCache`, persistent cache hook, selection-set push-down, `build_proxy_resolver`, SDL→Pydantic lift, GQL→REST route projection), `lib/Federation_REST.py` (REST upstream federation: OpenAPI→Pydantic importer with `$ref`/`allOf`/`oneOf`/`enum` handling, `RESTUpstreamTransport`, `derive_external_models` for REST→GQL projection), `lib/Federation_Bootstrap.py` (lifespan-event entry point), `extensions/AbstractGraphQLProvider.py` (provider abstract). Tests in `lib/Federation_GQL_test.py`, `lib/Federation_REST_test.py`, and `extensions/AbstractGraphQLProvider_test.py` exercise the full pipeline against real in-process upstreams (no mocks). Documentation lives in `lib/LIB.Federation.md` with cross-references from `endpoints/EP.GQL.md` and `extensions/PRV.External.md`.

**Severity:** High
**Scope:** New `AbstractGraphQLProvider`, new `MergedSchemaRegistry`, schema introspection and stitching pipeline, generated resolvers, gateway integration.
**Owner area:** Endpoints / GraphQL layer.

**Purpose.** When an upstream API is itself GraphQL, the current `*_via_provider` shape forces us to wrap it as RPC. This loses every benefit GraphQL was designed for: selection-set push-down, fragment merging, depth control, and the ability for one GraphQL operation to traverse a federated graph in a minimal number of upstream round trips. Federating a GraphQL upstream through the rotation system in the current shape will over-fetch on every call and make any meaningful federation impractical. The user has confirmed this must be done properly now, not deferred.

**Current state.** No GraphQL federation. External upstreams that happen to be GraphQL are wrapped as RPC.

**Target state.** A real schema-stitching and federation system layered on top of Strawberry, with the following shape:

`AbstractGraphQLProvider(AbstractStaticProvider)` declares:

- `upstream_url: ClassVar[str]`
- `upstream_auth_strategy: ClassVar[Type[AuthStrategy]]` — ties into Item 10
- `federation_style: Literal["apollo_v2", "stitching", "namespaced"]` — Apollo Federation v2 if the upstream advertises `_service { sdl }`, schema stitching if the upstream supports introspection only, namespaced (last resort) if we wish to prefix all upstream types to avoid collisions
- `type_namespace: Optional[str]` — e.g. `Stripe_*` prefix when stitching or namespacing

A startup pipeline runs per provider instance, with results cached:

1. Introspect the upstream and fetch the SDL.
2. Run a `SchemaTransformer` pipeline supporting rename, prefix, hide-fields, mask-arguments, and override-resolvers transformations.
3. Register the transformed types into the local Strawberry schema via a new `MergedSchemaRegistry`.
4. Generate Strawberry resolvers that take the Strawberry `Info` object, reconstruct the upstream selection set from `info.selected_fields`, build a real GraphQL document with the original variables, forward it to the upstream, and return the parsed result. Selection-set push-down is the entire point — without it we have rebuilt RPC inside a GraphQL costume.
5. Cache the merged SDL hash. Refresh introspection on a TTL or on webhook-triggered invalidation (Item 5).

The Apollo Federation v2 path applies when the upstream is a compliant subgraph. We honor `@key`, `@external`, `@requires`, and `@provides`. Local types declare `@key` references to upstream entities (e.g. `extend type Stripe_Customer @key(fields: "id")`), and a local `User` type gains a `stripe_customer: Stripe_Customer` field that the gateway resolves through the federated subgraph. We use a real federation router — Apollo Router, Mercurius-style, or a slim Strawberry-based router — at the gateway layer.

The stitching path applies for upstreams with introspection but no Federation directives. We become the gateway. `MergedSchemaRegistry` holds the merged schema. Resolvers are generated, not handwritten. Cross-subgraph joins are handled by a `BatchedFieldResolver` that respects `include` (Item 9) — the resolver collects N requests for `user.stripe_customer` into a single upstream call.

Authentication and per-request context propagate via `AuthStrategy.headers_for(requester)` from Item 10. A per-request response cache is keyed by `(query_hash, variables_hash, requester_credentials_hash)` and dedupes identical sub-requests within a single outer GraphQL operation. A persistent cache (Redis-backed) is opt-in per type via a `@cache(ttl=...)` directive on the merged type.

Errors and partial data follow real GraphQL semantics: upstream `errors` arrays propagate through, attached to the affected fields, not raised globally — the partial-data, partial-errors contract is preserved for clients.

Tests run against real upstreams using sandbox keys per Item 15. Schema-drift detection per Item 11 becomes "introspect upstream → diff against committed snapshot → fail CI."

**Implementation notes.** The merged schema is computed once at startup and on TTL refresh, not per request. The `BatchedFieldResolver` is the bridge between the framework's `include` mechanism and the upstream's batching capabilities; for upstreams that do not support list-by-id, the resolver falls back to bounded-concurrency individual calls (subject to Item 17 rate limiting). The Federation router selection should be treated as a swappable component; Apollo Router is the default but we should not bake its assumptions into our code.

**Acceptance criteria.** A GraphQL upstream registered as an `AbstractGraphQLProvider` becomes part of our schema after startup; queries against our schema that select fields under the upstream's namespaced types are forwarded with the correct upstream selection set; selection-set push-down is verified by inspecting outbound requests; partial errors propagate per GraphQL spec; an Apollo Federation v2 upstream is composed correctly and supports cross-subgraph joins.

**Dependencies.** Depends on Items 9 (include/batched resolution), 10 (auth strategies), 11 (drift detection), 15 (test contract).

---

## ~~Item 46~~ — ~~GraphQL composition contract for our own extensions~~ ✅ DONE

**Severity:** Medium
**Scope:** Strawberry schema generation, multi-extension Query/Mutation/Subscription root composition, conflict resolution.
**Owner area:** Endpoints / GraphQL.

**Purpose.** This item is distinct from Item 16, which addresses federating *external* GraphQL providers into our schema. Item 46 addresses how *our own* extensions contribute to the GraphQL surface. The CRUD-on-managers path (auto-generated `query/list/create/update/delete` plus `*_created/_updated/_deleted` subscriptions per `RouterMixin` manager) was already complete; what was missing was the contract for everything that cannot be derived from a CRUD manager: custom non-CRUD root fields, subscriptions beyond CRUD events, custom types not backed by a manager, federation directives, DataLoaders for cross-extension navigation, and rebuild-on-install.

**Resolution.** A `GraphQLContributionRegistry` lives inline in `src/serverframework/lib/Pydantic2Strawberry.py` (the same module that does Pydantic→Strawberry conversion for CRUD; non-CRUD contributions are still Pydantic→Strawberry, just from a different source). The public surface is:

- `@gql_query` / `@gql_mutation` / `@gql_subscription` decorators with `return_type`, `args`, `description`, `extension_name` (auto-derived from `extensions.<name>` module path), `namespace=False`, `priority=50`.
- `@gql_type` for custom types backed by `@strawberry.type`, with optional `name` override and `federation_directives` tuple.
- `register_dataloader(name, batch_load_fn)` for per-request batching; resolvers retrieve the `RequestDataLoader` via `info.context["dataloaders"][name]`.
- `FederationDirective(name, args)` — allowed names: `key`, `external`, `requires`, `provides`, `shareable`, `inaccessible`, `override`, `tag`. Anything else raises at registration time.

Three-stage collision resolution mirrors `CollisionDetection.FieldCollisionError`: identical contributions (same callable + return type + args + kind) merge as one; non-identical raises `GraphQLCompositionCollisionError` at schema build time naming both extensions; `namespace=True` opts into `{extension}_{name}` (fields) or `{Extension.capitalize()}{TypeName}` (types).

`GraphQLManager` subscribes to registry mutations on construction; each registration logs a structured added/removed diff. The merged schema is recomputed on the next `create_schema()` call, or eagerly via `GraphQLManager.rebuild()` (Item 20 hot path). `RequestDataLoader.load(key)` defers and batches; parallel `load()` calls collapse into one `batch_load_fn(deduped_keys)` call on the next event-loop tick.

**Acceptance criteria — met.** Two extensions adding the same `query.search` with byte-identical resolvers merge as one; with different resolvers raise a startup error naming both. An extension ships a custom Strawberry type via `@gql_type` and it appears in the merged schema without touching core. Federation directives attached to a type are appended to `__strawberry_definition__.directives` so SDL emission preserves them. Cross-extension navigation routes through `info.context["dataloaders"][name]` so N parallel resolutions collapse into one batched fetch. Schema rebuilds on extension install/uninstall log added/removed root fields and types.

Tests in `src/serverframework/lib/Pydantic2Strawberry_contribution_test.py` cover identical-merge, non-identical collision, namespacing bypass, mutation/query independence, custom type registration + collision + namespacing, federation directive validation (allowed list), DataLoader batching + dedupe + sync/async + length-mismatch + non-sequence + global collision + idempotent re-registration, fresh-loader-per-request, rebuild subscriber notification, suspend/resume batching, unsubscribe, signature diff, and decorator → registry plumbing for queries/mutations/subscriptions/types/dataloaders.

**Dependencies.** Cross-references Items 16 (external federation, ✅), 20 (extension install/uninstall, ✅), 23 (collision detection pattern, ✅), 40 (custom routes — REST done, GraphQL emission via this registry), 42 (event bus for cross-process subscriptions, ✅).

---

## ~~Item 76~~ — ~~Reconcile documented WebSocket subscriptions with implementation~~ ✅ DONE

**Severity:** Medium
**Scope:** `Framework.md`, `endpoints/AbstractGQLTest.py`, Strawberry subscription routing.
**Owner area:** GraphQL / endpoints.

**Purpose.** `Framework.md:128` lists "Real-Time Subscriptions: WebSocket-based real-time data updates" as a core GraphQL feature. `endpoints/AbstractGQLTest.py:1007` says "Testing subscriptions requires WebSocket support" and the surrounding test treats `WebSocket not supported` as an *expected* status code (`AbstractGQLTest.py:1908`). The doc and the test asymmetry is a code-vs-doc divergence: either subscriptions ship and the test should pass, or they do not ship and the doc should not claim them. An extension author reading `Framework.md` and trying to add a Strawberry subscription discovers the gap only at runtime.

**Current state.** Documentation claims feature; tests assert the feature does not work; no clear status flag.

**Target state.** Decide and document. Option A: implement subscriptions — wire Strawberry's WebSocket subscription transport into the FastAPI app, update `AbstractGQLTest` to assert subscriptions actually deliver events, and remove the "WebSocket not supported" expected branch. Option B: drop the claim — remove the `Framework.md` line and add a clear "Subscriptions are deferred (see Item 13/46/76)" note pointing to the streaming-service item (Item 13) and the GraphQL composition item (Item 46), since real subscription support depends on both. The decision must be reflected in code, tests, and docs in a single change.

**Implementation notes.** Option A has real depth: subscription delivery across multiple framework instances requires Item 42 (event bus) for cross-process fan-out; in-process delivery alone is fine for single-instance dev deployments but ships a feature with a quiet correctness gap on multi-instance prod. The recommended landing order is Option B now (drop the false claim, point at Items 13/42/46) plus Option A as a follow-up gated on those three items. Either way, the test file's "WebSocket not supported is expected" branch is wrong long-term: it ossifies the absence-of-feature into the test contract.

**Acceptance criteria.** `Framework.md` and the GraphQL test suite agree on whether subscriptions are supported. If supported, a Strawberry subscription declared on a `RouterMixin` manager delivers events end-to-end through the test harness. If deferred, the docs explicitly say so and point at the gating items.

**Dependencies.** Cross-references Items 13, 42, 46.

---

# Group 13 — Hook System Hardening

Determinism, reliability defaults, and type safety for the cross-cutting hook bus that every extension touches.

## ~~Item 21~~ — ~~Deterministic hook ordering across extensions~~ ✅ DONE

**Severity:** Medium
**Scope:** `@hook_bll` decorator, hook registry sort logic, optional explicit ordering constraints.
**Owner area:** Hook system.

**Purpose.** When multiple extensions register hooks against the same manager method, today's priority field controls coarse ordering but does not break ties or express genuine ordering dependencies. "Run audit_logging after auth_mfa" is a common requirement that priority numbers can only express by tribal knowledge ("audit is priority 41 because mfa is priority 11"). Without a deterministic rule, hook execution order across extensions is, in practice, nondeterministic — which produces test flakiness, behavior drift across deployments, and bugs that are essentially unreproducible.

**Current state.** Priority field exists. Tie-breaks are unspecified. There is no relative-ordering mechanism.

**Target state.** A four-tier deterministic ordering rule:

1. **Explicit `before=[ExtName]` and `after=[ExtName]` constraints**, when given on the `@hook_bll` decorator, are resolved as a topological sort. Cycles in the explicit-constraint phase are detected and raised as a startup error.
2. **Priority number** breaks ties among hooks with no explicit constraints relative to one another. Ranges retain their semantic meaning (1-10 critical, 11-20 business logic, etc.).
3. **Extension name, alphabetical**, breaks ties among hooks with the same priority.
4. **Hook function name, alphabetical**, breaks remaining ties.

This produces a fully deterministic ordering across runs and lets extensions express real dependencies without fabricating priority numbers.

**Implementation notes.** The topological sort runs once per `(manager, method)` at startup and is cached. A cycle detection error names every extension involved so the developer can resolve it quickly. The `before` and `after` constraints accept extension names (not specific hook names) for ergonomics — finer-grained ordering is not needed and would invite tighter coupling between extensions.

**Acceptance criteria.** Two extensions registering hooks on the same method with no explicit constraint produce identical execution order across every run, deterministically. Adding `after=["auth_mfa"]` on an `audit_logging` hook produces the desired ordering regardless of priority. A circular constraint (`A after B`, `B after A`) fails fast at startup with a clear error.

**Dependencies.** Independent.

---

## ~~Item 22~~ — ~~Implement the documented `blocking=False` hook parameter~~ ✅ DONE

**Severity:** Medium
**Scope:** `@hook_bll` decorator, hook execution flow.
**Owner area:** Hook system.
**Tracking:** GitHub issue #38.

**Purpose.** The hook system documentation describes a `blocking=False` parameter for AFTER hooks: when set, exceptions inside the hook are logged and a metric is emitted, but the operation succeeds. This is the right design for non-critical AFTER hooks (audit, notification, analytics) — a notification-send failure should not roll back the user-create operation it observed. Today the parameter is documented but not implemented; authors must remember to wrap their AFTER hooks in try/except blocks, and the inevitable forgetfulness produces production failures from non-essential observers.

**Current state.** Documented as proposed syntax. Verified absent: `hook_bll` at `src/logic/AbstractLogicManager.py:181` accepts only `target`, `timing`, `priority`, `condition` — no `blocking` parameter. `BLL.Hooks.md:634-646` documents the parameter that does not exist.

**Target state.** `blocking=True` is the default for BEFORE hooks (security and validation must fail loudly). `blocking=False` is the default for AFTER hooks (observers should not break the operation). Both defaults are overridable per hook. Non-blocking exceptions log at the appropriate level, emit a configurable metric, and never propagate. A `non_critical_hook` decorator alias is provided as ergonomic sugar for `@hook_bll(..., blocking=False)`.

**Implementation notes.** The metric name should be queryable per hook so operators can identify a hook that is failing silently. Document the policy: blocking-by-default for BEFORE, non-blocking-by-default for AFTER, with the explicit override in either direction available.

**Acceptance criteria.** An AFTER hook that raises does not fail the operation when `blocking=False`. A BEFORE hook that raises does fail the operation by default. The metric for non-blocking failures is emitted and visible.

**Dependencies.** Independent.

---

## ~~Item 41~~ — ~~Type-safe hook context with `ParamSpec` and `Generic`~~ ✅ DONE

**Severity:** Medium
**Scope:** `HookContext`, `@hook_bll` decorator, hook signatures.
**Owner area:** Hook system / type safety.

**Purpose.** `HookContext.args` and `HookContext.kwargs` are typed `Any`. A hook author targeting `UserManager.create` has no compile-time guarantee that they are reading the right fields — they index into `kwargs` by string name and trust convention. A signature drift on the target method silently breaks every hook against it. With Python's `ParamSpec` and `Generic`, the hook context can be parameterized by the target method's signature, and the type checker can enforce that hooks read fields actually present on the target.

**Current state.** `HookContext.args: Any`, `HookContext.kwargs: Any`. No compile-time correlation with the target method.

**Target state.** `HookContext[P, R]` is generic over the target's `ParamSpec` and return type. The `@hook_bll` decorator preserves the binding so a hook function declared `def my_hook(context: HookContext[UserManager.create])` has access to `context.kwargs` typed as the keyword arguments of `UserManager.create` and `context.result: R | None` typed as the return value. Static analysis catches hooks that read fields not present on the target method and hooks whose `set_result` argument does not match the target's return type.

**Implementation notes.** Python's typing for this pattern is well-established (`ParamSpec`, `Concatenate`) and works under `mypy --strict` and `pyright`. The framework provides typed helpers for the common ergonomic cases: `context.kwarg("user_id")` returns the field's typed value rather than an `Any`. Document the migration: existing hooks are gradually re-typed; the framework continues to accept untyped hooks during the transition with a deprecation warning. The hook context exposes the **merged signature** of the target — i.e. the core method's signature plus any `@extension_model` field injections (Item 23) discoverable at registry-finalize time — so a hook reading an extension-injected field type-checks cleanly. Without this, ParamSpec would produce `Any` for injected fields and erase most of the win.

**Acceptance criteria.** A hook reading `context.kwargs["nonexistent"]` against `UserManager.create` produces a static-type error. A hook calling `context.set_result(WrongType())` produces a static-type error. Existing untyped hooks continue to work during the deprecation window.

**Dependencies.** Independent.

---

# Group 14 — Extension Model, Migrations, Hot Reload, Optional Dependencies

How extension-contributed schema, tables, and dependencies are validated, ordered, and reloaded. Field collisions, migration ownership, FK-aware ordering, hot reload, and observable optional-dependency state all belong to one extension-system surface.

## ~~Item 23~~ — ~~`@extension_model` collision detection at startup~~ ✅ DONE

**Severity:** Medium
**Scope:** `@extension_model` decorator, extension registry validation pass.
**Owner area:** Extension model system.

**Purpose.** When two extensions both inject a field of the same name into the same core model (e.g., both `payment` and `legacy_billing` adding `external_payment_id` to `UserModel`), today's behavior is undefined — silent override, last-loaded-wins, or quiet corruption. The user has confirmed this should be a startup-time error: collisions are caught before the application begins serving traffic, with a clear message naming both extensions and the field.

**Current state.** Collision behavior is undocumented and untested.

**Target state.** After extension discovery completes, the registry walks the merged model graph and rejects field-name collisions across extensions. A collision is a startup error — the application refuses to start, the error message names both extensions, the model, and the colliding field. The only exception is when both extensions declare an exactly identical field (same type, same default, same metadata), in which case the registry accepts the duplicate as a no-op duplication.

**Implementation notes.** Type identity uses Pydantic's field-info comparison; "exactly identical" is strict. The error must include the file paths and line numbers of both declarations to give the developer a one-step path to the conflict.

**Acceptance criteria.** Two extensions declaring different `external_payment_id` fields on `UserModel` cause a startup error naming both. Two extensions declaring an identical field declaration on the same model start successfully.

**Dependencies.** Independent.

---

## ~~Item 24~~ — ~~Single canonical mechanism for migration ownership detection~~ ✅ DONE

**Severity:** Medium
**Scope:** `MigrationManager`, `@extension_model` decorator, table-args metadata, documentation.
**Owner area:** Database / migrations.

**Purpose.** The documentation describes migration ownership detection by three different mechanisms in three different places: file-path detection (`src/extensions/{name}/BLL_*.py`), an `@extension_model` decorator registry, and an `info={"extension": name}` entry in `__table_args__`. We need one mechanism, applied consistently, with the others removed from the documentation.

**Current state.** Three documented mechanisms, no clear authoritative source.

**Target state.** File-path detection is the authoritative mechanism for **extension-owned tables** (tables whose models live in `src/extensions/{name}/BLL_*.py`). The `info={"extension": name}` entry on `__table_args__` is the authoritative mechanism for **field injections into core tables** via `@extension_model` — the decorator sets the info dict automatically, so authors do not write it by hand. The `MigrationManager.env_is_table_owned_by_extension(table)` function checks the info dict first (covering the injection case) and falls back to file-path inspection (covering the new-table case). All three documents are updated to describe this single resolution rule and to remove the conflicting alternative descriptions.

**Implementation notes.** The decorator-set info dict must merge with any existing `__table_args__` rather than overwriting. Document the order of precedence clearly. Provide a small CLI command that lists every table and its owning extension (or core, when applicable), so operators can audit ownership.

**Acceptance criteria.** Every table in the database has exactly one identifiable owner via the canonical resolution rule. The CLI audit command lists ownership for every table. The documentation describes a single resolution rule.

**Dependencies.** Independent.

---

## ~~Item 49~~ — ~~Cross-extension migration ordering with foreign-key awareness~~ ✅ DONE

**Severity:** Medium
**Scope:** `MigrationManager`, extension dependency declarations, Alembic migration runner.
**Owner area:** Database / migrations.

**Purpose.** `DB.Migrations.md` documents migration ordering as "all core migrations, then all extension migrations in extension dependency order." This handles dependencies declared via `EXT_Dependency`, but says nothing about an extension whose models hold foreign keys into another extension's models. If extension B's table FKs into extension A's table, B's migration creating that FK must run after A's migration creating the referenced table — and the framework does not currently enforce this as a topological constraint over the merged migration graph. The result is that running migrations in a fresh database with multiple extensions can fail at FK creation time, even when both extensions individually pass their own migration tests.

**Current state.** Per-extension migration ordering is documented. Cross-extension FK ordering is not.

**Target state.** Migration ordering is computed as a topological sort over the union of (a) declared `EXT_Dependency` relationships and (b) FK references discovered by inspecting model definitions. An extension whose model has an FK into another extension's model implicitly depends on that extension for migration purposes, even if it did not declare the dependency explicitly. Cycles in the merged graph (an FK from A to B and another from B to A) fail at startup with a clear error naming the offending tables and extensions.

**Implementation notes.** The topological sort runs once at startup and is cached. Cross-extension FK detection inspects both `@extension_model` field injections and standalone extension tables. FK detection requires the model classes to be loaded before migrations run; the framework's extension registry imports all models on startup before delegating to the Alembic env, and the migration runner is documented as depending on this load order. Document the recommended pattern for extensions that genuinely need bidirectional references (introduce a join table owned by one of the extensions, rather than direct FKs in both directions).

**Acceptance criteria.** A fresh-database migration run with extensions A and B, where B has an FK into A, produces a correct migration order automatically without B declaring an explicit `EXT_Dependency` on A. A circular FK dependency fails at startup with a clear error.

**Dependencies.** Refines Item 24 (single-mechanism ownership detection). Cross-references Item 20 (hot install must respect the ordering for runtime-installed extensions) and Item 62 (extension-aware migration discovery must precede the FK-aware ordering pass).

---

## ~~Item 61~~ — ~~Out-of-tree extension import support (formerly P2)~~ ✅ DONE

**Severity:** Critical
**Scope:** Every `importlib.import_module(...)` call that targets an extension module; new reusable loader helper in `lib/Paths.py` or new `lib/ExtensionLoader.py`.
**Owner area:** Extension system / packaging.

**Purpose.** `ExtensionRegistry.__init__` already accepts `extensions_path` as of `cf5cc68`, and the path-resolution helpers honor it. But the actual module loading still goes through `importlib.import_module("extensions.<name>.<file>")`, which only works when the extensions directory lives at `<sys.path entry>/extensions/`. If a consumer points the framework at `./my_extensions`, the directory walk finds the right files but `importlib.import_module` cannot import them — the `extensions_path` parameter currently shipped is a lie: registration walks the right directory but the registry ends up empty for any extension whose source lives outside the package.

**Current state.** Module loading uses `importlib.import_module` against a hard-coded `extensions.` prefix.

**Target state.** Replace every `importlib.import_module(...)` call that targets an extension module with `importlib.util.spec_from_file_location` + `module_from_spec` + `spec.loader.exec_module`, using a synthesized module name (e.g. `serverframework_ext_<name>_<file>`, registered under both its synthesized name and `extensions.<name>.<file>` in `sys.modules` so existing intra-extension imports keep resolving). Sites to fix: `extensions/AbstractExtensionProvider.py` — `_register_dependencies` (the `dep_module_pattern` branch), `discover_extension_models`, `_discover_extension_providers`, the `classproperty` versions on `AbstractStaticExtension` (`providers`, `types`, `models`); `lib/Pydantic.py` — `scoped_import` (the BLL/PRV walker around line 2330) and the EP-loader around line 2945. Pull a reusable helper out into `lib/Paths.py` or a new `lib/ExtensionLoader.py` since every site does the same dance.

**Implementation notes.** Intra-extension imports (`from extensions.payment.BLL_Payment import ...` inside `extensions/payment/EP_Payment.py`) need to keep working. Easiest: register the synthesized module under both its package-qualified and file-based names in `sys.modules`. Migration discovery for out-of-tree extensions has the same problem; see Item 62.

**Acceptance criteria.** A consumer pointing the framework at `./my_extensions` has every extension under that path discovered, imported, registered, and operational, identical to in-package extensions. Intra-extension imports keep resolving without modification.

**Dependencies.** Independent. Pairs with Item 62 (migration discovery applies the same out-of-tree pattern).

---

## ~~Item 62~~ — ~~Extension-aware migration discovery (formerly P3)~~ ✅ DONE

**Severity:** High
**Scope:** `database/migrations/env.py`, `MigrationManager` discovery, Alembic `script_location` configuration.
**Owner area:** Extension system / database migrations.

**Purpose.** Each extension can ship its own `migrations/versions/` tree, and Alembic discovers them via `database/migrations/env.py`. That env script currently assumes `<src>/extensions/<name>/migrations/` — fine when extensions live in-package, broken when they don't. Once Item 61 lets extensions live outside the package, the migration runner stops finding their migrations.

**Current state.** Migration discovery hard-codes `<src>/extensions/<name>/migrations/`.

**Target state.** Make `env.py` (and any `MigrationManager` discovery) consult `lib.Paths.extensions_dir()` instead of computing the path inline. When the registry is constructed with `extensions_path`, that path becomes the search root for migrations as well as for code. Confirm that Alembic's `script_location` setup tolerates multiple roots (one for the framework's core migrations, N for each extension); splice extension migration directories in at runtime if needed.

**Implementation notes.** Once Item 60 (rename) lands, the `database.migrations` package moves under `serverframework.database.migrations` and the env script moves with it — that is a natural moment to also fix the discovery path, since the file is being touched anyway. The FK-aware ordering pass from Item 49 runs over the merged migration set produced by this discovery pass; both must agree on what counts as an extension-owned migration.

**Acceptance criteria.** A fresh-database migration run with an extension whose source and migrations live under `./my_extensions/payment/` succeeds and correctly applies that extension's migrations after the framework's core migrations. The Alembic `revision --autogenerate` command run from inside an out-of-tree extension produces a migration in the correct location.

**Dependencies.** Depends on Item 61 (out-of-tree imports must work first). Cross-references Items 49 (FK-aware ordering), 60 (rename moves the env script).

---

## ~~Item 20~~ — ~~Hot reload and manifest-based extension installation~~ ✅ DONE (refined scope)

**Status: ✅ DONE (refined scope; in-process hot reload remains explicitly out-of-scope per the refinement below).** Manifest-driven install (`ExtensionManifest`, `install_from_manifest`) and SIGHUP-driven graceful restart shipped per the post-audit refinement. (a) `app.install_sighup_handler(app)` registers a SIGHUP handler that computes the registry diff in-process (so the operator's logs document what changed) and exits with the documented sentinel `SIGHUP_RESTART_EXIT_CODE = 75` (EX_TEMPFAIL) so the supervisor (systemd `Restart=on-failure`, k8s `restartPolicy: Always`, `docker --restart=always`) respawns with the new extension state; (b) `extensions/HotReload.py::rebuild_registry` walks the configured extensions root, computes `RegistryDiff` (added/removed/changed_version/unchanged) against the live registry's `loaded_extensions` snapshot, builds a fresh `ExtensionRegistry` from on-disk state, and runs pending migrations for added/version-changed extensions; (c) the handler is a portable no-op on platforms without SIGHUP (Windows) — operators on those platforms run blue-green deployment instead. The in-process true-hot-reload portion is explicitly out-of-scope per the refinement (rewrites of Pydantic/SQLAlchemy/Strawberry registration paths to survive class-identity changes); deployments that need it use blue-green at the process level. Tests: `extensions/Hot_reload_test.py` (12 total: existing 9 + 3 new acceptance tests covering exit code, Windows portability, combined add+remove diff).

**Severity:** Medium
**Scope:** New `manifest.toml` format, `install_from_manifest` machinery, registry diff, hot-reload controller.
**Owner area:** Extension system.

**Purpose.** Extensions are currently discovered once at startup based on a CSV environment variable. There is no installation-from-registry, no manifest, no hot-reload. For a framework whose stated goal is to be extensible into any backend without core modification, the inability to install or update extensions without a restart is a meaningful limitation, particularly in long-running multi-tenant deployments.

**Current state.** Extension discovery is filesystem-based and CSV-gated. Adding an extension requires placing files in the extensions directory, updating the CSV, and restarting.

**Target state.** A `manifest.toml` per extension declaring metadata, dependencies, entry points, and version. An `install_from_manifest(url_or_path)` operation that fetches, validates, runs migrations, and registers the extension at runtime without restart. A hot-reload controller, triggered by a SIGHUP-style signal or an admin API call, that re-runs discovery and applies the registry diff: newly-present extensions initialize, removed extensions are torn down with their cleanup hooks, modified extensions reload. Static class identity must be preserved across reloads (`importlib.reload` carefully combined with a registry diff that maps old class objects to new ones for the hook registry's sake).

**Implementation notes.** Migrations during install must be reversible or at least non-destructive enough that a failed install can be rolled back without data loss. Document a rollback procedure. The manifest format should be minimal — name, version, dependencies (extensions, pip, system), entry-point module — and human-editable. A registry endpoint (an HTTP-served list of available manifests) is out of scope for this item but should be kept in mind as a natural extension. Hot reload of code carries known Python pitfalls (cached references in other modules, decorators, metaclass state); document the constraints clearly and treat full process restart as a fallback when hot reload cannot be performed safely.

**Acceptance criteria.** An admin can install a new extension at runtime via `install_from_manifest`, the extension's migrations run, its hooks register, and its endpoints become available without restarting the application. A SIGHUP triggers re-discovery and applies the diff cleanly. Removing an extension cleans up its hooks, endpoints, and background services.

**Dependencies.** Independent.

**Refinement after audit.** The original target state conflates two genuinely different problems: (a) **manifest-driven install with a clean process restart** (Medium, tractable, the manifest format and `install_from_manifest` orchestration plus a graceful restart) and (b) **true in-process hot reload** (High difficulty, requires `importlib.reload` to play well with cached class references in other modules, hook decorators that registered at import time, Pydantic models that cache `__pydantic_validator__` against class objects, SQLAlchemy mappers that cannot be cleanly unmapped, and Strawberry schemas baked at startup). The phrase "static class identity must be preserved across reloads" is the entire problem and cannot be solved without a class-registry rewrite that tracks every place a class object is captured. This item is split: Item 20 retains scope (a) at Medium severity, with the acceptance criterion narrowed to "a SIGHUP triggers a clean stop, registry rebuild, and start that surfaces the new extension without manual operator action." Scope (b) — true hot reload of *code* without restart — is documented as a stretch goal with no committed scope; deployments that need it use blue-green at the process level instead. The "modified extensions reload" sentence in the original target state is removed; install/uninstall via clean restart is the contract.

---

## ~~Item 30~~ — ~~Surface skipped optional dependencies at startup~~ ✅ DONE

**Severity:** Low
**Scope:** Dependency resolver, startup banner, optional `on_optional_missing` callback.
**Owner area:** Dependencies / startup.

**Purpose.** The topological dependency resolver silently skips optional dependencies that are not present. Extensions that depend optionally on another extension may end up running in a half-configured state — a feature is disabled without any signal to the operator or to the depending code. Silent half-configuration is one of the harder failure modes to debug because nothing visibly went wrong.

**Current state.** Optional dependencies are skipped without notification.

**Target state.** Each `EXT_Dependency` declared as `optional=True` accepts an `on_optional_missing` callback. The default callback logs a structured warning naming the missing dependency and the abilities it would have enabled. At startup, the framework prints a banner listing every skipped optional dependency and the resulting disabled abilities, so operators see at a glance what the running configuration omits. Extensions can register richer fallback behavior (use a degraded local implementation, disable a feature flag, send an admin notification) via the callback hook.

**Implementation notes.** The banner is emitted on stdout during startup and also written to a structured event in the audit log, so post-mortem debugging can recover the configuration state. The disabled-abilities portion requires extensions to declare which abilities depend on which optional dependencies — a small additional metadata declaration.

**Acceptance criteria.** Starting an application with an extension whose optional dependency is missing produces a clear startup banner naming the dependency and the disabled abilities. The condition is also queryable at runtime via an admin endpoint.

**Dependencies.** Independent.

---

# Group 15 — Provider Scope, Quotas, Residency, Tenant Isolation, Read Replicas

The multi-tenant resolution surface: which provider instance is consulted for which user, against which budget, in which jurisdiction — and how the database itself enforces and scales the same boundaries.

## ~~Item 19~~ — ~~Root and system providers, with unified per-user/per-team quota~~ ✅ DONE

**Severity:** High
**Scope:** `ProviderInstance.scope` field, separation of root-internal-only invocation path, new unified `Quota` table, integration into provider resolution.
**Owner area:** Provider system.

**Purpose.** The framework distinguishes four provider scopes, each with a specific role:

- **Root.** SaaS-owned, framework-internal use only. Users cannot access or invoke a root provider in custom manners. Root is reserved for hardcoded internal purposes that the user never picks. Example: a root SendGrid instance powers system notification emails (password resets, billing alerts). Reached only via direct lookup from framework code, never via user-context resolution.
- **System.** SaaS-owned, included in the subscription. Users invoke system providers like their own credentials, but the credentials underneath are SaaS-issued and the usage is metered against the user's included quota. Example: a system OpenAI instance backs the included monthly AI quota in a paid tier.
- **Team.** Team-owned credentials, used by any member of the team.
- **User.** User-owned credentials.

Crucially, **system, team, and user providers all share the same quota infrastructure.** What changes between them is where the credentials come from and who effectively pays for them; what does not change is how usage is tracked. A request consuming "five tokens of OpenAI capacity" debits the same `Quota` row regardless of whether the credentials underneath were system-issued, team-owned, or user-owned. This unification is the user's explicit design intent.

**Current state.** Provider scope is documented inconsistently. There is no clear separation between root-internal and user-invokable scopes. There is no quota table. Per-tenant configuration is described as missing — but the user has clarified that per-team and per-user provider instances already exist for exactly this purpose, and the documentation needs to make that explicit rather than implying the gap.

**Target state.** `ProviderInstance.scope: Literal["root", "system", "team", "user"]`. Resolution semantics differ by scope:

- Root instances are invoked only from explicit framework or extension code paths via direct lookup (e.g., `EXT_Email.get_root_instance(ability="system_notification")`). The user-context resolver never returns a root instance. Root invocation is audit-logged with the calling code path so internal callers are accountable.
- System, team, and user instances participate in the user-context resolution flow.

Resolution flow for a user-invokable call (inside `bond_instance` for an external manager):

1. Walk `user → team → system` for an instance matching this extension and ability.
2. First match wins. Root is never inspected on this path.
3. On match, check quota for `(user_id, team_id, ability)` per the unified quota model below. If exhausted, raise typed `QuotaExhaustedError(scope, ability, period)`.
4. Atomically decrement quota.
5. Bond and proceed.

If nothing resolves at any user-invokable scope, raise `NoProviderInstanceError(requester, ability)` — typed, with no silent fallback to root.

**Unified quota model.** A single `Quota` table serves all three user-invokable scopes. The user has specified that the table dual-purposes as both per-user and per-team partitioning by carrying nullable `user_id` and `team_id`, where populating both restricts the quota to that user within that team:

```python
class Quota(ApplicationModel, DatabaseMixin):
    user_id: Optional[str]                # NULL = team-wide quota
    team_id: Optional[str]                # NULL = user-scoped quota outside any team
                                          # both populated = this user's allotment within this team
    ability: str                          # canonical ability name
    period: Literal["minute","hour","day","month","billing_cycle"]
    period_key: str                       # e.g. "2026-04" or "2026-04-28T15:00"
    limit: int                            # 0 = blocked, sentinel for unlimited as appropriate
    consumed: int
    unit: Literal["call","token","byte","message","row"]
```

The semantics are:

- `user_id` populated, `team_id` NULL: a quota that belongs to a user across all of their contexts.
- `team_id` populated, `user_id` NULL: a quota that belongs to a team, shared across its members.
- Both populated: a per-user-within-team quota, allowing a team to partition its overall quota among its members.

The framework consumes from quota; it does not decide the limit values. Limit population is the responsibility of whoever owns the budget — the subscription extension writes system-tier limits when a user upgrades, team admins write team-level partitioning, and so forth. When multiple quota rows match a single request (e.g., a per-user-within-team row plus a team-wide row), the framework decrements all matching rows and refuses the request if any of them is exhausted.

System-scoped provider instances are unreachable to a user unless a quota row exists permitting their use. This is the safety property the user emphasized: no user can accidentally email from the SaaS's brand SendGrid, charge to the SaaS's Stripe account, or consume the included AI tier without an explicit quota allowance recorded.

**Implementation notes.** Atomic decrement uses `UPDATE ... WHERE consumed < limit RETURNING` semantics; the operation is a no-op when exhausted, surfacing as the typed error. Period-key derivation is a pure function of the period type and the current time, with timezone considerations documented. The audit log captures every quota decrement with the requester, ability, and which row(s) were debited.

**Acceptance criteria.** A user invoking an OpenAI ability against a system-scoped provider instance correctly debits their per-user-within-team quota row, their team-wide quota row, or both, depending on which exist. The same user, with quota exhausted, receives a typed `QuotaExhaustedError` and no upstream call is made. A root SendGrid instance is unreachable from any user-context call regardless of quota state.

**Dependencies.** Depends on Item 69 (atomic decrement runs on the shared distributed-counter primitive). Cross-references Item 17 (rate limits — different dimension, both apply).

**Refinement after audit.** Atomic-decrement implementation is no longer ad-hoc per this item — it consumes the `DistributedCounter` primitive from Item 69 with the same `UPDATE ... WHERE consumed < limit RETURNING` semantics described above. This unifies the multi-instance correctness guarantees across Items 17, 19, and 57 and removes the "three items independently invent we-need-a-shared-counter" condition the audit surfaced.

---

## ~~Item 36~~ — ~~Data residency and regional provider pools~~ ✅ DONE

**Status: ✅ DONE.** Framework primitives shipped (`ResidencyJurisdiction`, `ResidencyRegion`, `JurisdictionRegistry`, `ProviderInstance.region`) plus the in-framework filter that closes the contract: (a) `extensions/Residency.py::set_tenant_jurisdiction_resolver(callback)` is the registration seam through which the residency extension supplies the per-tenant policy lookup. The extension calls this at startup; the framework consults it during rotation. (b) `extensions/Residency.py::filter_chain_by_jurisdiction(chain, requester, *, ability, requester_id=None)` is the in-framework filter applied to a rotation chain — drops instances whose `provider_instance.region` is outside the caller's required jurisdiction, raises `NoInJurisdictionProviderError` (HTTPException 400 subclass) when the chain comes up empty under a non-`None` jurisdiction. The filter accepts both `RotationProviderInstanceModel` wrappers and bare `ProviderInstanceModel` entries (looking for `provider_instance.region` first, then `region`). Instances with `region=None` are treated as out-of-jurisdiction so the framework refuses to silently route under an unmarked policy. (c) `RotationManager.rotate` calls `filter_chain_by_jurisdiction` after `_get_ordered_rotation_provider_instances`; the filter is a no-op (chain unchanged) when no resolver is registered, when the resolver returns `None`, or when the resolver itself raises (defensive — residency must never crash rotation). The residency extension that lands later supplies only the resolver callback and any jurisdiction-to-region mappings via `JurisdictionRegistry.register`; no further framework changes required. Tests: `extensions/Residency_test.py` (30 total: existing 19 + 11 new covering resolver registration, no-op paths, in-jurisdiction filtering, `region=None` rejection, `NoInJurisdictionProviderError` raising, bare-provider-instance handling, defensive resolver-exception handling).

**Severity:** Medium
**Scope:** Provider instance metadata, resolution flow, per-tenant residency policy.
**Owner area:** Provider system / multi-tenancy.

**Note on existing work.** Data residency policy is already expressed in a separate extension that has not yet been merged into this repository. This item documents the framework-side primitives the unmerged extension expects, so that landing the extension is a registration-only change and not a core modification. Cross-reference the extension when it lands; until then this item stands as a forward-compatibility contract.

**Purpose.** Item 19 disambiguated user/team/system/root scopes. It did not address geographic placement — and for many real deployments (EU GDPR, US data localization, regulated healthcare jurisdictions) the residency of the provider instance matters as much as its scope. A user in an EU tenant must hit an EU-region Stripe account, an EU-region SendGrid pool, an EU-region OpenAI deployment. Without a residency primitive, every multi-tenant deployment will encode this in a different ad-hoc place.

**Current state.** Provider instances have no region or jurisdiction tag. Resolution does not consider residency.

**Target state.** Two distinct concepts, deliberately separated to avoid the equivalence problem (`eu-west-1` and `eu-central-1` are both EU-residency but different physical regions).

- **`ResidencyJurisdiction`** — the legal / policy umbrella. Examples: `EU`, `US`, `UK`, `CA`, `APAC`, `HEALTHCARE_HIPAA`. This is what tenant-side policy expresses ("this team's data must live in `EU`"). Free-form per deployment so each operator can define the jurisdictions their compliance regime cares about.
- **`ResidencyRegion`** — the physical placement of a specific provider instance. Examples: `eu-west-1`, `eu-central-1`, `us-east-1`, `apac-tokyo`. This is what instance-side metadata declares ("this Stripe account lives in `eu-west-1`").

Each `ResidencyJurisdiction` declares the set of `ResidencyRegion` values it includes, mapped at deployment time. `ProviderInstance` carries `region: Optional[ResidencyRegion]`. Tenants (teams, users) carry `data_residency: Optional[ResidencyJurisdiction]`. Resolution (Item 19) is extended: when a jurisdiction policy is set on the requester's tenant, only instances whose `region` is mapped under that jurisdiction are considered, regardless of scope. If no in-jurisdiction instance exists at any scope, raise `NoInJurisdictionProviderError(requester, ability, jurisdiction)`.

For providers that are inherently single-region (a regional payment processor), the `region` tag is fixed at instance creation time. For providers that are multi-region (Stripe accounts in EU vs US), each region is a distinct provider instance.

**Implementation notes.** Both `ResidencyJurisdiction` and `ResidencyRegion` are opaque string identifiers to the framework — values and the jurisdiction-to-region mapping are agreed by deployment policy and live in configuration, not in code. The shared HTTP client (Item 31) does not enforce regional egress; that is an infrastructure concern (HAProxy, regional egress proxies). Residency enforcement at the application layer is about provider-instance selection, not about network path. The framework provides a `JurisdictionRegistry` that operators populate at startup with the mapping; lookups are O(1).

**Acceptance criteria.** A user in an `EU`-jurisdiction team invoking a payment ability is routed to a Stripe instance whose region maps under `EU` (e.g. `eu-west-1` or `eu-central-1`) even when a `us-east-1` instance is otherwise higher in the resolution order. A jurisdiction miss surfaces as a typed error rather than silently routing out-of-jurisdiction. The currently-unmerged residency extension registers its policy enforcement through this primitive without touching framework code.

**Dependencies.** Extends Item 19. Anticipates the unmerged residency extension.

---

## ~~Item 55~~ — ~~Tenant data-isolation primitives~~ ✅ DONE

**Status: ✅ DONE.** Session-binder integration completed in `database/TenantScoped.py` and wired through `database/DatabaseManager.py`. (a) New `_tenant_context_var: ContextVar[Dict[str, str]]` holds the per-request mapping of tenant keys (e.g. `{"team_id": "...", "org_id": "..."}`); set by middleware via `set_tenant_context(...)`, read by the binder. (b) New `_privileged_bypass_var: ContextVar[bool]` carries the BYPASSRLS flag for admin endpoints; the binder reads it once at transaction begin. (c) New `bind_session_tenant_gucs(session)` registers an SA `after_begin` listener that, when the connection's dialect is `postgresql`, emits `SET LOCAL app.current_<key> = <value>` for every key in the contextvar (or `RESET app.current_<key>` under privileged-bypass). Non-Postgres dialects (SQLite tests) silently no-op so the test suite stays portable. (d) `DatabaseManager.get_session`, `_get_db_session`, and `_get_async_db_session` all call `bind_session_tenant_gucs(session)` after construction, so every framework-issued session enforces tenant isolation by default. The audit-flagged failure mode — RLS policies exist but no session populates their GUCs — is closed: tenant isolation is now enforced end-to-end on Postgres. Tests: `database/TenantScoped_test.py` (existing 12 + 8 new integration tests) covering contextvar round-trips, privileged-bypass semantics, dialect gating, and the framework-managed-session attachment path.

**Severity:** Medium
**Scope:** Postgres Row-Level Security policies, session GUC variables, tenant-scoped model declarations.
**Owner area:** Multi-tenancy / security.

**Purpose.** Item 36 covers data residency at the provider-instance level (which Stripe account does this user's traffic go to); it does not cover row-level data isolation at the database level (which rows can this user even see). Today every tenant-scoped query relies on the BLL author remembering to filter by `team_id` — a pattern that works under code review but inevitably leaks under refactoring, custom queries, raw SQL, or simple typos. A single missed filter clause is a cross-tenant data exposure. The framework needs a defense-in-depth primitive that enforces isolation at the database layer, regardless of what the application code does.

**Current state.** Tenant filtering is by convention. No row-level enforcement.

**Target state.** Tenant-scoped models declare themselves via a `TenantScopedMixin` that adds `team_id` (or a configurable tenant-key field) and registers a Postgres Row-Level Security policy at table-creation migration time. The session binder sets `app.current_team_id` as a Postgres GUC variable on every connection; the RLS policy filters reads and writes by matching `team_id = current_setting('app.current_team_id')::uuid`. A missing or unset GUC variable causes the policy to return zero rows, so an unauthenticated session or a forgotten tenant-context bind sees nothing rather than seeing everything.

System-level operations (admin endpoints, cross-tenant reporting, the framework's own internal operations) bind a privileged session that bypasses the RLS policy via a Postgres role with `BYPASSRLS`. The privilege boundary is at the session-bind layer, not at individual queries — there is no way to selectively bypass RLS for a single query without binding a privileged session, by design.

**Implementation notes.** Postgres RLS is well-supported and battle-tested but has known costs: queries on RLS-protected tables get a planner overhead, and policy expressions must be `STABLE` or simpler for the planner to optimize. The framework's policy template is the simplest possible (`USING (team_id = current_setting(...)::uuid)`) so the planner cost is predictable. Migration of an existing application to RLS is non-trivial: a phased rollout (RLS in `WARN` mode logging policy violations without enforcing, then `ENFORCE` mode) is documented. The framework includes a startup check that verifies every `TenantScopedMixin`-tagged table has an enforced RLS policy and refuses to start otherwise — the policy and the mixin must agree.

**Acceptance criteria.** A `TenantScopedMixin`-tagged model declared at extension load time produces a corresponding Postgres RLS policy in the migration. A query against the model from a session with `app.current_team_id` set returns only matching rows; a query from a session without the setting returns zero rows. A privileged session (admin, reporting) bypasses RLS via a separate role; no in-application code can selectively bypass RLS without binding the privileged session.

**Refinement after audit.** The single-key policy template is too narrow for real deployments — Item 19's `Quota` model carries both `user_id` and `team_id` and many applications add an `org_id` tier above team. The session binder now sets a tuple of GUCs (`app.current_org_id`, `app.current_team_id`, `app.current_user_id`); `TenantScopedMixin` is parameterized by which keys to filter against (`TenantScopedMixin.with_keys("team_id")` for team-only, `TenantScopedMixin.with_keys("org_id", "team_id")` for org→team hierarchy, etc.); the generated RLS policy combines the active keys with `AND`. A missing GUC for any declared key still returns zero rows. Cross-team admin views and per-user-within-team isolation (the latter is what Item 19's both-populated row enforces) both work without `BYPASSRLS` workarounds.

**Dependencies.** Cross-references Item 36 (residency, distinct concern), Item 49 (migration ordering — RLS policies are migration artifacts subject to FK-aware ordering).

---

## ~~Item 54~~ — ~~Read-replica routing for read-only operations~~ ✅ DONE

**Status: ✅ DONE.** Session-binder integration completed in `database/DatabaseManager.py`. (a) `init_engine_config` parses `DB_REPLICA_URLS` (comma-separated) into `self.replica_urls`. (b) `init_worker` builds per-replica engines (sync + async) and per-replica session factories, populates the `ReplicaPool` keyed by URL. (c) New `_select_session_factory()` / `_select_async_session_factory()` consults `should_route_to_replica()` (true ↔ `@read_only` is active AND no primary write has occurred in this request) and returns a replica factory when one is configured, else primary. (d) `get_session()`, `_get_db_session()` (sync), `_get_async_db_session()` (async) all route through the selectors. (e) Read-after-write consistency: `_attach_primary_write_listener(session)` registers a `before_flush` SA event handler on every primary session that calls `mark_primary_write_seen()` whenever the session has any new/dirty/deleted instances — the contextvar trips and subsequent reads in the same request bind primary regardless of `@read_only`. (f) `close_worker` disposes replica engines symmetrically. Tests: `database/ReadReplica_test.py` (existing 14 + 4 new integration tests) covering the no-replica passthrough, replica selection, post-write fallback, and primary-only listener attachment.

**Severity:** Medium
**Scope:** Database session binding, BLL method annotations, request-context routing.
**Owner area:** Database / scaling.

**Purpose.** Read-heavy workloads (list endpoints, dashboards, search) eventually outgrow a single primary database. Read-replicas are the standard escape valve, but only if the framework can route read-only operations to a replica without each extension manually selecting a session. Today every BLL method binds the primary session by default; an extension that wants replica routing has to reach into the session-management layer, which violates the no-touch-the-core principle. A first-class routing primitive lets extensions opt their reads onto replicas without bespoke session code.

**Current state.** Single primary session bound for every request. No replica concept.

**Target state.** Two complementary opt-in mechanisms:

- **Method-level annotation.** A `@read_only` decorator on BLL methods declares the method is safe to route to a replica. The framework's session binder consults the annotation at method dispatch and binds a replica session when one is configured, falling back to primary when no replica is configured or when the request is inside a write-transaction (a `@read_only` method called from within a write context still binds primary, since cross-session reads inside a transaction would lose read-after-write consistency).
- **Request-context flag.** A `RequestContext.read_only: bool` flag forces all session binding within the request to use replicas. Useful for dedicated read-only endpoints (a public catalog browse, a metrics export) where every operation under the request is known-safe.

The framework supports configuring a pool of replicas with simple round-robin selection between them; advanced load shaping is delegated to HAProxy or the database's own load balancer per Item 3.

**Implementation notes.** Read-after-write consistency is the trap: a write committed to primary may not be visible on a replica for tens or hundreds of milliseconds. The framework's transaction tracking ensures that within a single logical request, once any write has occurred against primary, subsequent reads in the same request bind primary regardless of `@read_only` annotation. The fallback policy is explicit: read-only mode requested with no replica configured is a deployment-config-only fallback to primary (with a warn-once log), not a runtime error. Replica health is consulted before binding; an unhealthy replica is removed from rotation per a configurable health-check interval.

**Acceptance criteria.** A list endpoint's BLL method annotated `@read_only` routes to a replica when configured, and falls back cleanly to primary when no replica is configured. A write followed by a read within the same logical request binds primary for the read, preserving read-after-write consistency. Replica failure removes that replica from rotation without affecting unrelated requests.

**Dependencies.** Independent. Cross-references Items 3 (L7 load shaping), 34 (health and routing metrics).

---

# Group 16 — Authorization: Permissions, OAuth Scopes, Field-Level ABAC

The permission registry that doubles as the OAuth scope catalog, and the field-level grants that share its naming so the same string gates a row read, a column read, and a token-bound action.

## ~~Item 18~~ — ~~Permissions registration tied to OAuth scope shape~~ ✅ DONE

**Severity:** High
**Scope:** New `PermissionDef` type, new `AbstractStaticExtension.get_permissions()` contract, integration with the OAuth extension's consent and authorization flows.
**Owner area:** Authorization / authentication.

**Purpose.** Today extensions document required permissions in prose (e.g., "payment:read, payment:write") but there is no class-method registration, no auto-seeding, and no canonical scope shape. This forces every new extension to either ship hand-written seed data into the core permission tables (which violates the no-touch-the-core rule) or to skip permission integration entirely (which leaves authorization holes). The framework needs a typed registration mechanism whose canonical names are also valid OAuth scopes, so that the OAuth extension can publish a consent catalog and validate token-granted scopes against the same registry.

**Current state.** Permissions live as seeded rows in the database. Extensions cannot contribute permissions through a typed API. OAuth scopes have no canonical shape.

**Target state.** Standardize the permission name shape as `{extension}.{resource}.{action}[:{qualifier}]`. Concrete examples: `payment.subscription.read`, `payment.subscription.write`, `auth.user.delete`, `meta_logging.audit.read:own`. The same string is the permission name and the OAuth scope; there is no parallel scope concept.

Define `PermissionDef` as a frozen dataclass with the following fields:

- `name`: the canonical scope string.
- `description`: the user-facing copy displayed on OAuth consent screens.
- `implies`: a tuple of names this permission implies (e.g., `payment.subscription` implies its `.read` and `.write` variants).
- `sensitive`: a boolean marking permissions that require step-up authentication or fresh consent regardless of token grant.
- `user_grantable`: a boolean controlling whether the permission can be granted via an OAuth token at all.
- `system_only`: a boolean marking permissions reserved for internal system actions, never appearing in tokens.

`AbstractStaticExtension` exposes `get_permissions() -> List[PermissionDef]`. The framework calls this at startup, validates uniqueness across the merged registry, and seeds the database. The OAuth extension reads `ExtensionRegistry.iter_permissions()` to produce its consent catalog and validates scope strings on token issuance against the same registry.

**Resolution at request time.** Database-backed roles grant a set of permission names (the existing path). OAuth tokens carry their granted scopes as a set of permission names. The effective permission set for a request is the intersection of role grants and token scopes — `effective = role_grants ∩ token_scopes` for OAuth-bearing requests, or `effective = role_grants` for direct authentication. **A token can never escalate beyond what its bearer's role allows.** This is the critical invariant. `requester.has_permission(name)` walks the `implies` graph. Permissions marked `sensitive=True` require a freshly-issued token (within a configurable window) regardless of scope grant.

Wildcard scopes are supported only at consent time (the user grants `payment.subscription.*`), expanded into the concrete permission set before the token is issued, so that revocation remains precise. Audit logs record every check that succeeded via OAuth scope (versus direct role) with the token id and the scope name used, providing per-scope usage data and a clean revocation story.

**Implementation notes.** The OAuth extension lives in a separate repository and consumes this registry through `ExtensionRegistry`. The framework provides the registry, the `PermissionDef` type, the seeding logic, and the `requester.has_permission` resolver; the OAuth extension provides the token issuance, consent UI, and scope-to-permission binding. Document the intersection invariant prominently — it is easy to invert by accident.

**Acceptance criteria.** An extension declaring `get_permissions()` produces correctly seeded permissions on startup. The OAuth extension can list all available scopes by reading the registry. A token with a scope outside the bearer's role grants is unable to perform the action. Sensitive permissions require fresh tokens.

**Dependencies.** Anticipates the external OAuth extension. Depends on Item 20 (extension registry plumbing if not already present). Cross-references Items 58 and 59 (passwordless grants must observe the freshness gate per their refinements).

**Refinement after audit.** The "freshly-issued" definition is extended to cover passwordless grants. A session issued via Item 58 (magic link) or Item 59 (device pairing) counts as freshly-issued **only for non-sensitive permissions**. Any operation requiring a `sensitive=True` permission against a grant-issued session must trigger a step-up MFA challenge before proceeding, regardless of the session's age. This closes the audit-flagged gap where a magic-link login could perform sensitive operations on an unverified device without ever satisfying the freshness gate that OAuth tokens are required to satisfy.

---

## ~~Item 45~~ — ~~Field- and column-level attribute-based access control~~ ✅ DONE

**Status: ✅ DONE.** Field metadata primitives shipped (`Field(..., requires=[...])`, `Sensitive[T]` annotation, allowed-field-set computation cached per `(manager, requester)` via `FieldACLCache`). REST integration: `apply_field_acl_to_payload` and `_resolve_has_permission` in `Pydantic2FastAPI.py` strip disallowed fields from the GET, LIST, and (when `manager.requester` exposes `has_permission`) include-population paths. Search/order-by rejection: `validate_field_acl_query` is invoked in the LIST handler before SQL is generated, raising HTTP 403 with `context="sort_by" | "filter" | "projection"` on restricted-field references. Per-deployment sentinel: `apply_field_acl_to_response` accepts `sentinel_mode=SENTINEL_OMIT | SENTINEL_MASK` (defaulting to `omit`, overridable via the `FIELD_ACL_SENTINEL` env var). **GraphQL resolver wiring (now landed):** `GraphQLManager._apply_field_acl(manager, result)` in `lib/Pydantic2Strawberry.py` strips disallowed fields from any resolver result; the four CRUD resolver builders (`_add_query_resolver`, `_add_list_query_resolver`, `_add_create_mutation_resolver`, `_add_update_mutation_resolver`) call into this helper before returning, so REST and GraphQL share one field-stripping policy with no per-resolver opt-in required. The helper resolves `manager.BaseModel` (core managers) or `manager.Model` (extension managers), reads `FIELD_ACL_SENTINEL` for the sentinel mode override, and is a no-op for managers without a resolvable `has_permission` (system-key audit jobs, framework-internal callers) so the same code paths work end-to-end with and without ABAC. Tests: `lib/FieldACL_test.py` (42 total) plus `lib/Pydantic2Strawberry_field_acl_test.py` (15 new) covering the helper across BaseModel/Model managers, dict/list/None payloads, AND-semantics for multi-permission fields, sentinel-mode env override, no-op pass-through paths, and resolver-source sanity assertions that lock in the four CRUD call sites.

**Severity:** Medium
**Scope:** Permission system, model field metadata, response serialization.
**Owner area:** Authorization.

**Purpose.** The current permission model is row-level — a user either can read a record or cannot. Real applications regularly need finer-grained control: a customer-support agent can read a user's email and name but not their SSN; a billing admin can read invoice totals but not line items. Today this is unsupported, so extensions either expose more than they should (security risk) or fork the response model per role (maintenance burden). A field-level ABAC layer integrated with the permission registry from Item 18 closes this gap.

**Current state.** Row-level permissions via `permission_references`. No field-level controls.

**Target state.** Pydantic field metadata captures field-level grants. A field marked `Field(..., requires=["payment.invoice.read_lines"])` is included in serialized output only when the requester has the named permission. The serialization layer applies the grant check at response time, replacing disallowed fields with a sentinel (or omitting them, configurable per deployment). Search and update operations honor the same grants — a user without `payment.invoice.write_lines` cannot update line items even if they can update the invoice's other fields.

The same metadata applies to GraphQL: the resolver for a marked field returns null with a typed error attached when the requester lacks the grant, preserving the partial-data partial-errors contract from Item 16.

**Implementation notes.** The grant check is integrated into the permission registry (Item 18) so the same `payment.invoice.read_lines` string serves as both an OAuth scope and a field-level gate. Document the precedence: row-level access controls visibility of the record at all; field-level filters which fields appear once the record is visible. A typed `Sensitive[T]` field annotation can replace the more verbose `Field(..., requires=...)` for the common case. **Performance:** applying field-level grants on a 10k-row list response is costly if the check runs per record; the framework computes the allowed-field set once per `(manager, requester)` at request bind and caches it for the request's lifetime, so the per-record cost is a single dictionary lookup. **Order-by and search:** restricted fields cannot be used for `ORDER BY` or filtering by requesters lacking the grant — both are rejected at request validation, since ordering by a restricted field leaks its values through inference attacks just as much as direct read does.

**Acceptance criteria.** A `User` model with `ssn: str = Field(..., requires=["auth.user.read_ssn"])` returns the field only to requesters with the grant; other requesters see the field omitted from REST responses and null in GraphQL responses with a typed error. Search criteria using a restricted field are rejected for requesters without the grant; ordering by a restricted field is similarly rejected.

**Dependencies.** Cross-references Item 18 (permission registry).

---

# Group 17 — Cross-Process Eventing, Background Job Fairness, Audit Retention

The fan-out seam to other services, the fairness primitive that prevents one tenant from starving the queue, and the retention contract for the audit trail.

## ~~Item 42~~ — ~~Cross-process event bus seam~~ ✅ DONE

**Status: ✅ DONE.** Two-part landing. (a) **Adapter refactor.** The `Kafka`, `NATS`, and `RedisStreams` event-bus classes are no longer SDK-importing stubs. They share a `_BrokerEventBus` parent that delegates every wire-level operation to a `BrokerTransport` ABC (`send` / `subscribe` / `close`). The framework's core (`logic/EventBus.py`) ships exactly two concrete transports: `InMemoryBrokerTransport` (production single-process + tests) and `BrokerTransport` (the contract). **All native broker SDK clients live in extensions, not in core.** The Valkey extension (`extensions/valkey/`) is the canonical home for Valkey/Redis-protocol clients — it owns `redis-py` connection-pool lifecycle and exposes `PRV_Valkey.build_streams_transport(instance)` returning a `BrokerTransport` the EventBus consumes. Future Kafka and NATS extensions wire the same way. (b) **Schema-compatibility checker.** `logic/EventBus_SchemaCheck.py` ships `event_schema(...)`, `write_snapshot(path, classes)`, `read_snapshot(path)`, `check_compatibility(snapshot, live)`, and `discover_event_classes(modules)`. The checker walks every Pydantic event class and detects breaking changes (field removed, renamed, type narrowed, optional → required). Additive changes (new fields, new event types) are allowed. CI workflow `.github/workflows/event-schema-check.yml` runs the checker on every PR touching `logic/` or `extensions/`; failure blocks merge. Tests: 19 in `logic/EventBus_test.py` (broker semantics + DLQ + transport contract) + 11 in `logic/EventBus_SchemaCheck_test.py` + 15 in `extensions/valkey/EXT_Valkey_test.py` (extension metadata, URL resolution, fake-transport publish/subscribe round trips, end-to-end EventBus consumption of the Valkey transport, missing-redis error contract).

**Architectural note.** The original Item 42 prose called for "real Kafka/NATS/Redis Streams adapters" inside `lib/EventBus.py`. The user pushed back at audit-close time: native broker SDK clients in core conflict with the framework's "extensions own external backends" pattern (the `database` extension model). The refactor pulled all three SDK-importing transports out of core; the Valkey extension is the in-tree reference for how a backend extension produces a `BrokerTransport`. A future Kafka extension and NATS extension follow the same shape (root provider, `build_*_transport(instance)` method, fake provider for tests).

**Naming policy.** The Valkey extension is named "valkey" (not "redis") per the framework's "FOSS api-parity naming" policy. Valkey is the Linux Foundation–stewarded continuation of Redis after Redis Inc.'s 2024 license change; the wire protocol is identical, so deployments using Redis OSS, Valkey, KeyDB, or DragonflyDB all work with this extension unmodified. The `redis-py` Python client is consumed (the package name on PyPI is still `redis`) because it is the canonical implementation of the wire protocol.

**Severity:** Medium
**Scope:** New event-bus abstraction, adapter pattern for Kafka / NATS / Redis Streams, integration with hooks and outbox.
**Owner area:** Eventing.

**Purpose.** Hooks today are in-process. They are powerful for cross-cutting concerns within a single application, but they cannot fan out events to other services in a microservices deployment. Many extensions will need to publish events to other systems — billing changes to an invoicing service, user signups to a marketing system, audit trails to a SIEM — and without a documented seam, every extension will reinvent the publish mechanism inconsistently.

**Current state.** No cross-process event bus. Hooks remain in-process.

**Target state.** An `AbstractEventBus` with a small contract: publish a typed event, subscribe to a typed event channel. Adapters ship for Kafka, NATS, Redis Streams, and an in-memory adapter for testing and single-process deployments. The bus is opt-in — hooks remain the recommended in-process seam — and it is fed by the outbox (Item 35) so that publish-and-local-mutation are transactional. A `@on_event` decorator subscribes a handler to a bus channel; handlers run in a `QueueConsumerService` (Item 28) so the bus and the service-lifecycle infrastructure are the same.

Events on the bus are typed Pydantic models, versioned, with backward-compatibility rules documented (additive-only, no field renames, no narrowed types) so that subscribers built against an older event version continue to work.

**Implementation notes.** The bus is not a replacement for hooks — it is a complement. Document the decision rule: in-process cross-cutting concerns use hooks; cross-process fan-out uses the bus. Schema registry integration (Confluent Schema Registry, NATS JetStream's schema support) is out of scope for v1 but documented as a future direction. The backward-compatibility rules (additive-only, no field renames, no narrowed types) are aspirational without enforcement; ship a CI compatibility checker (modeled on Item 11's drift snapshots) that diffs new event-model PRs against committed schemas and fails on breaking changes. Without this, the rules are documentation only and will be violated.

**Acceptance criteria.** An extension can publish a typed event via `event_bus.publish(UserCreated(...))` from inside a BLL hook; another service running its own framework instance can subscribe via `@on_event(UserCreated)` and receive the event. The outbox guarantees the publish is atomic with the local user-create. The in-memory adapter lets tests exercise the same code paths without standing up Kafka.

**Dependencies.** Depends on Items 28, 35.

---

## ~~Item 57~~ — ~~Background job priority and per-tenant fairness~~ ✅ DONE

**Severity:** Medium
**Scope:** `QueueConsumerService` (Item 28), outbox (Item 35), event-bus consumers (Item 42), per-tenant queue partitioning.
**Owner area:** Background processing / multi-tenancy.

**Purpose.** Item 28 introduces queue-consumer services and Item 35 introduces the outbox, but neither addresses fairness across tenants under load. A noisy tenant submitting ten thousand jobs will starve a quiet tenant's single job from being processed for an indefinite window, since a single FIFO queue gives the noisy tenant a structural advantage. Worse, an outage in a downstream provider (Stripe is degraded) backs up a single tenant's jobs in the queue and leaves every other tenant waiting behind them. The framework needs a fairness primitive so that bounded-latency processing is achievable per tenant regardless of other tenants' load.

**Current state.** FIFO across all tenants. No fairness, no priority lanes.

**Target state.** Two complementary mechanisms:

- **Per-tenant fair queuing.** The queue-consumer service partitions work by tenant key (typically `team_id`) and drains partitions in round-robin or weighted-fair order. A single tenant's backlog is bounded above by its own throughput; a tenant with no submitted jobs is not penalized for another tenant's backlog. Configuration is at the consumer level: the consumer declares a `tenant_key_resolver: Callable[[Job], str]` and the framework's scheduler enforces fairness.
- **Priority lanes.** Jobs declare a priority class — `high` (transactional, user-blocking, e.g. password resets), `normal` (default, e.g. send a marketing email), `low` (batch, e.g. nightly reconciliation). Within a tenant's partition, lanes drain in priority order; across tenants, fair-share enforces equal treatment within each lane. A high-priority job from any tenant runs before a low-priority job from any tenant; among same-lane jobs, fairness applies.

The combination delivers bounded-latency for high-priority work regardless of low-priority backlog, and bounded fairness across tenants regardless of any one tenant's load. Optional preemption (cancelling a low-priority job mid-execution to free a worker for a high-priority job) is offered but disabled by default — the additional complexity is rarely worth the gain unless workloads are very mixed.

**Implementation notes.** Per-tenant fair queuing is implemented via virtual-time scheduling (Weighted Fair Queuing) at the worker level rather than physical queue partitioning, so there is no proliferation of database queues. The fairness primitive is independent of the underlying queue store (Postgres-as-queue, Redis Streams, SQS) — it lives in the consumer's dispatch layer. Cross-process consumers coordinate via a small per-consumer state store (Redis or a dedicated table) to avoid one process unfairly draining one tenant. Metrics: `queue_wait_seconds{tenant_id, lane}` histogram lets operators see fairness in practice.

**Acceptance criteria.** A tenant submitting ten thousand `normal`-lane jobs does not delay another tenant's single `normal`-lane job by more than the configured fairness bound (typically a few seconds). A `high`-lane job from any tenant runs before any pending `low`-lane jobs, regardless of tenant. Fairness metrics are visible in the standard observability backends.

**Dependencies.** Cross-references Items 28 (queue-consumer service), 35 (outbox is the canonical source of work for many deployments), 42 (event-bus consumers participate in the same fairness model).

---

## ~~Item 56~~ — ~~Audit log retention and archival~~ ✅ DONE

**Severity:** Medium
**Scope:** `meta_logging` extension, retention policy declarations, scheduled archival service.
**Owner area:** Compliance / observability.

**Purpose.** The `meta_logging` extension produces structured audit logs but does not document a retention policy, an archival mechanism, or a deletion contract. For deployments subject to GDPR, HIPAA, SOC 2, or similar regimes, indefinite retention is non-compliant — and so is uncontrolled deletion, since audit trails frequently must be preserved for regulator-defined windows even when the underlying user data is deleted. The framework needs a first-class retention contract so deployments can express the regulatory regime once and have the audit subsystem honor it, rather than every deployment reinventing retention as cron jobs.

**Current state.** No retention policy. No archival contract. Audit logs accumulate indefinitely.

**Target state.** Each audit event class declares a retention window via `retention: ClassVar[RetentionPolicy]`. The policy carries a window (`30d`, `1y`, `7y`, `forever`), an archival target (S3, GCS, on-disk, none), and a `legal_hold: Optional[str]` field for operations that put a class on indefinite hold pending a regulatory action. A scheduled `RetentionService` (built on Item 28's `ScheduledService` flavor) runs nightly: events past their window are first archived to the configured target as compressed, integrity-checked artifacts, then purged from the live audit table. The archival step is non-skippable for events with non-`none` archival targets — the purge step refuses to run if archival did not succeed for any event in the batch.

The audit subsystem itself emits an audit event for each retention pass: how many events were archived, how many purged, the cryptographic digest of the archived artifact. This is the audit-of-the-audit and is itself subject to its own (typically `forever`) retention policy. Together, archival digests and the retention-pass audit trail give a regulator-defensible chain: every audit record can be shown to have been preserved within its window and retired according to declared policy, with verifiable archival on the other side.

**Implementation notes.** The archival target is pluggable per Item 43's object-storage abstract. Archives are written in a stable, consumer-friendly format (JSONL or Parquet) so a regulator can be handed an artifact that a third-party tool can read without framework knowledge. Legal hold is a runtime override that prevents purge regardless of retention window; releasing a hold requires a separate audit event and an admin-level operation. Time-zone handling for window expiration is documented (UTC, with a per-deployment override).

**Acceptance criteria.** An `AuthLoginEvent` declared with `retention=RetentionPolicy(window="1y", archive_to="s3://bucket/audit/")` is archived to S3 one year after creation and then purged from the live table; the archived artifact has a verifiable integrity digest recorded in the retention-pass audit trail. A class under legal hold is preserved past its window until the hold is explicitly released. The retention service runs on schedule, surviving process restarts without missed-window gaps.

**Dependencies.** Cross-references Items 28 (scheduled service), 43 (object-storage abstract for archival target).

**Status: ✅ DONE.** All four remaining pieces have landed: (a) `extensions/RetentionService.py::RetentionService` walks tagged registrations on a nightly `ScheduledService` driven by `make_retention_scheduled_service(...)`, archives expired rows via the registered callback, then purges via the registered delete callback (archive-success-gates-purge enforces compliance); (b) `extensions/RetentionArchive.py::make_object_storage_archive_callback(...)` adapts Item 43's `AbstractObjectStorageProvider` into a compliant archive callback that serializes rows as JSONL (sorted keys), gzip-compresses, builds a stable key `{prefix}/{archive_to}/{ts_ms}-{rowcount}-{sha256_prefix}.jsonl[.gz]`, and uploads via `object_storage.upload(key, body)`; (c) audit-of-the-audit emission via `RetentionService._audit_emit` carries the SHA-256 digest of the archived batch, the row counts, and a `forever_default()` retention policy stamp; (d) legal-hold release admin endpoint at `endpoints/EP_Retention_Admin.py` exposing `POST/GET/DELETE /admin/retention/legal-hold/release[...]`, backed by `RetentionService.release_legal_hold(name, *, reason, requester_id)`, `clear_release(name)`, `is_released(name)`, `list_released()`. Each release operation emits a `retention_legal_hold_release` audit event with `forever` retention. Tests: `RetentionPolicy_test.py` (44 existing), `RetentionService_test.py` (11 existing), `RetentionArchive_test.py` (9 new), `RetentionService_legal_hold_release_test.py` (9 new), `EP_Retention_Admin_test.py` (9 new). Closed by verification (82 passed).

---

# Group 18 — Provider Templates and Typed Seed Data

The vocabulary of abstract providers the framework ships, plus the typing of seed data so initial state passes the same Pydantic gates as runtime creates.

## Item 43 — Abstract provider templates for missing infrastructure categories

**Status: PARTIAL (templates + tool-calling harness complete; concrete production providers remain).** Six `AbstractProvider_*` templates and their `PRV.X.md` contracts shipped (object storage, cache, queue/scheduler, search index, AI/LLM, notification fan-out), along with toy reference implementations. The pre-estimate/post-true-up quota pattern for AI/LLM is implemented in `AbstractProvider_AI`. **Tool-calling testing harness (now landed):** `extensions/PRV_AI_ToolCallingHarness.py` ships `AbstractToolCallingHarness` — a reusable contract test bundle that concrete AI providers extend by overriding `make_provider()` / `make_instance()` / `model_name()`. The harness verifies the response-shape contract (`validate_chat_response_shape`), translates upstream-specific tool-call payloads into the canonical `ToolCall` (`validate_tool_call_payload` handles OpenAI `function.arguments` JSON strings, Anthropic `input` dicts, and the canonical name+arguments dict), runs the full round-trip (model emits `ToolCall` → caller executes → `ToolResult` is fed back into the next chat turn), and validates the pre-estimate / post-true-up quota helpers. The framework's reference `FakeToolCallingProvider` satisfies the harness without an upstream so provider authors have a known-good baseline; concrete providers gate their harness subclass on the `external_api` pytest marker (Item 15) so it auto-xfails when no sandbox credential is configured. Tests: `extensions/PRV_AI_ToolCallingHarness_test.py` (30 tests covering response-shape accept/reject, OpenAI/Anthropic/canonical translation, fake-provider behavior across all abilities, and a `TestHarnessSelfTest(AbstractToolCallingHarness)` subclass that runs the full bundle against the reference fake to prove the harness contract closes). **Outstanding:** concrete production-grade provider implementations (S3/GCS/Azure for object storage, Redis/memcached for cache, Elasticsearch/OpenSearch for search, OpenAI/Anthropic for AI/LLM, push/SMS for notifications) live in separate extensions and have not all landed.

**Severity:** Medium
**Scope:** New abstract providers and accompanying documentation for object storage, cache, queue/scheduler, search index, AI/LLM, and notification fan-out.
**Owner area:** Provider system / extension templates.

**Purpose.** The framework currently ships abstract provider templates for database, email, payment, MFA, and meta-logging. To deliver on its "any back-end" promise, it should also bake in templates (or at minimum documented patterns) for the remaining infrastructure categories every non-trivial application needs. Without these, the first three applications built on the framework will each implement object storage, cache, and job queues three different ways, all incompatible with each other. The templates do not need full reference implementations — they need an `AbstractProvider_*` defining the abilities and a `PRV.X.md` documenting the contract, so that real implementations can land later as separate extensions following the established pattern.

**Current state.** Database, email, payment, MFA, meta-logging templates exist. Object storage, cache, queue, search, AI, notification fan-out are absent.

**Target state.** Six new abstract provider templates land:

- **Object / blob storage** — abilities for upload, download, list, delete, presigned URL generation, multipart upload. Reference targets: S3, GCS, Azure Blob, local filesystem.
- **Cache** — abilities for get, set with TTL, delete, increment, set-if-not-exists, multi-get. Reference targets: Redis, memcached, in-memory.
- **Queue / job scheduler** — abilities for enqueue, schedule (delayed), cron-style recurring, dead-letter, status query. Reference targets: Celery, RQ, Arq, native (using Item 28's `QueueConsumerService`).
- **Search index** — abilities for index, query, delete, bulk-index, schema management. Reference targets: Elasticsearch, OpenSearch, Meilisearch, Algolia.
- **AI / LLM** — abilities for completion, chat, embedding, streaming completion (using Item 13), tool/function calling, image generation. Reference targets: OpenAI, Anthropic, local providers.
- **Notification fan-out** — abilities for push (mobile), SMS, in-app — coordinated alongside the existing email provider so a single "notify" call composes over the existing email contract (it does not replace `EXT_Email`) and selects additional channels per recipient preferences.

**AI/LLM-specific quota model.** AI/LLM consumption is not a fixed unit per call — token usage is variable and only fully known after the response. The framework's quota system (Item 19) must be designed for this from the start rather than retrofitted, otherwise every LLM provider will reinvent budget enforcement. The template establishes a **pre-estimate / post-true-up** pattern as a first-class quota mechanism:

- **Pre-call estimate.** Before issuing the call, the provider computes a conservative upper-bound token estimate (input tokens + `max_tokens` ceiling) and pre-decrements the quota by that amount. If pre-estimate exceeds remaining budget, the call is refused before the upstream is contacted, with `QuotaExhaustedError`.
- **Post-call true-up.** After the response returns with the actual token count, the framework reconciles: if actual < pre-estimate, the difference is credited back; if actual > pre-estimate (rare, but possible for streaming completions whose continuations exceeded the conservative ceiling), the overage is debited and the requester sees a typed `QuotaOverrunWarning` in the response envelope, with the operator able to configure whether overruns hard-fail subsequent calls or warn-and-continue.
- **Streaming-completion handling.** For streamed responses (Item 13), the true-up runs incrementally per chunk so a runaway stream is cancelled when the budget is depleted rather than after the response completes.

**Nested quotas.** AI/LLM budgets are routinely structured as an overall ceiling with per-model sub-ceilings (e.g. "this team has 1M tokens per month overall, of which at most 200K may be against `gpt-4-turbo`"). Item 19's `Quota` table is extended to support hierarchical decrement: a single call debits *every matching row* whose dimension matches (`(team, *)`, `(team, model=gpt-4-turbo)`, `(user, *)`, `(user, model=gpt-4-turbo)`), and is refused if any of them is exhausted. Quota rows declare their dimension via an optional `qualifier: dict` (e.g. `{"model": "gpt-4-turbo"}`); rows without a qualifier match all calls to that ability. The atomic-decrement primitive walks all matching rows in one transaction so partial debits are impossible.

**Tool-calling and structured outputs.** Tool/function-calling differs significantly across upstreams (OpenAI's function calling, Anthropic's tool-use blocks, local-model approaches). The abstract template defines the canonical vocabulary — `ToolDefinition`, `ToolCall`, `ToolResult` — and concrete providers translate to and from their upstream's specific shape via the field-mapping pipeline from Item 6. Structured-output / JSON-mode is similarly normalized to a single `output_format: Optional[JsonSchema]` parameter with per-provider translation. A follow-on item should formalize tool-calling testing once the abstract template is in place; it is non-trivial enough to warrant its own scope.

**Implementation notes.** Each template defines abilities using Item 37's typed-ability declarations, with paired Pydantic input/output models. The documentation describes the contract and references at least one concrete provider implementation (which may live in a separate repository for each). The templates establish the vocabulary so that downstream extensions agree on ability names and signatures. The pre-estimate / post-true-up pattern is implemented inside `AbstractProvider_AI` itself rather than per-concrete-provider, so all LLM providers inherit correct quota semantics by default.

**Acceptance criteria.** Each of the six categories has a committed `AbstractProvider_*.py` and matching `PRV.X.md` describing the abilities and the required settings model. A toy concrete provider for each category (e.g. local-filesystem object storage, in-memory cache) ships as a reference implementation. An LLM call against a near-exhausted budget refuses pre-call when the estimate exceeds remaining; an LLM call that uses fewer tokens than estimated credits the difference back; a nested quota structure with overall and per-model rows correctly decrements both on each call and refuses when either is exhausted.

**Dependencies.** Cross-references Items 13 (streaming for AI/LLM and large-file storage), 28 (queue consumer for the scheduler category), 37 (typed abilities).

---

## ~~Item 77~~ — ~~Reconcile documented Postgres support with the failing test~~ ✅ DONE

**Severity:** Medium
**Scope:** `database/DatabaseManager.py`, `database/DatabaseManager_test.py`, `Framework.md` Multi-Database Support claim.
**Owner area:** Database / providers.

**Purpose.** `Framework.md:81` claims "Multi-database support (PostgreSQL, SQLite, MariaDB, MSSQL, Vector)." `database/DatabaseManager_test.py:268` carries `@pytest.mark.xfail(reason="Postgres not yet supported.")` on the engine-config test. Either the claim is true and the test should be promoted to xpass, or the claim is aspirational and should be tagged so. An extension author choosing the framework on the strength of multi-DB support and discovering the xfail in their first test pass wastes time.

**Current state.** Doc claims production support; test asserts not-yet-supported.

**Target state.** Implement Postgres engine config and connection plumbing in `DatabaseManager` (the xfail test becomes an xpass and is rewritten as a real assertion) **or** demote the `Framework.md` claim to "PostgreSQL support is in progress (Item 77); SQLite is the production-ready default." The choice depends on whether Item 55's Row-Level Security primitive is the operational driver for Postgres support — Item 55 requires Postgres, so if Item 55 lands, this must too.

**Implementation notes.** Postgres engine config is small in isolation (asyncpg/psycopg drivers, connection-string assembly, pool sizing) but interacts with Items 49 (FK-aware migration ordering — Postgres is the test target), 53 (advisory locking — `pg_advisory_lock` is the default backend), 55 (RLS — Postgres-specific), 69 (distributed counter — Postgres-backed default). Resolving Item 77 is effectively the precondition for honest implementation of those four items.

**Acceptance criteria.** `test_init_engine_config_postgresql` passes against a real Postgres instance in CI; `Framework.md` and the test agree.

**Dependencies.** Blocks Items 49, 53, 55, 69 from honest end-to-end testing. Cross-references Item 77 to the broader DB-portability story.

**Resolution.** Reconciled by demoting the framework's claim to honest. `Framework.md:81` now states SQLite is the production-ready default and that PostgreSQL/MariaDB/MSSQL/Vector engine-config branches exist but are gated by Item 77. The xfail on `test_init_engine_config_postgresql` was retained (the engine-config branch is plumbed but the asyncpg/psycopg driver is not pinned and CI does not provision a live Postgres) and rewritten with a precise `reason=` enumerating the unblock requirements (driver pinning, live-Postgres CI, items 49/53/55/69). When that test xpasses, the demotion in `Framework.md:81` should be promoted back. Doc and test now agree.

---

## ~~Item 84~~ — ~~Cost observability per tenant~~ ✅ DONE

**Severity:** Medium
**Scope:** `AbstractProvider_AI` (Item 43) and any provider with billable upstream calls; new `CostModel` per provider; metrics emission.
**Owner area:** Observability / billing.

**Purpose.** Item 43's pre-estimate/post-true-up handles AI/LLM quota in tokens. It does not handle dollars-per-tenant — the actual finance signal operators need. Item 34's metrics are latency and error rate, not cost. A finance team asking "which tenant spent the most on OpenAI last month" has no answer in the documented surface. Cost-attribution drift is the most expensive class of operational bug, because by the time finance notices it, the spend has already happened.

**Current state.** No cost model. No per-tenant cost metric.

**Target state.** Each provider with billable upstream calls declares a `CostModel`: a `cost(request, response) -> Decimal` callable that returns USD (or the deployment's configured base currency). For AI/LLM (Item 43) the cost is `prompt_tokens * prompt_price + completion_tokens * completion_price + per_request_fixed_cost`; for payment processors the cost is the fee component of the transaction (already returned by the upstream); for outbound HTTP generally the cost is null unless the provider specifies. The framework emits `provider_cost_usd_total{tenant, provider, ability}` as a counter and writes per-request cost into the audit log so a tenant's spend across providers is reconstructible from the audit trail. Cost rows roll up daily into a `CostSummary` table for fast queries; the audit log remains the source of truth.

**Implementation notes.** Cost-model callables are pure: they take typed request/response models and return a `Decimal`. Currency conversion is out of scope; a deployment with multi-currency upstreams declares a single base currency and the cost model returns in that currency. The metric label cardinality is bounded (tenant ids are bounded; provider+ability is small). Item 43's overrun configuration ("overruns hard-fail subsequent calls or warn-and-continue") is extended with a per-tenant USD cap that triggers the same hard-fail/warn-and-continue branch.

**Acceptance criteria.** A finance dashboard query of "which tenant spent the most on OpenAI last month" returns an answer from the audit log or `CostSummary` table without a custom report. A provider that lacks a `CostModel` does not contribute to the cost metrics (rather than emitting zero-cost noise).

**Dependencies.** Refines Item 43 (AI/LLM templates declare CostModels). Cross-references Item 34 (metrics emission) and Item 56 (audit-log retention applies to cost rows; cost rows typically need 7-year retention for finance compliance).

**Status: ✅ DONE.** All three remaining pieces have landed: (a) outbound-call hook in `RotationManager.rotate` consults `_resolve_cost_model(provider_instance)` after a successful response, computes `cost_dec = Decimal(str(cost_model((args, kwargs), result)))`, increments `_cost_counter[(tenant, provider, ability)]`, and consults `get_cost_audit_emitter()` (from `extensions/CostAuditEmitter.py`) to write an `AuditLogModel` row tagged `provider_cost`; (b) `extensions/CostSummary.py::CostSummaryModel` (with `tenant_id` / `provider` / `ability` / `period` / `period_key` / `total_cost_usd` / `call_count`) plus `CostRollupAggregator`, and `extensions/CostSummaryService.py::CostSummaryRollup` running on `ScheduledService` (default daily) that drains the in-process `_cost_counter` into `CostSummary` rows and resets the counter only on success; (c) Item 19 quota integration via new `Quota.limit_usd` and `Quota.consumed_usd` fields plus `RotationManager.rotate` USD-cap pre-check (raises `QuotaExhaustedError` when the pre-estimate would exceed `limit_usd - consumed_usd`) and post-call true-up under the `_FALLBACK_LOCK`. Tests: `CostModel_test.py` (21 existing), `CostSummary_test.py` (13 new), `CostSummaryService_test.py` (16 new), `CostAuditEmitter_test.py` (11 new), `Quota_usd_cap_test.py` (new). Closed by verification.

---

## ~~Item 38~~ — ~~Pydantic-typed seed data~~ ✅ DONE

**Severity:** Low
**Scope:** `seed_data` ClassVar / method on Pydantic models, seeding system.
**Owner area:** Database / seeding.

**Purpose.** Seed data today is declared as `List[Dict[str, Any]]`. A typo in a field name, a wrong-typed value, or a missing required field is caught only when the seeding job runs against a live database. For a framework that prides itself on Pydantic-first single source of truth, accepting raw dicts at the seeding boundary is an exception that should not exist.

**Current state.** Seed data is `List[Dict[str, Any]]`.

**Target state.** Seed data is declared as `List[<ModelClass>]` or, equivalently, `List[<ModelClass>.Create]` for entities that distinguish creation from full state. Field typos, type mismatches, and missing required fields fail at import time. The seeding system reads typed instances and writes them through the same validation pipeline as runtime creates, so seeded data is guaranteed to satisfy the same constraints as user-created data.

**Implementation notes.** Existing seed declarations migrate by replacing dict literals with model instantiations. The migration is mechanical and can be scripted. Document the placeholder-resolution pattern (the `_extension_name`, `_provider_name` substitutions) as a typed pre-processor that runs before validation, so placeholders survive the type-check. For forward references between seeds (extension B's seed needing the ID of an entity created by extension A's seed), provide a typed `Ref[Model]("identifier")` placeholder that resolves at seed time after dependency-ordered application; this preserves type safety while supporting cross-extension seed dependencies that the dict-based form papered over with magic strings.

**Acceptance criteria.** A seed entry with a typo in a field name fails at import. Seeded data passes the same validators as runtime creates without re-implementing validation.

**Dependencies.** Independent.

---

# Group 19 — Authentication Extensions: Magic Link and Device Pairing

Two passwordless authentication flows. Each item specifies *both* the framework provisions required to leave the door open and the extension that walks through it. They share `OneTimeTokenMixin` and `PasswordlessGrantRegistry`.

## ~~Item 58~~ — ~~Magic-link (passwordless email) authentication~~ ✅ DONE

**Status:** Framework provisions and extension implementation both landed. 5/5 BLL tests passing.

**Severity:** Medium
**Scope:** Framework provisions in `BLL_Auth.py` (one-time-token primitive, passwordless grant abstraction, `UserManager.login_via_grant` hook target); new `EXT_Auth_MagicLink` extension implementing the user-facing flow.
**Owner area:** Authentication / extensions.

**Purpose.** Magic-link authentication — the user enters their email, receives a one-time link, clicks it, and is logged in — is a baseline expectation of modern SaaS products. The framework today supports password login, MFA (TOTP / email / SMS), recovery codes, and basic-auth / API-key flows, but does not support passwordless email login. An audit of the auth subsystem (`BLL_Auth.py`, `BLL.Authentication.md`, `EXT_Auth_MFA.py`) confirms the building blocks exist — `SessionModel` already carries `requires_verification` and `expires_at`, the recovery-code pattern in `MultifactorRecoveryCodeModel` shows correct one-time-token shape with hash + salt + `is_used`, and the hook system supports BEFORE/AFTER hooks on `UserManager.login` — but the door for a magic-link extension to plug in is not explicitly opened. This item leaves that door open with the smallest possible framework changes and ships the extension itself in the same line item.

**Current state.** No magic-link support. No formal one-time-token primitive (the recovery-code pattern is private to `auth_mfa`). No passwordless grant abstraction in `UserManager`. An author wanting to ship magic-link today must either subclass `UserManager` (touching the core conceptually) or hand-roll the entire flow including the session-issuance path.

### Framework provisions (the door)

The following minimal additions in core land before the extension is authored:

1. **`OneTimeTokenMixin`**, a small reusable model mixin in `src/logic/BLL_Auth.py` that captures the recovery-code pattern as a first-class primitive: `code_hash`, `code_salt`, `expires_at`, `is_used`, `used_at`, `created_ip`, plus the `verify(submitted_code) -> bool` and `mark_used()` methods. The `MultifactorRecoveryCodeModel` is migrated to use this mixin so the pattern has one canonical home. Magic-link tokens, QR-pairing tokens (Item 59), invitation codes, and any future one-time-token primitive build on this. Tokens are hashed at rest using bcrypt with per-token salt; raw codes appear only in the original response and are never recoverable.

2. **`PasswordlessGrant` abstraction** on `UserManager`: a typed `login_via_grant(grant_type: str, grant_payload: BaseModel) -> SessionModel` method that takes a registered grant kind (e.g. `"magic_link"`, `"device_pairing"`, `"oauth"`) and a typed payload, validates the grant via a registered handler, and issues a session. A grant registry (`PasswordlessGrantRegistry`) accepts `(grant_type, validator)` registrations from extensions; the validator is a callable taking the typed payload and returning the authenticated `UserModel` or raising `InvalidGrantError`. This is the explicit hook point — extensions register grant validators against the registry and `UserManager.login_via_grant` dispatches to them.

3. **`SessionModel.grant_type` field**: a new optional `grant_type: Optional[str]` field on the existing `SessionModel`, recording which grant kind issued the session. This is observability — operators can audit which sessions came from password login, magic link, OAuth, device pairing, etc. — and is also consumed by `requires_verification` semantics if a particular grant type warrants step-up before sensitive operations.

4. **Extension dependency declaration**: `EXT_Auth_MagicLink` declares `EXT_Email` as a required dependency via the existing extension-dependency mechanism.

These four changes are scoped narrowly enough to land independently of Items 10 (auth strategies — different concern; that handles outbound auth, this handles inbound passwordless grants) and 18 (permission registry — orthogonal). They do not break any existing auth path.

### Extension implementation (`EXT_Auth_MagicLink`)

Lives in `src/extensions/auth_magic_link/`. Files:

- **`EXT_Auth_MagicLink.py`** — the extension manifest. Declares the extension name, version, dependencies (`EXT_Email`, core auth), settings (`magic_link_ttl_minutes: int = 15`, `magic_link_base_url: str`, `magic_link_email_template: str`).
- **`BLL_Auth_MagicLink.py`** — the BLL.
  - Model `AuthMagicLinkToken(ApplicationModel, DatabaseMixin, OneTimeTokenMixin)`: adds `user_id: str` and `requested_email: str` (so a token issued for `alice@example.com` cannot be used to log in as `bob@example.com` even if the request is replayed against a different user).
  - Manager `MagicLinkManager(AbstractBLLManager, RouterMixin)` with two custom routes via `@custom_route` (Item 40):
    - `POST /v1/auth/magic-link/request` — input `MagicLinkRequest(email: str)`, generates a token, sends an email through `EXT_Email` containing `{magic_link_base_url}?token={raw_code}`, returns `202` with no token in the response (the token reaches the user *only* through email, never the response). To avoid user-enumeration, the response is identical whether the email is registered or not.
    - `POST /v1/auth/magic-link/verify` — input `MagicLinkVerify(token: str)`, validates the token via `OneTimeTokenMixin.verify`, marks it used, and calls `UserManager.login_via_grant("magic_link", MagicLinkGrantPayload(user_id=...))` to issue a session.
  - Grant validator `magic_link_grant_validator`, registered against `PasswordlessGrantRegistry` at extension load.
- **`EP.Schema.auth_magic_link.md`** — documents the two endpoints.
- **`EXT.auth_magic_link.md`** — extension overview, security considerations, configuration.
- **Tests** — covers token expiry, replay protection (a token cannot be used twice), wrong-email replay (a token for `alice@example.com` is rejected when the verify request omits or mismatches the email), email-enumeration absence (request response is identical for registered vs unregistered emails), TTL enforcement, and the integration with `SessionModel.grant_type="magic_link"`.

**Implementation notes.** Token entropy is at least 256 bits, base64url-encoded for URL safety. The TTL default of fifteen minutes is short enough to mitigate replay from leaked emails and long enough for a normal user flow. Rate limiting on `POST /v1/auth/magic-link/request` is enforced per email and per IP (default ten requests per hour per email, twenty per IP) to prevent email-based denial-of-service. The verify endpoint is hardened against timing attacks via constant-time hash comparison (already provided by `OneTimeTokenMixin.verify`). When a token verifies, all *other* outstanding tokens for the same user are invalidated, so a user requesting three links and clicking the latest cannot then have an attacker click an older one. The email template is configurable per deployment but the default ships with a clear from-address, subject, and "you didn't request this — ignore this email" copy.

**Acceptance criteria.** A user submitting their email to `POST /v1/auth/magic-link/request` receives an email containing a one-time link. Clicking the link issues a fully-functional session indistinguishable from password-login except for `SessionModel.grant_type="magic_link"`. Replay of the same token fails. A token used outside its TTL fails. Rate limiting kicks in under abuse. No user enumeration is possible from the request endpoint. The extension installs and uninstalls cleanly without touching core code.

**Dependencies.** Framework provisions are independent of other items but build cleanly atop Item 40 (custom routes) and Item 22 (blocking-vs-non-blocking hooks for the audit AFTER hook). Cross-references Items 18 (permission registry — magic-link sessions issue with the user's full role grants subject to the freshness gate per Item 18's refinement), 32 (the email template's from-address is a `Secret`-marked setting), 41 (typed hook context — the registered grant validator can be a typed hook).

**Refinement after audit.** Magic-link sessions issue with the user's full role grants but **only for non-sensitive permissions**. The first attempt to use a `sensitive=True` permission against a magic-link session triggers a step-up MFA challenge (TOTP / email / SMS via `EXT_Auth_MFA`) before the operation proceeds; on success the session is upgraded in place to "freshly-verified" and the freshness window from Item 18 begins. This satisfies the freshness invariant without forcing an immediate MFA prompt on every magic-link login (which would defeat the UX win the flow is designed for).

---

## ~~Item 59~~ — ~~QR-code device-pairing authentication (Steam Guard / Discord style)~~ ✅ DONE

**Status:** Framework provisions, SSE approval channel, and extension implementation all landed. 9/9 BLL tests passing covering expiry, replay, denial, unauthenticated approve rejection, polling parity, and SSE generator.

**Severity:** Medium
**Scope:** Framework provisions in `BLL_Auth.py` (pending-session approval state, real-time approval channel, cross-device grant abstraction); new `EXT_Auth_DevicePairing` extension implementing the user-facing flow.
**Owner area:** Authentication / extensions.

**Purpose.** "Scan this QR code with your already-logged-in mobile app to log in here" is the pairing flow popularized by Steam, Discord, WhatsApp Web, and most recent SaaS desktop applications. Compared with magic-link, it has two attractive properties: it requires no email round trip (the new device's wait time is bounded by how long the user takes to pick up their phone), and it gives the *already-authenticated* device cryptographic certainty that the new device's request is in the same physical room (the QR is on the new device's screen, the camera is on the authenticated device — an attacker has to get visual access to either the new device or the photo of the QR). The framework today has neither the pending-session primitive nor the cross-device approval channel, so a deployment wanting this flow must either author it in the core or skip the feature. This item leaves the door open with framework primitives and ships the extension.

**Current state.** No QR-pairing support. `SessionModel.requires_verification` exists but has no documented "approval pending" workflow. No cross-device approval channel (the inbound webhook infrastructure from Item 5 is the closest primitive but is shaped for upstream-provider events, not intra-application device approvals). The investigation in `BLL_Auth.py` confirms `SessionModel` already carries the right shape (`session_key`, `expires_at`, `revoked`, `requires_verification`, `trust_score`, `device_type`, `device_name`) and the `OneTimeTokenMixin` from Item 58 covers the QR-token primitive — what is missing is the approval-channel and the grant abstraction.

### Framework provisions (the door)

The following minimal additions in core land before the extension is authored. Items 58 and 59 share the `OneTimeTokenMixin` and the `PasswordlessGrantRegistry`; this item additionally adds:

1. **`PendingSessionState`** — a new field `SessionModel.pending_state: Optional[Literal["awaiting_approval", "approved", "denied"]]`. A session created in `awaiting_approval` is not yet usable — bonded sessions check the state and refuse to authorize requests until the state is `approved`. The `requires_verification` flag becomes a derived getter from `pending_state`, preserving backward compatibility for callers that set it explicitly.

2. **Real-time approval channel** — a small SSE endpoint `GET /v1/auth/pairing/{pairing_id}/stream` that the unauthenticated client subscribes to after generating its QR. The endpoint streams approval-status updates (`pending`, `approved+session_token`, `denied`, `expired`) so the client receives the result within milliseconds of the authenticated device approving, without polling. Polling fallback is supported via `GET /v1/auth/pairing/{pairing_id}/status` for clients that cannot maintain SSE. The SSE channel is implemented using the streaming-service infrastructure from Item 13 once landed; until then a simpler ASGI-streaming response suffices.

3. **`CrossDeviceGrant` abstraction** — extends the `PasswordlessGrantRegistry` from Item 58 with a grant kind whose validation requires *another* authenticated requester to approve, rather than a token in the unauthenticated request. The grant validator signature is `(pairing_request, approver_session) -> UserModel`, distinguishing it from the magic-link single-step validator. The framework's `UserManager.login_via_grant` for cross-device grants accepts the approver-session context as part of the grant payload.

These three changes are scoped narrowly. They do not require Item 5's webhook infrastructure (this is intra-application, not cross-system) and they do not require Item 13's streaming service to land first (the SSE endpoint is an ASGI-streaming response in the simple form), though they integrate cleanly with both when those items ship.

### Extension implementation (`EXT_Auth_DevicePairing`)

Lives in `src/extensions/auth_device_pairing/`. Files:

- **`EXT_Auth_DevicePairing.py`** — the extension manifest. Declares dependencies (core auth, optionally `EXT_Auth_MFA` if step-up is required for approval), settings (`pairing_ttl_seconds: int = 300`, `pairing_qr_payload_format: Literal["url","raw_token"] = "url"`, `pairing_base_url: str`, `require_approver_mfa: bool = False`).
- **`BLL_Auth_DevicePairing.py`** — the BLL.
  - Model `DevicePairingRequest(ApplicationModel, DatabaseMixin, OneTimeTokenMixin)`: adds `requesting_device_type: str` (mobile / desktop / web), `requesting_device_name: Optional[str]` (a human-readable nickname like "Office Chrome"), `requesting_ip: str`, `requesting_user_agent: str`, `approver_user_id: Optional[str]` (set on approval), `approved_at: Optional[datetime]`, `denied_at: Optional[datetime]`, `pending_session_id: Optional[str]` (the `awaiting_approval`-state session that the client will receive on approval).
  - Manager `DevicePairingManager(AbstractBLLManager, RouterMixin)` with three custom routes (Item 40):
    - `POST /v1/auth/pairing/request` — input `PairingRequest(device_type, device_name)`, generates a `DevicePairingRequest` with a fresh one-time token and an `awaiting_approval` session reserved, returns `PairingResponse(pairing_id, qr_payload, expires_in)`. The `qr_payload` is either a deep link URL (`{pairing_base_url}/approve?token={raw_token}`) for mobile-app interception or the raw token for QR libraries to encode directly.
    - `POST /v1/auth/pairing/approve` (authenticated) — input `PairingApprove(token: str)`, called by the *already-authenticated* device after it scans the QR. The endpoint validates the token via `OneTimeTokenMixin.verify`, optionally requires step-up MFA from the approver per `require_approver_mfa`, marks the pairing approved, transitions the reserved pending session to `approved`, attaches `approver_user_id`, and writes an audit event. The approver's user identity becomes the new device's user identity (the new device logs in as the approver, not as a separate identity).
    - `POST /v1/auth/pairing/deny` (authenticated) — input `PairingDeny(token: str)`, called when the approver explicitly rejects the pairing. Marks the request denied, invalidates the pending session, audits.
    - `GET /v1/auth/pairing/{pairing_id}/stream` (unauthenticated) — SSE endpoint subscribed by the new device after request, streams the resolution in real-time. The stream emits at most one status event then closes; the framework enforces a maximum stream lifetime equal to the pairing TTL.
    - `GET /v1/auth/pairing/{pairing_id}/status` (unauthenticated) — polling fallback, returns the same status payload as the SSE stream.
  - Grant validator `device_pairing_grant_validator`, registered against `PasswordlessGrantRegistry` as a `CrossDeviceGrant`.
- **`EP.Schema.auth_device_pairing.md`** — documents the five endpoints and the SSE event format.
- **`EXT.auth_device_pairing.md`** — extension overview, security considerations, threat model, configuration.
- **Tests** — covers token expiry (request after TTL fails), replay protection (a token cannot approve twice), denial path (the new device receives `denied` and cannot retry the same token), unauthenticated approve attempt (rejected with 401), step-up MFA enforcement when configured, SSE-stream resolution end-to-end, polling-fallback parity with SSE, the awaiting-approval session is unusable until approved, the new device's session matches the approver's user identity exactly.

**Implementation notes.** The QR payload includes the token directly so a malicious bystander photographing the QR could approve elsewhere — this is mitigated by the short TTL (five minutes default), by the requirement that the approver be already authenticated on a trusted device, and by the audit trail recording the approval IP and device. For high-security deployments, the extension supports an optional `pairing_requires_proximity_proof: bool` setting that adds a numeric short-code visible on both devices that the approver must confirm matches what the new device displays — the same out-of-band confirmation pattern Discord uses. Rate limiting on `POST /v1/auth/pairing/request` prevents pairing-request flooding (default twenty per IP per hour). The SSE stream is bounded by the pairing TTL and closes on any terminal state. The `pending_state="awaiting_approval"` session reservation strategy avoids a race where two parallel requests issue overlapping sessions: the session is created up front in the un-usable state, and approval flips the state atomically rather than creating a new session.

**Threat model summary.** (1) Photo-of-QR attack: an attacker photographs the QR over-the-shoulder. Mitigated by short TTL, optional proximity proof, and the fact that the approver still must consciously approve from their authenticated device. (2) Approver-device-compromise: if the approver's already-authenticated device is malicious or compromised, it can approve arbitrary pairings — this is fundamental to the model and is the same trust assumption Steam Guard and Discord operate under. (3) New-device-MITM: an attacker intercepts the QR-payload URL from the new device's display. Mitigated by short TTL and TLS on every endpoint. (4) Session-substitution: the awaiting-approval session is unusable, so even if the QR is leaked the leaked session has no authority until approved.

**Acceptance criteria.** A new device calls `POST /v1/auth/pairing/request` and displays the returned QR. The approver scans the QR on their authenticated device, the device-pairing app calls `POST /v1/auth/pairing/approve`, and the new device's SSE stream immediately receives an `approved` event with the now-usable session token. The new device's session is bound to the approver's user identity. Denial works symmetrically. Expired pairings cannot be approved. Replay of an approved token fails. Polling fallback returns the same final state as the SSE stream. The extension installs and uninstalls cleanly without touching core code.

**Dependencies.** Framework provisions are independent. Cross-references Items 13 (streaming service — the SSE channel migrates to it once landed), 18 (permission registry — pairing sessions issue with the approver's full role grants subject to the freshness gate per Item 18's refinement), 22 (blocking-vs-non-blocking hooks for audit), 40 (custom routes), 41 (typed hook context), 58 (shares `OneTimeTokenMixin` and `PasswordlessGrantRegistry`).

**Refinement after audit.** Same freshness-gate treatment as Item 58: pairing sessions issue with the approver's full role grants but only for non-sensitive permissions; first attempt to use a `sensitive=True` permission triggers step-up MFA before the operation proceeds. Additionally, when `require_approver_mfa=True` is configured, the approver's step-up MFA at approval time satisfies the freshness gate transitively for the new device's session, since the approver has just demonstrated possession-of-second-factor in the same approval flow that issued the session — this is the one path where a grant-issued session legitimately starts in the freshly-verified state.

---

# Group 20 — Public Contracts Documentation

A single canonical reference for every primitive an extension author touches.

## ~~Item 52~~ — ~~Single `EXT.Contracts.md` enumerating the framework's public primitives~~ ✅ DONE (94-entry JSON manifest + autogen tooling + 97-test guardrail)

**Severity:** Low
**Scope:** New documentation file collating contracts referenced from elsewhere.
**Owner area:** Documentation.

**Purpose.** Several framework primitives are referenced repeatedly across the existing documentation as if defined: `ExtensionRegistry`, `RotationManager`, `ProviderInstanceModel`, `external_navigation_property`, the non-BLL `@hook` decorator, `@extension_model`, `@custom_route`, `AbstractExternalAPIClient`, `AbstractProviderInstance`. Their full signatures, invariants, and intended usage are scattered across multiple files (or, in some cases, only present implicitly in code). An extension author piecing together the contract from prose ends up guessing, and guessing reliably produces extensions that almost-but-not-quite follow the framework's expectations. A single canonical reference document closes this gap.

**Current state.** Primitives are referenced everywhere, defined in scattered places, sometimes implicit in code only.

**Target state.** A new `EXT.Contracts.md` enumerates every primitive an extension author touches, with the full typed signature, the invariants the framework guarantees, the invariants the extension must uphold, the recommended usage pattern, and cross-references to the deeper documents that describe the surrounding system. The document is generated where possible from docstrings and type annotations, so it stays in sync with the code; sections that cannot be generated (invariants, recommended usage) are written by hand and reviewed on each public-API change.

**Implementation notes.** The generation step is a simple Sphinx-like or `pdoc` pass over the public symbols, producing the typed-signature half. The invariant and usage sections are hand-written stubs that fail CI when a new public symbol lands without a matching entry. Document the policy: if a symbol does not appear in `EXT.Contracts.md`, extension authors should not use it — it is not stable. To keep the manifest reliable, commit a JSON manifest of expected public primitives alongside the markdown file and have CI fail when (a) a public symbol is added without a matching manifest entry, or (b) a manifest entry has no corresponding documented contract. Pdoc-style generation alone is brittle under tool churn; the manifest is the source of truth.

**Acceptance criteria.** Every primitive named in `EXT.Patterns.md`, `PRV.Patterns.md`, `PRV.External.md`, and the BLL/EP/SDK pattern docs has a corresponding entry in `EXT.Contracts.md`. Adding a new public primitive requires adding its contract entry in the same change.

**Dependencies.** Transitively depends on every item that introduces a public primitive: 1 (typed errors), 2 (`RotationPolicy`), 4 (`@idempotent`), 5 (`@webhook_handler`, `WebhookContext`), 10 (`AuthStrategy` and concrete subclasses), 14 (`@mirror_on_*`), 17 (`RateLimit`), 22 (`@hook_bll(blocking=...)`), 23 (`@extension_model`), 26 (`AbstractProviderInstance`), 28 (service flavors), 32 (`CredentialRef`, `Secret[T]`), 37 (`Settings`, `EnvSchema`), 40 (`@custom_route`, `AbstractActionEndpoint`), 41 (`HookContext[P, R]`), 43 (abstract provider templates), 48 (`DegradationPolicy`), 53 (`acquire_lock`, `AdvisoryLock`), 56 (`RetentionPolicy`), 58 (`OneTimeTokenMixin`, `PasswordlessGrantRegistry`), 59 (`PendingSessionState`, `CrossDeviceGrant`), 68 (formerly P9 — `serverframework.__all__`), 69 (`DistributedCounter`).

**Refinement after audit.** This item's original "Independent" dependency claim was incorrect; it transitively depends on every item that ships a public primitive (the dependency line above is the explicit list). It must be sequenced **last** in any build plan and is best implemented as code-generation from docstrings/type annotations against a committed manifest of expected primitives, so that contributors adding a new public primitive in any other item are forced to also add its contract entry in the same change (the CI check from the implementation notes enforces this). Earlier confusion about "Independent" came from misreading "the document can be authored without prerequisites" — true only for the empty stub; the populated document depends on everything.

---

## ~~Item 68~~ — ~~Documented public API surface (formerly P9)~~ ✅ DONE

**Severity:** Low
**Scope:** `serverframework/__init__.py`, package docstring, optional `serverframework.types` re-export module.
**Owner area:** Documentation / packaging.

**Purpose.** `serverframework/__init__.py` re-exports `instance`, `build_app`, `run`, and `set_extensions_root`. There is no formal contract about what is stable vs. internal — anyone who imports `serverframework.lib.Pydantic.ModelRegistry` is doing so at their own risk, but nothing tells them so. The first external consumer that depends on an internal symbol locks the framework into preserving that symbol forever, even though it was never intended to be public.

**Current state.** Re-exports exist with no `__all__`. No documented internal-vs-public boundary.

**Target state.** `serverframework/__init__.py` declares `__all__` listing the committed public API. The package docstring documents that anything not in `__all__` is internal and may change without notice. Optionally a `serverframework.types` module re-exports the Pydantic models that consumers need to type-hint against (`UserModel.Create`, `SessionModel`, etc.) so the type-imports do not pull from internal modules.

**Implementation notes.** The `__all__` contract is enforced by Item 52's contracts manifest: a symbol is in `serverframework.__all__` if and only if it has a corresponding entry in `EXT.Contracts.md`. CI fails on drift in either direction. The `serverframework.types` re-export module is the recommended import surface for consumers writing type hints; the rule is "anything you `from serverframework.lib.Pydantic import ...` is at your own risk." Nobody is depending on internals yet; lock this down before the first external consumer ships.

**Acceptance criteria.** Importing a symbol named in `__all__` from `serverframework` works and is documented as stable. Importing anything not in `__all__` works but produces a documented "internal API" disclaimer. CI fails when a symbol is added to `__all__` without a matching `EXT.Contracts.md` entry.

**Dependencies.** Cross-references Item 52 (the public-primitives manifest is the source of truth). Blocked on Item 60 (rename) for the final `__all__` to live under `serverframework`.

---

## ~~Item 78~~ — ~~Document and contract the Localization subsystem~~ ✅ DONE

**Severity:** Low
**Scope:** `src/Localization.py` (1163 lines, undocumented at audit time), `Framework.md`, `lib/LIB.Overview.md`, new `LIB.Localization.md`, `EXT.Contracts.md` entry from Item 52.
**Owner area:** Documentation / i18n.

**Purpose.** A 1163-line `Localization.py` module ships in `src/` with locale loading, a gettext-style translation API, and singleton management. It is not mentioned in `Framework.md`, in `lib/LIB.Overview.md`, or in any item of this document prior to the fourth-round audit. Extension authors who need to localize user-facing strings (email templates from Item 58, error messages from Item 1's typed exceptions, audit-log copy from Item 56) cannot discover that the primitive exists. Worse, the module's public surface is not pinned to Item 52's contract manifest, so changes to it can silently break consumers that found it by spelunking.

**Current state.** Module exists and works (no test failures). Documentation absent. Not in `EXT.Contracts.md`. `lib/LIB.Overview.md:9` references a nonexistent `LIB.Environment.md`, suggesting the LIB-doc directory has drifted from the code; Localization is the most-egregious example.

**Target state.** A new `lib/LIB.Localization.md` documents the subsystem: locale loading order, the translation API (`_("string")` style or whatever the module actually exposes), how extensions register their own translation files, fallback locale, and the singleton lifecycle. `Framework.md` cross-references it from the architecture section. `EXT.Contracts.md` (Item 52) lists every public Localization symbol as a stable contract. The broken `LIB.Environment.md` cross-reference at `lib/LIB.Overview.md:9` is fixed in the same change (it should point at `LIB.Dependencies.md` per the actual file at `lib/Dependencies.py`).

**Implementation notes.** Read the module before writing the doc; its public surface is what the doc describes, not what the doc would prefer. If the module's design has known shortcomings (e.g., singleton at module-import time prevents per-request locale switching, or no extension-contributed catalog mechanism), surface those as separate items rather than papering over them in the doc. The fix to the broken cross-reference is one line.

**Acceptance criteria.** `lib/LIB.Localization.md` exists and accurately describes the live module. `Framework.md` cross-references it. `EXT.Contracts.md` lists the public surface. `lib/LIB.Overview.md:9` no longer references a nonexistent file.

**Dependencies.** Cross-references Item 52 (contracts manifest), Item 68 (public API surface).

**Status: COMPLETE.** `lib/LIB.Localization.md` exists and the public Localization surface is documented; the contracts-manifest entry from Item 52 covers it. The previously-broken xref at `lib/LIB.Overview.md:9` now points at `LIB.Dependencies.md` (which integrates `Environment.py` under "Environment Integration") rather than the nonexistent `LIB.Environment.md`. Closed.

---

# Group 21 — Distribution and Packaging

The breaking and ecosystem-shaping pieces of the pip-package conversion that follow the additive groundwork commit `cf5cc68`, plus the supply-chain hygiene every PyPI release needs. The non-packaging former-P items (P2/61, P3/62, P5/64, P6/65, P9/68) live in their topical groups; this group holds the items whose primary concern is package layout and release safety.

## ~~Item 60~~ — ~~Rename top-level packages under a single namespace (formerly P1)~~ ✅ DONE

**Severity:** Critical
**Scope:** `src/` layout; every absolute import in the tree; `extensions_path` synthesized module names; migration env scripts.
**Owner area:** Packaging.

**Purpose.** The framework currently exposes `lib/`, `logic/`, `database/`, `endpoints/`, `extensions/`, `sdk/`, and `pydantic2/` as **top-level** packages. Names like `lib` and `database` are virtually guaranteed to collide with other packages on a consumer's `sys.path`, and `logic` / `endpoints` are generic enough that any non-trivial application is likely to want them too. The façade shipped in `cf5cc68` papers over this for the moment by inserting `src/` onto `sys.path` at import time, but that is a short-term hack — as soon as a consumer has their own `lib/` or `database/` package, imports will resolve to whichever one happened to land on the path first.

**Current state.** Top-level packages collide with virtually any consumer namespace. `serverframework/__init__.py` mutates `sys.path` to make in-package imports resolve.

**Target state.** Move every top-level package under a single namespace: `src/serverframework/{lib,logic,database,endpoints,extensions,pydantic2,app.py,bootstrap.py,__init__.py}`. Rewrite every absolute import: `from lib.Logging import logger` → `from serverframework.lib.Logging import logger`; `from logic.BLL_Auth import UserManager` → `from serverframework.logic.BLL_Auth import UserManager`; and so on for every `from extensions.…`, `from endpoints.…`, `from pydantic2.…` site. This touches hundreds of import sites but the change is mechanical and can be driven by a codemod (`ruff check --select I --fix` will not help; use a targeted `sed` or `libcst` script).

**Implementation notes.** Test discovery patterns (`pytest`'s `python_files = "*_test.py"` with `--import-mode=importlib`) need to keep working — verify after the rename. The `extensions/<name>/EXT_…` import strings constructed dynamically inside `ExtensionRegistry` need updating: today they say `f"extensions.{ext_name}.{file}"`; after the rename they need to say `f"serverframework.extensions.{ext_name}.{file}"` (the path-override case from Item 61 produces neither). Migration env scripts under `database/migrations/env.py` likely reference `database.…` modules — update those too. `sdk/` is excluded from this rename; see Item 64.

**Acceptance criteria.** A consumer with their own `lib/` or `database/` package can `pip install serverframework` and import the framework without collision. The `sys.path.insert(0, str(_SRC_DIR))` hack in `serverframework/__init__.py` is removed (Item 66 closes this loop). The full test suite passes after the rename.

**Dependencies.** Independent. Blocks Items 62 (env.py moves with the rename), 64 (SDK separation moves `src/sdk/` out), 66 (sys.path cleanup), 68 (final `__all__` lives under `serverframework`).

---

## ~~Item 63~~ — ~~Console entry point and module entry (formerly P4)~~ ✅ DONE

**Severity:** High
**Scope:** New `serverframework/__main__.py`, `[project.scripts]` entry in `pyproject.toml`, `serverframework.cli` module, `python app.py` forward.
**Owner area:** Packaging.

**Purpose.** The promised end state is "`main.py` is just `from serverframework import run; run(...)`," which works as of `cf5cc68`. But for the case where someone wants to invoke the server without writing a `main.py` at all, the package should also expose a console script (`server-framework run --extensions payment --extensions-path ./exts`) and a module entry (`python -m serverframework run …`). Without these, the framework presents two install pathways (clone-and-run-app.py, pip-install-and-write-main.py) and a third use case (pip-install-and-just-run) is left undiscoverable.

**Current state.** `python app.py` works via the existing entry. The pip-installed pathway requires writing a `main.py`. No console script. No `__main__.py`.

**Target state.** Add `serverframework/__main__.py` that parses argv (argparse) and forwards to `run()`. Add a `[project.scripts]` entry to `pyproject.toml`: `server-framework = "serverframework.cli:main"`. Make the existing `python app.py` path forward to the same CLI so there is one source of truth for the run loop. The bootstrap (`bootstrap.py`) is now self-contained and can be invoked from the CLI as a separate subcommand (`server-framework bootstrap`) for the "first run on a fresh checkout" case.

**Implementation notes.** The CLI module is the canonical entrypoint; `python app.py`, `python -m serverframework`, and the `server-framework` console script all dispatch through it. Keep argv parsing minimal (argparse) — the framework's runtime configuration lives in env vars (Item 65 makes this safe across import order), not in CLI flags.

**Acceptance criteria.** `pip install serverframework` exposes the `server-framework` command in the user's PATH; `python -m serverframework run` and `server-framework run` and `python app.py` all start the same server with the same configuration.

**Dependencies.** Depends on Item 60 (rename — `serverframework.cli` lives under the namespace package). Cross-references Item 65 (lazy env so CLI flags do not race with import-time env capture).

---

## ~~Item 66~~ — ~~Drop `sys.path` mutation from the façade (formerly P7)~~ ✅ DONE

**Severity:** Medium
**Scope:** `serverframework/__init__.py`.
**Owner area:** Packaging.

**Purpose.** `serverframework/__init__.py` currently does `sys.path.insert(0, str(_SRC_DIR))` followed by `from app import build_app, instance`. This is the mechanism that lets the façade import the un-renamed top-level modules. It is load-bearing today but evil long-term: mutating `sys.path` from a library is exactly the sort of thing that makes packages hard to vendor, embed, or run from a zipapp, and silently masks namespace collisions a consumer would otherwise catch immediately.

**Current state.** `__init__.py` mutates `sys.path` and imports from un-namespaced modules.

**Target state.** Remove the `sys.path` insertion and the `from app import …` once Item 60 (rename) lands. Replace with `from serverframework.app import build_app, instance`. This is purely a cleanup that is blocked on Item 60.

**Implementation notes.** The change is a few lines but it is the visible signal that the rename has fully landed; until this drops, the package still has the workaround in plain sight.

**Acceptance criteria.** `serverframework/__init__.py` contains no `sys.path` mutation. Consumers can run the framework from a zipapp.

**Dependencies.** Blocked on Item 60.

---

## ~~Item 67~~ — ~~Source the version string from package metadata only (formerly P8)~~ ✅ DONE

**Severity:** Low
**Scope:** `src/version` (file), `build_app` version-resolution logic, `pyproject.toml`.
**Owner area:** Packaging.

**Purpose.** `build_app` reads a sibling `version` file (now with an `importlib.metadata.version` fallback). Once the package is installed from a wheel, the metadata is authoritative; the sibling file is dead weight that risks drifting from the actual release version.

**Current state.** Two sources of truth for the version string. The sibling file wins on uninstalled checkouts; the metadata wins on installed wheels.

**Target state.** Remove `src/version` from the repo. Remove the file-read fallback in `build_app`. Source `[project.version]` from a single place (e.g., dynamic versioning via `setuptools-scm` keyed off git tags) so the version string is never out of sync with the release.

**Implementation notes.** Works fine as-is; this is paperwork and ships with the rest of the packaging cleanup. `setuptools-scm` is the recommended version source because it ties the version string to git tags, which is also the input to Item 86's release-signing pipeline.

**Acceptance criteria.** Only one source of the version string exists in the repo, and it derives from git tags via `setuptools-scm` (or equivalent).

**Dependencies.** Independent. Cross-references Item 86 (release tagging is the upstream of both this and supply-chain signing).

---

## ~~Item 86~~ — ~~Supply-chain hygiene for the published package~~ ✅ DONE

**Severity:** High
**Scope:** PyPI release pipeline; `pyproject.toml` and CI configuration; SBOM generation; signed releases; pinned-hash policy; vuln scanning.
**Owner area:** Security / packaging.

**Purpose.** Items 60-67 ship `serverframework` to PyPI as a public package that integrates a credential vault (Item 32) and ships supply-chain-attractive primitives (auth strategies, OAuth integration). A package with that surface cannot ship to PyPI without the supply-chain hygiene the rest of the Python ecosystem has standardized on. The audit found zero items addressing SBOM, signed releases, dependency pinning, or supply-chain attack defense across P1-P9 (Item 67 mentions setuptools-scm — that is "where does the version string come from," not supply-chain).

**Current state.** No SBOM. No signed releases. No pinned-hash policy. No vuln scanning gate.

**Target state.** **(a) SBOM.** Every released wheel ships a CycloneDX SBOM (`bom.json`) generated at build time from the wheel's metadata; the SBOM is published as a release asset alongside the wheel. **(b) Signed releases.** Wheels are signed with sigstore/cosign as part of the release pipeline; the signature is verifiable from the public sigstore transparency log without requiring the framework to maintain its own signing-key infrastructure. **(c) Pinned hashes.** `pyproject.toml`'s dependency declarations remain version-range expressions, but the published wheel ships with a `requirements.lock` (or equivalent) that pins each transitive dependency to a specific version+hash; consumers using the lock get reproducible installs. **(d) Vuln scanning.** CI runs `pip-audit` or `safety` against the lock on every PR and on a nightly cron; a finding above a configurable severity threshold fails the build and opens an issue. **(e) Security policy.** A `SECURITY.md` documents the supported-versions matrix, the disclosure channel, and the response-time commitment.

**Implementation notes.** Sigstore is the recommended signing path because it does not require the project to maintain its own signing keys (the developer's GitHub/Google identity is the trust anchor via OIDC). The CycloneDX format is the industry-standard SBOM shape; the `cyclonedx-python` tool generates one from `pyproject.toml`. `pip-audit` is the recommended vuln scanner because it consumes the same lock format `pip-tools` produces. The policy document follows GitHub's `SECURITY.md` conventions.

**Acceptance criteria.** A released wheel on PyPI has a verifiable sigstore signature and a published `bom.json`. The release pipeline fails if any direct or transitive dependency has an open critical vulnerability. The repository carries a `SECURITY.md` with the disclosure channel and response-time commitments.

**Dependencies.** Cross-references Item 60 (rename ships before the first signed release), Item 67 (version string is the input to release tagging). Blocks the first PyPI publish.

**Status: ✅ DONE.** All five target-state pieces in place: (a) CycloneDX SBOM generated in `.github/workflows/release-sign-and-sbom.yml` via `cyclonedx-bom` and published as a release asset (`bom.json`); (b) sigstore keyless OIDC signing in the same workflow attaches `.sigstore` bundles for both wheel and sdist on every tag push, leveraging GitHub Actions' `id-token: write` permission so no maintained signing key is needed; (c) `requirements.lock` with pinned hashes (already shipped); (d) `pip-audit` cron in new `.github/workflows/pip-audit-cron.yml` runs nightly + on every PR touching `requirements.lock`/`pyproject.toml`, fails the build at HIGH+ severity, opens a GitHub issue automatically on scheduled-run failure (already shipped pyproject.toml config + new workflow); (e) `SECURITY.md` (already shipped) updated to reference the new release workflow path so the verification command in the doc matches the actual identity certificate. The library-level surface (`lib/SupplyChain.py` with `generate_sbom`, `verify_sigstore_signature`, `run_pip_audit`) is wired and documented; tests cover the SBOM JSON shape, the pip-audit severity gate, and the sigstore stub's clear error contract.

---

# Group 22 — Inbound Surface Hardening

The framework already documents and partially implements per-extension rate limits (e.g. `meta_logging_rate_limiting_hook`) and per-flow throttling (Items 58, 59), but there is no canonical primitive for "this endpoint is publicly exposed and needs to be defended against abuse." Group 22 fills that gap.

## ~~Item 71~~ — ~~CORS policy, inbound rate limiting, and brute-force/lockout protection~~ ✅ DONE

**Severity:** High
**Scope:** `app.py:334` CORS middleware; new `@rate_limit(...)` endpoint decorator; new `LockoutPolicy` per auth flow; integration with Item 69's distributed counter.
**Owner area:** Inbound API surface / security.

**Purpose.** Three distinct gaps share one entrypoint. **(a) CORS.** `src/app.py:334` configures `allow_origins=["*"]` together with `allow_credentials=True` — the RFC-violating combination that browsers will treat as `null` origin. There is no documented production CORS policy, no per-deployment override, no environment-based origin filtering. **(b) Inbound rate limiting.** Item 17 protects the framework's calls *to* upstream providers; nothing protects the framework *from* inbound abuse. The existing `meta_logging_rate_limiting_hook` is a per-extension instance, not a generic primitive. Items 58 and 59 each invent per-endpoint defaults ("ten requests per hour per email"), reinventing the wheel. **(c) Brute-force / lockout.** Auth flows (password login, magic-link request, MFA verify, device-pairing approve) have no documented lockout policy. A single attacker can brute-force a magic-link verify endpoint until the TTL expires.

**Current state.** CORS wildcards. Per-extension rate-limit hooks. No generic `@rate_limit` decorator. No `LockoutPolicy`. No anomaly-detection hook.

**Target state.** **(a) CORS.** Production deployments declare allowed origins via a config primitive (`APP_CORS_ALLOWED_ORIGINS=https://app.example.com,https://admin.example.com`); the framework refuses to start in `APP_ENV=production` if origins are wildcarded. Development deployments may continue to use `*` with a startup warning. The `allow_credentials=True` path is rejected when origins are `*` (RFC compliance, not just our preference). **(b) Inbound rate limiting.** A `@rate_limit("100/min", scope="ip")` decorator on `RouterMixin` methods, custom routes (Item 40), and webhook endpoints (Item 5) enforces a per-(scope) request rate using Item 69's `DistributedCounter`. Scopes: `ip`, `user`, `tenant`, `(ip, endpoint)`, `(user, endpoint)`. Defaults are documented per-endpoint-shape (auth endpoints: 10/min by IP; mutating endpoints: 60/min by user; read endpoints: 600/min by user). **(c) Lockout.** A `LockoutPolicy(failures_per_window=5, window=15m, lockout=30m)` on auth flows triggers a per-(actor, flow) lockout after the configured failures; lockout state lives in a small DB table and is observable via an admin endpoint. The pluggable `AnomalyDetector` ABC (default: no-op) accepts `report_failure(actor, flow)` calls and can escalate (captcha, MFA step-up, alert) without each auth flow inventing the integration.

**Implementation notes.** The CORS startup check is one assertion in `app.py`. The `@rate_limit` decorator is a thin wrapper over Item 69 — the heavy lifting is in the counter primitive. The `LockoutPolicy` table is small (`actor_key`, `flow`, `failure_count`, `locked_until`); cleanup is a scheduled task per Item 28's `ScheduledService`. The `AnomalyDetector` is the integration point for captcha (hCaptcha, reCAPTCHA), step-up MFA (reuses Item 18's freshness gate), and SIEM alerting (reuses Item 85's `ErrorReporter`).

**Acceptance criteria.** A production-mode startup with wildcard CORS fails fast with a clear error. A `@rate_limit("10/min", scope="ip")`-decorated endpoint returns 429 on the 11th request from a single IP. A magic-link verify endpoint locks out an IP for 30 minutes after 5 failures within 15 minutes. The audit log records every lockout with the actor, flow, and trigger.

**Dependencies.** Depends on Item 69 (distributed counter). Cross-references Items 17 (outbound rate limit — different dimension), 58, 59 (auth flows consume `LockoutPolicy` rather than inventing per-flow throttling), 85 (lockout events route through the structured-log/error-reporter pipeline).

**Status: ✅ DONE.** All three target-state pieces have landed in `src/serverframework/lib/InboundSecurity.py`: (a) production-mode CORS startup assertion via `validate_cors_config(allow_origins, allow_credentials, app_env)` wired into `app.py:440-482`, refusing to start with wildcard origins under `APP_ENV=production` and rejecting the `*` + credentials combination in any environment; (b) `@rate_limit("100/min", scope="ip")` decorator stamps endpoint metadata, `RateLimitMiddleware` (mounted in `app.py`) enforces the budget per-(scope, actor) using a sliding-window `_InMemoryCounter` with a `set_rate_limit_counter(...)` hook for production multi-process Redis; `discover_rate_limited_routes(app)` walks the router after mount and populates the registry; route-resolution honors path templates so `/v1/user/{id}` is enforced uniformly; (c) `LockoutPolicy` dataclass + `LockoutTracker` (in-memory; same swap-for-Redis surface) tracks per-(actor, flow) failures with documented sliding-window-and-cooldown semantics, and the pluggable `AnomalyDetector` ABC (default `NoOpAnomalyDetector`) accepts `report_failure(actor, flow)` calls for captcha/MFA-step-up/SIEM integration. Tests: 37 in `lib/InboundSecurity_test.py` covering CORS denials, registry round-trips, route discovery, middleware passthrough, 429-after-limit, anonymous-actor fail-open, and per-actor budget independence. Closed by verification.

---

# Group 23 — Operational Resilience

The day-2 concerns the framework currently does not address: backups, deploys without downtime, multi-region placement, and the operator surface that runs all of it. None of these gate provider authorship; all of them gate production deployment.

## ~~Item 79~~ — ~~Backup, restore, and point-in-time recovery primitive~~ ✅ DONE

**Severity:** High
**Scope:** Per-table classification (`backup-critical` vs `ephemeral`), scheduled DB snapshot service, integrity-verified restore drill, RTO/RPO documentation.
**Owner area:** Operational resilience / database.

**Purpose.** Items 24 and 49 cover migrations *forward*; Item 56 covers audit-log archival; Items 54 and 55 cover read replicas and RLS. Nothing in the document addresses nightly DB snapshots, point-in-time recovery, backup verification, restore drills, RTO/RPO targets, or backup encryption. The framework holds credentials (Item 32 fallback to encrypted column), quota state (Item 19), sessions (BLL_Auth), and outbox entries (Item 35); a deployment cannot ship to production without a backup story for any of these. The omission is not in the Deferred legend, so this item closes the gap.

**Current state.** No backup contract. No restore drill. RTO/RPO undocumented.

**Target state.** Each table declares a `backup_class: ClassVar[BackupClass]`: `critical` (data loss is unacceptable; included in nightly snapshots and continuous WAL archiving), `recoverable` (data loss is recoverable from upstream sources, e.g. external entities resolved via federation; included in nightly snapshots only), `ephemeral` (cache, session, sticky-routing state; excluded from backups). A scheduled `BackupService` (Item 28's `ScheduledService` flavor) runs nightly and drives the underlying DB engine's snapshot/dump command (`pg_dump` for Postgres, equivalent for others) into a configured `BackupTarget` (Item 43's object-storage abstract; S3/GCS/local-filesystem). PITR is supported for engines with WAL streaming (Postgres) via a separate continuous-archive job. A monthly restore drill — automated CI job that takes the latest backup, restores it into a scratch DB, runs a smoke test, and discards — verifies that backups are actually restorable. RTO/RPO targets are declared per deployment and tracked as a metric (`backup_age_seconds`, `last_successful_restore_drill_age_seconds`).

**Implementation notes.** The Postgres path is the reference implementation; the framework documents per-engine alternatives. `pg_dump` is the simple option but does not give PITR; `pg_basebackup` plus continuous WAL archiving to the `BackupTarget` is the production option. Restore drills must run on isolated infrastructure (not against the live DB) — they are non-trivial to automate and are the failure mode most production deployments overlook. The `outbox` table (Item 35) and `Quota` table (Item 19) need careful handling on restore: outbox entries past the deadline are stale and should be marked DLQ on restore rather than re-fired; quota counters are restored as-of the backup time and the gap between backup and restore is a known small over-count window.

**Acceptance criteria.** A scheduled snapshot lands in the `BackupTarget` nightly. The restore drill runs monthly in CI and asserts the smoke test passes. `backup_age_seconds` and `last_successful_restore_drill_age_seconds` metrics are emitted and observable. A documented runbook describes the manual restore procedure.

**Dependencies.** Depends on Items 28 (scheduled service) and 43 (object-storage abstract for the backup target). Cross-references Items 35 (outbox restore semantics), 19 (quota restore semantics), 56 (audit-log archival is independent of DB backup).

**Status: ✅ DONE.** All four target-state pieces are in place. (a) `BackupClass` Literal + `BACKUP_REGISTRY` per-table classification in `lib/Backup.py:67-92`. (b) `BackupService` (`ScheduledService` flavor) at `lib/Backup.py:365+` runs `command.dump()` on the configured cadence and uploads to a `BackupTarget` (`LocalFilesystemBackupTarget` ships as the reference; `S3BackupTarget` stub for Item 43 wiring); `take_backup()` resets `backup_age_seconds` to 0.0 on success. (c) `RestoreDrillService` at `lib/Backup.py:453+` downloads the latest snapshot, runs `command.restore` into a scratch DB, smoke-tests, and resets `last_successful_restore_drill_age_seconds` on success; `DrillReport` carries the structured outcome. (d) Monthly CI restore drill — new `.github/workflows/restore-drill.yml` runs the framework's own `serverframework.lib._restore_drill_runner` on every push to main and on the 1st of each month at 04:00 UTC, exercising the full `seed → SqliteBackupCommand.dump → LocalFilesystemBackupTarget.upload → RestoreDrillService.run_drill → smoke_test` path; the runner writes a JSON report uploaded as a workflow artifact. (e) Operator runbook documenting the manual restore procedure and the outbox/quota restore semantics lives at `lib/LIB.Backup.md`. Tests: `lib/Backup_test.py` (18 existing) + `lib/_restore_drill_runner_test.py` (1 new end-to-end runner test gated on `sqlite3` PATH availability).

---

## ~~Item 80~~ — ~~Zero-downtime / rolling-deploy migration window contract~~ ✅ DONE

**Severity:** High
**Scope:** Migration framework (Items 24, 49, 62), `@extension_model` field-injection contract (Item 23), startup checks.
**Owner area:** Database / deployment.

**Purpose.** Items 24, 49, and 62 cover migration *correctness* (ownership, FK ordering, out-of-tree discovery). They do not cover the deploy-time scenario where v1 and v2 of the application both run against the same DB during a rolling deploy. A NOT NULL column added by an `@extension_model` injection (Item 23) breaks v1 the moment the migration runs; a column drop in the same release that adds the replacement breaks v1 during the window v2 has not finished rolling out. The framework's no-touch-the-core principle is at risk if extension authors must pick "ship the migration manually outside CI" to keep production up.

**Current state.** Migrations are forward-only and assumed atomic. No expand/contract phase. No invariants enforcing rolling-deploy safety.

**Target state.** Every migration is split into two logical phases: **expand** (add new structure, leave old structure in place; both old and new versions of the application run cleanly against the post-expand DB) and **contract** (remove the old structure, only after the new version has fully rolled out and the old version has been retired). The framework enforces invariants that make rolling deploys safe by default: NOT NULL columns added by `@extension_model` must declare a default (so v1 inserts continue to succeed); column drops are gated by a `removed_in: str` declaration that the migration generator turns into a separate contract migration in a later release; FK adds are split into "add column with FK" (expand) and "set NOT NULL on the FK" (contract). A startup check rejects a migration that violates these invariants.

**Implementation notes.** The expand/contract split mirrors the standard pattern from Liquibase, gh-ost, and Postgres operational guides. Tooling: extend the Alembic templates to emit a stub for the contract migration in the next release; rejection is at `alembic revision --autogenerate` time, not at apply time. Items 24 (ownership) and 49 (FK ordering) gate the discovery of which extension owns which migration; this item gates the *shape* of migrations that have already been ordered correctly.

**Acceptance criteria.** A `@extension_model`-injected NOT NULL column without a default is rejected at migration generation time with a clear error. A column-drop migration without a paired earlier expand migration is rejected at startup. A rolling deploy with v1 and v2 running concurrently against the post-expand DB succeeds without data corruption.

**Dependencies.** Depends on Items 23, 24, 49. Cross-references Item 20 (hot install must respect rolling-deploy invariants for runtime-installed extensions).

---

## ~~Item 81~~ — ~~Operational surface: probes, alerts, runbooks, DLQ admin UX~~ ✅ DONE

**Severity:** High
**Scope:** New `/healthz` and `/readyz` endpoints; `Alert` declaration on hooks/services; runbook generation tied to typed errors; admin UX for DLQ entries and `failed`-state services.
**Owner area:** Operational resilience / observability.

**Purpose.** Item 27 is per-provider health. Item 35 mentions "an admin endpoint with replay and discard actions" for DLQ — that is the *only* operator-UX line in the document. Item 44 has a `failed`-state admin reset. There is no framework-level `/healthz`/`/readyz` distinction, no alerting taxonomy tying the metrics from Items 22/34/44/57 to runbook entries, no DLQ operator UX with filtering/bulk-replay/classification. The framework will be unoperatable in its current shape — operators will be paged for unfamiliar metrics with no documented response.

**Current state.** No `/healthz` or `/readyz`. No alert taxonomy. No DLQ admin UX. The `failed`-state CLI from Item 44 is the closest existing operator surface.

**Target state.** **(a) Probes.** `/healthz` returns 200 if the process is up (always, modulo total failure); `/readyz` returns 200 only if the DB is reachable, the credential store is reachable, and every Critical-tier provider's `health_check` (Item 27) is OK. K8s liveness probes target `/healthz`; readiness probes target `/readyz`. **(b) Alerts.** Every emitted metric has an associated `Alert` declaration: `Alert(metric="provider_silent_drop_total", threshold=0, window="5m", runbook="docs/runbooks/silent-drop.md")`. The framework ships a generated `alerts.yaml` that operators import into Prometheus AlertManager (or equivalent); the alerts file is the inventory the on-call rotation uses. **(c) Runbooks.** Every typed framework error (Item 1's `BaseExternalError` hierarchy, `QuotaExhaustedError`, `DeadlineExceededError`, `LockTimeoutError`) carries a `runbook_url: ClassVar[Optional[str]]` that the structured logger emits; operators clicking the URL get the documented diagnosis and response. **(d) DLQ admin UX.** A `/admin/dlq` admin endpoint lists DLQ entries (Item 35) with filtering by extension, ability, error class, and timestamp; bulk-replay and bulk-discard actions are typed and audit-logged. The same surface lists `failed`-state services (Item 44) with the reset action.

**Implementation notes.** The `/readyz` semantics are the trap: a Critical-tier provider that flaps will cycle the deployment in/out of the load balancer if `/readyz` keys on it directly. Mitigate with a hysteresis window — `/readyz` returns 503 only after the provider has been DOWN for more than the configured window (default 60s). Alert declarations are colocated with the metric emission (Item 34's tracing layer), not in a separate file. Runbook URLs are stable identifiers; broken runbook links are caught by a CI link-checker.

**Acceptance criteria.** K8s liveness/readiness probes correctly distinguish process-up from system-ready. A simulated provider failure produces an alert in AlertManager with the correct runbook URL. The DLQ admin UX permits filtering and bulk-replay against a real DLQ entry without writing custom queries.

**Dependencies.** Depends on Items 27 (provider health), 35 (DLQ), 44 (failed-state services). Cross-references Items 22 (hook metrics), 34 (metrics emission), 57 (queue-fairness metrics), 85 (structured logging carries the runbook URL).

---

## ~~Item 83~~ — ~~Cross-region deployment contract~~ ✅ DONE

**Severity:** Medium
**Scope:** Documentation; explicit primitives that would need to change for active-active.
**Owner area:** Multi-region deployment.

**Purpose.** Item 36 covers data residency at the *provider-instance* level (which Stripe account does this user's traffic go to). It does not cover which DB instance writes go to, how Item 35's outbox drains across regions, how Item 51's sticky-session cache federates across regions, or how Item 19's atomic quota decrement crosses an Atlantic latency boundary. Item 54's read replicas are documented as same-region. The framework will silently work in active-passive only; an operator who reads the docs and deploys active-active will discover the gaps in production.

**Current state.** Multi-region implicit. Active-active is not addressed.

**Target state.** This item is primarily documentation: declare the supported deployment topology explicitly (active-passive multi-region; active-active is out of scope for v1). Identify the primitives that would need to change for active-active and list them so a future "v2 active-active" roadmap has a starting point: (a) Item 19's `Quota` decrement needs cross-region consensus or per-region partitioning; (b) Item 35's outbox needs per-region sharding to avoid a global drainer becoming the bottleneck or splitting writes; (c) Item 51's sticky-session cache needs a global Redis or session-affinity at the load-balancer; (d) Item 32's credential cache invalidation must propagate across regions; (e) Item 69's `DistributedCounter` must operate against a regional Redis with documented eventual-consistency semantics or against a globally-replicated Postgres. The doc explicitly states that mixing active-active with the v1 framework risks silent over-counting on quota, duplicate sends from the outbox, and routing inconsistency on stickiness — none of which produce immediate errors but all of which produce financial or correctness drift.

**Implementation notes.** No code change in this item. The active-passive topology is the recommended deployment for v1: a primary region runs the framework against a primary DB; secondary regions run read-only replicas (Item 54) for read-heavy workloads; failover is operator-driven (DNS or load-balancer cutover) with documented RTO/RPO from Item 79. Item 36's residency primitive routes user traffic to the in-jurisdiction region's primary; cross-region writes go to the user's home region.

**Acceptance criteria.** `docs/deployment/multi-region.md` explicitly states active-passive is supported, active-active is out-of-scope for v1, and lists the primitives that would change for v2. Operators planning multi-region deployments find the limits documented before they hit them in production.

**Dependencies.** Cross-references Items 19, 32, 35, 36, 51, 54, 69, 79.

---

# Group 24 — Compliance and Privacy

Item 45 (field-level ABAC) controls *access* to fields; Item 56 (audit retention) sets the retention bar; Item 32's credential vault encrypts secrets. None of those satisfy GDPR/CCPA right-to-erasure or PII classification. Group 24 fills that gap.

## ~~Item 82~~ — ~~PII classification and right-to-erasure orchestration~~ ✅ DONE

**Severity:** High
**Scope:** New `pii: PIIClass` field annotation; per-extension `erase_user(user_id)` hook; audit-log conflict resolution; data-export hook for portability.
**Owner area:** Compliance / privacy.

**Purpose.** GDPR Article 17 (right to erasure) and CCPA §1798.105 (right to delete) require that, on a verified user request, the controller erases the user's personal data across every system that holds it. The framework today provides no inventory of where a user's data lives across extensions, no deletion orchestration, no audit-log redaction-on-erasure mechanism (which conflicts with Item 56's `forever` retention class). An EU/CA/UK deployment cannot ship without these. PII classification is the prerequisite — without classifying which fields are personal data, the erase operation cannot be scoped correctly.

**Current state.** No PII classification. No right-to-erasure orchestration. Audit-log retention from Item 56 makes no provision for erasure-on-request even when the underlying user data is deleted.

**Target state.** **(a) PII classification.** A `pii: PIIClass` field annotation on Pydantic model fields declares the field's classification: `direct_identifier` (name, email, phone), `pseudonymous` (user_id, session_key — replaceable on erasure with a sentinel), `sensitive` (SSN, financial info, health), `derived` (model output that may have absorbed PII via training), `none` (the default; field is not personal data). The annotation is enumerable via Item 52's contracts manifest, so a per-deployment PII inventory falls out of the model graph. **(b) Per-extension erase hook.** `AbstractStaticExtension.erase_user(user_id)` is an optional method extensions implement to delete or pseudonymize the user's data within their domain. The framework's central `UserManager.erase()` orchestrates the call across every installed extension in dependency order (reverse of Item 24/49 ordering), is idempotent (re-calling produces the same result), and is itself audit-logged with a special `erasure_event` class. **(c) Audit-log conflict.** Item 56's retention conflicts with erasure for legitimate-interest audit events; the resolution is field-level: the audit event keeps its outline (timestamp, action, outcome) but PII fields within the event are redacted to a sentinel on erasure, with a per-event-class flag (`pii_redactable: bool` defaulting True) that lets compliance-mandated full-fidelity retention opt out. **(d) Data export.** A symmetric `AbstractStaticExtension.export_user(user_id) -> dict` hook produces the user's data in a portable shape (JSON or CSV) for the GDPR Article 20 (data portability) right.

**Implementation notes.** The `pii: PIIClass` annotation is metadata on the Pydantic field — it does not affect serialization unless paired with Item 45's field-level ABAC. The erasure orchestration must be transactional within each extension (`erase_user` runs inside a single transaction per extension); cross-extension atomicity is not guaranteed (which is realistic for a federated system) but the orchestration is idempotent so a partial-failure-then-retry produces a complete erasure. The `erasure_event` audit class is itself `pii_redactable=False` and `retention="forever"` — the audit-of-the-erasure must be preserved indefinitely as the regulator-defensible record.

**Acceptance criteria.** A user submitting a right-to-erasure request via the documented endpoint has their data erased or pseudonymized across every installed extension; the orchestration is idempotent and audit-logged. A field annotated `pii=PIIClass.direct_identifier` appears in the generated PII inventory (the extension's `EXT.Contracts.md` entry per Item 52 lists it). A data-export request returns the user's data in JSON across every installed extension's `export_user` hook output.

**Dependencies.** Cross-references Items 45 (field-level ABAC reuses the `pii` annotation), 52 (contracts manifest enumerates PII), 56 (audit retention is amended for redaction-on-erasure).

---

# Group 25 — Code Hygiene and Verified Doc/Code Divergences

Items in this group are fourth-round audit findings against the live codebase: code-hygiene sweeps and specific code-vs-doc divergences that must be resolved alongside the architectural items above. These are not architectural improvements; they are debt the architectural work cannot land cleanly atop.

## ~~Item 73~~ — ~~Code-hygiene sweep: prints, bare excepts, removed-model TODOs~~ ✅ DONE

**Severity:** Medium
**Scope:** ~43 `print()` calls in non-test source; ~20+ bare `except:` clauses; two `# TODO @Kristy NetworkModel doesn't exist anymore` markers.
**Owner area:** Code quality.

**Purpose.** Three code-hygiene patterns surfaced uniformly across the codebase that AGENTS.md ("write concise code; fail fast") and Item 85 (structured logging) both implicitly disallow: stray `print()` calls in production source paths; bare `except:` clauses without even `as e` binding (silent error swallowing); and stale TODO markers from an incomplete refactor (`NetworkModel` was removed but the codepaths that emit its name still exist). Each in isolation is small; together they are enough drag on production debugging that Item 85 (structured logging) cannot ship cleanly until they are resolved.

**Current state.** Verified at audit time:
- 43 `print()` calls in non-test source: `lib/Pydantic.py:1212`, `lib/Pydantic2FastAPI.py:2257-2290`, `app.py:379-404` (the last prints POSTed bodies unfiltered — a PII risk in production logs).
- ~20+ bare `except:` clauses: `app.py:389,449,468`; `extensions/AbstractExtensionProvider.py:1268,1312,1479,1494,1513,1579,1613`; `lib/Pydantic2SQLAlchemy.py:146,156,168,493,512,1501,1503`; `extensions/meta_logging/BLL_Meta_Logging.py:574`; `lib/Dependencies.py:1357`.
- Two `# TODO @Kristy NetworkModel doesn't exist anymore` markers at `lib/AbstractPydantic2.py:929` and `lib/Pydantic.py:588`, with the surrounding code still emitting the obsolete class name.

**Target state.** **(a) Prints.** Every `print()` in non-test source is removed in the same change that lands Item 85's structured logging contract. The 43 sites are mechanically rewritten to `logger.debug(...)` with a useful message and structured fields where applicable. The `app.py:379-404` POST-response-body prints are removed entirely — they are debugging leftovers, not logging. **(b) Bare excepts.** Every bare `except:` is rewritten to `except SpecificException as e:` with the most-narrow type that matches the actual cases observed; `as e` is mandatory; the handler at minimum `logger.warning("...", exc_info=True)`s the exception; re-raising via `raise` is preferred where the current behavior of swallowing is itself a bug. AGENTS.md's "fail fast" rule applies. **(c) NetworkModel.** The two TODO markers are resolved by either reinstating the `NetworkModel` class as a thin alias around the replacement (`BaseNetworkModel`) for backward compatibility, or deleting the codepaths that reference it. Pick one and commit.

**Implementation notes.** This is a large mechanical change. Best landed as a single PR with a clear commit message; CI signals the regression set. The print-to-logger rewrite is a sed-friendly transformation; the bare-except rewrite requires per-site judgment about which exception to catch and whether to re-raise. The NetworkModel resolution is a one-day refactor, not architecture work — the model registry's emission paths around `lib/Pydantic.py:586-1290` need a single coherent strategy.

**Acceptance criteria.** No `print()` call exists in any non-test source file. No bare `except:` clause exists outside of `_test.py` files (test fixtures may use bare excepts in narrow setup/teardown contexts). The two `NetworkModel` TODO markers are resolved.

**Dependencies.** Coordinates with Item 85 (structured logging is the destination for the print-rewrite). Independent otherwise.

---

## ~~Item 74~~ — ~~Replace mocked subscription-status return with real Stripe rotation call~~ ✅ DONE

**Severity:** High
**Scope:** `extensions/payment/BLL_Payment.py:312`.
**Owner area:** Payment extension.

**Purpose.** `BLL_Payment.py:312` carries the comment `# TODO: Implement actual subscription checking via rotation system` and returns a hardcoded `{"subscription_id": "sub_mock", "current_period_end": "2025-12-31T23:59:59Z"}`. The payment extension is documented as functional but ships a mocked subscription-status return that masquerades as a real query. Any caller relying on the result — auth flows that gate based on subscription state, billing-cycle reset logic, churn detection — is operating on a fiction.

**Current state.** `get_subscription_status` returns a hardcoded mock dict.

**Target state.** Replace the mock with a real call through the rotation system to the `subscription` ability on the active payment provider. The implementation pattern follows the documented `*_via_provider` shape from Item 1 (typed result contract) and Item 4 (idempotency) once those land; in the interim, the hand-coded path through the existing rotation primitive is acceptable as long as the result reflects the actual upstream state. The mocked return value is removed; an upstream failure surfaces as the typed error from Item 1's hierarchy rather than a fictional success.

**Implementation notes.** This is a payment-extension concern, not framework. Item 14 (mirror-on-create) and Item 35 (outbox) provide the orchestration shape for keeping the local subscription record in sync with Stripe; this item is the simpler "read-through" path that does not require either. The `payment_info["has_payment_setup"]` check above the TODO is the existing precondition; preserve it.

**Acceptance criteria.** `get_subscription_status(user_id)` returns the user's real subscription state from the upstream payment provider, not a hardcoded mock. A test against sandbox Stripe credentials per Item 15 verifies the real path.

**Dependencies.** Cross-references Items 1 (typed errors), 4 (idempotency), 14 (mirror-on-create — the create path is symmetric), 15 (sandbox testing).

---

## ~~Item 75~~ — ~~Move soft-delete enforcement from BLL into the DB layer~~ ✅ DONE

**Severity:** Medium
**Scope:** `BLL_Auth.py:1165` workaround; `database/AbstractDatabaseEntity.py` (soft-delete primitive); every BLL query that filters by `deleted_at`.
**Owner area:** Database / BLL.

**Purpose.** `BLL_Auth.py:1165` carries the comment `# TODO: This is a temporary fix to block users from logging in after they have been deleted but the DB layer should handle this`. The login path checks `if user["deleted_at"]:` by hand and rejects. This pattern leaks across BLL: every query that should respect soft-delete depends on the BLL author remembering to filter, which is exactly the failure-mode `DB.Patterns.md` declares should not exist. A single missed filter is a tombstoned-record bug; in the case of login, it is a security-relevant tombstoned-record bug.

**Current state.** `deleted_at` filtering is by convention in the BLL. No DB-layer enforcement.

**Target state.** Soft-delete is implemented as a `SoftDeleteMixin` on `AbstractDatabaseEntity` that adds the `deleted_at` column and registers a SQLAlchemy `before_compile` query event that auto-injects `deleted_at IS NULL` into every read against tables tagged with the mixin. A bypass parameter (`include_deleted=True`) is offered for admin queries that genuinely need tombstoned records; without it, deleted rows are invisible to the BLL. The `BLL_Auth.py:1165` workaround is removed in the same change that lands the mixin; the login path naturally returns "user not found" because the soft-deleted user is invisible to the read.

**Implementation notes.** SQLAlchemy's query-event approach is well-trodden; the framework already uses `before_compile` for related concerns (per `lib/Pydantic2SQLAlchemy.py`). The bypass flag uses a session-scoped marker so admin queries are explicit. This pairs with Item 55 (RLS) — both implement the "filter at the DB layer" defense-in-depth pattern, RLS for tenant isolation and this for soft-delete.

**Acceptance criteria.** A `SoftDeleteMixin`-tagged model is queried from the BLL and tombstoned rows are invisible without any BLL-author filter clause. The `BLL_Auth.py:1165` workaround comment and the hand-written `if user["deleted_at"]:` check are removed; the login path produces the same correct rejection through the DB-layer filter alone.

**Dependencies.** Cross-references Item 55 (RLS — sibling defense-in-depth pattern). Independent otherwise.

**ROOT bypass restored as a follow-up.** The `BLL_Auth.UserManager.auth` flow's team-membership enrichment calls `TeamModel.DB(...).get(requester_id=ROOT_ID, ...)`; under the auto-filter, that pathway 404s when any team a user is bonded to is soft-deleted. The fix preserves the prior "ROOT can see tombstoned rows for admin/audit reads" contract by passing `query.execution_options(include_deleted=True)` from `cls.get`, `cls.list`, `cls.exists` (both branches), and `cls.count` whenever the requester is ROOT — `_soft_delete_before_compile` already honors that execution option as its bypass. Non-ROOT requesters still get the auto-filter; admin/audit code keeps its visibility into deleted state.

---

# Sequencing recommendations

The work is organized into four roughly-parallel tracks once the eight Critical-path items land. The Critical-path summary at the top of this document names the gating set; everything below organizes the remaining work.

- **Track A — External federation core.** Items 1, 2, 4, 5, 6, 10, 14, 17, 26, 35, 70. Ordered roughly: 1 → 2 → 4 (depend on the typed error hierarchy); 5 and 10 in parallel; 6 in parallel; 14 after 28 and 35; 17 anytime after 2 and 69; 26 and 70 are paired sibling-Critical items pinning the manager-and-bonded-instance contract.
- **Track B — Pagination, search, navigation, GraphQL federation.** Items 7, 8, 9, 11, 12, 16, 29, 76, 87. Ordered roughly: 7 and 8 in parallel; 9 after 7; 11 anytime; 12 after 1, 4; 16 after 9, 10, 11, 15; 29 after 7, 8; 76 closes the documented-WebSocket gap; 87 closes GitHub #10.
- **Track C — Cross-cutting framework hardening.** Items 15, 18, 19, 20, 21, 22, 23, 24, 25, 27, 28, 30, 64, 69, 72, 85. All largely independent of one another; 28 before 13 and 14; 18 before any extension that needs to register permissions; 19 before any provider work that involves billable usage; 64 paired with 25; 69 before 17/19/57; 72 closes the no-mock-pillar enforcement gap; 85 closes the structured-logging gap and the print()-cleanup half of Item 73.
- **Track D — Operational, compliance, and packaging.** Items 60, 61, 62, 63, 65, 66, 67, 68, 71, 73, 74, 75, 77, 78, 79, 80, 81, 82, 83, 86. The pip-package items (60, 61, 62, 63, 65, 66, 67, 68) form an internal ordering keyed by Item 60 (rename) — see the Group 21 dependency notes; 86 (supply chain) blocks the first PyPI publish. The operational items (79, 80, 81) are independent of provider authorship and can land in any order; 82 is a compliance precondition for EU/CA/UK deployments; 83 is documentation only.
- **Documentation-only.** Items 3, 52, 78.

The expected Critical-path completion is the gate for opening provider work; the remaining items can be landed iteratively while provider authorship begins on the now-stable foundation. Track D items are gating for the *first PyPI publish* (Item 86) and the *first production deployment* (Items 79, 81), but not for in-tree provider authorship.

---

# Group 26 — Email Extension Reshape (post-prereq backlog)

These items capture work on the email extension's API surface that was scoped during the security-audit / Stalwart-and-SMTP2go effort but is gated on the framework primitives in Groups 1–10. Each line item names the specific upstream Items it depends on so the work can be lit up in the order the prereqs land.

The Phase-1 work that did **not** require any of those prereqs (typed value models, capability flags, the friendly `send`/`update_email`/`list_emails` surface, and the `EmailMessage`-aware security mixin) shipped in `claude/security-audit-email-EqBda` ahead of this group. Everything below is the deferred remainder.

## ~~Item 88~~ — ~~Email reshape: typed-error migration~~ ✅ DONE

**Severity:** High
**Scope:** `AbstractEmailProvider`, `SendgridProvider`, `StalwartProvider`, `Smtp2goProvider`, `AbstractEmailProviderSecurityTests`.
**Owner area:** Email extension.
**Prereq:** Item 1 (typed external-error hierarchy). Cross-refs Items 2 (rotation policy), 27 (health check).

**Purpose.** Today every email-provider entry point returns `str` for both success and failure (`"Email sent successfully to ..."` vs `"Failed to send email: ..."`), and the security-deny tests substring-match on the result. After Item 1 lands, the email surface migrates to raise typed exceptions on failure and return `SentMessage` (id + provider + accepted-at) on success; the security mixin re-targets to `with pytest.raises(InvalidInputExternalError)` and to assert on the typed payload.

**Implementation.** Map the existing failure strings produced by `_validate_send_inputs` to `InvalidInputExternalError` subclasses (`EmailHeaderInjectionError`, `EmailPayloadTooLargeError`, `EmailMalformedAddressError`, `EmailAttachmentTraversalError`). Map upstream 4xx/5xx/429/auth-failure to the canonical `InvalidInputExternalError` / `TransientExternalError` / `RateLimitExternalError` / `AuthExternalError` per Item 1. The legacy `send_email(...) -> str` contract stays as a deprecation-aliased shim that catches the new exceptions and re-stringifies for one release; new callers use `send(EmailMessage)` which raises.

**Acceptance.** `pytest -m security` asserts `with pytest.raises(EmailHeaderInjectionError)` for CRLF cases, etc. The string-substring matchers are removed. SendGrid+Stalwart+SMTP2go each route at least one upstream-failure shape (401, 429, 503) into the correct typed exception, verified by integration tests gated on real credentials per Item 15.

## ~~Item 89~~ — ~~Email reshape: bond to AbstractProviderInstance contract~~ ✅ DONE

**Severity:** High
**Scope:** `AbstractEmailProvider`, `AbstractEmailProviderInstance` (new), all three concrete providers.
**Owner area:** Email extension.
**Prereq:** Item 26 (explicit `AbstractProviderInstance` contract). Cross-refs Item 37 (typed Settings/abilities).

**Purpose.** Phase-1 introduced friendly classmethods on `AbstractEmailProvider` (`send`, `update_email`, `list_emails`) that take `provider_instance` as a positional arg. This is correct Phase-1 shape but threaded through every call. Item 26 promotes `AbstractProviderInstance` to a real ABC; once it lands, the email extension defines `AbstractEmailProviderInstance(AbstractProviderInstance)` carrying the eight typed abilities (`send`, `send_bulk`, `list_emails`, `get_email`, `update_email`, `reply`, `download_attachment`, `list_threads`) so call sites become `bonded.send(message)` instead of `Provider.send(provider_instance, message)`.

**Implementation.** `AbstractEmailProviderInstance` declares the eight abstracts and a typed `capabilities: ClassVar[FrozenSet[Capability]]` (lifted from Phase-1's class-level capability flag). `bond_instance` returns the typed instance; `_instance` is declared as a typed `ClassVar` per Item 26 and a mypy gate enforces the contract. The Phase-1 classmethods on `AbstractEmailProvider` become `@deprecated` shims that delegate to the bonded instance for one release.

**Acceptance.** `mypy` rejects a concrete provider whose `bond_instance` returns a non-`AbstractEmailProviderInstance`. New callers use `bonded = Provider.bond_instance(model); await bonded.send(msg)`. The Phase-1 surface keeps working through deprecation warnings.

## ~~Item 90~~ — ~~Email reshape: typed Settings, EnvSchema, Secret-marked credentials~~ ✅ DONE

**Severity:** Medium
**Scope:** `AbstractEmailProvider`, all three concrete providers, BLL hook registration.
**Owner area:** Email extension / configuration.
**Prereq:** Item 37 (typed `ProviderSettings` and ability declarations). Cross-refs Items 32 (credential vault) and 50 (sandbox/live discriminator).

**Purpose.** The current `_env: Dict[str, Any]` (`SENDGRID_API_KEY`, `STALWART_PASSWORD`, `SMTP2GO_API_KEY`, etc.) is a stringly-typed dict with no Pydantic validation, no `Secret` markers, and no startup-time error when a required value is missing. After Item 37 lands, each abstract provider declares `Settings` as an inner Pydantic model, and `_env` becomes an `EnvSchema` with typed names, defaults, required flags, and `Secret`-marked sensitive values that integrate with Item 32's redaction.

**Implementation.** `AbstractEmailProvider.Settings` declares the shared shape (`from_email: EmailStr`, `default_provider_name: Optional[str]`, etc.). Each concrete provider extends it: `SendgridProvider.Settings(from_email: EmailStr, api_key: Secret[str])`, `StalwartProvider.Settings(host: str, port: int = 587, username: str, password: Secret[str], use_tls: bool = True, from_email: EmailStr)`, `Smtp2goProvider.Settings(api_key: Secret[str], from_email: EmailStr, api_url: HttpUrl = "https://api.smtp2go.com/v3")`. The startup check refuses to boot if a required value is missing for any registered provider. The `BLL_EMail._EMAIL_PROVIDER_REGISTRY` tuple-loop is replaced by iterating over registered provider classes whose `Settings.is_configured()` is true.

**Acceptance.** A deployment missing `SENDGRID_API_KEY` while `EMAIL_PROVIDER=sendgrid` produces a clear startup error naming the field. `Secret`-marked fields never appear in log output (Item 32 redaction). The BLL hooks no longer need a hardcoded registry tuple — the typed Settings drive registration.

## ~~Item 91~~ — ~~Email reshape: idempotent send + bulk send~~ ✅ DONE

**Severity:** High
**Scope:** `AbstractEmailProviderInstance.send`, new `send_bulk`, all three concrete providers.
**Owner area:** Email extension.
**Prereq:** Item 4 (idempotency primitive), Item 12 (bulk endpoint expression). Cross-refs Item 1.

**Purpose.** A 5xx retry storm sends the same invitation email twice. `EmailMessage` carries no idempotency slot today. After Item 4 lands, `send_via_provider` is decorated `@idempotent` and the framework's key derivation handles retry safety; after Item 12 lands, `send_bulk_via_provider` is added as a true batch endpoint that returns per-recipient typed errors instead of looping over single sends.

**Implementation.** Decorate `send_via_provider` with `@idempotent`. Implement `send_bulk_via_provider` for SendGrid (`personalizations` array, up to 1000 recipients), SMTP2go (`to[]` array, up to 1000), Stalwart (multiple `RCPT TO` in one DATA). Each per-item rejection surfaces as a typed `InvalidInputExternalError` with the specific recipient in the error payload. Stalwart's batch path is opportunistic — most SMTP submission servers accept multiple `RCPT TO` against one `MAIL FROM`, but some enforce a single recipient; the provider declares the supported batch size and the framework falls back to a serial loop for providers that don't support batching.

**Acceptance.** A 1000-recipient invitation send issues one upstream call to SendGrid/SMTP2go and surfaces 17 invalid addresses as 17 individual typed errors. A retry of `send` after a 503 carries the same idempotency key as the first attempt; the upstream returns the prior result rather than creating a duplicate.

## ~~Item 92~~ — ~~Email reshape: auth-strategy adoption (Stalwart BasicAuth)~~ ✅ DONE

**Severity:** Medium
**Scope:** `StalwartProvider.bond_instance`, `SendgridProvider.bond_instance`, `Smtp2goProvider.bond_instance`.
**Owner area:** Email extension.
**Prereq:** Item 10 (pluggable AuthStrategy).

**Purpose.** Today each provider's `bond_instance` reaches into env directly for credentials. Stalwart specifically bundles host/port/username/password/use_tls into a config dict that's passed by hand. After Item 10 lands, Stalwart declares `default_auth_strategy = BasicAuth` and the bonding layer resolves the credential blob through the registry; SendGrid and SMTP2go declare `default_auth_strategy = APIKeyAuth`. This unblocks a future `EXT_Auth_OAuth` from contributing `OAuth2Auth` for Workspace impersonation or Microsoft Graph without touching the email providers.

**Implementation.** Each `bond_instance` calls `auth_strategy = AuthStrategyRegistry.get(instance.auth_strategy_name or cls.default_auth_strategy_name)` and passes the strategy into the bonded instance. Strategy `headers_for(requester)` is consulted by the shared HTTP client (Item 31) for SendGrid/SMTP2go; for Stalwart, the strategy yields `(username, password)` for the SMTP AUTH handshake.

**Acceptance.** A `Root_Stalwart` instance with `auth_strategy_name="basic"` authenticates correctly. A future Workspace integration registers `OAuth2Auth` and a per-user `Stalwart` instance with `auth_strategy_name="oauth2"` works without modifying `StalwartProvider`.

## Item 93 — Email reshape: federation translators (FieldMapping, Paginator, QueryDSL)

**Status: PARTIAL.** SendGrid declares `paginator`, `query_translator`, and `field_mappings = [...]` per Items 6/7/8; round-trip tests pass for the SendGrid surface. **Outstanding:** the equivalent declarations on Stalwart (IMAP search translator, page-token paginator) and SMTP2go (key-value translator) are not yet shipped — these providers still rely on the Phase-1 free-form `query` string and raw `cursor` shape rather than the typed federation surface.

**Severity:** Medium
**Scope:** `AbstractEmailProviderInstance.list_emails`, `AbstractEmailProvider`-level field mappings.
**Owner area:** Email extension.
**Prereq:** Items 6 (FieldMapping pipeline), 7 (Pagination homogenization), 8 (Search DSL translation).

**Purpose.** Phase-1's `list_emails(*, folder, query, limit, cursor)` takes a raw string cursor and a free-form query string. Once Items 6/7/8 land, this becomes typed throughout: cursors round-trip through `next_token` envelopes per Item 7, queries pass through `AbstractQueryDSLTranslator` per Item 8, and the `EmailMessage` ↔ provider DTO translation is declared via `field_mappings = [...]` per Item 6 instead of being open-coded in each `send_email` body.

**Implementation.** Stalwart declares `paginator = PageTokenPaginator` (or `CursorPaginator` if JMAP) and `query_translator = IMAPSearchTranslator`. SendGrid and SMTP2go's log/messages-search endpoints declare `KeyValueTranslator`. `EmailAddress(name, address)` ↔ RFC 5322 mailbox roundtrip becomes `Compose`/`Decompose` mappings. `Importance` enum ↔ provider-specific headers becomes `EnumRemap`. Round-trip tests run automatically per Item 6's acceptance criteria.

**Acceptance.** A `list_emails(query="from:alice", limit=50)` against Stalwart issues a correct IMAP `SEARCH FROM alice` command without per-provider translation code. A 200-message paged list returns `next_token` opaque cursors that round-trip cleanly across providers. Field-mapping round-trip tests pass for every declared `EmailMessage` field.

## Item 94 — Email reshape: inbound webhook handlers

**Status: PARTIAL.** SendGrid: `verify_signature` (ECDSA-SHA256) + 8 event handlers (`bounce/delivered/open/click/spam_report/unsubscribe/dropped/processed`) + canonical `EmailDeliveryEvent` fan-out. **Outstanding:** SMTP2go bounce-activity webhook handlers (bearer-token check on `SMTP2GO_WEBHOOK_SECRET`) and Stalwart inbound-mail webhook handlers (HMAC-SHA256 over body) are not yet registered. The target state called for each provider; today only SendGrid is wired.

**Severity:** Medium
**Scope:** `EXT_EMail` webhook registration, per-provider `verify_signature`, hook fan-out into `Email_*Manager` AFTER hooks.
**Owner area:** Email extension.
**Prereq:** Item 5 (inbound webhook handler infrastructure).

**Purpose.** SendGrid Event Webhook delivers `bounce`, `delivered`, `open`, `click`, `spam_report`, `unsubscribe` events. SMTP2go has bounce-activity webhooks. SendGrid Inbound Parse delivers received mail through HTTP POST. Stalwart can be configured to POST custom hooks on inbound mail. None of these are wired today. After Item 5 lands, each provider registers `@webhook_handler(EXT_Email, provider="sendgrid", event="bounce")` style handlers; signature verification is mandatory; events fan into the same hook bus that internal `Email_*Manager` mutations fire.

**Implementation.** SendGrid: `verify_signature` checks the `ECDSA-SHA256` signature against `SENDGRID_WEBHOOK_PUBLIC_KEY`; events are dispatched on `event` field. SMTP2go: bearer-token check on `SMTP2GO_WEBHOOK_SECRET`. Stalwart: HMAC-SHA256 over body with `STALWART_WEBHOOK_SECRET`. A canonical `EmailDeliveryEvent(message_id, provider, event_type, recipient, timestamp, raw)` model normalizes the payload across providers; downstream consumers (suppression-list hook, bounce-tracking metrics, inbound-parse-routing) bind to the normalized model.

**Acceptance.** A SendGrid bounce webhook hits `/webhook/email/sendgrid/bounce`, signature verifies, the canonical `EmailDeliveryEvent` fans into the AFTER-update hook chain, and a downstream consumer (e.g. Item 67's suppression list) records the bounce automatically. A signature failure produces 401 without invoking the handler.

## Item 95 — Email reshape: capability ladder (validation, templates, suppression, stats)

**Status: PARTIAL.** Typed abilities (`validate_address`, `send_with_template`, `list_suppressions`, `add_suppression`, `remove_suppression`, `get_stats`, `list_messages`) declared on `AbstractEmailProviderInstance`; `SendgridEmailInstance` implements the SendGrid-supported subset and declares its `capabilities`. **Outstanding:** SMTP2go and Stalwart have not yet declared their capability subsets or implemented the supported abilities; calling these abilities against either provider raises `NotImplementedError` rather than the typed `NotSupportedError(provider, capability)` the contract requires.

**Severity:** Medium
**Scope:** New abilities on `AbstractEmailProviderInstance`, opt-in implementation per provider.
**Owner area:** Email extension.
**Prereq:** Items 26 (provider-instance contract), 37 (typed abilities), 12 (bulk for batch validate). Cross-refs Item 6 (template field mapping).

**Purpose.** Beyond `send` and `list_emails`, every shipped provider has a non-trivial set of administrative abilities the framework currently ignores: pre-flight email-address validation (SendGrid `/v3/validations/email`, SMTP2go `/v3/email-validation`); server-side templates (SendGrid dynamic templates, SMTP2go templates, Stalwart local-file render); suppression-list management (SendGrid `suppression/*`, SMTP2go `bounces`/`unsubscribes`, Stalwart synth-from-queue); send statistics and history. Phase-1 surfaces none of these. Each is a typed ability, opt-in per provider via `Capability` flags.

**Implementation.** Define typed abilities `validate_address`, `send_with_template`, `list_suppressions`, `add_suppression`, `remove_suppression`, `get_stats`, `list_messages` on `AbstractEmailProviderInstance`. Each provider declares the subset it supports in `capabilities`; calling an unsupported ability raises `NotSupportedError(provider, capability)` rather than silently returning empty. SendGrid's contact/template/campaign external models that already exist in `PRV_SendGrid_EMail.py` migrate to declare these abilities cleanly through Item 6's field-mapping pipeline.

**Acceptance.** A caller branches on `Capability.VALIDATE_ADDRESS in bonded.capabilities` before invoking; a SendGrid+SMTP2go deployment can dedup-validate addresses in batch before send; a Stalwart-only deployment skips the validation step. Suppression-list hooks (Item 66 webhook → Item 67 add_suppression) keep `bounces` current automatically.

## ~~Item 96~~ — ~~Email reshape: ops policies (rate limit, health, degradation, deadlines, residency)~~ ✅ DONE

**Severity:** Medium
**Scope:** Per-provider declarations; integration with rotation, health, deadline, outbox, residency primitives.
**Owner area:** Email extension.
**Prereq:** Items 17 (rate limit), 27 (health check), 47 (deadline propagation), 48 (degradation policy), 35 (outbox), 36 (residency). Cross-refs Items 2, 31.

**Purpose.** Email-specific ops policies are declarations, not code: rate limits per upstream tier, health-check endpoint, fail-fast vs queue-and-retry per ability, residency-tagged provider instances. These light up automatically once the framework primitives land.

**Implementation.** SendGrid: `rate_limit = RateLimit(rps=10, burst=20)` (free tier; configurable per instance for paid tiers). `health_check` calls `GET /v3/scopes` with the API key. SMTP2go: `rate_limit = RateLimit(rps=100, burst=200)` (paid tier default); `health_check` calls `GET /v3/stats/email_summary`. Stalwart: `rate_limit` reads the local server's submission queue limit; `health_check` performs `NOOP` over a kept SMTP connection. `degradation_policy = FailFast()` is declared on transactional abilities (`send` for invitation/password-reset/MFA contexts), `degradation_policy = QueueAndRetry()` on marketing-tagged sends. EU/US residency variants of SendGrid are declared as residency-tagged provider instances per Item 36.

**Acceptance.** An invitation send during a SendGrid outage surfaces 500 fast (FailFast); a marketing send during the same outage returns 202 with a tracking id and drains from the outbox once SendGrid recovers (QueueAndRetry). A SendGrid 429 storm pauses the provider per Item 17 without rotation. A health check failure marks the provider DOWN per Item 27.

## Item 97 — Email reshape: shared HTTP client routing + credential vault migration

**Status: PARTIAL.** SMTP2go's outbound calls route through `ProviderHTTPClient` and inherit shared trace/retry/rate-limit/log-redaction behavior. **Outstanding:** SendGrid SDK transport injection (the `python-http-client` session swap) is deferred — SendGrid still bypasses the shared HTTP client. Credential-vault migration of `SENDGRID_API_KEY`/`STALWART_PASSWORD`/`SMTP2GO_API_KEY` to `CredentialRef` resolved through OpenBao + Item 50's discriminator is also deferred. Today only SMTP2go enjoys the cross-cutting concerns.

**Severity:** Medium
**Scope:** SendGrid SDK transport hook, SMTP2go HTTP calls, Stalwart credential resolution.
**Owner area:** Email extension.
**Prereq:** Items 31 (ProviderHTTPClient), 32 (credential vault), 50 (sandbox/live).

**Purpose.** SendGrid's SDK and SMTP2go's `httpx` calls today bypass the framework's cross-cutting HTTP layer (no shared trace propagation, no shared retry/backoff, no shared rate-limit token bucket, no shared idempotency-key injection, no shared log redaction). After Item 31 lands, both providers route through `ProviderHTTPClient`. After Item 32 lands, `SENDGRID_API_KEY` / `STALWART_PASSWORD` / `SMTP2GO_API_KEY` become `CredentialRef` resolved through OpenBao, env-var fallback, or encrypted column. Item 50's `_TEST` / `_LIVE` discriminator picks the right key per environment.

**Implementation.** SendGrid SDK accepts a custom HTTP transport (the `python-http-client` underlying it has a `session` setter); replace with `ProviderHTTPClient` instance. SMTP2go is direct `httpx` already; swap for shared client. Stalwart's SMTP transport is exempt from HTTP cross-cutting (documented; SMTP submission is a long-lived TCP stream, not request/response). Credential resolution moves to per-request through `instance.api_key.resolve()` rather than per-bond. A SendGrid 401 cache-busts the credential per Item 32.

**Acceptance.** A SendGrid send produces a coherent trace across the framework's tracing backend (Item 34). A SendGrid 429 is throttled by the shared token bucket without producing a 429 storm. Credentials never appear in log output. Rotating `SENDGRID_API_KEY` in OpenBao takes effect within one renewal cycle without a framework restart.

---

# Group 27 — Provider/Extension Restructuring

Items in this group correct extensions whose initial shape mistakenly placed a single concrete provider at the extension level rather than under a properly-named protocol-family extension. The framework's canonical pattern (mirroring `EXT_Database` over `PRV_SQLite`, `PRV_InfluxDB`, `PRV_Fake_Database`) is "the extension owns the protocol family; providers implement specific backends." Provider/extension drift here violates Item 19's scope contract (root vs. team vs. user instances) and the Item 23 collision-detection invariants because the extension namespace is taken by a single product when it should belong to a family.

## ~~Item 98~~ — ~~Restructure Valkey from extension to provider under `EXT_DatabaseMemory`~~ ✅ DONE

**Status: ✅ DONE.** The directory `extensions/valkey/` was removed and replaced with `extensions/database_memory/` containing: `EXT_DatabaseMemory.py` (extension manifest + `AbstractDatabaseMemoryProvider` ABC, the protocol-family abstract retaining the four canonical abilities — `key_value`, `streams`, `pubsub`, `counter`); `PRV_Valkey.py` (Valkey/Redis-protocol concrete provider via redis-py asyncio; class name `PRV_Valkey` retained, now subclassing `AbstractDatabaseMemoryProvider`); `PRV_Fake_DatabaseMemory.py` (the protocol-agnostic in-memory fake — renamed from `PRV_Fake_Valkey` because the family-level fake belongs to the family, not to one product); `EXT.database_memory.md` (rewritten extension contract + provider table + cross-references). The `EXT_DatabaseMemory.get_root_instance()` returns `PRV_Valkey` as the default reference backend; future providers (`PRV_Memcached`, `PRV_DragonflyDB`, `PRV_KeyDB`, `PRV_Garnet`) land as siblings under the same extension. The canonical env name is `DATABASE_MEMORY_URL`; the legacy `VALKEY_URL` is consulted as a fallback so deployments that pre-dated the rename keep working without a config edit. Downstream consumers updated: `logic/EventBus.py::RedisStreamsEventBus` docstring + `EventBus_test.py` docstring point at `EXT_DatabaseMemory`. The framework has not yet shipped to PyPI (Item 86) so no compat shim is required — the old import paths are gone in the same change. Tests: `extensions/database_memory/EXT_DatabaseMemory_test.py` (21 total covering protocol-family naming, ability declarations, redis-py dep, default root provider, URL resolution including the canonical-vs-legacy precedence, fake transport semantics + isolation + handler-error swallowing + close-blocks-further-ops, end-to-end `RedisStreamsEventBus` consumption, broker-transport contract conformance, and acceptance assertions for the removed `extensions/valkey/` path and the unresolvable old import).

**Severity:** Medium
**Scope:** `src/serverframework/extensions/valkey/` → `src/serverframework/extensions/database_memory/`; rename `EXT_Valkey` → `EXT_DatabaseMemory`; keep `PRV_Valkey` and `PRV_Fake_Valkey` as concrete providers under the new extension.
**Owner area:** Extensions / packaging.

**Purpose.** Today `extensions/valkey/EXT_Valkey.py` claims the entire `valkey` extension namespace for a single concrete product (Valkey/Redis-protocol). This is the same shape mistake `EXT_Database` does *not* make — the SQL extension is named for its **protocol family** ("database"), and `PRV_SQLite`, `PRV_InfluxDB`, `PRV_Fake_Database` live as concrete providers under it. The Valkey work followed a different convention by accident: the protocol family is "in-memory data store" (anything that speaks the Valkey/Redis wire protocol or a competing in-memory protocol — Memcached, KeyDB, DragonflyDB, Garnet), not "Valkey." A future Memcached or DragonflyDB provider authored against the framework today has no natural extension home; either it shadows `EXT_Valkey` (incorrect — Memcached does not speak Valkey protocol) or it ships as its own extension (incorrect — it shares all of the same abilities and the in-memory-store family contract). The correct shape is **Valkey is a provider, not an extension**; the extension is `EXT_DatabaseMemory`.

This also restores parity with the existing `EXT_Database` precedent so any future extension author looking at the two side-by-side sees one consistent rule: "extensions are named for protocol families; providers implement specific backends."

**Current state.** `extensions/valkey/` contains:

- `EXT_Valkey.py` (extension manifest + `AbstractValkeyProvider` ABC)
- `PRV_Valkey.py` (concrete Valkey/Redis-protocol provider via `redis-py asyncio`)
- `PRV_Fake_Valkey.py` (in-memory fake for tests)
- `EXT.valkey.md`, `EXT_Valkey_test.py`

Downstream consumers — the `EventBus` Redis-Streams adapter (Item 42), the inbound rate-limit counter (Item 71), the `DistributedCounter` Redis backend (Item 69) — currently import `EXT_Valkey` directly to reach the bonded provider.

**Target state.** Move the directory to `extensions/database_memory/` and rename:

- `EXT_Valkey.py` → `EXT_DatabaseMemory.py` (extension manifest + `AbstractDatabaseMemoryProvider` ABC, the protocol-family abstract). The abstract retains the four canonical abilities — `key_value`, `streams`, `pubsub`, `counter` — that any in-memory data store may implement.
- `PRV_Valkey.py` stays as the Valkey/Redis-protocol concrete provider, now subclassing `AbstractDatabaseMemoryProvider` rather than `AbstractValkeyProvider`. Class name `PRV_Valkey` retained.
- `PRV_Fake_Valkey.py` → `PRV_Fake_DatabaseMemory.py` (the framework-level fake; the protocol-agnostic in-memory test double belongs to the family, not to one product). The class is renamed `PRV_Fake_DatabaseMemory`.
- `EXT.valkey.md` → `EXT.database_memory.md`. Cross-references in `Framework.md`, `EXT.Patterns.md`, and `EXT.Contracts.md` (Item 52) update accordingly.

Future providers (`PRV_Memcached`, `PRV_DragonflyDB`, `PRV_KeyDB`, `PRV_Garnet`) land under the same extension as siblings to `PRV_Valkey` without further restructuring. Providers that do not speak the same protocol declare which abilities they support via the existing `Capability` flag mechanism (Item 95's pattern).

**Implementation notes.** The migration is mostly mechanical. Three substantive concerns:

- **Downstream import sites.** Every consumer of `EXT_Valkey` (EventBus broker transport in `logic/EventBus.py`, inbound rate-limit middleware via Item 71, Item 69's Redis-backed `DistributedCounter`) updates to `EXT_DatabaseMemory`. The bonded-provider lookup is the same shape regardless of name; only the import statement changes.
- **Backwards compatibility.** Because the framework has not yet shipped to PyPI (Item 86 supply-chain hygiene is the gating item), there are no external consumers depending on `serverframework.extensions.valkey`. No deprecation alias is required. Drop the old name in the same change rather than carrying a back-compat shim.
- **Item 23 collision detection.** The extension-name field-injection registry (`@extension_model` collision check) keys off the extension name. The rename surfaces in the registry as a normal namespace change; no code in the registry needs updating.
- **Migration files.** The Valkey extension does not currently own any tables (it is a connection-management extension, like `EXT_Database`'s connection-pool concern). No Alembic migration needs renaming. If a future in-memory-store provider grows tables (rare; would be unusual for an in-memory store), Item 24's file-path detection picks up the new path automatically.

**Acceptance criteria.** A fresh checkout has `src/serverframework/extensions/database_memory/{EXT_DatabaseMemory.py, PRV_Valkey.py, PRV_Fake_DatabaseMemory.py, EXT.database_memory.md}` and no `extensions/valkey/` directory. `EventBus` Redis-Streams transport, the Item 71 rate-limit counter, and the Item 69 `DistributedCounter` Redis backend all consume the renamed extension and pass their existing tests against `PRV_Fake_DatabaseMemory`. A future `PRV_Memcached` author can land their provider as a sibling to `PRV_Valkey` without changing `EXT_DatabaseMemory` or any other framework code (Item 23/26 contracts are unchanged). Cross-references in `Framework.md`, `EXT.Patterns.md`, and `EXT.Contracts.md` are updated.

**Dependencies.** Independent. Cross-references Item 19 (provider scope — root/team/user instances of `PRV_Valkey` continue to behave per Item 19), Item 23 (collision detection — the extension-namespace rename is observed by the registry), Item 24 (migration ownership — file-path detection picks up the renamed directory), Item 26 (`AbstractProviderInstance` contract — `AbstractDatabaseMemoryProvider` retains the abstract-provider shape), Item 42 (EventBus broker transport consumes the renamed extension), Item 69 (DistributedCounter Redis backend consumes the renamed extension), Item 71 (rate-limit counter consumes the renamed extension), Item 86 (no external PyPI consumers yet, so no compat shim needed).


