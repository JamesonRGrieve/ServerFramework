# Endpoint Layer Patterns

Primary reference for endpoint generation, configuration, and testing.

## Quick Reference

### Route Types Generated

| Route | Method | Path | Description |
|-------|--------|------|-------------|
| create | POST | `/v1/{resource}` | Create single/batch |
| get | GET | `/v1/{resource}/{id}` | Get by ID |
| list | GET | `/v1/{resource}` | List with filters |
| search | POST | `/v1/{resource}/search` | Complex queries |
| update | PUT | `/v1/{resource}/{id}` | Update single |
| delete | DELETE | `/v1/{resource}/{id}` | Delete single |
| batch_update | PUT | `/v1/{resource}` | Update multiple |
| batch_delete | DELETE | `/v1/{resource}` | Delete multiple |

### Authentication Types

| Type | Use Case | Header |
|------|----------|--------|
| `AuthType.JWT` | User operations (default) | `Authorization: Bearer <token>` |
| `AuthType.API_KEY` | System/admin operations | `X-API-Key: <key>` |
| `AuthType.BASIC` | Login endpoint only | `Authorization: Basic <base64>` |
| `AuthType.NONE` | Public endpoints | None |

## Manager Configuration

```python
from lib.Pydantic2FastAPI import RouterMixin, AuthType
from logic.AbstractLogicManager import AbstractBLLManager

class ResourceManager(RouterMixin, AbstractBLLManager):
    # Required ClassVars
    prefix: ClassVar[str] = "/v1/resource"
    tags: ClassVar[List[str]] = ["Resource"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    # Optional ClassVars
    route_auth_overrides: ClassVar[Dict[RouteType, AuthType]] = {
        RouteType.CREATE: AuthType.API_KEY,
        RouteType.DELETE: AuthType.API_KEY,
    }
    routes_to_register: ClassVar[List[RouteType]] = None  # None = all
    custom_routes: ClassVar[List[CustomRouteConfig]] = []
    nested_resources: ClassVar[Dict[str, NestedResourceConfig]] = {}
    example_overrides: ClassVar[Dict[str, Dict[str, Any]]] = {}
```

### System Entity Auto-Configuration

Entities with `is_system_entity=True` automatically get API key auth for writes:

```python
# Auto-applied for system entities:
route_auth_overrides = {
    RouteType.CREATE: AuthType.API_KEY,
    RouteType.UPDATE: AuthType.API_KEY,
    RouteType.DELETE: AuthType.API_KEY,
    RouteType.BATCH_UPDATE: AuthType.API_KEY,
    RouteType.BATCH_DELETE: AuthType.API_KEY,
}
```

## Network Model Structure

Required for router generation. Auto-generated from BLL models but can be customized:

```python
class ResourceNetworkModel:
    # Request models
    class GET(BaseModel):
        fields: Optional[List[str]] = None
        includes: Optional[List[str]] = None

    class POST(BaseModel):
        resource: ResourceCreateModel

    class PUT(BaseModel):
        resource: ResourceUpdateModel

    class SEARCH(BaseModel):
        resource: ResourceSearchModel

    # Response models
    class ResponseSingle(BaseModel):
        resource: ResourceModel

    class ResponsePlural(BaseModel):
        resources: List[ResourceModel]
```

## Request/Response Formats

### Single Resource
```json
// Request
{"resource": {"name": "value", "description": "text"}}

// Response
{"resource": {"id": "uuid", "name": "value", "created_at": "2024-01-01T00:00:00Z"}}
```

### List Response
```json
{"resources": [{"id": "uuid1", ...}, {"id": "uuid2", ...}]}
```

### Batch Create
```json
{"resources": [{"name": "one"}, {"name": "two"}]}
```

### Batch Update
```json
{"resource": {"status": "active"}, "target_ids": ["id1", "id2"]}
```

### Batch Delete
```json
{"target_ids": ["id1", "id2"]}
```

## Custom Routes

### Via Decorator

```python
from lib.Pydantic2FastAPI import static_route, HTTPMethod, AuthType

class ResourceManager(RouterMixin, AbstractBLLManager):
    @static_route("/status", method=HTTPMethod.GET, auth_type=AuthType.NONE)
    @classmethod
    def get_status(cls) -> Dict[str, Any]:
        return {"status": "active"}

    @static_route("/{id}/activate", method=HTTPMethod.POST)
    def activate(self, id: str) -> ResourceModel:
        # Implementation
        return self.get(id)
```

