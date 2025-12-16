# Business Logic Layer Patterns

This document outlines BLL-specific patterns and conventions.

> **Common Patterns**: For CRUD model patterns, error handling, and configuration patterns shared across all layers, see [Framework.md](../Framework.md#common-patterns-across-layers).

## Manager Class Patterns

### Standard Manager Structure
```python
class EntityManager(AbstractBLLManager):
    _model = EntityModel  # Set the model class for the manager

    def __init__(
        self,
        model_registry,
        requester_id: str,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        parent: Optional[Any] = None,
    ) -> None:
        """
        Initialize EntityManager.
        
        Args:
            model_registry: ModelRegistry instance (required, first parameter)
            requester_id: ID of the user making the request
            target_id: ID of the target entity for operations
            target_team_id: ID of the target team
            parent: Parent manager for nested operations
        """
        super().__init__(
            model_registry=model_registry,
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            parent=parent,
        )
        # Initialize manager-specific properties
        self._child_manager = None

    @property
    def child_manager(self) -> "ChildManager":
        """Lazy-loaded child manager to avoid circular imports"""
        if self._child_manager is None:
            self._child_manager = ChildManager(
                model_registry=self.model_registry,
                requester_id=self.requester.id,
                target_id=self.target_id,
                target_team_id=self.target_team_id,
            )
        return self._child_manager
```

### Validation Patterns
```python
def create_validation(self, entity):
    """Override for custom creation validation"""
    # Check foreign key references using ModelRegistry pattern
    if entity.parent_id and not ParentEntity.DB(self.model_registry.DB.manager.Base).exists(
        requester_id=self.requester.id, 
        model_registry=self.model_registry,
        id=entity.parent_id
    ):
        raise HTTPException(status_code=404, detail="Parent entity not found")
    
    # Check business rules
    if entity.name and len(entity.name) < 2:
        raise HTTPException(status_code=400, detail="Name too short")

def search_validation(self, params):
    """Override for custom search validation"""
    # Validate search parameters
    if "team_id" in params and not params["team_id"]:
        raise HTTPException(status_code=400, detail="Team ID cannot be empty")
```

### Custom Methods Pattern
```python
def custom_business_action(self, entity_id: str, **kwargs) -> Dict[str, Any]:
    """Custom business logic methods follow this pattern"""
    # 1. Validate inputs
    entity = self.get(id=entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    
    # 2. Perform business logic
    result = self._perform_complex_operation(entity, **kwargs)
    
    # 3. Return structured response
    return {
        "success": True,
        "message": "Operation completed successfully",
        "data": result
    }
```

## Hook System Patterns

**For comprehensive hook system documentation, patterns, and examples, see [BLL.Hooks.md](./BLL.Hooks.md).**

The hook system provides powerful abilities for implementing cross-cutting concerns like auditing, security, performance monitoring, and business logic validation across BLL managers. The dedicated documentation covers:

- **Class-level hooks**: Apply to ALL methods of a manager class
- **Method-specific hooks**: Target individual methods using method references
- **Priority-based execution**: Control hook execution order
- **Conditional execution**: Use conditions to control when hooks run
- **Type safety**: Full type annotations and IDE support
- **Error handling patterns**: Critical vs non-critical hook strategies
- **Real-world examples**: Complete implementation guides

## Model Patterns

### Base Model Structure
```python
class EntityModel(ApplicationModel, NameMixinModel, UpdateMixinModel):
    """Main entity model with core fields"""
    description: Optional[str] = Field(None, description="Entity description")
    
    model_config = {"extra": "ignore", "populate_by_name": True}
    
    class ReferenceID:
        """Reference structure for foreign keys"""
        entity_id: str = Field(..., description="The ID of the related entity")
        
        class Optional:
            entity_id: Optional[str] = None
            
        class Search:
            entity_id: Optional[StringSearchModel] = None
    
    class Create(BaseModel, NameMixinModel):
        """Fields allowed/required for creation"""
        description: Optional[str] = Field(None, description="Entity description")
        
        @model_validator(mode="after")
        def validate_creation(self):
            """Custom validation for creation"""
            if self.name and len(self.name) < 2:
                raise ValueError("Name must be at least 2 characters")
            return self
    
    class Update(BaseModel, NameMixinModel.Optional):
        """Fields allowed for updates"""
        description: Optional[str] = Field(None, description="Entity description")
    
    class Search(ApplicationModel.Search, NameMixinModel.Search):
        """Search criteria fields"""
        description: Optional[StringSearchModel] = None
```

### Reference Model Pattern
```python
class EntityReferenceModel(EntityModel.ReferenceID):
    """Reference model for relationships"""
    entity: Optional[EntityModel] = None
    
    class Optional(EntityModel.ReferenceID.Optional):
        entity: Optional[EntityModel] = None
```

### Network Model Pattern
```python
class EntityNetworkModel:
    """API interaction models"""
    
    class POST(BaseModel):
        entity: EntityModel.Create
    
    class PUT(BaseModel):
        entity: EntityModel.Update
    
    class PATCH(BaseModel):
        """For partial updates with specific fields"""
        entity: EntityModel.Patch
    
    class SEARCH(BaseModel):
        entity: EntityModel.Search
    
    class ResponseSingle(BaseModel):
        entity: EntityModel
        
        @model_validator(mode="before")
        @classmethod
        def validate_partial_data(cls, data):
            """Handle partial data responses"""
            # Custom validation logic for responses
            return data
    
    class ResponsePlural(BaseModel):
        entities: List[EntityModel]
```

## Search Transformer Patterns

### Standard Search Transformers
```python
def _register_search_transformers(self):
    """Register custom search transformers"""
    self.register_search_transformer("name", self._transform_name_search)
    self.register_search_transformer("recent", self._transform_recent_search)
    self.register_search_transformer("overdue", self._transform_overdue_search)

def _transform_name_search(self, value):
    """Multi-field name search"""
    if not value:
        return []
    
    search_value = f"%{value}%"
    return [
        or_(
            Entity.first_name.ilike(search_value),
            Entity.last_name.ilike(search_value),
            Entity.display_name.ilike(search_value),
            Entity.username.ilike(search_value),
        )
    ]

def _transform_recent_search(self, hours):
    """Time-based search transformer"""
    if not hours or not isinstance(hours, int):
        hours = 24
    
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
    return [Entity.created_at >= cutoff_time]

def _transform_overdue_search(self, value):
    """Boolean search transformer"""
    if not value:
        return []
    
    now = datetime.now(timezone.utc)
    return [Entity.due_date < now, Entity.completed_at == None]
```

## Error Handling Patterns

See [Framework.md Error Handling Pattern](../Framework.md#error-handling-pattern) for standard status codes.

**BLL-Specific Error Handling:**
```python
def create_validation(self, entity):
    """BLL validation patterns"""
    # Foreign key validation → 404
    if entity.parent_id and not Parent.exists(...):
        raise HTTPException(status_code=404, detail="Parent not found")

    # Uniqueness validation → 409
    if Entity.exists(requester_id=self.requester.id, db=self.db, name=entity.name):
        raise HTTPException(status_code=409, detail="Name already in use")

    # Business rule validation → 400
    if not self._check_business_rule(entity):
        raise HTTPException(status_code=400, detail="Business rule violation")

def get(self, **kwargs) -> Any:
    """404 handling with ResourceNotFoundError"""
    entity = super().get(**kwargs)
    if entity is None:
        from endpoints.AbstractEndpointRouter import ResourceNotFoundError
        raise ResourceNotFoundError("entity", kwargs.get("id") or "unknown")
    return entity
```

## Batch Operation Patterns

### Batch Updates
```python
def batch_update(self, items: List[Dict[str, Any]]) -> List[Any]:
    """Pattern for batch operations with error collection"""
    results = []
    errors = []
    
    for item in items:
        try:
            entity_id = item.get("id")
            if not entity_id:
                raise ValueError("Missing required 'id' field")
            
            update_data = item.get("data", {})
            updated_entity = self.update(id=entity_id, **update_data)
            results.append(updated_entity)
            
        except Exception as e:
            errors.append({"id": item.get("id", "unknown"), "error": str(e)})
    
    if errors:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "One or more operations failed",
                "errors": errors,
                "successful_updates": len(results),
                "failed_updates": len(errors),
            }
        )
    
    return results
```

## Metadata Patterns

### Metadata Management
```python
def create(self, **kwargs):
    """Pattern for handling metadata alongside main entity"""
    # Separate metadata from model fields
    metadata_fields = {}
    model_fields = {}
    
    model_fields_set = set(self.Model.Create.__annotations__.keys())
    # Add mixin fields dynamically
    model_fields_set.update(["name", "description", "image_url"])
    
    for key, value in kwargs.items():
        if key in model_fields_set:
            model_fields[key] = value
        else:
            metadata_fields[key] = value
    
    # Create main entity
    entity = super().create(**model_fields)
    
    # Create metadata entries
    if metadata_fields and entity:
        for key, value in metadata_fields.items():
            self.metadata.create(
                entity_id=entity.id,
                key=key,
                value=str(value),
            )
    
    return entity

def get_metadata(self) -> Dict[str, str]:
    """Standard metadata retrieval pattern"""
    if not self.target_entity_id:
        raise HTTPException(status_code=400, detail="Entity ID is required")
    
    metadata_items = EntityMetadata.list(
        requester_id=self.requester.id,
        db=self.db,
        entity_id=self.target_entity_id
    )
    
    return {item.key: item.value for item in metadata_items}
```

## Circular Import Prevention

### Lazy Property Pattern
```python
def __init__(self, **kwargs):
    super().__init__(**kwargs)
    # Initialize to None to prevent circular imports
    self._child_manager = None
    self._related_manager = None

@property
def child_manager(self):
    """Lazy-loaded manager to avoid circular imports"""
    if self._child_manager is None:
        from logic.BLL_Child import ChildManager  # Import at runtime
        self._child_manager = ChildManager(
            model_registry=self.model_registry,
            requester_id=self.requester.id,
            target_id=self.target_id,
            target_team_id=self.target_team_id,
        )
    return self._child_manager
```

## Permission Validation Patterns

### Access Control
```python
def get(self, **kwargs) -> Any:
    """Pattern for permission checking in operations"""
    # Check read permissions
    if "team_id" in kwargs:
        if not self.DB.user_has_read_access(
            user_id=self.requester.id,
            team_id=kwargs.get("team_id"),
            db=self.db
        ):
            raise HTTPException(status_code=403, detail="Read access denied")
    
    return super().get(**kwargs)

def update(self, id: str, **kwargs):
    """Pattern for update permission checking"""
    # Get entity to check permissions
    entity = self.get(id=id)
    
    # Check write permissions
    if hasattr(entity, 'team_id') and entity.team_id:
        if not self.DB.user_has_write_access(
            user_id=self.requester.id,
            team_id=entity.team_id,
            db=self.db
        ):
            raise HTTPException(status_code=403, detail="Write access denied")
    
    return super().update(id, **kwargs)
```

## Runtime Discovery Patterns

### Extension/Provider Discovery
```python
@staticmethod
def list_runtime_providers():
    """Pattern for discovering available providers"""
    return ["OpenAI", "Anthropic", "LocalLLM"]

@staticmethod
def get_runtime_provider_options(provider_name):
    """Pattern for provider configuration options"""
    options_map = {
        "OpenAI": {"OPENAI_API_KEY": "", "OPENAI_MODEL": "gpt-4"},
        "Anthropic": {"ANTHROPIC_API_KEY": "", "ANTHROPIC_MODEL": "claude-3"},
        "LocalLLM": {"LOCAL_ENDPOINT": "http://localhost:8080"}
    }
    return options_map.get(provider_name, {})

@staticmethod
def list_runtime_extensions():
    """Pattern for discovering extensions from filesystem"""
    import glob
    import os
    from lib.Environment import env
    
    # Check environment variable first
    app_extensions = env("APP_EXTENSIONS")
    if app_extensions:
        return [ext.strip() for ext in app_extensions.split(",") if ext.strip()]
    
    # Fallback to filesystem discovery
    try:
        extensions_dir = os.path.join(os.path.dirname(__file__), "..", "extensions")
        extensions = []
        
        for ext_path in glob.glob(
            os.path.join(extensions_dir, "**", "EXT_*.py"), 
            recursive=True
        ):
            ext_name = os.path.splitext(os.path.basename(ext_path))[0]
            if ext_name.startswith("EXT_"):
                extensions.append(ext_name[4:])  # Remove EXT_ prefix
        
        return extensions
    except Exception:
        return []
```

## Best Practices

### Manager Design
1. **Single Responsibility** - Each manager handles one primary entity type
2. **Lazy Loading** - Use property decorators for child managers to prevent circular imports
3. **Consistent Validation** - Override `create_validation()` and `search_validation()` for custom rules
4. **Error Handling** - Use appropriate HTTP status codes and descriptive messages
5. **Metadata Support** - Implement metadata patterns for extensible entity data

### Model Design  
1. **Inheritance** - Use mixins for common field patterns
2. **Validation** - Implement `@model_validator` for complex validation rules
3. **Flexibility** - Use Optional fields and separate Create/Update/Search models
4. **Documentation** - Include field descriptions for API documentation

### Search Implementation
1. **Transformers** - Register custom search transformers for complex queries
2. **Type Safety** - Use typed search models (StringSearchModel, etc.)
3. **Performance** - Generate efficient SQLAlchemy filters
4. **Flexibility** - Support both simple and complex search parameters

### Hook Usage
1. **Decoration** - Use `@bll_hook` decorator for hook methods
2. **Discovery** - Hooks are automatically discovered during manager initialization
3. **Error Handling** - Hook errors should not prevent core operations
4. **Documentation** - Document hook behavior and expected parameters