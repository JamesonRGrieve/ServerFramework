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

## Module map

| File | Purpose |
|------|---------|
| `lib/Federation_GQL.py` | GraphQL upstream federation: introspection, transformer, registry, batched resolver, response cache, SDL→Pydantic, GQL→REST projection. |
| `lib/Federation_REST.py` | REST upstream federation: OpenAPI→Pydantic, transport, REST→GQL projection. |
| `lib/Federation_Bootstrap.py` | Lifespan-event entry point that runs the pipeline for every configured provider. |
| `extensions/AbstractGraphQLProvider.py` | Provider abstract: declares `upstream_url`, `federation_style`, `type_namespace`, transformer overrides, and the introspect/register/lift orchestration. |
