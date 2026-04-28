# Framework Improvements — Pre-Provider Work Plan

## Executive Summary

This document captures the design gaps identified during the review of the framework's documentation against its stated goal: **extensible into any backend application without touching the core, with minimal supplementary code, while preserving full strong typing, documentation, and test coverage.**

The review found thirty discrete items requiring attention. Seven of those are critical-path: until they are resolved, every provider or extension we write will leak provider-specific compensating code into our codebase and will eventually force us to modify the core to recover. The remainder are tractable in parallel once the critical-path items are landed, and several are already partly implemented and only require either documentation, enforcement, or completion.

Each item below is scoped as a deliverable. The intent is that this document becomes the working backlog for the framework hardening effort that precedes provider authorship.

### Severity legend

- **Critical** — must land before provider work begins; gating.
- **High** — required for a clean v1 of the federation story; should land in the same milestone as the critical items.
- **Medium** — quality / completeness of a system that already mostly works.
- **Low** — polish, ergonomics, or developer experience.
- **Deferred** — explicitly out of scope; documented here so we don't relitigate.

### Critical-path summary

The seven gating items that must land before provider authorship are:

1. Item 1 — Unified result contract for external provider calls.
2. Item 2 — Failure classification and rotation policy.
3. Item 4 — Idempotency primitive for external operations.
4. Item 5 — Inbound webhook handler infrastructure.
5. Item 10 — Pluggable authentication strategies on provider instances.
6. Item 14 — Mirror-on-create lifecycle primitive.
7. Item 26 — Explicit `AbstractProviderInstance` contract.

Everything else can iterate without retrofit once those seven are in place.

---

## Item 1 — Unified result contract for external provider calls

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

## Item 2 — Failure classification and rotation policy

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

## Item 3 — Load balancing among providers

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

## Item 4 — Idempotency primitive for external operations

**Severity:** Critical
**Scope:** `AbstractExternalManager`, `RotationManager`, every external provider performing mutating operations.
**Owner area:** Extensions / external federation core.

**Purpose.** Mutating external operations must be safely retryable. When the rotation system retries a transient failure, the first attempt may have actually succeeded but the response was lost in transit. Without an idempotency key on the wire, the retry double-acts: a second charge is issued, a second customer is created, a second email is sent. Stripe, Square, Adyen, and most modern APIs accept an `Idempotency-Key` header for exactly this reason. The framework must provide this primitive rather than asking each provider author to reinvent it.

**Current state.** No idempotency support. Retries are unsafe for any non-idempotent upstream operation.

**Target state.** `AbstractExternalManager` exposes an `idempotency_key(operation, args) -> str` method with a default implementation that hashes the requester id, operation name, and a canonicalized representation of the arguments. Subclasses override when an upstream demands a particular key shape. The bonded provider instance is given a hook to inject the key into the outgoing request — typically as a header, sometimes as a body field — depending on the upstream's contract. The rotation system caches the most recently emitted key per `(provider, operation, key)` tuple for the rotation duration so that a retry inside the same logical operation reuses the key rather than minting a new one.

**Implementation notes.** Idempotency is a property of mutating operations only; reads do not require it. Mark `*_via_provider` methods with a class-level `@idempotent` decorator so the rotation system knows whether to mint a key. The default canonicalization should be stable across Python invocations: sorted-keys JSON of primitives, with `Decimal` and `datetime` rendered in ISO form. Document the key lifetime and recommend providers expire entries after the rotation budget is exhausted to avoid unbounded memory growth.

**Acceptance criteria.** A provider author writing `create_charge_via_provider` annotates it `@idempotent` and the framework guarantees that any retry performed by the rotation system will carry the same key as the original attempt. Replaying a request from the client side with the same logical inputs produces the same key and the upstream returns the prior result rather than creating a duplicate.

**Dependencies.** Depends on Items 1 and 2 (typed errors and rotation policy). Independent otherwise.

---

## Item 5 — Inbound webhook handler infrastructure

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

## Item 6 — Field-mapping pipeline beyond 1:1 renames

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

## Item 7 — Pagination homogenization

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

## Item 8 — Search DSL translation

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

## Item 9 — N+1 prevention through `include` for external navigation

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

## Item 10 — Pluggable authentication strategies on provider instances

**Severity:** Critical
**Scope:** `AbstractStaticProvider.bond_instance`, `ProviderInstanceModel`, new `AuthStrategy` ABC and concrete subclasses; integration point for the OAuth extension.
**Owner area:** Provider system.

**Purpose.** Today `bond_instance` assumes static credentials — typically an API key — extracted from `ProviderInstanceModel`. Real upstream APIs require a wide range of authentication shapes: OAuth2 with refresh, AWS SigV4 request signing, JWT-bearer with rotation, mTLS, short-lived STS-style tokens, GitHub-app installation tokens, and per-end-user delegated credentials (Stripe Connect, Google Workspace impersonation). The user has confirmed that an OAuth extension exists in a separate repository and is expected to plug into this system; the framework must accept the plug-in, not bake API-key assumptions into the bonding layer.

