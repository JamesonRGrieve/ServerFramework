# Endpoint Layer Patterns & Usage Guide

This document covers common patterns and best practices for using the endpoint layer in the framework. The system automatically generates routers from BLL managers using the `RouterMixin` approach.

## Quick Start

### Basic Manager with RouterMixin

```python
from lib.Pydantic2FastAPI import RouterMixin, AuthType
from logic.AbstractLogicManager import AbstractBLLManager
from typing import ClassVar, List, Dict

class ResourceManager(RouterMixin, AbstractBLLManager):
    # Router configuration via ClassVars
    prefix: ClassVar[str] = "/v1/resource"
    tags: ClassVar[List[str]] = ["Resource Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    route_auth_overrides: ClassVar[Dict[str, AuthType]] = {}

    # Standard CRUD methods implemented here
    def create(self, **kwargs): ...
    def get(self, id: str, **kwargs): ...
    def list(self, **kwargs): ...
    # etc.

    # Router automatically available via .Router(model_registry) class method
```

### System Entity Setup

```python
# System entities automatically use API key auth for writes
class ExtensionManager(RouterMixin, AbstractBLLManager):
    prefix: ClassVar[str] = "/v1/extension"
    tags: ClassVar[List[str]] = ["Extensions"]
    auth_type: ClassVar[AuthType] = AuthType.JWT  # Default for reads

    # System entities auto-detected via Model.is_system_entity attribute
    # Automatically applies API key auth for writes: create, update, delete, batch_*
```

## Authentication Patterns

### Authentication Types

- **JWT**: Default for user operations (`AuthType.JWT`)
- **API Key**: System operations (`AuthType.API_KEY`)
- **Basic**: Login only (`AuthType.BASIC`)
- **None**: Public endpoints (`AuthType.NONE`)

### Route-Specific Authentication

```python
class ResourceManager(RouterMixin, AbstractBLLManager):
    auth_type: ClassVar[AuthType] = AuthType.JWT  # Default
    route_auth_overrides: ClassVar[Dict[str, AuthType]] = {
        "create": AuthType.API_KEY,  # Override specific operations
        "update": AuthType.API_KEY,
        "delete": AuthType.API_KEY,
    }
```

### System Entity Auto-Configuration

Entities with `is_system_entity=True` automatically get API key authentication for write operations:

```python
# Automatically applied:
route_auth_overrides = {
    "create": AuthType.API_KEY,
    "update": AuthType.API_KEY,
    "delete": AuthType.API_KEY,
    "batch_update": AuthType.API_KEY,
    "batch_delete": AuthType.API_KEY,
}
```

## Standard Routes

RouterMixin automatically generates these routes:

| Route        | Method | Path                  | Description           |
|--------------|--------|-----------------------|-----------------------|
| create       | POST   | `/v1/resource`        | Create resource(s)    |
| get          | GET    | `/v1/resource/{id}`   | Get single resource   |
| list         | GET    | `/v1/resource`        | List with filters     |
| search       | POST   | `/v1/resource/search` | Complex search        |
| update       | PUT    | `/v1/resource/{id}`   | Update single         |
| delete       | DELETE | `/v1/resource/{id}`   | Delete single         |
| batch_update | PUT    | `/v1/resource`        | Update multiple       |
| batch_delete | DELETE | `/v1/resource`        | Delete multiple       |

## Request/Response Patterns

### Standard Formats

**Single Resource Request:**
```json
{"resource_name": {"field1": "value1", "field2": "value2"}}
```

**Single Resource Response:**
```json
{"resource_name": {"id": "uuid", "field1": "value1", "created_at": "2024-01-01T00:00:00Z"}}
```

**List Response:**
```json
{"resource_name_plural": [{"id": "uuid1", "field1": "value1"}, {"id": "uuid2", "field1": "value2"}]}
```

### Batch Operations

**Batch Create:**
```json
{"resource_name_plural": [{"field1": "value1"}, {"field1": "value2"}]}
```

**Batch Update:**
```json
{"resource_name": {"field1": "new_value"}, "target_ids": ["id1", "id2"]}
```

**Batch Delete:**
```json
{"target_ids": ["id1", "id2"]}
```

## Custom Routes

### Method Decorators

```python
class ResourceManager(RouterMixin, AbstractBLLManager):
    # Custom routes via method decorators
    @custom_route(method="post", path="/{id}/activate")
    def activate(self, id: str) -> ResourceModel:
        """Activate a resource."""
        # Implementation here
        return self.get(id)

    @static_route(method="get", path="/status")
    @classmethod
    def get_status(cls) -> Dict[str, Any]:
        """Get system status."""
        return {"status": "active"}
```

## Example Generation

### Automatic Examples

Examples are generated automatically using intelligent field name patterns:

```python
# Auto-generated based on field names and types
examples = ExampleGenerator.generate_operation_examples(
    NetworkModel, "resource_name"
)
```

## Network Model Structure

Required pattern for router compatibility:

```python
class ResourceNetworkModel:
    class POST(BaseModel):
        resource: ResourceCreateModel  # Field name = resource_name

    class PUT(BaseModel):
        resource: ResourceUpdateModel  # Field name = resource_name

    class SEARCH(BaseModel):
        resource: ResourceSearchModel  # Field name = resource_name

    class ResponseSingle(BaseModel):
        resource: ResourceResponseModel  # Field name = resource_name

    class ResponsePlural(BaseModel):
        resources: List[ResourceResponseModel]  # Plural form
```

## Error Handling

Consistent error responses through manager exceptions:

```python
# In managers, raise these for automatic HTTP conversion:
raise ResourceNotFoundError("resource", resource_id)     # → 404
raise ResourceConflictError("resource", "already exists") # → 409
raise InvalidRequestError("Invalid data")                # → 400
```

## Testing Integration

```python
class TestResourceEndpoints(AbstractEPTest):
    base_endpoint = "resource"
    entity_name = "resource"
    required_fields = ["name", "description"]
    string_field_to_update = "name"

    create_fields = {
        "name": lambda: f"Test {faker.word()}",
        "description": "Test description"
    }
```

## Best Practices

1. **Consistent Naming**: Use same `resource_name` throughout
2. **Manager Logic**: Keep business logic in managers, not routes
3. **Authentication**: JWT for users, API keys for system operations
4. **Model Structure**: Follow NetworkModel requirements exactly
5. **Testing**: Use AbstractEPTest for comprehensive coverage
6. **Performance**: Implement pagination in managers
7. **Security**: Proper auth and input validation
