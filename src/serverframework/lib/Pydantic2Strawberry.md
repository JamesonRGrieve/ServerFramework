# Pydantic2Strawberry

This module provides automatic GraphQL schema generation from Pydantic models using Strawberry GraphQL. It also hosts the **GraphQL composition contract (Item 46)** for our own extensions: custom Query / Mutation / Subscription roots, custom types, federation directives, and per-request DataLoaders are registered into a process-wide registry that the schema builder consumes alongside the auto-generated CRUD surface.

## Overview

The `Pydantic2Strawberry` module dynamically generates GraphQL schemas from Pydantic models registered in the ModelRegistry. It creates complete CRUD operations (queries, mutations, subscriptions) for each model without requiring manual schema definitions, then folds in extension-contributed roots and types from `GraphQLContributionRegistry`.

## Key Components

### GraphQLManager

The main class that orchestrates schema generation with comprehensive error handling and relationship management:

- **`__init__(model_registry)`**: Initialize with a ModelRegistry instance and set up type registries
- **`create_schema()`**: Generate complete GraphQL schema from registered models with Query, Mutation, and Subscription types
- **`_generate_components_for_model(model_class, manager_class)`**: Generate all GraphQL components for a specific model with error isolation
- **`_convert_python_type_to_gql(python_type)`**: Convert Python types to GraphQL types with comprehensive type handling
- **`_analyze_model_relationships(model_class)`**: Analyze and register forward/reverse relationships between models
- **`_create_gql_type_from_model(model_class)`**: Create GraphQL output types with relationship navigation support
- **`_create_input_type_from_model(model_class, suffix)`**: Create GraphQL input types for mutations
- **`_create_filter_type_from_model(model_class)`**: Create filter input types for queries
- **`_apply_field_acl(manager, result)`**: **Item 45 — field-level ABAC.** Strips disallowed fields from a resolver result before Strawberry hands it back to the client. Walks `manager.requester.has_permission` and the model's `Field(..., requires=[...])` metadata; honors the `FIELD_ACL_SENTINEL=omit|mask` deployment override. The four CRUD resolvers (`_add_query_resolver`, `_add_list_query_resolver`, `_add_create_mutation_resolver`, `_add_update_mutation_resolver`) call into this helper before returning, so REST and GraphQL share one field-stripping policy. No-op for managers without a resolvable permission resolver (system-key audit jobs, framework-internal callers).

### Type Conversion

The module handles various Python type conversions:

- **Basic Types**: str, int, float, bool, datetime, date
- **Complex Types**: Dict, List, Optional, Any
- **Enum Types**: Regular enums and string-based enums
- **Nested Models**: Pydantic models as GraphQL types

### Enum Handling

Special handling for enum types to ensure compatibility:

1. **String-based enums** (e.g., `class MyEnum(str, Enum)`) are converted to simple enum values
2. **IntEnum types** are converted to int type for GraphQL compatibility
3. **Regular enums** (e.g., `class MyEnum(Enum)`) preserve their values when possible
4. **Extension enums** get prefixed to avoid naming collisions
5. **Dict/non-type objects** are detected and converted to string type
6. **Problematic enums** fall back to string type with a warning logged

### Scalar Types

Custom scalar types for complex data:

- **DateTimeScalar**: ISO format datetime serialization
- **DateScalar**: ISO format date serialization
- **ANY_SCALAR**: JSON-serializable values
- **DICT_SCALAR**: JSON objects
- **LIST_SCALAR**: JSON arrays

## Generated Operations

For each model, the following operations are generated with authentication and context handling:

### Queries
- **`{modelName}(id: String!)`**: Get single item by ID (special user handling for self-queries)
- **`{modelNamePlural}(filter: FilterInput, limit: Int, offset: Int)`**: List items with filtering and pagination

### Mutations
- **`create{ModelName}(input: CreateInput!)`**: Create new item with automatic context injection
- **`update{ModelName}(id: String!, input: UpdateInput!)`**: Update existing item (special user handling for self-updates)
- **`delete{ModelName}(id: String!)`**: Delete item with broadcast events

### Subscriptions
- **`{modelName}Created`**: Subscribe to creation events via broadcaster
- **`{modelName}Updated`**: Subscribe to update events via broadcaster
- **`{modelName}Deleted`**: Subscribe to deletion events via broadcaster

