# Endpoint Layer Architecture

This document covers the architectural design and core abstractions of the endpoint layer, focusing on how the system achieves consistency, scalability, and maintainability across all API resources.

Routers are automatically generated from BLL managers using the `RouterMixin` class in `lib/Pydantic2FastAPI.py`.

## Architecture Overview

### Core Design Principles

1. **Manager-Driven Generation**: Routers automatically generated from BLL manager classes
2. **RouterMixin Pattern**: BLL managers inherit from `RouterMixin` to get router generation
3. **Convention over Configuration**: Standard patterns reduce boilerplate
4. **Automatic Generation**: CRUD operations generated from models
5. **Consistent Behavior**: Same patterns across all resources
6. **Flexible Authentication**: Route-specific auth configuration via ClassVars
7. **Model Registry Integration**: Automatic router building via model registry
8. **Comprehensive Testing**: Automated test generation for all endpoints

### System Layers

```
┌─────────────────────────────────────────────────────────────────┐
│                     API Layer (FastAPI)                        │
├─────────────────────────────────────────────────────────────────┤
│                RouterMixin (Route Generation)                  │
├─────────────────────────────────────────────────────────────────┤
│               Manager Layer (Business Logic)                   │
├─────────────────────────────────────────────────────────────────┤
│                Database Layer (SQLAlchemy ORM)                 │
└─────────────────────────────────────────────────────────────────┘
```

## Core Abstractions

### RouterMixin Pattern

The system uses the `RouterMixin` class that BLL managers inherit from to automatically generate routers:

```python
# BLL Manager with RouterMixin
class ResourceManager(RouterMixin, AbstractBLLManager):
    # Router configuration via ClassVars
    prefix: ClassVar[str] = "/v1/resource"
    tags: ClassVar[List[str]] = ["Resource Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    route_auth_overrides: ClassVar[Dict[str, AuthType]] = {}

    # Router automatically generated via .Router(model_registry) class method
    @classmethod
    def Router(cls, model_registry) -> APIRouter:
        # Implemented by RouterMixin - generates full CRUD router
        pass
```

#### Key Capabilities

- **Automatic CRUD Generation**: Creates 8 standard REST endpoints
- **Authentication Integration**: JWT, API Key, Basic, and None auth
- **System Entity Detection**: Auto-configures API key auth for system entities
- **Nested Resource Support**: Parent-child relationships with automatic routing
- **Batch Operations**: Built-in bulk create, update, and delete
- **Example Generation**: Intelligent documentation examples
- **Error Handling**: Consistent HTTP error responses
- **Custom Route Integration**: Easy addition of non-CRUD operations

### Network Model Convention

Standardized model structure that drives automatic generation:

```python
class ResourceNetworkModel:
    # Request models (what comes in)
    class POST(BaseModel):
        resource: ResourceCreateModel
        
    class PUT(BaseModel): 
        resource: ResourceUpdateModel
        
    class SEARCH(BaseModel):
        resource: ResourceSearchModel
    
    # Response models (what goes out)
    class ResponseSingle(BaseModel):
        resource: ResourceResponseModel
        
    class ResponsePlural(BaseModel):
        resources: List[ResourceResponseModel]
```

This convention enables the router to:
- Validate request bodies automatically
- Generate appropriate response schemas  
- Create realistic documentation examples
- Handle both single and plural operations

### Manager Interface Abstraction

Business logic is encapsulated in manager classes that inherit from `RouterMixin` and `AbstractLogicManager`:

```python
class ResourceManager(RouterMixin, AbstractLogicManager):
    # Router configuration
    prefix: ClassVar[str] = "/v1/resource"
    tags: ClassVar[List[str]] = ["Resource Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    
    # Standard CRUD methods (inherited from AbstractLogicManager)
    def create(self, **kwargs) -> ResourceModel: ...
    def get(self, id: str, **kwargs) -> ResourceModel: ...
    def list(self, **kwargs) -> List[ResourceModel]: ...
    def search(self, **kwargs) -> List[ResourceModel]: ...
    def update(self, id: str, **kwargs) -> ResourceModel: ...
    def delete(self, id: str) -> None: ...
    def batch_update(self, resource_data: Dict, target_ids: List[str]) -> List[ResourceModel]: ...
    def batch_delete(self, target_ids: List[str]) -> None: ...
    
    # Custom routes via decorators
    @custom_route(method="post", path="/{id}/activate")
    def activate(self, id: str) -> ResourceModel: ...
```

This abstraction:
- Combines business logic with automatic router generation
- Separates routing configuration from implementation
- Provides consistent error handling patterns
- Enables easy testing and mocking
- Supports context injection (user, team, permissions)
- Allows custom routes via method decorators