**Current state.** `bond_instance` examples show direct extraction of `api_key` and a few settings. Multi-step auth flows (token refresh, request signing, credential exchange) have no documented home.

**Target state.** Define `AuthStrategy` as an abstract base class with the contract `headers_for(requester) -> dict`, `params_for(requester) -> dict`, `body_modifier(requester, body) -> body`, and `refresh_if_needed()`. Ship concrete subclasses: `APIKeyAuth`, `OAuth2Auth` (consumed and extended by the OAuth extension), `AWSSigV4Auth`, `JWTBearerAuth`, `MTLSAuth`. `ProviderInstanceModel` carries an `auth_strategy_name: str` and an opaque credentials blob that the strategy interprets. `bond_instance` looks up the strategy by name from a registry, hydrates it from the blob, and the bonded instance routes every outbound call through the strategy's headers/params/body hooks.

**Implementation notes.** The strategy registry is populated by the framework for built-in strategies and extended at runtime by extensions (the OAuth extension contributes `OAuth2Auth` with refresh handling). A single provider may declare a default strategy and accept overrides per provider instance, so a Stripe provider class can default to `APIKeyAuth` and have a per-instance override to `OAuth2Auth` for Stripe Connect users. Strategies must be reloadable so that the OAuth extension can hot-swap a refreshed token without re-bonding the entire instance.

**Acceptance criteria.** A Stripe Connect integration installable as an extension can register an `OAuth2Auth` strategy, attach it to per-user provider instances, and have outbound calls signed correctly without modifying the Stripe provider class. Adding a new authentication scheme is a matter of contributing a new `AuthStrategy` subclass; no changes are required in `AbstractStaticProvider` or in any existing provider.

**Dependencies.** Anticipates the external OAuth extension. Independent of other items in this document.

---

## Item 11 — Schema drift and contract testing

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

## Item 12 — Bulk endpoint expression for upstream APIs that support them

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

## Item 13 — Streaming, websocket, SSE, and long-poll support

**Severity:** Medium
**Scope:** Service interface broadening (see Item 28), new `StreamingService` abstraction, integration with the hook bus.
**Owner area:** Services + external federation.

**Purpose.** Real-time integrations — Stripe's events firehose, Slack RTM, Kafka-style consumers, websocket-driven chat upstreams — do not fit the request/response shape of `*_via_provider`. They are long-lived, asynchronous, and stateful. The framework's current service interface, designed for perpetual time-based loops (a thirty-second agentic loop, for example), is the correct conceptual home but must be broadened to accommodate connection-oriented work.

**Current state.** The service interface is shaped around perpetual time-based tasks. There is no documented pattern for streaming or connection-oriented services.

**Target state.** Broaden the service abstraction (Item 28) to include `StreamingService` with two flavors. `ConsumerService` covers long-lived inbound connections (websocket subscribers, SSE listeners, Kafka consumers); its lifecycle is `connect → on_message(event) → disconnect`, with automatic backoff and reconnection on disconnect. `ProducerService` covers long-lived outbound streams that we write to. Both flavors fan their events into the same hook bus that internal mutations and inbound webhooks (Item 5) use, so a Stripe event arriving via the events firehose triggers the same downstream hooks as the equivalent webhook delivery.

**Implementation notes.** Reconnection backoff is exponential with jitter, capped at a configurable maximum. Long-lived services participate in graceful shutdown: on framework stop, the service receives a stop signal, drains in-flight events with a deadline, and disconnects cleanly. State that the service must persist across restarts (last-seen-event cursors, subscription tokens) lives in a small per-service state table or in the provider's external state store, not in process memory.

**Acceptance criteria.** A `StreamingService` author can declare a Stripe events subscriber, have the framework manage connection lifecycle, reconnect automatically on transient failures, and route received events into the existing hook chain on the corresponding `Stripe_*Manager` classes without writing connection-management code.

**Dependencies.** Depends on Item 28 (broadened service interface). Cross-references Item 5 (shared hook bus path).

---

## Item 14 — Mirror-on-create lifecycle primitive

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

**Dependencies.** Depends on Item 28 (background service for compensating actions).

---

## Item 15 — External API test contract reconciled with the no-mock pillar

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

## Item 16 — Real GraphQL federation, not RPC wrapping

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

## Item 17 — Rate-limit and quota awareness per provider

**Severity:** Medium
**Scope:** Per-provider `RateLimit`, token-bucket implementation, response-header parsers, integration into rotation.
**Owner area:** Provider rotation system.

**Purpose.** Without rate-limit awareness the rotation system will, on a sustained-rate workload, drive a provider to its rate limit, observe the 429 responses, and rotate to the backup — which it will then drive to the same rate limit. This is rate-limit storming. The correct behavior is to queue, throttle, and respect the upstream's `Retry-After` (or equivalent) signal against the same provider rather than rotating away from it.

**Current state.** No rate-limit primitives. Rotation responds to 429 like any other failure.

