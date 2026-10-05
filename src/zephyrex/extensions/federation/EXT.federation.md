# External Federation (Item 16)

> **GraphQL surface:** [endpoints/EP.GQL.md](../endpoints/EP.GQL.md) | **External providers:** [extensions/PRV.External.md](../extensions/PRV.External.md)

## Overview

External federation lifts upstream APIs — GraphQL or REST — into the framework's model registry so a single inbound request can transparently traverse local data and federated upstreams. Two modules cover the four directions:

| Module | Upstream kind | Inbound surfaces |
|--------|---------------|------------------|
| `lib/Federation_GQL.py` | GraphQL | GraphQL (true federation, default) + REST (route projection) |
| `lib/Federation_REST.py` | REST | REST (existing `AbstractExternalModel`) + GraphQL (Pydantic lift) |

The design intent: do the schema lift once, into Pydantic. Both inbound surfaces (REST and GraphQL) come for free via the existing `Pydantic2FastAPI` and `Pydantic2Strawberry` pipelines. The selection-set push-down, batched cross-subgraph resolver, and merged-schema registry remain in place for the GraphQL → GraphQL passthrough path where a Pydantic round trip would lose Apollo Federation v2 semantics.

## Federation styles

`AbstractGraphQLProvider.federation_style` selects how the upstream is composed.

| Style | When to use | Result |
|-------|-------------|--------|
| `apollo_v2` | Upstream advertises `_service { sdl }` and `@key`/`@external`/`@requires`/`@provides`. | Honors federation directives. Cross-subgraph joins via the framework gateway. |
| `stitching` | Upstream supports introspection only. | Framework merges the upstream SDL; resolvers push the selection set through `BatchedFieldResolver`. |
| `namespaced` | Upstream's type names would collide with local types. | Every upstream type is prefixed (`Stripe_*`) before merging. |

## GraphQL → GraphQL pipeline

```
upstream
  │
  ▼
┌────────────────┐
│ introspect     │  ─ Apollo: send `_service { sdl }`
│ (ProviderHTTP) │  ─ Stitching/namespaced: standard introspection query
└──────┬─────────┘
       ▼
┌──────────────┐
│ SchemaTrans-  │  rename / prefix / hide_fields / mask_arguments / override_resolvers
│ former        │
└──────┬───────┘
       ▼
┌──────────────────┐
│ MergedSchema-    │  per-subgraph SDL stored; merged Query/Mutation/Subscription
│ Registry         │  is rebuilt with collision detection
└──────┬───────────┘
       ▼
┌──────────────────┐
│ BatchedField-    │  collapses N concurrent `user.stripe_customer` calls
│ Resolver         │  into one upstream call (or bounded individual calls)
└──────┬───────────┘
       ▼
┌────────────────┐
│ ResponseCache  │  per-request, keyed by (query_hash, variables_hash, requester_creds_hash)
└────────────────┘
```

Selection-set push-down is the entire point — without it the framework has rebuilt RPC inside a GraphQL costume. Every generated resolver reconstructs the upstream selection set from `info.selected_fields` before forwarding the document.

## GraphQL → REST projection (Federation_GQL.py)

`project_gql_as_rest(subgraph, transport, prefix)` walks the upstream's `Query` and `Mutation` types and mounts a FastAPI `APIRouter` with one route per root field:

| Operation type | HTTP method | Behavior |
|----------------|-------------|----------|
| `Query` field | GET | Args from query string. Default selection covers every leaf scalar plus one composite layer. |
| `Mutation` field | POST | Args from JSON body. Same default selection rule. |

The transport is the same one driven by GraphQL resolvers, so REST clients reach an upstream that only speaks GraphQL through identical rotation/auth/rate-limit machinery.

## REST → GraphQL projection (Federation_REST.py)

Two-step path:

1. `openapi_to_pydantic_models(spec, prefix=...)` lifts the OpenAPI document into Pydantic models, an enum table, and an `OperationSpec` table describing every `paths.<path>.<method>`.
2. `derive_external_models(pydantic_result, transport)` synthesizes `AbstractExternalModel` subclasses whose `*_via_provider` methods dispatch through `RESTUpstreamTransport`. Once registered with the framework's model registry, the existing `Pydantic2Strawberry` pipeline projects them as GraphQL types automatically.

