# Router Implementation Details

Internal implementation details for developers extending the router system. For usage patterns, see [EP.Patterns.md](EP.Patterns.md).

## RouterMixin Internals

```python
class RouterMixin:
    # ClassVars for configuration
    prefix: ClassVar[Optional[str]] = None
    tags: ClassVar[Optional[List[str]]] = None
    auth_type: ClassVar[AuthType] = AuthType.JWT
    routes_to_register: ClassVar[Optional[List[RouteType]]] = None
    route_auth_overrides: ClassVar[Dict[RouteType, AuthType]] = {}
    custom_routes: ClassVar[List[CustomRouteConfig]] = []
    nested_resources: ClassVar[Dict[str, NestedResourceConfig]] = {}
    example_overrides: ClassVar[Dict[str, Dict[str, Any]]] = {}

    @classmethod
    def Router(cls, model_registry) -> APIRouter:
        return create_router_from_manager(
            manager_class=cls, model_registry=model_registry
        )
```

## System Entity Detection

Auto-detects system entities via model inspection:

```python
def _detect_system_entity(model_class) -> bool:
    """
    Detection order:
    1. Check model_class.is_system_entity attribute
    2. Check ResponseSingle class inheritance
    3. Check POST class inheritance
    """
```

When detected, applies API_KEY auth to write operations.

## Route Registration

`register_route()` function handles each route type:

```python
def register_route(
    router: APIRouter,
    route_type: RouteType,
    manager_class: Type[AbstractBLLManager],
    model_registry: Any,
    auth_type: AuthType,
    route_auth_overrides: Dict[RouteType, AuthType],
    examples: Dict[str, Dict[str, Any]],
    child_manager_class: Type[AbstractBLLManager] = None,
    parent_param_name: Optional[str] = None,
    manager_property: Optional[str] = None,
) -> None:
```

### Route Registration Flow

1. Get bound BaseModel from model_registry
2. Derive resource names (singular/plural)
3. Get Network model for request/response schemas
4. Generate examples via ExampleGenerator
5. Create auth dependency
6. Create manager factory
7. Register route handler

## Manager Factory

Creates manager instances with auth context:

```python
def create_manager_factory(
    manager_class: Type[AbstractBLLManager],
    model_registry: Any,
    auth_type: AuthType,
) -> Callable:
    """
    Returns factory function that:
    1. Extracts auth from request headers
    2. Resolves requester_id from JWT/API key
    3. Creates manager instance with requester context
    """
```

## Body Data Extraction

```python
def extract_body_data(
    body: Union[Dict, BaseModel, List],
    resource_name: str,
    resource_name_plural: str,
) -> Union[Dict, List[Dict]]:
    """
    Extracts resource data from request body.
    Handles: Pydantic models, dicts, lists.
    Checks both singular and plural keys.
    """
```

## Nested Router Generation

### Network Model Inference

```python
def _infer_network_model_class(child_resource_name: str, manager_property: str):
    """
    Search order:
    1. logic.BLL_Auth.{ResourceName}Model
    2. logic.BLL_{ResourceName}.{ResourceName}Model
    3. logic.BLL_Providers (for provider-related models)
    4. logic.BLL_Extensions (for extension-related models)
    """
```

### Special Name Mappings

| Property Name | Model Name |
|--------------|------------|
| abilities | AbilityModel |
| instances | ProviderInstanceModel |
| rotations | RotationModel |
| provider_instances | ProviderInstanceModel |

## Query Parameter Handling

```python
def create_query_model_dependency(model_cls: Type[BaseModel]) -> Callable:
    """
    Creates FastAPI dependency that:
    1. Maps query params to model fields (with alias support)
    2. Handles list-style params (field[] or field=a,b,c)
    3. Validates via Pydantic model
    """
```

## Error Handling

```python
def handle_resource_operation_error(err: Exception) -> None:
    """
    Exception → HTTP mapping:
    - ValidationError → 422
    - ValueError → 422
    - HTTPException → re-raise
    - Other → 500
    """
```

## Field/Include Projection

```python
def _apply_field_projection_to_entity(
    entity: Any, fields: List[str], includes: List[str]
) -> Any:
    """
    Filters serialized entity to requested fields.
    Preserves included relation keys.
    Handles dotted paths (e.g., "user.name").
    """
```

## Response Serialization

```python
def serialize_for_response(data: Union[None, Dict, BaseModel, List]) -> Any:
    """
    Converts manager results to JSON-serializable format.
    Handles: None, dicts, Pydantic models, lists.
    """
```

## Pydantic2FastAPI Utilities

Key functions in `lib/Pydantic2FastAPI.py`:

| Function | Purpose |
|----------|---------|
| `create_router_from_manager()` | Main entry point for router generation |
| `register_route()` | Registers individual routes |
| `create_manager_factory()` | Creates auth-aware manager factory |
| `get_auth_dependency()` | Returns FastAPI auth dependency |
| `extract_body_data()` | Extracts data from request body |
| `serialize_for_response()` | Serializes manager results |
| `ExampleGenerator` | Generates OpenAPI examples |

## Legacy: AbstractEPRouter

The old `AbstractEPRouter` class is deprecated. Migration path:

```python
# Old (deprecated)
class ResourceRouter(AbstractEPRouter):
    config = RouterConfig(prefix="/v1/resource", ...)

# New (current)
class ResourceManager(RouterMixin, AbstractBLLManager):
    prefix: ClassVar[str] = "/v1/resource"
    # Router auto-generated
```

## Performance Notes

- Route registration: O(1) per route type
- Example generation: cached by model class
- Manager factory: lightweight closure
- Auth dependency: single Depends() call
- Body extraction: single pass over data