**Target state.** Each provider declares an optional `rate_limit: ClassVar[Optional[RateLimit]]` carrying the steady-state requests-per-second and burst capacity. The framework maintains a per-provider token bucket; outbound calls acquire a token before issuing and block (with timeout) when the bucket is empty. Response headers honor `Retry-After`, `X-RateLimit-Reset`, and provider-specific equivalents via a per-provider header parser. When a rate-limit signal is observed, the rotation system pauses that provider for the indicated window rather than rotating.

**Implementation notes.** The token bucket is in-process for single-instance deployments; for multi-instance deployments, point the rate-limit accounting at a shared store (Redis). Per-provider concurrency caps complement rate limits and are configured similarly. Document the interaction with quota tracking (Item 19): rate limits constrain wire-level throughput, quotas constrain logical-billing usage — they are independent dimensions and both apply.

**Acceptance criteria.** A provider with `rate_limit = RateLimit(rps=100, burst=20)` saturating at 100 requests per second never produces a 429 under steady load. When a 429 does occur (e.g., from a different consumer of the same upstream key), the provider is paused for the upstream-indicated window and resumes automatically. No rotation is triggered by rate-limit signals.

**Dependencies.** Depends on Item 2 (rotation policy hosts the rate-limit branches). Cross-references Item 19 (quota tracking).

---

## Item 18 — Permissions registration tied to OAuth scope shape

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

**Dependencies.** Anticipates the external OAuth extension. Depends on Item 20 (extension registry plumbing if not already present).

---

## Item 19 — Root and system providers, with unified per-user/per-team quota

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

**Dependencies.** Independent of other items. Cross-references Item 17 (rate limits — different dimension, both apply).

---

## Item 20 — Hot reload and manifest-based extension installation

**Severity:** Medium
**Scope:** New `manifest.toml` format, `install_from_manifest` machinery, registry diff, hot-reload controller.
**Owner area:** Extension system.

**Purpose.** Extensions are currently discovered once at startup based on a CSV environment variable. There is no installation-from-registry, no manifest, no hot-reload. For a framework whose stated goal is to be extensible into any backend without core modification, the inability to install or update extensions without a restart is a meaningful limitation, particularly in long-running multi-tenant deployments.

**Current state.** Extension discovery is filesystem-based and CSV-gated. Adding an extension requires placing files in the extensions directory, updating the CSV, and restarting.