### Authentication Handling
- All operations require authentication via JWT or API key
- User operations are restricted to self-access (users can only query/update themselves)
- Context extraction from GraphQL Info object with fallback authentication
- Root API key support for system-level operations

## Advanced Features

### Relationship Management
Automatic discovery and handling of model relationships:

- **Forward Relationships**: Many-to-one relationships via foreign key fields (e.g., `user_id` -> `user`)
- **Reverse Relationships**: One-to-many relationships with navigation properties
- **Relationship Analysis**: Automatic detection of relationships from field naming conventions
- **Navigation Resolvers**: Dynamic resolver creation for relationship traversal
- **Circular Dependency Handling**: Safe handling of circular model references

### Error Handling
Comprehensive error handling with graceful degradation:

- **Model-Level Isolation**: Failed models don't prevent schema generation for other models
- **Batch Operations**: Multiple operations grouped with error recovery
- **Type Registry**: Prevents duplicate type creation and infinite recursion
- **Fallback Types**: ANY_SCALAR fallback for problematic type conversions
- **Detailed Logging**: Comprehensive error reporting with module and model context

### Extension Support

The module handles extension models specially:

- Extension models that enhance existing types are skipped from schema generation
- Extension enums get prefixed with the extension name to avoid collisions
- Type names from extensions are prefixed to ensure uniqueness
- Extension-specific resolver handling

## GraphQL Composition Contract (Item 46)

Distinct from external federation (`Federation_GQL.py`, Item 16): this contract governs how *our own* extensions contribute non-CRUD surface to the merged schema. The CRUD-on-managers path is unchanged and continues to auto-emit `query/list/create/update/delete` plus `*_created/_updated/_deleted` subscriptions per `RouterMixin`-tagged manager.

### Registration surface

Extensions register four kinds of contribution into the process-wide `GraphQLContributionRegistry` (accessor: `gql_contribution_registry()`):

