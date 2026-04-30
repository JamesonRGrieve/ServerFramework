# Core Abstractions

This document provides a comprehensive overview of the core abstractions used throughout the framework:

1. [Business Logic Layer Manager](#business-logic-layer-manager-abstractbllmanager)
2. [Service Layer](#service-layer-abstractservice)
3. [Model System](#model-system)
4. [Hook System](#hook-system)
5. [Search System](#search-system)

## Business Logic Layer Manager (`AbstractBLLManager`)

The Business Logic Layer (BLL) provides a standardized pattern for managing domain entities with consistent CRUD operations, search abilities, validation, and extensibility through hooks.

### Core Structure

```python
class AbstractBLLManager:
    Model = TemplateModel  # Main Pydantic model for this entity
    ReferenceModel = TemplateReferenceModel  # Reference model for relationships
    NetworkModel = TemplateNetworkModel  # Network models for API interactions

    # Search transformer functions
    search_transformers: Dict[str, Callable] = {}

    # Router configuration - can be overridden by subclasses
    endpoint_config: ClassVar[Dict[str, Any]] = {}
    custom_routes: ClassVar[List[Dict[str, Any]]] = []
    nested_resources: ClassVar[Dict[str, Any]] = {}
    route_auth_overrides: ClassVar[Dict[str, AuthType]] = {}

    # Manager factory configuration
    factory_params: ClassVar[List[str]] = ["target_id", "target_team_id"]
    auth_dependency: ClassVar[Optional[str]] = None
    requires_root_access: ClassVar[bool] = False

    def __init__(
        self,
        model_registry,
        requester_id: str,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        parent: Optional[Any] = None,
    )
```

### CRUD Operations

#### Create Operations
- `create(**kwargs)` - Create single or multiple entities (supports `entities` list parameter)
- `_create_single_entity(**kwargs)` - Internal method for single entity creation
- `create_validation(entity)` - Override for custom validation
- Automatic hook argument preservation for non-Pydantic fields

#### Read Operations
- `get(include=None, fields=None, **kwargs)` - Get single entity with optional relationships
- `list(include=None, fields=None, sort_by=None, sort_order="asc", filters=None, limit=None, offset=None, **kwargs)` - List entities with filtering and pagination
- `search(include=None, fields=None, sort_by=None, sort_order="asc", filters=None, limit=None, offset=None, **search_params)` - Advanced search with complex criteria
- `fields` selections are validated against the SQLAlchemy mapper; unknown attributes raise a `ValueError`, ensuring API requests return a 422 instead of silently ignoring typos or causing loader errors.

#### Update Operations  
- `update(id: str, **kwargs)` - Update single entity
- `batch_update(items: List[Dict[str, Any]])` - Update multiple entities

#### Delete Operations
- `delete(id: str)` - Delete single entity
- `batch_delete(ids: List[str])` - Delete multiple entities

### Database Session Management

The manager provides automatic database session management through ModelRegistry:
- Requires `model_registry` parameter as first constructor argument for database access and extensions
- Database access via `self.model_registry.DB.session()` for active sessions
- Provides `db` property for active session access via `self.model_registry.DB.session()`
- Model registry must be committed before use (`model_registry.is_committed()` must return True)
- Uses BLL models with automatic SQLAlchemy generation via `.DB(self.model_registry.DB.manager.Base)`
- Model access via `self.Model` property which applies registry transformations (`self.model_registry.apply(self._model)`)

### Target Entity Management

The manager provides lazy-loaded target entity access:
- `target_id`: Optional ID of the target entity for operations
- `target` property: Lazy-loaded target record, automatically retrieved on first access
- `target_user_id` property: Backward compatibility, returns `target_id` or requester ID
- Target can be manually set via the `target` setter

## Service Layer (`AbstractService`)

Background service abstraction for long-running tasks and periodic operations.

### Core Structure

```python
class AbstractService(ABC):
    def __init__(
        self,
        requester_id: str,
        db: Optional[Session] = None,
        interval_seconds: int = 60,
        max_failures: int = 3,
        retry_delay_seconds: int = 5,
        service_id: Optional[str] = None,
        **kwargs,
    )
```

### Lifecycle Management

- `start()` - Start the service
- `stop()` - Stop the service  
- `pause()` - Pause execution temporarily
- `resume()` - Resume from pause
- `run_service_loop()` - Main async loop
- `cleanup()` - Resource cleanup

### Error Handling

- Configurable `max_failures` before stopping
- `retry_delay_seconds` for failure recovery
- Automatic failure counting and reset
- Exception logging with stack traces

### Service Registry

Global registry for managing service instances:

```python
ServiceRegistry.register(service_id, service)
ServiceRegistry.unregister(service_id) 
ServiceRegistry.get(service_id)
ServiceRegistry.list()
ServiceRegistry.start_all()
ServiceRegistry.stop_all()
ServiceRegistry.cleanup_all()
```

## Model System

The framework uses a sophisticated model system with automatic generation and registry-based extension support.

### ModelMeta Metaclass

The `ModelMeta` metaclass automatically generates Reference and Network classes for models with ReferenceID:

```python
class ModelMeta(ModelMetaclass):
    """Metaclass that generates .Reference and .Network nested classes for models with ReferenceID."""
    
    def __new__(mcs, name, bases, namespace, **kwargs):
        cls = super().__new__(mcs, name, bases, namespace, **kwargs)
        
        # Only generate for models with ReferenceID (i.e., actual entity models)
        if hasattr(cls, "ReferenceID"):
            # Generate Reference class
            cls.Reference = mcs._create_reference_class(cls, name)
            cls.Reference.ID = cls.ReferenceID
            cls.Reference.ID.Optional = cls.ReferenceID.Optional
            
            # Generate Network class using NetworkMixin functionality
            def get_network(model_registry):
                return mcs._get_network_class(cls, model_registry)
            cls.Network = get_network
        
        return cls
```

### Base Mixins

- `ApplicationModel` - Core fields (id, created_at, created_by_user_id) with ModelMeta metaclass
- `UpdateMixinModel` - Update tracking (updated_at, updated_by_user_id)
- `ParentMixinModel` - Hierarchical relationships (parent_id)
- `NameMixinModel` - Name field with validation
- `DescriptionMixinModel` - Description field
- `ImageMixinModel` - Image URL field

### Model Structure Pattern

Each entity follows a consistent model structure with DatabaseMixin and NetworkMixin:

```python
class EntityModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    NameMixinModel.Optional,
    
    
    metaclass=ModelMeta,
):
    model_config = {"extra": "ignore", "populate_by_name": True}
    
    # Entity-specific fields
    description: Optional[str] = Field(None, description="Entity description")
    
    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = "Description of the entity table"
    seed_data: ClassVar[List[Dict[str, Any]]] = []  # Static seed data
    # OR dynamic seed data:
    # @classmethod
    # def seed_data(cls, model_registry=None) -> List[Dict[str, Any]]:
    #     return []
    
    class Create(BaseModel):
        # Fields required/allowed for creation
        
    class Update(BaseModel):
        # Fields allowed for updates
        
    class Search(ApplicationModel.Search):
        # Search criteria fields

# Automatically generated by ModelMeta metaclass:
# EntityModel.Reference - includes full model reference with ID fields
# EntityModel.Reference.ID - just the ID fields
# EntityModel.Reference.Optional - optional ID fields
```

### Search Models

Typed search criteria for different field types:

- `StringSearchModel` - inc (contains), sw (starts with), ew (ends with), eq (equals) operations
- `NumericalSearchModel` - lt (less than), gt (greater than), lteq (less than or equal), gteq (greater than or equal), neq (not equal), eq (equals) operations  
- `DateSearchModel` - before, after, on, eq operations
- `BooleanSearchModel` - eq (equals) operations

## Hook System

Extensible lifecycle hooks for entity operations with comprehensive context and execution control.

### Hook System Architecture

The framework provides a powerful hook system with:
- **Class-level hooks**: Apply to ALL methods of a manager class
- **Method-specific hooks**: Target individual methods using method references  
- **Timing control**: Execute before or after method execution
- **Priority ordering**: Control execution order with numeric priorities
- **Conditional execution**: Use conditions to control when hooks run
- **Context access**: Full access to manager state, arguments, and results

### Hook Context

The `HookContext` provides comprehensive access to the execution environment:

```python
class HookContext:
    def __init__(
        self,
        manager: "AbstractBLLManager",
        method_name: str,
        args: tuple,
        kwargs: dict,
        result: Any = None,
        timing: HookTiming = HookTiming.BEFORE,
    ):
        self.manager = manager
        self.method_name = method_name
        self.timing = timing
        self.args = list(args)  # Mutable for modification
        self.kwargs = kwargs.copy()  # Mutable for modification
        self.result = result
        self.skip_execution = False
        self.modified_result = None
        self.condition_data = {}
```

### Hook Registration

```python
# Class-level hook (applies to ALL methods)
@hook_bll(UserManager, timing=HookTiming.BEFORE, priority=5)
def audit_all_user_operations(context: HookContext) -> None:
    logger.info(f"User {context.manager.requester.id} executing {context.method_name}")

# Method-specific hook using method reference
@hook_bll(UserManager.create, timing=HookTiming.BEFORE, priority=10)
def validate_user_creation(context: HookContext) -> None:
    if context.kwargs.get('email', '').endswith('@blocked.com'):
        raise HTTPException(status_code=403, detail="Domain blocked")

# Conditional hook execution
@hook_bll(
    UserManager.update,
    timing=HookTiming.BEFORE,
    priority=15,
    condition=lambda ctx: 'email' in ctx.kwargs
)
def validate_email_changes(context: HookContext) -> None:
    # Only runs when email is being updated
    pass
```

### Hook Discovery and Auto-Registration

The hook system automatically:
- Sets up hook registries for each manager class via `__init_subclass__`
- Discovers hookable methods using `discover_hookable_methods()`
- Wraps methods with hook execution using `wrap_method_with_hooks()`
- Supports inheritance with parent registry chaining via `HookRegistry`
- Auto-populates `target_id` for get/update/delete operations
- Preserves hook-modified arguments that may not be in Pydantic schemas

## Search System

Advanced search abilities with transformers and filters:

### Search Transformers

Custom search logic for complex queries:

```python
def _register_search_transformers(self):
    self.register_search_transformer('custom_field', self._transform_custom_search)

def _transform_custom_search(self, value):
    # Return list of SQLAlchemy filter conditions
    return [Model.field.ilike(f"%{value}%")]
```

### Filter Building

Automatic filter generation from search parameters:
- Type-aware field processing
- String pattern matching (contains, starts with, ends with)
- Numerical comparisons  
- Date range filtering
- Boolean state filtering

### Relationship Loading

- `include` parameter for eager loading relationships
- `fields` parameter for selective field loading
- Automatic join generation with `generate_joins()`

### Pagination and Sorting

- `limit` and `offset` for pagination
- `sort_by` and `sort_order` for result ordering
- Integration with database query optimization 
