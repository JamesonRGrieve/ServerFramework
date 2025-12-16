# Endpoint Layer Architecture

High-level architecture overview. See [EP.Patterns.md](EP.Patterns.md) for usage patterns.

## System Layers

```
┌─────────────────────────────────────────────────────────────────┐
│                     API Layer (FastAPI)                         │
├─────────────────────────────────────────────────────────────────┤
│               RouterMixin (Route Generation)                    │
├─────────────────────────────────────────────────────────────────┤
│              Manager Layer (Business Logic)                     │
├─────────────────────────────────────────────────────────────────┤
│               Database Layer (SQLAlchemy ORM)                   │
└─────────────────────────────────────────────────────────────────┘
```

## Core Design Principles

1. **Manager-Driven Generation**: Routers generated from BLL manager classes
2. **RouterMixin Pattern**: BLL managers inherit `RouterMixin` for auto-generation
3. **Model Registry Integration**: `model_registry.build_routers()` builds all routers
4. **Convention over Configuration**: Standard patterns reduce boilerplate
5. **Automatic CRUD**: 8 standard routes generated from model structure

## Data Flow

```
Request → Router → Body Extraction → Manager → Response → Client
   ↓         ↓           ↓             ↓          ↓
Validate  Route      Extract       Business   Format
Schema   Match      Resource      Logic      Response
```

## Core Abstractions

### RouterMixin

Mixin added to BLL managers for automatic router generation:

```python
class ResourceManager(RouterMixin, AbstractBLLManager):
    prefix: ClassVar[str] = "/v1/resource"
    tags: ClassVar[List[str]] = ["Resource"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    # Router generated via:
    router = ResourceManager.Router(model_registry)
```

### Network Model

Defines request/response structure for auto-generation:

```python
class ResourceModel:
    class Network:
        class GET: ...      # Query params
        class POST: ...     # Create request body
        class PUT: ...      # Update request body
        class SEARCH: ...   # Search request body
        class ResponseSingle: ...  # Single item response
        class ResponsePlural: ...  # List response
```

### Manager Interface

BLL managers provide business logic methods:

```python
class ResourceManager(RouterMixin, AbstractBLLManager):
    def create(self, **kwargs) -> Model: ...
    def get(self, id: str, **kwargs) -> Model: ...
    def list(self, **kwargs) -> List[Model]: ...
    def search(self, **kwargs) -> List[Model]: ...
    def update(self, id: str, **kwargs) -> Model: ...
    def delete(self, id: str) -> None: ...
    def batch_update(self, data: Dict, target_ids: List[str]) -> List[Model]: ...
    def batch_delete(self, target_ids: List[str]) -> None: ...
```

## Authentication Flow

1. Route checks for auth override (`route_auth_overrides`)
2. System entity detection (auto API_KEY for writes)
3. Falls back to router default (`auth_type`)
4. Dependency injection provides authenticated user context

## Router Generation Process

```python
# In ModelRegistry.build_routers():
for manager_class in self.get_router_managers():
    if hasattr(manager_class, 'Router'):
        router = manager_class.Router(self)
        routers.append(router)
```

## Nested Resources

Parent-child relationships via manager properties:

```python
class TeamManager(RouterMixin, AbstractBLLManager):
    @property
    def invitations(self):
        return InvitationManager(parent_team_id=self.target_id, ...)

# Generates: /v1/team/{team_id}/invitation
```

## Error Handling

Manager exceptions → HTTP responses (automatic conversion):

- `ResourceNotFoundError` → 404
- `ResourceConflictError` → 409
- `PermissionDeniedError` → 403
- `ValidationError` → 422

## GraphQL Integration

Parallel schema generation from same models:

- Types generated from Pydantic models
- Resolvers delegate to manager methods
- Same auth system as REST

## Performance

- Route registration: only needed routes
- Example caching: by model class
- Manager factories: lightweight DI
- Auth dependencies: cached resolution

## Related Documentation

- [EP.Patterns.md](EP.Patterns.md) - Usage patterns and configuration
- [EP.Router.md](EP.Router.md) - Implementation details
- [EP.Test.md](EP.Test.md) - Testing framework
- [EP.Schema.md](EP.Schema.md) - API endpoint reference
- [EP.GQL.md](EP.GQL.md) - GraphQL integration