**Target state.** A `manifest.toml` per extension declaring metadata, dependencies, entry points, and version. An `install_from_manifest(url_or_path)` operation that fetches, validates, runs migrations, and registers the extension at runtime without restart. A hot-reload controller, triggered by a SIGHUP-style signal or an admin API call, that re-runs discovery and applies the registry diff: newly-present extensions initialize, removed extensions are torn down with their cleanup hooks, modified extensions reload. Static class identity must be preserved across reloads (`importlib.reload` carefully combined with a registry diff that maps old class objects to new ones for the hook registry's sake).

**Implementation notes.** Migrations during install must be reversible or at least non-destructive enough that a failed install can be rolled back without data loss. Document a rollback procedure. The manifest format should be minimal — name, version, dependencies (extensions, pip, system), entry-point module — and human-editable. A registry endpoint (an HTTP-served list of available manifests) is out of scope for this item but should be kept in mind as a natural extension. Hot reload of code carries known Python pitfalls (cached references in other modules, decorators, metaclass state); document the constraints clearly and treat full process restart as a fallback when hot reload cannot be performed safely.

**Acceptance criteria.** An admin can install a new extension at runtime via `install_from_manifest`, the extension's migrations run, its hooks register, and its endpoints become available without restarting the application. A SIGHUP triggers re-discovery and applies the diff cleanly. Removing an extension cleans up its hooks, endpoints, and background services.

**Dependencies.** Independent.

---

## Item 21 — Deterministic hook ordering across extensions

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

## Item 22 — Implement the documented `blocking=False` hook parameter

**Severity:** Medium
**Scope:** `@hook_bll` decorator, hook execution flow.
**Owner area:** Hook system.

**Purpose.** The hook system documentation describes a `blocking=False` parameter for AFTER hooks: when set, exceptions inside the hook are logged and a metric is emitted, but the operation succeeds. This is the right design for non-critical AFTER hooks (audit, notification, analytics) — a notification-send failure should not roll back the user-create operation it observed. Today the parameter is documented but not implemented; authors must remember to wrap their AFTER hooks in try/except blocks, and the inevitable forgetfulness produces production failures from non-essential observers.

**Current state.** Documented as proposed syntax. Not implemented.

**Target state.** `blocking=True` is the default for BEFORE hooks (security and validation must fail loudly). `blocking=False` is the default for AFTER hooks (observers should not break the operation). Both defaults are overridable per hook. Non-blocking exceptions log at the appropriate level, emit a configurable metric, and never propagate. A `non_critical_hook` decorator alias is provided as ergonomic sugar for `@hook_bll(..., blocking=False)`.

**Implementation notes.** The metric name should be queryable per hook so operators can identify a hook that is failing silently. Document the policy: blocking-by-default for BEFORE, non-blocking-by-default for AFTER, with the explicit override in either direction available.

**Acceptance criteria.** An AFTER hook that raises does not fail the operation when `blocking=False`. A BEFORE hook that raises does fail the operation by default. The metric for non-blocking failures is emitted and visible.

**Dependencies.** Independent.

---

## Item 23 — `@extension_model` collision detection at startup

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

## Item 24 — Single canonical mechanism for migration ownership detection

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

## Item 25 — Generated SDK handlers from BLL `RouterMixin`

**Severity:** Medium
**Scope:** New SDK generator, existing `SDK_Auth.py`, `SDK_Providers.py`, `SDK_Extensions.py` (potentially regenerated), the `AbstractSDKHandler` contract.
**Owner area:** SDK.

**Purpose.** Inspection of the SDK directory confirms that handlers are hand-written today: `UserSDK`, `TeamSDK`, and the rest each declare their `ResourceConfig` blocks manually. This is configuration-driven, but it is not generation-driven — every new BLL manager that declares `RouterMixin` must be matched by a hand-written SDK handler. The same Pydantic-and-`RouterMixin` source already powers REST routing, OpenAPI schemas, and the GraphQL surface; the SDK should derive from it equally. As-is, the SDK will silently drift behind extensions, and the framework's promise of "minimal supplementary code" leaks an SDK-handler-per-resource tax onto every extension author.

**Current state.** SDK handlers are hand-authored using `ResourceConfig`. New BLL managers require new SDK files.

**Target state.** A generator walks the registry of `RouterMixin`-tagged managers and emits an `SDKHandler` per resource at build time (or at runtime, with cached output). The generated handlers are deterministic and overwrite-safe — regeneration produces the same file, byte for byte. Hand-authored handlers remain for non-CRUD operations (login, logout, install_extension, etc.) and for resources that need behavior beyond the mechanical CRUD shape; these are clearly distinguished and live in separate files. The generator runs as part of the SDK build and is gated by CI.

**Implementation notes.** The generator should use the same source-of-truth as the OpenAPI generator and the REST router generator, so that all three are derived from one introspection pass over the registry. Field selection and pagination support, search transformers, and authentication overrides flow into the generated handlers automatically. The generated SDK is published per the existing `SDK.Publish.md` process. Authors writing custom handlers extend or override the generated ones via subclassing.

**Acceptance criteria.** Adding a new `RouterMixin`-decorated manager to an extension produces a corresponding SDK handler with full CRUD, search, and batch support, without any SDK code being written by the author. The generated SDK is byte-stable across regenerations.

**Dependencies.** Cross-references the documentation pass that consolidates registry introspection.

---

## Item 26 — Explicit `AbstractProviderInstance` contract

**Severity:** Critical
**Scope:** `AbstractProviderInstance`, `bond_instance` typing, all existing and future provider authors.
**Owner area:** Provider system.

**Purpose.** Provider instance classes today are described as "what `bond_instance` returns." Provider methods access `self._instance.something()` without any typed contract specifying what `_instance` is or what methods it must expose. The required interface is implicit, discovered by trial and error, and undocumented in code. Every provider author rediscovers it on their own and may end up implementing different shapes that the framework cannot rely on uniformly.

**Current state.** `AbstractProviderInstance` exists as a near-empty base class. The bound `_instance` attribute is referenced in examples but never declared on the provider class. There is no typed contract enforcing the shape.

**Target state.** Promote `AbstractProviderInstance` to a real contract. Make it either an `abc.ABC` with required abstract methods or a `typing.Protocol` capturing the canonical shape. The contract specifies: `__init__(self, instance: ProviderInstanceModel)` so bonding is uniform; `validate_credentials() -> bool` for self-test before first use; `close()` for cleanup of any held connections; and any extension-specific abstract methods (e.g., `AbstractPaymentProviderInstance` requires `create_charge`, `refund`, etc.).

The provider class declares `_instance: ClassVar[Optional[AbstractProviderInstance]] = None` as a typed attribute, and `bond_instance` is typed as `(cls, ProviderInstanceModel) -> AbstractProviderInstance` so static type checking catches contract violations. A `mypy`-or-equivalent gate enforces the typing in CI.

**Implementation notes.** Existing provider examples in the documentation are migrated to declare the typed attribute and to inherit from the appropriate `AbstractProviderInstance` subclass. The `_instance` attribute is set during bonding in a single canonical code path (a base-class helper), so authors do not write the assignment themselves.

**Acceptance criteria.** A provider author writing a new provider knows exactly which methods their `*ProviderInstance` class must implement, the static type checker enforces it, and forgotten methods produce a clear error rather than a runtime `AttributeError`. The `_instance` attribute on every provider class is typed and resolves correctly under static analysis.

**Dependencies.** Cross-references Item 10 (auth strategies live alongside the bonded instance).

---

## Item 27 — Separate `is_configured` from `health_check`

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

## Item 28 — Broaden the service interface beyond perpetual time-based loops

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

## Item 29 — Unskip pagination, filtering, and search-pagination tests in core endpoints

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

## Item 30 — Surface skipped optional dependencies at startup

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

## Sequencing recommendations

The work is organized into three roughly-parallel tracks once the critical-path items land. The seven critical items (1, 2, 4, 5, 10, 14, 26) form the gating set and should be addressed before provider authorship begins.

- **Track A — External federation core.** Items 1, 2, 4, 5, 6, 10, 14, 17, 26. Ordered roughly: 1 → 2 → 4 (depend on the typed error hierarchy); 5 and 10 in parallel; 6 in parallel; 14 after 28 lands the compensating-service infrastructure; 17 anytime after 2.
- **Track B — Pagination, search, navigation, GraphQL federation.** Items 7, 8, 9, 11, 12, 16, 29. Ordered roughly: 7 and 8 in parallel; 9 after 7; 11 anytime; 12 after 1, 4; 16 after 9, 10, 11, 15; 29 after 7, 8.
- **Track C — Cross-cutting framework hardening.** Items 15, 18, 19, 20, 21, 22, 23, 24, 25, 27, 28, 30. All largely independent of one another; 28 before 13 and 14; 18 before any extension that needs to register permissions; 19 before any provider work that involves billable usage.
- **Documentation-only.** Item 3.

The expected critical-path completion is the gate for opening provider work; the remaining items can be landed iteratively while provider authorship begins on the now-stable foundation.

---

## Second-round audit additions

A subsequent independent documentation audit surfaced additional gaps that the first thirty items did not cover. They are added below as Items 31–52. Several extend or refine earlier items; cross-references are noted in each entry.

---

## Item 31 — Shared outbound HTTP client primitive

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

## Item 32 — Credential vault with OpenBao as default

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

**Dependencies.** Independent. Cross-references Item 10 (auth strategies consume credentials through this layer).

---

## Item 33 — Upstream API version pinning per provider

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

## Item 34 — Distributed tracing and provider-call metrics

**Severity:** Medium
**Scope:** `RotationManager.rotate`, shared HTTP client (Item 31), `RequestContext`, metrics emission layer.
**Owner area:** Observability.

**Purpose.** A single user request traverses API → BLL → rotation → bonded SDK → outbound HTTP. Diagnosing latency or failures along that path requires correlated traces across all layers. Metrics on rotation behavior (how often we rotated, which provider succeeded, how long each attempt took) are essential for capacity planning and for catching upstream degradation before it becomes user-visible. Today none of this is documented.

**Current state.** `meta_logging` extension exists for structured logs; no trace context propagation; no provider-call metrics.

**Target state.** W3C `traceparent` and `baggage` headers propagate from the inbound request through `RequestContext`, into `RotationManager.rotate`, into the shared HTTP client (Item 31), and out to the upstream. The framework emits a fixed set of provider-call metrics: latency histogram per `(provider, ability)`, success/error rate, rotation-attempt counter (how many providers we walked before success), per-provider health gauge (consumed from Item 27's `health_check`), idempotency-key cache hit rate. Metrics emission is pluggable — Prometheus, OpenTelemetry, Statsd backends — with a no-op default for environments that do not collect.

**Implementation notes.** Trace context lives on `RequestContext` and is set by an ASGI middleware on inbound requests. The shared HTTP client reads it from the active context. `RotationManager.rotate` opens a span per attempt, tagged with the provider name and the attempt index, so a multi-provider rotation appears as a parent span with N attempt-children. Document the span naming convention; recommended convention is to follow OTel HTTP-client semantic conventions (`http.method`, `http.url.template`, `http.response.status_code`, `peer.service` as the upstream provider name) so backends like Jaeger, Honeycomb, and Datadog give correlated UX out of the box. Metric labels are bounded — provider names and ability names form a small cardinality set; do not add unbounded labels (such as user id) to metric tags.

**Acceptance criteria.** A request tagged with a `traceparent` produces a coherent trace from inbound API through outbound provider call in the configured tracing backend. Provider-call metrics are visible in the configured metrics backend with consistent label names. A simulated rotation with three failed attempts and one success appears as four attempt-spans nested under one parent.

**Dependencies.** Cross-references Items 27, 31. Independent of other items.

---

## Item 35 — Outbox, dead-letter queue, and reconciliation primitive

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

**Dependencies.** Depends on Item 28. Cross-references Item 14 (which becomes a specific use case of this primitive).

---

## Item 36 — Data residency and regional provider pools

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

## Item 37 — Typed `ProviderSettings` and ability declarations

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

## Item 38 — Pydantic-typed seed data

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

## Item 39 — Per-resource endpoint versioning

**Severity:** Medium
**Scope:** `RouterMixin`, route generation, OpenAPI generation, SDK generation (Item 25).
**Owner area:** Endpoints.

**Purpose.** Endpoint paths today are hardcoded `/v1/<resource>`. There is no documented mechanism for promoting a resource to `/v2/`, no side-by-side dual-version routing, no deprecation contract. Any non-trivial framework will eventually need to ship a v2 of some resources while v1 remains supported; the current shape gives no path to that without core changes — which is the situation the framework most wants to avoid.

**Current state.** `prefix: ClassVar[str] = "/v1/<resource>"` is hand-set per manager. No version field. No per-version routing.

**Target state.** `RouterMixin` exposes `version: ClassVar[str] = "v1"` (default) and the prefix is computed from the version plus the resource name. Multiple managers may register the same resource at different versions; both versions route concurrently, both appear in OpenAPI, both are present in the generated SDK as version-suffixed methods. A `deprecated_in: ClassVar[Optional[str]]` and `sunset_in: ClassVar[Optional[str]]` carry the deprecation contract — the framework adds `Deprecation` and `Sunset` HTTP headers automatically and emits a logged warning per-call after the deprecation date.

**Implementation notes.** Versions are alphanumeric tokens (`v1`, `v2`, `v2beta`, `v3rc1`); ordering for "latest" is lexicographic with documented quirks for prereleases. The SDK generator (Item 25) emits versioned method names. REST gets path-versioned routes; GraphQL gets field-level `@deprecated` and `@sunset` directives by default, since real GraphQL evolution is field-level deprecation plus additive change rather than wholesale type renaming. Full type-version namespacing in GraphQL is opt-in for breaking renames where field-level deprecation cannot express the change. Persisted-query interaction: a persisted query bound to v1 continues to resolve against v1 types after v2 ships, so persisted-query stores must record the version they were registered against.

**Acceptance criteria.** A `UserManagerV2(AbstractBLLManager, RouterMixin)` declared alongside the existing `UserManager` produces a `/v2/user` route concurrently with `/v1/user`, both versions in OpenAPI, both versions in the SDK. Sunsetting `/v1/` emits the documented headers and logs.

**Dependencies.** Cross-references Item 25 (SDK generation).

---

## Item 40 — Custom-route contract with SDK, GraphQL, and test parity

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

## Item 41 — Type-safe hook context with `ParamSpec` and `Generic`

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

## Item 42 — Cross-process event bus seam

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

## Item 43 — Abstract provider templates for missing infrastructure categories

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

## Item 44 — Service layer async and lifecycle semantics

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

## Item 45 — Field- and column-level attribute-based access control

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

## Item 46 — GraphQL composition contract for our own extensions

**Severity:** Medium
**Scope:** Strawberry schema generation, multi-extension Query/Mutation/Subscription root composition, conflict resolution.
**Owner area:** Endpoints / GraphQL.

**Purpose.** This item is distinct from Item 16, which addresses federating *external* GraphQL providers into our schema. Item 46 addresses how *our own* extensions contribute to the GraphQL surface. `EP.GQL.md` says GraphQL is auto-generated from BLL, but does not specify how an extension contributes Query / Mutation / Subscription root fields, how conflicts between extensions adding the same root field are resolved, or whether Strawberry Federation directives govern how extension-contributed types interact. Without this contract, the first extension that wants to add a non-CRUD GraphQL field will require a core change — directly violating the framework's "extensions only" principle.

**Current state.** GraphQL is described as auto-generated from BLL `RouterMixin` managers. Multi-extension composition is undocumented.

**Target state.** A documented composition contract. Each extension's `RouterMixin`-tagged managers contribute their Query/Mutation/Subscription roots into a merged schema. Custom routes (Item 40) participate via the same `expose_in` flag. The merge resolves conflicts in three stages: identical contributions (same name, same signature) merge as a single field; non-identical contributions on the same field name fail at startup with a clear error naming the offending extensions (mirroring Item 23's collision detection); namespacing under the extension name is offered as an opt-in for extensions that explicitly want to avoid collisions. Extensions can also contribute new types (not just root fields) to the schema; type-name collisions follow the same three-stage resolution.

If Strawberry Federation is the target architecture, document which Federation directives extensions may declare on their types (`@key`, `@external`, `@requires`, `@provides`) and how those compose with Item 16's federation of external GraphQL upstreams. Otherwise, document that extensions ship a single merged subgraph and that gateway-level federation is out of scope for our own composition (only relevant for external providers per Item 16).

**Implementation notes.** Subscriptions require event-bus integration (Item 42) for cross-process delivery; document the in-process subscription path as the default (WebSocket / SSE-backed) and Item 42 as the upgrade path for multi-process deployments. The merged schema is rebuilt on extension install/uninstall (Item 20) and the rebuild diff is logged so operators can see what changed. **DataLoader integration:** internal cross-extension navigation (e.g. extension B's resolver accesses extension A's model) participates in the same `include`-driven batched-resolver mechanism Item 9 establishes for external navigation. A per-request DataLoader keyed by `(model, set_of_ids)` collects N parallel resolutions of `field.related` into a single batched fetch, so cross-extension joins do not N+1 the database. Without this, our own multi-extension surface produces the same N+1 storm Item 9 prevents at the federation boundary.

**Acceptance criteria.** Two extensions both adding fields under the root `Query` produce a merged schema with both fields present; two extensions both attempting to add a field with the same name and a different signature produce a clear startup error. An extension can ship its own GraphQL types and have them appear in the merged schema without modifying core.

**Dependencies.** Cross-references Items 16, 20, 23, 40, 42.

---

## Item 47 — Deadline budget propagation through `RequestContext`

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

## Item 48 — Graceful degradation contract

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

---

## Item 49 — Cross-extension migration ordering with foreign-key awareness

**Severity:** Medium
**Scope:** `MigrationManager`, extension dependency declarations, Alembic migration runner.
**Owner area:** Database / migrations.

**Purpose.** `DB.Migrations.md` documents migration ordering as "all core migrations, then all extension migrations in extension dependency order." This handles dependencies declared via `EXT_Dependency`, but says nothing about an extension whose models hold foreign keys into another extension's models. If extension B's table FKs into extension A's table, B's migration creating that FK must run after A's migration creating the referenced table — and the framework does not currently enforce this as a topological constraint over the merged migration graph. The result is that running migrations in a fresh database with multiple extensions can fail at FK creation time, even when both extensions individually pass their own migration tests.

**Current state.** Per-extension migration ordering is documented. Cross-extension FK ordering is not.

**Target state.** Migration ordering is computed as a topological sort over the union of (a) declared `EXT_Dependency` relationships and (b) FK references discovered by inspecting model definitions. An extension whose model has an FK into another extension's model implicitly depends on that extension for migration purposes, even if it did not declare the dependency explicitly. Cycles in the merged graph (an FK from A to B and another from B to A) fail at startup with a clear error naming the offending tables and extensions.

**Implementation notes.** The topological sort runs once at startup and is cached. Cross-extension FK detection inspects both `@extension_model` field injections and standalone extension tables. FK detection requires the model classes to be loaded before migrations run; the framework's extension registry imports all models on startup before delegating to the Alembic env, and the migration runner is documented as depending on this load order. Document the recommended pattern for extensions that genuinely need bidirectional references (introduce a join table owned by one of the extensions, rather than direct FKs in both directions).

**Acceptance criteria.** A fresh-database migration run with extensions A and B, where B has an FK into A, produces a correct migration order automatically without B declaring an explicit `EXT_Dependency` on A. A circular FK dependency fails at startup with a clear error.

**Dependencies.** Refines Item 24 (single-mechanism ownership detection). Cross-references Item 20 (hot install must respect the ordering for runtime-installed extensions).

---

## Item 50 — Sandbox versus live credential split convention

**Severity:** Low
**Scope:** Provider configuration, `_env` schema (Item 37), shared HTTP client (Item 31).
**Owner area:** Provider system / configuration.

**Purpose.** Most external APIs distinguish test mode from live mode (Stripe's `sk_test_*` versus `sk_live_*`, SendGrid's sandbox flag, Twilio's test credentials). Today there is no documented convention for naming the paired environment variables, no per-environment selection mechanism, and no enforcement that a deployment marked `production` will refuse test credentials and vice versa. Every provider author rolls their own scheme.

**Current state.** No sandbox/live convention.

**Target state.** A canonical convention: paired env vars are named `{PROVIDER}_API_KEY_TEST` and `{PROVIDER}_API_KEY_LIVE` (and similarly for any other secret), with selection driven by a top-level `APP_ENV` discriminator (`development`, `staging`, `production`). The framework's typed `EnvSchema` (Item 37) declares which variables are paired and the shared HTTP client (Item 31) selects the right value at request time. Production deployments refuse to start if any provider resolves to a `_TEST_` credential; development deployments warn (but do not refuse) on `_LIVE_` credentials.

**Implementation notes.** Some upstreams use a single key with a server-side test/live flag rather than paired keys; document that the convention is opt-out for those providers and the discriminator is provider-specific. The headers injected per environment (e.g. `Stripe-Account` for Connect, sandbox flags) are likewise declarative on the provider rather than imperative inside `bond_instance`. `APP_ENV` is the default discriminator but providers can override via `environment_source: ClassVar[str]` (e.g. `STRIPE_ENV`) for deployments that genuinely need per-provider environment selection — staging processes that hit production-Stripe-test-mode plus staging-DB are common enough to warrant the escape hatch.

**Acceptance criteria.** A staging deployment uses `_TEST_` credentials automatically; a production deployment fails to start if any `_TEST_` credential is configured for an in-use provider; the documentation describes the convention in one place.

**Dependencies.** Cross-references Items 31, 37.

---

## Item 51 — Sticky-session routing in rotation

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

## Item 52 — Single `EXT.Contracts.md` enumerating the framework's public primitives

**Severity:** Low
**Scope:** New documentation file collating contracts referenced from elsewhere.
**Owner area:** Documentation.

**Purpose.** Several framework primitives are referenced repeatedly across the existing documentation as if defined: `ExtensionRegistry`, `RotationManager`, `ProviderInstanceModel`, `external_navigation_property`, the non-BLL `@hook` decorator, `@extension_model`, `@custom_route`, `AbstractExternalAPIClient`, `AbstractProviderInstance`. Their full signatures, invariants, and intended usage are scattered across multiple files (or, in some cases, only present implicitly in code). An extension author piecing together the contract from prose ends up guessing, and guessing reliably produces extensions that almost-but-not-quite follow the framework's expectations. A single canonical reference document closes this gap.

**Current state.** Primitives are referenced everywhere, defined in scattered places, sometimes implicit in code only.

**Target state.** A new `EXT.Contracts.md` enumerates every primitive an extension author touches, with the full typed signature, the invariants the framework guarantees, the invariants the extension must uphold, the recommended usage pattern, and cross-references to the deeper documents that describe the surrounding system. The document is generated where possible from docstrings and type annotations, so it stays in sync with the code; sections that cannot be generated (invariants, recommended usage) are written by hand and reviewed on each public-API change.

**Implementation notes.** The generation step is a simple Sphinx-like or `pdoc` pass over the public symbols, producing the typed-signature half. The invariant and usage sections are hand-written stubs that fail CI when a new public symbol lands without a matching entry. Document the policy: if a symbol does not appear in `EXT.Contracts.md`, extension authors should not use it — it is not stable. To keep the manifest reliable, commit a JSON manifest of expected public primitives alongside the markdown file and have CI fail when (a) a public symbol is added without a matching manifest entry, or (b) a manifest entry has no corresponding documented contract. Pdoc-style generation alone is brittle under tool churn; the manifest is the source of truth.

**Acceptance criteria.** Every primitive named in `EXT.Patterns.md`, `PRV.Patterns.md`, `PRV.External.md`, and the BLL/EP/SDK pattern docs has a corresponding entry in `EXT.Contracts.md`. Adding a new public primitive requires adding its contract entry in the same change.

**Dependencies.** Independent.

---

## Third-round audit additions

A subsequent gap-spotting pass surfaced infrastructure primitives the framework needs that the first two rounds did not call out, plus the two authentication doors-open items the user explicitly requested. Items 53–57 are the gap items; items 58–59 are the auth extensions, each captured as a single line item that specifies *both* the framework provisions required to leave the door open and the extension implementation that walks through it.

---

## Item 53 — Advisory locking primitive

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

## Item 54 — Read-replica routing for read-only operations

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

## Item 55 — Tenant data-isolation primitives

**Severity:** Medium
**Scope:** Postgres Row-Level Security policies, session GUC variables, tenant-scoped model declarations.
**Owner area:** Multi-tenancy / security.

**Purpose.** Item 36 covers data residency at the provider-instance level (which Stripe account does this user's traffic go to); it does not cover row-level data isolation at the database level (which rows can this user even see). Today every tenant-scoped query relies on the BLL author remembering to filter by `team_id` — a pattern that works under code review but inevitably leaks under refactoring, custom queries, raw SQL, or simple typos. A single missed filter clause is a cross-tenant data exposure. The framework needs a defense-in-depth primitive that enforces isolation at the database layer, regardless of what the application code does.

**Current state.** Tenant filtering is by convention. No row-level enforcement.

**Target state.** Tenant-scoped models declare themselves via a `TenantScopedMixin` that adds `team_id` (or a configurable tenant-key field) and registers a Postgres Row-Level Security policy at table-creation migration time. The session binder sets `app.current_team_id` as a Postgres GUC variable on every connection; the RLS policy filters reads and writes by matching `team_id = current_setting('app.current_team_id')::uuid`. A missing or unset GUC variable causes the policy to return zero rows, so an unauthenticated session or a forgotten tenant-context bind sees nothing rather than seeing everything.

System-level operations (admin endpoints, cross-tenant reporting, the framework's own internal operations) bind a privileged session that bypasses the RLS policy via a Postgres role with `BYPASSRLS`. The privilege boundary is at the session-bind layer, not at individual queries — there is no way to selectively bypass RLS for a single query without binding a privileged session, by design.

**Implementation notes.** Postgres RLS is well-supported and battle-tested but has known costs: queries on RLS-protected tables get a planner overhead, and policy expressions must be `STABLE` or simpler for the planner to optimize. The framework's policy template is the simplest possible (`USING (team_id = current_setting(...)::uuid)`) so the planner cost is predictable. Migration of an existing application to RLS is non-trivial: a phased rollout (RLS in `WARN` mode logging policy violations without enforcing, then `ENFORCE` mode) is documented. The framework includes a startup check that verifies every `TenantScopedMixin`-tagged table has an enforced RLS policy and refuses to start otherwise — the policy and the mixin must agree.

**Acceptance criteria.** A `TenantScopedMixin`-tagged model declared at extension load time produces a corresponding Postgres RLS policy in the migration. A query against the model from a session with `app.current_team_id` set returns only matching rows; a query from a session without the setting returns zero rows. A privileged session (admin, reporting) bypasses RLS via a separate role; no in-application code can selectively bypass RLS without binding the privileged session.

**Dependencies.** Cross-references Item 36 (residency, distinct concern), Item 49 (migration ordering — RLS policies are migration artifacts subject to FK-aware ordering).

---

## Item 56 — Audit log retention and archival

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

---

## Item 57 — Background job priority and per-tenant fairness

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