- **Root fields** via `@gql_query`, `@gql_mutation`, `@gql_subscription`. Each carries `return_type`, optional `args`, optional `description`, optional `extension_name` (auto-derived from the caller's `extensions.<name>` module path), `namespace=False`, and `priority=50`.
- **Custom types** via `@gql_type` applied to a Strawberry-decorated class. Carries an optional `name` override, `federation_directives` tuple, and `namespace`.
- **DataLoaders** via `register_dataloader(name, batch_load_fn)`. Names are global; resolvers retrieve the per-request loader via `info.context["dataloaders"][name]`.
- **Federation directives** via `FederationDirective(name, args)` attached to a `TypeContribution`. Allowed names: `key`, `external`, `requires`, `provides`, `shareable`, `inaccessible`, `override`, `tag`. Anything else raises at registration time.

### Three-stage collision resolution

When two extensions contribute the same emitted root-field name or type name, the registry resolves as follows:

1. **Identical** — same callable, same return type, same args, same kind → merged as one. Duplicate registrations are no-ops.
2. **Non-identical** — `GraphQLCompositionCollisionError` at schema build time, naming every contributing extension and the offending field/type. Mirrors `CollisionDetection.FieldCollisionError`.
3. **Namespaced opt-in** — `namespace=True` on the contribution emits the field as `{extension}_{name}` (or the type as `{Extension.capitalize()}{TypeName}`), bypassing both. Two extensions both wanting `query.search` declare `namespace=True` and emit `query.ext_a_search` and `query.ext_b_search`.

DataLoader name collisions follow a stricter rule: the same name with the same `batch_load_fn` is idempotent (so multiple consumers can share a loader); the same name with a different function raises immediately.

### Per-request DataLoader

`RequestDataLoader.load(key)` returns a `Future` that resolves once the deferred batch fires on the next event-loop tick. Parallel resolutions of `thing.related_in_other_extension` collapse into a single `batch_load_fn(deduped_keys)` call. Sync and async batch functions are both accepted. The framework constructs a fresh DataLoader per request via `build_request_dataloaders()` and exposes the dict at `info.context["dataloaders"]`. Length-mismatched returns and non-sequence returns raise into every awaiting future.

### Rebuild on extension install/uninstall

`GraphQLManager` subscribes to `GraphQLContributionRegistry` mutations at construction. Registrations log a structured diff (`"GraphQL contribution registry changed -- added: [...], removed: [...]"`); the schema is recomputed on the next `create_schema()` call so a flurry of installs only triggers one rebuild. Operators that need an immediate rebuild call `GraphQLManager.rebuild()` directly. The `suspend()/resume()` pair on the registry batches multiple registrations into a single notification.

### Federation directive emission

Directives attached to a type contribution are appended to `__strawberry_definition__.directives` so Strawberry emits them in SDL. The framework's own `Sunset` directive (Item 39) is preserved when extension-contributed directives are added. Use `key`/`shareable`/etc. to make our merged schema a valid Apollo Federation v2 subgraph; this composes with the inbound federation pipeline from Item 16.

### Public API

```python
from serverframework.lib.Pydantic2Strawberry import (
    gql_query, gql_mutation, gql_subscription, gql_type,
    register_dataloader, FederationDirective,
    gql_contribution_registry, reset_gql_contribution_registry,
    GraphQLCompositionCollisionError, FieldKind,
    RequestDataLoader, build_request_dataloaders,
)
```

## Custom-Route GraphQL Emission (Item 40)

`@custom_route`-tagged methods on `RouterMixin` managers (and `AbstractActionEndpoint` subclasses) now project automatically onto the GraphQL surface alongside REST and the auto-generated SDK. The hook lives in `GraphQLManager._register_custom_routes_for_manager`, which calls `CustomRoute.register_custom_routes_to_graphql(manager_class)` for every model during schema build.

Mapping rules:

- HTTP `GET` → `FieldKind.QUERY`. Anything else (`POST`/`PUT`/`PATCH`/`DELETE`) → `FieldKind.MUTATION`.
- `@custom_route(graphql_kind="query"|"mutation")` overrides the inferred kind. Subscriptions are emitted by the dedicated streaming decorator (Item 13), not by `@custom_route`.
- Routes whose `expose_in` excludes both `GRAPHQL` and `ALL` are skipped.
- Per-`(manager_cls, method_name)` registration is idempotent so schema rebuilds don't re-emit duplicate fields.

Resolvers wrap the bound method, validate the input model, and coerce dict returns into the spec's `output_model`. The contribution flows through the same Item 46 collision rules as any other extension-contributed field.

## Graceful Degradation in GraphQL (Item 48)

Rotation-exhaustion sentinels (`QueuedForRetry`, `SilentDropped`) project onto GraphQL via two strawberry-decorated mirrors:

- `QueuedForRetryGQL { status, trackingId }` — equivalent to the REST 202 response.
- `SilentDroppedGQL { status, provider, ability }` — equivalent to the REST 200 silent-drop response.

The schema author opts in by declaring the resolver's return type as a union arm, e.g.:

```python
SendResult = Annotated[
    Union[WidgetGQL, QueuedForRetryGQL, SilentDroppedGQL],
    strawberry.union("SendResult"),
]

@degradation_aware
async def send(info: Info) -> SendResult:
    return await widget_manager.send(...)
```

`@degradation_aware` wraps both sync and async resolvers, calling `render_degradation_sentinel_gql` after the resolver returns to convert sentinels into the typed GraphQL arm. Non-sentinel returns pass through unchanged. The decorator preserves `__name__` / `__qualname__` / `__doc__` so Strawberry introspection sees the original signature.

## Error Handling

The schema generation is resilient to individual model failures:

- Failed models are logged but don't stop the entire schema generation
- Enum conversion errors fall back to string type
- Complex types that can't be converted use ANY_SCALAR

## Usage Example

```python
from serverframework.lib.Pydantic import ModelRegistry
from serverframework.lib.Pydantic2Strawberry import GraphQLManager

# Create and populate model registry
registry = ModelRegistry()
registry.register_models(...)

# Generate GraphQL schema
graphql_manager = GraphQLManager(registry)
schema = graphql_manager.create_schema()

# Use with FastAPI/Strawberry
from strawberry.fastapi import GraphQLRouter
graphql_app = GraphQLRouter(schema)
```

## Recent Changes

### Enum Conversion Fix (2025-07-04)

Fixed issues with enum conversion that were causing GraphQL schema generation to fail:

- Added proper handling for string-based enums (e.g., `ConversationVisibility(str, Enum)`)
- Added try-catch wrapper around enum conversion to gracefully fall back to string type
- Improved error messages to identify which enums are causing issues
- String-based enums now convert their values to simple strings for GraphQL compatibility

This fix ensures that all enum types can be successfully converted to GraphQL, preventing schema generation failures while maintaining type safety where possible.