The generated transport substitutes path placeholders (`{id}`), maps `GET`/`DELETE` arguments to query strings, maps mutating methods' arguments to JSON bodies, and forwards optional idempotency keys.

## Per-request response cache

`ResponseCache` deduplicates identical sub-requests within a single outer GraphQL operation. The cache key is `sha256(query_hash | variables_hash | requester_credentials_hash)` — two distinct requesters never share a cached upstream result. The cache is bound on a `contextvars.ContextVar` and is created/torn down by the inbound GraphQL middleware; it does not leak across requests.

A persistent (Redis-shaped) cache is opt-in per upstream type via `AbstractGraphQLProvider.persistent_cache_ttls = {"Stripe_Customer": 30.0}`.

## Cross-subgraph batching (`BatchedFieldResolver`)

N concurrent resolutions of `user.stripe_customer` collapse into one upstream call when the upstream supports list-by-id (`list_by_id_arg="ids"`). For upstreams without batched fetch the resolver falls back to bounded individual calls subject to the provider's rate limit. The resolver is per-request and bound on a contextvar; a `BatchedNavigationResolver` from `extensions/AbstractExternalModel.py` handles the same pattern at the REST boundary.

## Bootstrap

`lib/Federation_Bootstrap.py:install_external_federation()` runs at app startup (lifespan event):

1. Discover concrete `AbstractGraphQLProvider` subclasses with a configured `upstream_url`.
2. Invoke `register_with_registry()` for each (introspect → transform → register).
3. Invoke `lift_to_pydantic(ingested)` for each (when `lift_into_pydantic=True`).
4. Materialize the merged schema once.

Failures are logged and skipped — federation MUST NOT prevent the rest of the framework from starting; an unreachable upstream surfaces via `health_check`.

## Apollo Federation v2 directives

`build_schema` rejects subgraph SDL that references `@key` / `@external` / `@requires` / `@provides` without declarations. The registry prepends `APOLLO_DIRECTIVES_PREAMBLE` to every SDL it builds so subgraphs that ship raw federation SDL parse cleanly. The preamble covers `@key`, `@external`, `@requires`, `@provides`, `@shareable`, `@inaccessible`, `@override`, and `@tag`.

## Failure modes

| Symptom | Cause | Fix |
|---------|-------|-----|
| `RuntimeError: ... apollo_v2 but upstream did not return _service.sdl` | Provider declared `apollo_v2` against a non-Federation upstream. | Switch to `stitching` or set `require_apollo_v2_when_advertised=False`. |
| `ValueError: Federated root field collision on 'order'` | Two subgraphs claim the same root field. | Add a `type_namespace` to one or rename via `schema_rename`. |
| `TypeError: Unknown directive '@key'` | Subgraph SDL was built without the federation preamble. | Use `MergedSchemaRegistry.build()` rather than `build_schema()` directly. |
| Selection-set push-down inactive (full result returned) | Resolver bypassed `build_proxy_resolver` and called the transport directly. | Route the call through the proxy resolver or reconstruct `info.selected_fields` manually before sending. |

## Commit-time integration with ModelRegistry

`EXT_Federation.on_initialize` registers `BLL_Federation_Bootstrap.bootstrap_federation` as the registry's federation hook (it does nothing for an app that does not load this extension; `GQL_FEDERATION=false` turns it off). `ModelRegistry.commit()` calls it at Phase 3.5: after migrations and SQLAlchemy model generation (a source may read its configuration, such as provider instances, from the database), before routers and the GraphQL schema are generated. The registry is not locked yet, so binding is allowed, and the table generation has already run. The lifespan-event path (`install_external_federation`) remains for environments that need an out-of-band refresh.

`install_external_federation_sync(model_registry=...)`, over the app's own extensions:

