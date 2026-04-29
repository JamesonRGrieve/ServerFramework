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

## Multi-Extension Schema Composition

Each extension's `RouterMixin`-tagged managers contribute their Query/Mutation/Subscription roots into a merged schema. Custom routes participate via the same `expose_in` flag. The merge resolves conflicts in three stages:

1. **Identical contributions** — same name, same signature — merge as a single field.
2. **Non-identical contributions** on the same field name fail at startup with a clear error naming the offending extensions (mirroring the field-injection collision rule).
3. **Namespacing under the extension name** is offered as an opt-in for extensions that explicitly want to avoid collisions.

Extensions can also contribute new types (not just root fields) to the schema; type-name collisions follow the same three-stage resolution.

Internal cross-extension navigation (e.g. extension B's resolver accesses extension A's model) participates in the same `include`-driven batched-resolver mechanism that prevents N+1 at the federation boundary. A per-request DataLoader keyed by `(model, set_of_ids)` collects N parallel resolutions of `field.related` into a single batched fetch, so cross-extension joins do not N+1 the database.

The merged schema is rebuilt on extension install/uninstall and the rebuild diff is logged so operators can see what changed. Extensions may declare Strawberry Federation directives on their types (`@key`, `@external`, `@requires`, `@provides`); these compose with external GraphQL provider federation.

## Federation of External GraphQL Upstreams

When an upstream API is itself GraphQL, the framework federates the schema rather than wrapping it as RPC. `AbstractGraphQLProvider(AbstractStaticProvider)` declares:

- `upstream_url: ClassVar[str]`
- `upstream_auth_strategy: ClassVar[Type[AuthStrategy]]`
- `federation_style: Literal["apollo_v2", "stitching", "namespaced"]` — Apollo Federation v2 if the upstream advertises `_service { sdl }`, schema stitching if introspection-only, namespaced (last resort) if all upstream types must be prefixed
- `type_namespace: Optional[str]` — e.g. `Stripe_*` prefix when stitching or namespacing

A startup pipeline runs per provider instance, with results cached:

1. Introspect the upstream and fetch the SDL.
2. Run a `SchemaTransformer` pipeline supporting rename, prefix, hide-fields, mask-arguments, and override-resolvers transformations.
3. Register the transformed types into the local Strawberry schema via `MergedSchemaRegistry`.
4. Generate Strawberry resolvers that take the Strawberry `Info` object, reconstruct the upstream selection set from `info.selected_fields`, build a real GraphQL document with the original variables, forward to the upstream, and return the parsed result. **Selection-set push-down is the entire point** — without it the framework has rebuilt RPC inside a GraphQL costume.
5. Cache the merged SDL hash. Refresh introspection on a TTL or on webhook-triggered invalidation.

The Apollo Federation v2 path applies when the upstream is a compliant subgraph. The framework honors `@key`, `@external`, `@requires`, and `@provides`. Local types declare `@key` references to upstream entities (`extend type Stripe_Customer @key(fields: "id")`), and a local `User` type gains a `stripe_customer: Stripe_Customer` field that the gateway resolves through the federated subgraph.

The stitching path applies for upstreams with introspection but no Federation directives. The framework becomes the gateway. `MergedSchemaRegistry` holds the merged schema. Resolvers are generated, not handwritten. Cross-subgraph joins are handled by a `BatchedFieldResolver` that respects `include` — the resolver collects N requests for `user.stripe_customer` into a single upstream call.

Authentication and per-request context propagate via `AuthStrategy.headers_for(requester)`. A per-request response cache is keyed by `(query_hash, variables_hash, requester_credentials_hash)` and dedupes identical sub-requests within a single outer GraphQL operation. A persistent cache (Redis-backed) is opt-in per type via a `@cache(ttl=...)` directive on the merged type.

Errors and partial data follow real GraphQL semantics: upstream `errors` arrays propagate through, attached to the affected fields, not raised globally — the partial-data, partial-errors contract is preserved for clients.
