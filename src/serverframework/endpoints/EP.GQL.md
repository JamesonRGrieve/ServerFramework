# GraphQL Integration

> **REST endpoints:** [EP.Abstraction.md](EP.Abstraction.md) | **Testing:** [EP.Test.md](EP.Test.md)

## Overview

GraphQL schemas are auto-generated from Pydantic BLL models. Works alongside REST endpoints from RouterMixin.

## Auto-Generated Operations

| Operation Type | Naming Pattern | Example |
|----------------|----------------|---------|
| Query (single) | `get_<resource>` | `get_project(id: "...")` |
| Query (list) | `<resources>` | `projects` |
| Create | `create_<resource>` | `create_project(input: {...})` |
| Update | `update_<resource>` | `update_project(id: "...", input: {...})` |
| Delete | `delete_<resource>` | `delete_project(id: "...")` |
| Subscription | `<resource>_created/updated/deleted` | `project_created` |

## Type Generation

Pydantic models → GraphQL types automatically:

| Pydantic | GraphQL |
|----------|---------|
| `str` | `String` |
| `int` | `Int` |
| `float` | `Float` |
| `bool` | `Boolean` |
| `datetime` | `DateTimeScalar` (ISO format) |
| `Optional[T]` | Nullable `T` |
| `List[T]` | `[T]` |
| Nested model | Referenced type |

## FastAPI Integration

```python
from strawberry.fastapi import GraphQLRouter

Query, Mutation, Subscription = build_dynamic_strawberry_types()
schema = strawberry.Schema(query=Query, mutation=Mutation, subscription=Subscription)

app.include_router(
    GraphQLRouter(schema=schema, graphiql=True),
    prefix="/graphql"
)
```

## Authentication

Same as REST - via `Authorization` header:

```python
# Extracted in resolver context
auth_header = request.headers.get("Authorization")
user = UserManager.auth(auth_header)
requester_id = user.id
```

## Subscriptions

Real-time via `broadcaster` library:

```python
# Auto-generated channels
project_created   # New projects
project_updated   # Modified projects
project_deleted   # Removed projects
```

**Client subscription:**
```graphql
subscription {
  project_created {
    id
    name
  }
}
```

## Example Queries

### Get Single

```graphql
query {
  get_project(id: "uuid") {
    id
    name
    tasks { id, status }
  }
}
```

### List

```graphql
query {
  projects {
    id
    name
  }
}
```

### Create

```graphql
mutation {
  create_project(input: {name: "New", description: "..."}) {
    id
    name
  }
}
```

### Update

```graphql
mutation {
  update_project(id: "uuid", input: {name: "Updated"}) {
    id
    name
  }
}
```

## Configuration

```python
# Increase recursion depth for deeply nested models (default: 3)
Query, Mutation, Subscription = build_dynamic_strawberry_types(max_recursion_depth=4)

# Enable camelCase field names
from strawberry.schema.config import StrawberryConfig
config = StrawberryConfig(auto_camel_case=True)
```

## Model Requirements

For GraphQL generation, BLL models need:

1. **Main model**: `ProjectModel` with type annotations
2. **Reference model**: `ProjectReferenceModel` (for relationships)
3. **Network model**: `ProjectNetworkModel` (for input types)
4. **Manager class**: `ProjectManager` with CRUD methods

## Debugging

| Issue | Check |
|-------|-------|
| Missing type | Model has proper type annotations |
| Circular reference error | Reduce `max_recursion_depth` or simplify relationships |
| Resolver error | Manager method exists and handles context |
| Subscription not firing | Broadcast channel name matches, broadcaster initialized |
| Auth failure | `Authorization` header present, valid JWT |

## Multi-Extension Schema Composition (Item 46)

CRUD-on-managers is automatic: every `RouterMixin`-tagged manager registered in the `ModelRegistry` auto-emits `query/list/create/update/delete` plus `*_created/_updated/_deleted` subscriptions. The composition contract below covers everything that **cannot** be derived from a CRUD manager: custom root fields, non-CRUD subscriptions, custom types, federation directives, and per-request DataLoaders. The registry lives in `lib/Pydantic2Strawberry.py` (`gql_contribution_registry()`).

