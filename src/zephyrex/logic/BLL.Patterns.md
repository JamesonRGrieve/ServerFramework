# Business Logic Layer Patterns

This document outlines BLL-specific patterns and conventions.

> **Common Patterns**: For CRUD model patterns, error handling, and configuration patterns shared across all layers, see [Framework.md](../Framework.md#common-patterns-across-layers).

## File Organization

**Important**: Pydantic models and their corresponding managers are defined together in the same `BLL_*.py` file. This co-location ensures:
- Single source of truth for entity definition and business logic
- Easier maintenance and navigation
- Clear relationship between data structure and operations

For example, `BLL_Auth.py` contains both `UserModel` (Pydantic schema) and `UserManager` (business logic), while `BLL_Providers.py` contains `ProviderModel`, `ProviderManager`, `ProviderInstanceModel`, `ProviderInstanceManager`, etc.

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
        from zephyrex.endpoints.AbstractEndpointRouter import ResourceNotFoundError
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
        from zephyrex.logic.BLL_Child import ChildManager  # Import at runtime
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
    from zephyrex.lib.Environment import env
    
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

## Provider Scope and Quota

Provider instances carry `scope: Literal["root", "system", "team", "user"]`. The four scopes are not interchangeable.

- **Root.** SaaS-owned, framework-internal use only. Users cannot access or invoke a root provider in custom manners. Reached only via direct lookup from framework code (`EXT_Email.get_root_instance(ability="system_notification")`); the user-context resolver never returns a root instance. Root invocation is audit-logged with the calling code path.
- **System.** SaaS-owned, included in the subscription. Users invoke system providers like their own credentials, but the credentials underneath are SaaS-issued and the usage is metered against the user's included quota.
- **Team.** Team-owned credentials, used by any member of the team.
- **User.** User-owned credentials.

System, team, and user providers all share the same quota infrastructure. What changes between them is where the credentials come from and who effectively pays for them; what does not change is how usage is tracked.

Resolution flow for a user-invokable call inside `bond_instance`: walk `user → team → system` for an instance matching this extension and ability; first match wins; root is never inspected on this path. On match, check quota for `(user_id, team_id, ability)`; if exhausted, raise `QuotaExhaustedError(scope, ability, period)`. Atomically decrement quota, then bond and proceed. If nothing resolves at any user-invokable scope, raise `NoProviderInstanceError(requester, ability)` — typed, with no silent fallback to root.

A single `Quota` table serves all three user-invokable scopes:

```python
class Quota(ApplicationModel, DatabaseMixin):
    user_id: Optional[str]                # NULL = team-wide quota
    team_id: Optional[str]                # NULL = user-scoped quota outside any team
                                          # both populated = this user's allotment within this team
    ability: str                          # canonical ability name
    period: Literal["minute","hour","day","month","billing_cycle"]
    period_key: str                       # e.g. "2026-04" or "2026-04-28T15:00"
    limit: int                            # 0 = blocked, sentinel for unlimited as appropriate
    consumed: int
    unit: Literal["call","token","byte","message","row"]
    qualifier: Optional[dict]             # e.g. {"model": "gpt-4-turbo"} for nested quotas
```

Semantics:

- `user_id` populated, `team_id` NULL — quota that belongs to a user across all of their contexts.
- `team_id` populated, `user_id` NULL — quota that belongs to a team, shared across its members.
- Both populated — per-user-within-team quota, allowing a team to partition its overall quota among its members.

The framework consumes from quota; it does not decide the limit values. Limit population is the responsibility of whoever owns the budget (the subscription extension writes system-tier limits when a user upgrades, team admins write team-level partitioning, etc.). When multiple quota rows match a single request, the framework decrements all matching rows and refuses the request if any of them is exhausted. Atomic decrement runs on the `DistributedCounter` primitive with `UPDATE ... WHERE consumed + ? <= limit RETURNING` semantics — a no-op when exhausted, surfacing as the typed error.

Nested quotas are routinely structured as an overall ceiling with per-model sub-ceilings (e.g. "this team has 1M tokens per month overall, of which at most 200K against `gpt-4-turbo`"). Quota rows declare their dimension via the optional `qualifier: dict`; rows without a qualifier match all calls to that ability. A single call debits every matching row in one transaction so partial debits are impossible. AI/LLM providers integrate the pre-estimate / post-true-up pattern automatically: a conservative upper-bound estimate pre-decrements before the call; the actual token count reconciles after, crediting back any over-estimate or surfacing `QuotaOverrunWarning` on under-estimate.

System-scoped provider instances are unreachable to a user unless a quota row exists permitting their use. No user can accidentally email from the SaaS's brand SendGrid, charge to the SaaS's Stripe account, or consume the included AI tier without an explicit quota allowance recorded.

## Field-Level Access Control

Pydantic field metadata captures field-level grants. A field marked `Field(..., requires=["payment.invoice.read_lines"])` is included in serialized output only when the requester has the named permission. The serialization layer applies the grant check at response time, replacing disallowed fields with a sentinel (or omitting them, configurable per deployment). Search and update operations honor the same grants — a user without `payment.invoice.write_lines` cannot update line items even if they can update the invoice's other fields.

The same metadata applies to GraphQL: the resolver for a marked field returns null with a typed error attached when the requester lacks the grant, preserving the partial-data partial-errors contract.

Precedence: row-level access controls visibility of the record at all; field-level filters which fields appear once the record is visible. A typed `Sensitive[T]` field annotation can replace the more verbose `Field(..., requires=...)` for the common case. Performance: applying field-level grants on a 10k-row list response is costly if the check runs per record; the framework computes the allowed-field set once per `(manager, requester)` at request bind and caches it for the request's lifetime, so the per-record cost is a single dictionary lookup. Restricted fields cannot be used for `ORDER BY` or filtering by requesters lacking the grant — both are rejected at request validation, since ordering by a restricted field leaks its values through inference attacks just as much as direct read does.

The grant string is the same string used for OAuth scopes and database-backed roles — one canonical permission name serves as scope, role grant, and field-level gate.
4. **Documentation** - Document hook behavior and expected parameters