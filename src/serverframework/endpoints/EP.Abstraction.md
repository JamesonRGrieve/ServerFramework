# Endpoint Layer Architecture

> **See also:** [Framework.md](../Framework.md) for overall architecture, [EP.Patterns.md](EP.Patterns.md) for quick-reference patterns

## Core Principle

**Pydantic models define everything.** BLL managers with `RouterMixin` automatically generate FastAPI routers with full CRUD operations, authentication, and OpenAPI documentation.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        API Request                               │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Endpoint Layer (EP_*.py)                                        │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │ AbstractEPRouter│  │   RouterMixin   │  │  GraphQL Layer  │  │
│  │ (manual routes) │  │ (auto-generate) │  │  (Strawberry)   │  │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘  │
└───────────┼────────────────────┼────────────────────┼───────────┘
            │                    │                    │
            ▼                    ▼                    ▼
┌─────────────────────────────────────────────────────────────────┐
│  Business Logic Layer (BLL_*.py)                                 │
│  AbstractBLLManager with CRUD operations + hooks                 │
└─────────────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  Database Layer (DB_*.py) - Auto-generated from Pydantic        │
└─────────────────────────────────────────────────────────────────┘
```

## RouterMixin

Inherit `RouterMixin` in your BLL manager to auto-generate REST endpoints.

### Minimal Example

```python
from serverframework.lib.Pydantic2FastAPI import RouterMixin, AuthType

class ItemManager(AbstractBLLManager, RouterMixin):
    BaseModel = Item  # Pydantic model with Network subclass

    # Optional overrides (all have sensible defaults)
    prefix: ClassVar[str] = "/items"           # Default: derived from class name
    tags: ClassVar[List[str]] = ["Items"]      # Default: derived from class name
    auth_type: ClassVar[AuthType] = AuthType.JWT  # Default: JWT
```

### Configuration Options

| Option                 | Type                              | Default              | Description                   |
| ---------------------- | --------------------------------- | -------------------- | ----------------------------- |
| `prefix`               | `str`                             | Auto from class name | URL prefix (e.g., `/items`)   |
| `tags`                 | `List[str]`                       | Auto from class name | OpenAPI tags                  |
| `auth_type`            | `AuthType`                        | `JWT`                | Default auth for all routes   |
| `routes_to_register`   | `List[RouteType]`                 | All 8 routes         | Which CRUD routes to create   |
| `route_auth_overrides` | `Dict[RouteType, AuthType]`       | `{}`                 | Per-route auth override       |
| `custom_routes`        | `List[CustomRouteConfig]`         | `[]`                 | Additional custom endpoints   |
| `nested_resources`     | `Dict[str, NestedResourceConfig]` | `{}`                 | Child resource routes         |
| `example_overrides`    | `Dict[str, Dict]`                 | `{}`                 | OpenAPI example customization |

### Generated Routes

| RouteType      | Method | Path      | Description                |
| -------------- | ------ | --------- | -------------------------- |
| `GET`          | GET    | `/{id}`   | Get single resource        |
| `LIST`         | GET    | `/`       | List resources (paginated) |
| `CREATE`       | POST   | `/`       | Create single/batch        |
| `UPDATE`       | PUT    | `/{id}`   | Update resource            |
| `DELETE`       | DELETE | `/{id}`   | Delete resource            |
| `SEARCH`       | POST   | `/search` | Search with filters        |
| `BATCH_UPDATE` | PUT    | `/batch`  | Update multiple            |
| `BATCH_DELETE` | DELETE | `/batch`  | Delete multiple            |

### Route Registration

```python
app.include_router(ItemManager.Router(model_registry))
```

## Authentication

| AuthType  | Header                          | Use Case                |
| --------- | ------------------------------- | ----------------------- |
| `JWT`     | `Authorization: Bearer <token>` | User sessions (default) |
| `API_KEY` | `X-API-Key: <key>`              | Service-to-service      |
| `BASIC`   | `Authorization: Basic <b64>`    | Simple auth             |
| `NONE`    | None                            | Public endpoints        |

### Per-Route Override

```python
class ItemManager(AbstractBLLManager, RouterMixin):
    auth_type = AuthType.JWT
    route_auth_overrides = {
        RouteType.LIST: AuthType.NONE,       # Public listing
        RouteType.CREATE: AuthType.API_KEY,  # Service-only creation
    }