## Authentication Architecture

### Multi-Level Authentication System

```python
# Manager-level authentication configuration
class ResourceManager(RouterMixin, AbstractBLLManager):
    # Default authentication type
    auth_type: ClassVar[AuthType] = AuthType.JWT

    # Route-specific overrides
    route_auth_overrides: ClassVar[Dict[str, AuthType]] = {
        "create": AuthType.API_KEY,
        "update": AuthType.API_KEY,
        "delete": AuthType.API_KEY,
    }

# System entity auto-configuration
# Entities with is_system_entity=True automatically get API key auth for writes
```

### Authentication Flow

1. **Route-Level Check**: Does this route have a specific auth override?
2. **System Entity Check**: Is this a system entity requiring API key auth?
3. **Default Auth**: Fall back to router-level default auth type
4. **Dependency Injection**: Inject appropriate auth dependency into route

## Nested Resource Architecture

### Hierarchical Resource Modeling

The system supports complex resource hierarchies through:

1. **Parent-Child Relationships**: `/v1/team/{team_id}/invitation`
2. **Manager Property Paths**: Access child managers via parent properties
3. **Network Model Inference**: Automatic discovery of child model classes
4. **Mirror Routes**: Both nested and standalone access patterns

### Nesting Implementation

Nested resources are handled through manager property paths and relationship definitions in the BLL layer:

```python
# Parent manager with RouterMixin
class TeamManager(RouterMixin, AbstractBLLManager):
    prefix: ClassVar[str] = "/v1/team"

    # Nested resource managers accessed through properties
    @property
    def invitations(self):
        return InvitationManager(
            requester_id=self.requester_id,
            parent_team_id=self.target_id
        )
```

### Network Model Inference

The system automatically infers network model classes for nested resources by:

1. **Manager Instance Inspection**: Attempts to get NetworkModel from child manager
2. **Module Search**: Looks in multiple locations:
   - `logic.BLL_Auth` (common location)
   - `logic.BLL_{ResourceName}` (resource-specific)
   - `logic.BLL_Providers` (provider-related models)
   - `logic.BLL_Extensions` (extension-related models)
3. **Name Mapping**: Handles special cases:
   - `abilities` → `AbilityModel`
   - `instances` → `ProviderInstanceModel`
   - `rotations` → `RotationModel`
   - `provider_instances` → `ProviderInstanceModel`
4. **Pattern Support**: `ModelName.Network` pattern for network model definitions

### Manager Property Resolution

For nested resources, the system uses property paths to access child managers:

```python
# Parent manager
class TeamManager:
    @property
    def invitations(self):
        return InvitationManager(team_id=self.team_id, ...)

# Router accesses: get_manager(team_manager, "invitations") → team_manager.invitations
```

## Example Generation Architecture

### Intelligent Pattern Recognition

The `ExampleGenerator` uses a sophisticated pattern matching system:

1. **Field Name Analysis**: 40+ regex patterns for field names
2. **Type Inference**: Smart generation based on Python types
3. **Faker Integration**: Realistic fake data for documentation
4. **Caching System**: Performance optimization for repeated generation
5. **Operation-Specific Examples**: Different examples for create, update, search

### Example Generation Flow

```python
# 1. Analyze network model structure
network_classes = {
    "POST": NetworkModel.POST,
    "PUT": NetworkModel.PUT, 
    "SEARCH": NetworkModel.SEARCH,
    "ResponseSingle": NetworkModel.ResponseSingle,
    "ResponsePlural": NetworkModel.ResponsePlural
}

# 2. Extract field information from each class
for class_name, model_class in network_classes.items():
    fields = analyze_model_fields(model_class)
    
# 3. Generate examples using pattern matching
examples = generate_operation_examples(fields, patterns)

# 4. Apply custom overrides
apply_example_overrides(examples, user_overrides)
```

## Testing Architecture

### Comprehensive Test Generation

The `AbstractEPTest` framework provides:

1. **Standard Test Coverage**: All CRUD operations + edge cases
2. **Authentication Testing**: All auth types and error scenarios  
3. **Nested Resource Testing**: Parent-child relationship validation
4. **GraphQL Integration**: Query, mutation, and subscription tests
5. **Batch Operation Testing**: Bulk create, update, delete validation
6. **Error Scenario Testing**: 400, 401, 403, 404, 409 responses

### Test Architecture Flow