### Via Configuration

```python
custom_routes: ClassVar[List[CustomRouteConfig]] = [
    CustomRouteConfig(
        path="/{id}/clone",
        method=HTTPMethod.POST,
        function="clone_resource",
        auth_type=AuthType.JWT,
        summary="Clone a resource",
    )
]
```

## Nested Resources

Configure parent-child relationships:

```python
class TeamManager(RouterMixin, AbstractBLLManager):
    prefix: ClassVar[str] = "/v1/team"

    nested_resources: ClassVar[Dict[str, NestedResourceConfig]] = {
        "invitations": NestedResourceConfig(
            child_resource_name="invitation",
            manager_property="invitations",
            routes_to_register=[RouteType.LIST, RouteType.CREATE, RouteType.DELETE],
        )
    }

    @property
    def invitations(self):
        return InvitationManager(
            requester_id=self.requester_id,
            parent_team_id=self.target_id
        )
```

Generates: `/v1/team/{team_id}/invitation`, `/v1/team/{team_id}/invitation/{id}`

## Error Handling

Manager exceptions auto-convert to HTTP responses:

| Exception | HTTP Status |
|-----------|-------------|
| `ResourceNotFoundError` | 404 |
| `ResourceConflictError` | 409 |
| `InvalidRequestError` | 400 |
| `PermissionDeniedError` | 403 |
| `AuthenticationError` | 401 |
| `ValidationError` | 422 |

```python
# In manager methods:
raise ResourceNotFoundError("resource", resource_id)  # -> 404
raise ResourceConflictError("resource", "already exists")  # -> 409
```

### Error Response Format
```json
{
    "detail": {
        "message": "Resource not found",
        "details": "resource with id 'abc123' not found"
    }
}
```

## Example Generation

`ExampleGenerator` auto-generates OpenAPI examples using field name patterns:

```python
# Pattern matching for field names -> Faker generators
"*email*" -> faker.email()
"*name*" -> faker.name()
"*id*" -> uuid4()
"*url*" -> faker.url()
"*date*" -> faker.date()
# ... 40+ patterns
```

Override with:
```python
example_overrides: ClassVar[Dict[str, Dict[str, Any]]] = {
    "create": {"name": "My Custom Name"},
    "get": {"status": "active"},
}
```

## Testing

Use `AbstractEPTest` for comprehensive endpoint coverage:

```python
class TestResourceEndpoints(AbstractEPTest):
    # Required
    base_endpoint = "resource"
    entity_name = "resource"
    required_fields = ["name"]
    string_field_to_update = "name"

    # Entity creation config
    create_fields = {
        "name": lambda: f"Test {faker.word()}",
        "description": "Test description",
    }

    # Optional: parent entities for nested resources
    parent_entities = [
        ParentEntity(
            name="team",
            foreign_key="team_id",
            path_level=1,
            create_fields={"name": lambda: f"Team {faker.word()}"},
        )
    ]
```

### Standard Tests Generated

- `test_POST_201_single` - Create single entity
- `test_POST_201_batch` - Create multiple entities
- `test_GET_200_single` - Get by ID
- `test_GET_200_list` - List entities
- `test_GET_200_fields` - Field projection
- `test_GET_200_includes` - Include relations
- `test_POST_200_search` - Search entities
- `test_PUT_200_single` - Update entity
- `test_PUT_200_batch` - Batch update
- `test_DELETE_204_single` - Delete entity
- `test_DELETE_204_batch` - Batch delete
- `test_*_401_unauthorized` - Auth failure tests
- `test_*_404_not_found` - Not found tests

See [EP.Test.md](EP.Test.md) for full testing documentation.

## Best Practices

1. **Manager Logic**: Keep business logic in managers, not routes
2. **Consistent Naming**: Use same `resource_name` in model, manager, and network model
3. **Auth Strategy**: JWT for users, API_KEY for system operations
4. **Error Handling**: Throw typed exceptions, let router convert to HTTP
5. **Validation**: Use Pydantic models for request validation
6. **Testing**: Use AbstractEPTest for all endpoint tests
7. **Documentation**: Leverage auto-generated examples, override as needed