1. Concrete `AbstractGraphQLProvider` subclasses with an `upstream_url`: introspect (sync HTTP) → transform → register with `MergedSchemaRegistry` → lift to Pydantic → wrap each type as an `AbstractExternalModel` → bind it table-less with a typed manager (read only: list and get).
2. REST upstream descriptors (extensions' `openapi_spec_provider`): import OpenAPI → derive external models → bind them likewise (the operations the spec has; no save, as the spec names no version).
3. Federated sources (below): read each catalogue, lift each type, bind it likewise.
4. GQL→REST projection routers on the `FederationCommitReport`, which `build_app` mounts at `/federated/{provider}/...`.

The report (`app.state.federation_report`) carries the bound models, their managers and, per source or type, why anything is missing. Errors are isolated per upstream and per type, and logged with the reason — an unreachable upstream never prevents the registry from committing or the app from starting.

### Table-less models in the registry

A lifted model declares `is_external_model = True` (every `AbstractExternalModel` does) and is bound with `ModelRegistry.bind_external(model, manager)`, never `bind()` (each refuses the other's models). It is kept in `external_models`, apart from `bound_models`, which the SQLAlchemy generation, migrations, seeding and database permission filters read, so no table, migration, seed or row filter is ever made for it. `apply()` resolves it; the router generation and the GraphQL schema serve its manager's typed routes. Its name may not be a local model's (the registry refuses either binding second). The manager is a `RouterMixin` manager serving the model.

## Typed federated models (`BLL_Federation_Typed.py`)

The provider-neutral contract by which an upstream's record types become typed models, live, with nothing stored here. ERPNext is its first source (`erp/EXT.erp.md`, which has the client contract); a WordPress source (posts, custom post types, taxonomies, meta, from the WP REST API's JSON Schemas) fits it unchanged, proved by `WordPressShapedSource.py` and `BLL_Federation_Typed_test.py`.

A source (`AbstractFederatedSource`) is one upstream as one account sees it. It supplies:

| | |
|---|---|
| `namespace` | stable, distinct among the app's sources, `[a-z][a-z0-9_]*` (a second source with one already served is left out) |
| `title`, `reference` | what it is called; what it is known by elsewhere (an ERP instance's id), for discovery |
| `catalogue()` | read once at boot (at most 300 s): a `FederatedType` per record type — its `name`, its record's JSON Schema (`properties`; nested types under `$defs`/`definitions` by `$ref`, inline objects allowed; `["x", "null"]` an optional x; `readOnly`/`readonly` what only the upstream sets), `key_field`, `version_field`, `operations` |
| `session(call)` | for one requester (`FederatedCall`: registry, requester id), 404 when they may not use the source: `list(type, FederatedQuery)`, `get(type, key)`, `create(type, data)`, `update(type, key, data, expected)`, `delete(type, key, expected)`; a stale save raises `StaleRecord(current)` |

An extension registers its sources' factory with `register_federated_sources(<extension>, factory)` at `on_initialize`; only the factories of the extensions an app loads are asked.

The framework lifts each type through `openapi_to_pydantic_models` into a record model (every field optional, unknown fields ignored) and a write payload (read-only fields left out, unknown fields refused; an object with nothing writable left out), each nested object a model of its own; a property no model field can hold (`_links`) is left out. A type without a `version_field` is served without update and delete (a save held to no version would overwrite blindly). Each type gets a synthesized `FederatedTypeEndpoints` manager whose typed `@custom_route`s serve it on REST and GraphQL, JWT-authenticated, through the source's session for the requester. A row the upstream answers outside the schema is 502.

Names (`NS`, `Slug`: the namespace's and slug's words, each first letter capitalized):

| | |
|---|---|
| slug | the type name's ASCII letter/digit runs, lower case, `_`-joined (`t_` before a leading digit; `_2`… for a shared slug, in catalogue order) |
| REST | `POST /v1/federated/<namespace>/<slug>/{list,get,create,update,delete}` |
| GraphQL fields | `<namespace>_<slug>_<operation>`, camelCased (`wordpressAb12cd34PostGet`) |
| types | record `<NS><Slug>Type`, nested `<NS><Slug><Nested>Type`, write input `<NS><Slug>WriteInput`, operation input `<NS><Slug><Op>ArgsInputInput`, page `<NS><Slug>PageType` |

No model name has an underscore: GraphQL type names are re-cased at each one.

Versions: a record's ETag is its `version_field`. `update` and `delete` name the version in `If-Match` (REST) or `if_match` (the input); none is 428 (`IF_MATCH_REQUIRED`), a stale one 412 with `{detail, current}` and the current ETag.

Discovery: `GET /v1/federated/catalogue` (GraphQL `federatedCatalogue`) lists the typed models the requester may use (a source whose `session` refuses them is left out): REST paths, GraphQL names, JSON Schemas. It is the boot's snapshot: a type the upstream gains later is served typed only after a restart.

## Matrix homologation testing

Item 16's acceptance criteria say "regardless of upstream wire format, both inbound surfaces work." The framework proves this with a 4-quadrant × 5-CRUD test matrix:

| | external GQL | external REST |
|---|---|---|
| **local GQL surface** | GQL→GQL | REST→GQL |
| **local REST surface** | GQL→REST | REST→REST |

`extensions/AbstractFederationMatrixTest.py` is the base class. Subclasses provide a `FederationFixture` declaring the upstream kind, a built transport, sample IDs, and supported operations. The base class then exhaustively walks the 20 cells, asserting each succeeds and that REST/GQL surface payloads agree on shared fields.

`extensions/Federation_Matrix_Generator.py` provides programmatic generation. A bundled extension ships explicit fixtures in a test-only `federation_fixtures_test.py` module whose `federation_matrix_fixtures()` returns them (canned seed data stays out of production code); the generator imports every such module.

`extensions/Federation_Matrix_test.py` calls `generate_matrix_tests(target_namespace=globals())` at import time, which mutates the test module's namespace to inject one `Test_Federation_<extension>_<type>_Matrix` class per discovered fixture. Pytest collects them automatically; adding a new external extension automatically buys 20 cells of homologation coverage.

In-process upstreams are the default for CI determinism. Live upstreams activate when the fixture's `requires_credentials=True` and `credentials_present()` returns True; otherwise pytest auto-xfails the suite per `EXT.Test.External.md`.

Payment ships `payment/federation_fixtures_test.py`: fixtures for every resource of the Stripe, Square, PayPal, Moneris and Helcim REST APIs, bound to in-process REST upstreams. Email ships `email/federation_fixtures_test.py`: SendGrid's Mail resource, bound to an in-process REST upstream.

## Module map

| File | Purpose |
|------|---------|
| `lib/Federation_GQL.py` | GraphQL upstream federation: introspection, transformer, registry, batched resolver, response cache, SDL→Pydantic, GQL→REST projection. |
| `lib/Federation_REST.py` | REST upstream federation: OpenAPI→Pydantic, transport, REST→GQL projection. |
| `lib/Federation_Bootstrap.py` | Sync entry point used by `ModelRegistry.commit()` (and async for lifespan). Discovers providers, REST descriptors and federated sources, runs the introspect→transform→register→lift→bind pipeline, returns a `FederationCommitReport` carrying lifted models, synthesized managers, GQL→REST routers, and per-provider error isolation. |
| `BLL_Federation_Typed.py` | The federated-source contract, the JSON Schema lift, typed managers, the catalogue route, boot-time binding. |
| `WordPressShapedSource.py` | A WordPress-shaped source in process, for the tests. |
| `extensions/AbstractGraphQLProvider.py` | Provider abstract: declares `upstream_url`, `federation_style`, `type_namespace`, transformer overrides; offers both async (`introspect`/`register_with_registry`) and sync (`introspect_sync`/`register_with_registry_sync`) orchestration. |
| `extensions/AbstractFederationMatrixTest.py` | 4-quadrant × 5-CRUD matrix test base class. Subclasses supply a `FederationFixture`. |
| `extensions/Federation_Matrix_Generator.py` | Programmatic test generator. Walks loaded extensions, gathers fixtures or synthesizes them from OpenAPI/SDL providers, emits one `Test_Federation_*_Matrix` class per fixture into a target namespace. |
| `extensions/Federation_Matrix_test.py` | Hand-written reference matrix for in-process GQL and REST upstreams + driver call to `generate_matrix_tests` for every discovered extension. |