```python
# 1. Configuration-driven test generation
class TestResourceEndpoints(AbstractEPTest):
    base_endpoint = "resource"
    entity_name = "resource"
    required_fields = ["name", "description"]
    parent_entities = [...]  # Automatic nesting tests
    
# 2. Automatic test method generation
# Framework generates ~30 test methods based on configuration

# 3. Dependency-aware execution
# Tests run in proper order using pytest dependency markers

# 4. Automatic cleanup
# Created entities cleaned up after each test
```

## Error Handling Architecture

### Consistent Error Response System

Manager exceptions are automatically converted to appropriate HTTP responses:

```python
# In manager classes
raise ResourceNotFoundError("resource", resource_id)    # → 404
raise ResourceConflictError("resource", "already exists") # → 409
raise InvalidRequestError("Invalid data")                # → 400
raise PermissionDeniedError("Access denied")             # → 403
raise AuthenticationError("Invalid credentials")         # → 401

# Router automatically handles conversion to HTTP responses
```

### Error Response Format

```json
{
    "detail": "Resource not found",
    "status_code": 404,
    "errors": [
        {
            "field": "id",
            "message": "Resource with id 'abc123' not found"
        }
    ]
}
```

## Request/Response Architecture

### Standardized Data Flow

1. **Request Validation**: Pydantic models validate incoming data
2. **Body Extraction**: Router extracts resource data from request body
3. **Manager Invocation**: Business logic executed in manager layer
4. **Response Generation**: Manager results wrapped in response models
5. **Documentation**: Examples and schemas auto-generated for OpenAPI

### Data Flow Diagram

```
Request → Router → Body Extraction → Manager → Response → Client
   ↓         ↓           ↓             ↓          ↓         ↑
Validate  Route     Extract       Business   Format    JSON
Schema   Match      Resource      Logic      Response  Response
```

## Extension Points

### Custom Route Integration

```python
# Direct decorator approach
@router.post("/{id}/activate")
async def activate_resource(id: str, manager=Depends(get_manager)):
    return manager.activate(id)

# Helper method approach  
router.with_custom_route(
    method="post",
    path="/{id}/activate",
    endpoint=activate_resource,
    summary="Activate resource",
)
```

### Current Router Generation

Routers are now generated automatically by the model registry from BLL managers:

```python
# In lib/Pydantic.py - ModelRegistry.build_routers()
def build_routers(self):
    """Build FastAPI routers using RouterMixin from BLL managers."""
    logger.info("Building routers using RouterMixin approach - NO EP files")
    
    # Use the RouterMixin approach exclusively
    router_instances = self.build_all_routers_from_managers()
    
    # Convert to format expected by application
    routers = []
    for router in router_instances:
        routers.append({
            "router": router,
            "model_name": router_name,
            "module_name": f"RouterMixin_{router_name}",
        })
    
    return routers
```

## Integration Architecture

### FastAPI Integration

```python
# Router creation via RouterMixin
resource_router = ResourceManager.Router(model_registry)

# FastAPI app integration
app = FastAPI()
app.include_router(resource_router)

# Automatic OpenAPI generation with examples
# /docs endpoint includes generated documentation
```

### GraphQL Integration

The system provides automatic GraphQL schema generation:

1. **Type Generation**: Pydantic models → GraphQL types
2. **Resolver Creation**: Manager methods → GraphQL resolvers  
3. **Subscription Support**: Real-time updates via broadcasting
4. **Authentication**: Same auth system as REST endpoints

## Performance Considerations

### Optimization Strategies

1. **Route Registration**: Only registers needed routes
2. **Example Caching**: Generated examples cached by model class
3. **Manager Factories**: Lightweight dependency injection
4. **Authentication**: Cached auth dependency resolution
5. **Documentation**: Pre-generated OpenAPI schemas

### Scalability Patterns

1. **Stateless Design**: All routers are stateless and thread-safe
2. **Manager Isolation**: Each request gets fresh manager instance
3. **Database Sessions**: Proper session management and cleanup
4. **Resource Boundaries**: Clear separation between resource domains

## Best Practices

### Architectural Guidelines

1. **Single Responsibility**: One router per resource type
2. **Consistent Conventions**: Follow naming and structure patterns
3. **Manager Delegation**: Keep routing logic minimal
4. **Error Boundary**: Handle errors at appropriate levels
5. **Documentation**: Leverage automatic generation with overrides
6. **Testing**: Use provided abstractions for comprehensive coverage

### Design Patterns

1. **Factory Pattern**: Manager factories for dependency injection
2. **Template Method**: Standard route generation with customization points
3. **Strategy Pattern**: Authentication type selection
4. **Observer Pattern**: GraphQL subscriptions and real-time updates
5. **Decorator Pattern**: Custom route enhancement