### Decorators

```python
from serverframework.lib.Pydantic2Strawberry import (
    gql_query, gql_mutation, gql_subscription, gql_type,
    register_dataloader, FederationDirective,
)

@gql_query(name="searchProjects", return_type=SearchResults)
async def search_projects(info, query: str, limit: int = 25) -> SearchResults: ...

@gql_subscription(name="taskAssigned", return_type=str)
async def task_assigned(info, user_id: str): ...
```

`extension_name` is auto-derived from the caller's `extensions.<name>` module path; pass it explicitly otherwise.

### Three-stage collision resolution

1. **Identical contributions** — same callable, return type, args, and kind — merge as one (no-op duplicate).
2. **Non-identical** on the same emitted name — `GraphQLCompositionCollisionError` at schema build time, naming every contributing extension. Mirrors `CollisionDetection.FieldCollisionError`.
3. **Namespacing** opt-in via `namespace=True` emits as `{extension}_{name}` (fields) or `{Extension.capitalize()}{TypeName}` (types).

### Custom types and federation directives

```python
@gql_type(
    federation_directives=(
        FederationDirective(name="key", args={"fields": "id"}),
        FederationDirective(name="shareable"),
    ),
)
@strawberry.type
class ProjectAggregate:
    id: str
    total: int
```

Allowed directives: `key`, `external`, `requires`, `provides`, `shareable`, `inaccessible`, `override`, `tag`. Anything else raises at registration. Directives are appended to `__strawberry_definition__.directives` so SDL emission preserves the framework's `Sunset` directive (Item 39). Use these to make our merged schema a valid Apollo Federation v2 subgraph composable with the inbound federation pipeline from Item 16.

### Per-request DataLoader

```python
def batch_load_users(user_ids):
    return UserManager.list(filter={"id__in": list(user_ids)})

register_dataloader("users", batch_load_users)

@gql_query(name="messageAuthor", return_type=User)
async def message_author(info, message_id: str) -> User:
    msg = await MessageManager.get(id=message_id)
    return await info.context["dataloaders"]["users"].load(msg.author_id)
```

`RequestDataLoader.load(key)` returns a future that resolves on the next event-loop tick. Parallel `load()` calls collapse into a single `batch_load_fn(deduped_keys)` call. Length-mismatched returns and non-sequence returns raise into every awaiting future. Sync and async batch functions are both supported. The framework builds a fresh DataLoader dict per request and attaches it at `info.context["dataloaders"]`. DataLoader name collisions with the same function are idempotent; with a different function they raise.

### Rebuild on install/uninstall

`GraphQLManager` subscribes to registry mutations at construction. Each registration logs a structured diff (`added: [...], removed: [...]`); the merged schema is recomputed on the next `create_schema()` call so a flurry of installs only triggers one rebuild. Operators that need an immediate rebuild call `GraphQLManager.rebuild()` (Item 20 install/uninstall hot path). `suspend()/resume()` batches multiple registrations into a single notification.

Custom routes (Item 40) participate in REST via the same `expose_in` flag; an extension that also wants the route on GraphQL registers the corresponding `@gql_query` / `@gql_mutation`.

## Federation of External Upstreams (Item 16)

> **Detailed reference:** [lib/LIB.Federation.md](../lib/LIB.Federation.md)

External federation lifts upstream APIs — GraphQL or REST — into the framework's model registry so a single inbound request can transparently traverse local data and federated upstreams. Two modules cover the four directions:

| Module | Upstream kind | Inbound surfaces |
|--------|---------------|------------------|
| `lib/Federation_GQL.py` | GraphQL | GraphQL (true federation) + REST (`project_gql_as_rest`) |
| `lib/Federation_REST.py` | REST | REST (existing `AbstractExternalModel`) + GraphQL (Pydantic lift) |

### `AbstractGraphQLProvider`

Declares an upstream GraphQL endpoint:

- `upstream_url: ClassVar[str]`
- `auth_strategy_name: ClassVar[str]` (inherited)
- `federation_style: Literal["apollo_v2", "stitching", "namespaced"]` — Apollo Federation v2 if the upstream advertises `_service { sdl }`, schema stitching if introspection-only, namespaced (last resort) if all upstream types must be prefixed
- `type_namespace: Optional[str]` — e.g. `Stripe_*` prefix when stitching or namespacing
- `schema_rename`, `schema_hide_fields`, `schema_mask_arguments`, `schema_override_resolvers` — transformer pipeline configuration
- `lift_into_pydantic: bool = True` — when True, the SDL is also lifted into Pydantic models so `Pydantic2Strawberry` and `Pydantic2FastAPI` project the upstream onto BOTH inbound surfaces.

### Startup pipeline

`Federation_Bootstrap.install_external_federation()` runs per provider in the FastAPI lifespan event:

1. **Introspect** — Apollo: send `_service { sdl }`. Stitching/namespaced: standard introspection query.
2. **Transform** — `SchemaTransformer` applies rename / prefix / hide-fields / mask-arguments / override-resolvers in order.
3. **Register** — store the transformed SDL on `MergedSchemaRegistry` keyed by provider name.
4. **Lift** — when `lift_into_pydantic=True`, generate Pydantic models from the SDL via `sdl_to_pydantic_models(sdl, prefix=...)` for downstream registry integration.
5. **Build** — `MergedSchemaRegistry.build()` materializes the merged `GraphQLSchema` once. Subsequent calls return the cache unless an SDL hash has changed.

Selection-set push-down is wired into every generated resolver via `build_proxy_resolver`: the resolver reconstructs the upstream selection from `info.selected_fields` before forwarding the document. Without push-down the framework has rebuilt RPC inside a GraphQL costume.

The Apollo Federation v2 path honors `@key`, `@external`, `@requires`, `@provides`. The merged registry prepends `APOLLO_DIRECTIVES_PREAMBLE` to every SDL it builds so subgraphs that ship raw federation SDL parse cleanly.

The stitching path uses `BatchedFieldResolver`: N concurrent resolutions of `user.stripe_customer` collapse into one upstream call when the upstream supports list-by-id; otherwise the resolver falls back to bounded individual calls subject to the provider's rate limit.

### REST upstream → GraphQL surface

`Federation_REST.openapi_to_pydantic_models(spec, prefix=...)` imports an OpenAPI document into Pydantic models, enums, and an `OperationSpec` table. `derive_external_models(pydantic_result, transport=RESTUpstreamTransport(...))` synthesizes `AbstractExternalModel` subclasses whose `*_via_provider` methods dispatch through the transport. Once registered with the framework's model registry, `Pydantic2Strawberry` projects them onto the GraphQL surface automatically.

### GraphQL upstream → REST surface

`Federation_GQL.project_gql_as_rest(subgraph, transport, prefix)` walks the upstream's `Query` and `Mutation` types and mounts a FastAPI `APIRouter` with one route per root field. Default selection covers every leaf scalar plus one composite layer so REST clients receive a flat result without GraphQL knowledge.

### Per-request response cache

`ResponseCache` deduplicates identical sub-requests within a single outer GraphQL operation. The cache key is `sha256(query_hash | variables_hash | requester_credentials_hash)` so two distinct requesters never share a cached upstream result. The cache is bound on a contextvar by the GraphQL request middleware and torn down at request end.

A persistent (Redis-shaped) cache is opt-in per upstream type via `AbstractGraphQLProvider.persistent_cache_ttls = {"Stripe_Customer": 30.0}`.

### Authentication and context propagation

`AuthStrategy.headers_for(requester)` from the framework's auth system is invoked by `ProviderHTTPClient` on every outbound call, so federated requests carry the right credentials per-request. Rotation, idempotency, rate limiting, and trace propagation all participate identically — the federation modules do not re-implement any of these.

### Errors

Upstream `errors` arrays propagate through, attached to the affected fields, not raised globally — the partial-data, partial-errors contract is preserved for clients.