```

### Auth Resolution Order
1. Route-specific override (`route_auth_overrides`)
2. System entity check (auto API_KEY for system entities on writes)
3. Manager default (`auth_type`)

## Network Model Convention

Every `BaseModel` requires a `Network` inner class:

```python
class Item(BaseModel):
    id: str
    name: str
    description: Optional[str] = None

    class Network:
        class GET(BaseModel):
            include: Optional[List[str]] = None
            fields: Optional[List[str]] = None

        class LIST(BaseModel):
            include: Optional[List[str]] = None
            fields: Optional[List[str]] = None
            offset: int = 0
            limit: int = 100
            sort_by: Optional[str] = None
            sort_order: str = "asc"

        class POST(BaseModel):
            item: ItemCreate

        class PUT(BaseModel):
            item: ItemUpdate

        class SEARCH(BaseModel):
            item: ItemSearch

        class ResponseSingle(BaseModel):
            item: Item

        class ResponsePlural(BaseModel):
            items: List[Item]
```

## Query Parameters

| Parameter    | Example                         | Description                  |
| ------------ | ------------------------------- | ---------------------------- |
| `fields`     | `?fields=id,name`               | Return only specified fields |
| `include`    | `?include=created_by_user,team` | Load related entities        |
| `offset`     | `?offset=20`                    | Skip N records (pagination)  |
| `limit`      | `?limit=50`                     | Max records to return        |
| `sort_by`    | `?sort_by=created_at`           | Field to sort by             |
| `sort_order` | `?sort_order=desc`              | `asc` or `desc`              |

## Nested Resources

```python
class TeamManager(AbstractBLLManager, RouterMixin):
    nested_resources = {
        "members": NestedResourceConfig(
            child_resource_name="member",
            manager_property="Member_manager",
            child_manager_class=MemberManager,
            routes_to_register=[RouteType.GET, RouteType.LIST, RouteType.CREATE],
        ),
    }
```

**Generated routes:**
- `GET /teams/{team_id}/members`
- `GET /teams/{team_id}/members/{id}`
- `POST /teams/{team_id}/members`

### Manager Property Resolution
Child managers accessed via parent property path:
```python
class TeamManager:
    @property
    def Member_manager(self):
        return MemberManager(team_id=self.team_id, ...)
```

## Custom Routes

### Instance Method

```python
class ItemManager(AbstractBLLManager, RouterMixin):
    custom_routes = [
        CustomRouteConfig(
            path="/{id}/archive",
            method=HTTPMethod.POST,
            function="archive_item",
            summary="Archive item",
        ),
    ]

    def archive_item(self, id: str) -> Item:
        return self.update(id, archived=True)
```

### Static Route (Extensions)

```python
from serverframework.lib.Pydantic2FastAPI import static_route

class EXT_MyExtension:
    @static_route("/status", method=HTTPMethod.GET, auth_type=AuthType.NONE)
    @classmethod
    def get_status(cls) -> dict:
        return {"status": "active"}
```

## Error Handling

| Code | Exception               | When                           |
| ---- | ----------------------- | ------------------------------ |
| 400  | `InvalidRequestError`   | Malformed request              |
| 401  | `AuthenticationError`   | Missing/invalid auth           |
| 403  | `PermissionDeniedError` | Insufficient permissions       |
| 404  | `ResourceNotFoundError` | Resource doesn't exist         |
| 409  | `ResourceConflictError` | Duplicate/constraint violation |
| 422  | `ValidationError`       | Pydantic validation failed     |
| 500  | `Exception`             | Unexpected failure             |

**Response format:**
```json
{
    "detail": "Resource not found",
    "status_code": 404,
    "errors": [{"field": "id", "message": "Resource with id 'abc' not found"}]
}
```

## OpenAPI Examples

`ExampleGenerator` creates realistic examples based on field names:

| Field Pattern | Generated                 |
| ------------- | ------------------------- |
| `*email*`     | `user@example.com`        |
| `*name*`      | `John Smith`              |
| `*_id`, `*id` | UUID                      |
| `*url*`       | `https://example.com`     |
| `*date*`      | ISO date                  |
| `*status*`    | `active`, `pending`, etc. |

