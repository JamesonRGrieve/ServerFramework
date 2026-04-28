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