**Custom overrides:**
```python
class ItemManager(AbstractBLLManager, RouterMixin):
    example_overrides = {
        "create": {"name": "My Custom Item"},
    }
```

## Data Flow

```
Request → Validate → Route → Extract Body → Manager → Response → JSON
```

1. **Validate**: Pydantic validates request against Network model
2. **Route**: FastAPI matches route
3. **Extract**: `extract_body_data()` pulls resource from body
4. **Manager**: Business logic in BLL manager
5. **Response**: Wrap in `ResponseSingle`/`ResponsePlural`
6. **JSON**: Serialize for client

## Implementation Files

| File                                | Purpose                             |
| ----------------------------------- | ----------------------------------- |
| `lib/Pydantic2FastAPI.py`           | RouterMixin, route generation, auth |
| `endpoints/AbstractEPRouter.py`     | Base class for manual routers       |
| `endpoints/AbstractEPTest.py`       | REST endpoint test base             |
| `endpoints/AbstractEPMatrixTest.py` | Matrix testing                      |
| `endpoints/AbstractGQLTest.py`      | GraphQL test base                   |

## Per-Resource Versioning

`RouterMixin` exposes `version: ClassVar[str] = "v1"` (default) and the prefix is computed from the version plus the resource name. Multiple managers may register the same resource at different versions; both versions route concurrently, both appear in OpenAPI, both are present in the generated SDK as version-suffixed methods.

`deprecated_in: ClassVar[Optional[str]]` and `sunset_in: ClassVar[Optional[str]]` carry the deprecation contract — the framework adds `Deprecation` and `Sunset` HTTP headers automatically and emits a logged warning per-call after the deprecation date. Versions are alphanumeric tokens (`v1`, `v2`, `v2beta`, `v3rc1`); ordering for "latest" is lexicographic with documented quirks for prereleases.

REST gets path-versioned routes (`/v1/user`, `/v2/user`); GraphQL gets field-level `@deprecated` and `@sunset` directives by default, since real GraphQL evolution is field-level deprecation plus additive change rather than wholesale type renaming. Full type-version namespacing in GraphQL is opt-in for breaking renames where field-level deprecation cannot express the change. A persisted query bound to v1 continues to resolve against v1 types after v2 ships, so persisted-query stores record the version they were registered against.

## Custom Routes with SDK / GraphQL / Test Parity

`@custom_route` is a typed decorator capturing everything the framework needs to extend the auto-generated surface beyond CRUD. The decorator declares: HTTP method, path (relative to the manager's prefix), input model (Pydantic), output model (Pydantic), authentication type, OpenAPI tags, and an optional `expose_in` set controlling whether the route appears in REST only, GraphQL only, SDK only, or all three.

The SDK generator emits a method per custom route. The GraphQL generator emits a field (mutation by default, query for safe operations) per custom route. The test scaffolder generates a baseline test with the standard auth, validation, and happy-path checks. The GraphQL operation kind is inferred from the HTTP method by default — `GET` → query, anything else → mutation — with an explicit `graphql_kind` override for cases where the inference is wrong (a `POST` that is genuinely read-only, for example).

For genuinely RPC-shaped routes (no clear resource), the decorator can be applied to a free-standing class derived from `AbstractActionEndpoint` rather than to a `RouterMixin` subclass; the same generators handle it. Custom routes must declare typed inputs and outputs — untyped routes are rejected at registration to preserve the framework's typing guarantees. The streaming case uses `@streaming_route`; the webhook case uses `@webhook_handler`. Subscriptions are not produced from `@custom_route`; they require a stream output type and use the streaming decorator.
