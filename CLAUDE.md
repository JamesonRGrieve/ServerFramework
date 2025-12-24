# ServerFramework - AI Agent Guidelines

## Core Philosophy

**Pydantic-First Architecture**: Single source of truth. Pydantic models auto-generate SQLAlchemy database models, API schemas, and documentation. Define once, propagate everywhere.

**Extension-Only Development**: All custom implementations go in `extensions/` directory. Never modify `src/lib/`, `src/database/`, `src/logic/`, `src/endpoints/`. This enables conflict-free framework updates.

**No-Mock Testing**: Real implementations with isolated database instances. Each extension permutation gets its own database. Never mock BLL managers, endpoint handlers, or extension functionality.

**RORO Pattern**: All methods follow "Receive Object, Return Object" for consistency and type safety.

## Absolute Rules

### ALWAYS
- Use required positional parameters without defaults for functionality that won't work without them
- Update accompanying `_test.py` files with comprehensive tests (NO MOCKS) when modifying functionality
- Update relevant `.md` documentation in the same directory when changing code
- Write concise code (avoid obvious comments, use one-liners where possible)
- Ask clarifying questions before implementing when requirements are unclear
- Handle errors at the beginning of functions with early raises using FastAPI HTTPExceptions
- Use UUID primary keys throughout (String for SQLite, UUID for PostgreSQL)

### NEVER
- Make assumptions or respond with "is likely", "probably", or "might be"
- Use frame-local or thread-local variables instead of passing data via parameters
- Skip a failing test instead of fixing the root issue
- Keep broken functionality as a fallback instead of implementing proper functionality
- Re-implement existing functionality elsewhere to bypass it
- Use bandaid fixes instead of fixing core functionality
- Mock business logic or integration tests

## Syntax Standards

### Python Imports
```python
# CORRECT: Import datetime children directly
from datetime import date, datetime, timedelta

# WRONG: Never do this
import datetime
datetime.date  # NO

# CORRECT: Relative to ./src
from logic.BLL_Auth import UserManager

# WRONG: Never include src prefix
from src.logic.BLL_Auth import UserManager  # NO
```

## Architecture Quick Reference

### Layered Structure
```
src/
├── lib/          # Foundation utilities, configuration, model management
├── database/     # DatabaseManager, migrations, seeding
├── logic/        # BLL managers, Pydantic models, services
├── endpoints/    # EP routers (abstracted via RouterMixin)
├── extensions/   # All custom implementations here
└── sdk/          # Client SDK
```

### File Naming
- `BLL_*.py` - Business logic managers + Pydantic models (co-located)
- `EXT_*.py` - Extension definitions
- `PRV_*.py` - Provider implementations + external models
- `SVC_*.py` - Background services
- `*_test.py` - Test files

### Base Mixins
- `ApplicationModel` - id, created_at, created_by_user_id
- `UpdateMixinModel` - updated_at, updated_by_user_id, deleted_at, deleted_by_user_id
- `NameMixinModel` - name field with validation
- `ParentMixinModel` - parent_id for hierarchical relationships
- `ImageMixinModel` - image_url field
- `DatabaseMixin` - provides `.DB(base)` method for SQLAlchemy generation

### Model Pattern (BLL-First)
```python
class EntityModel(ApplicationModel, DatabaseMixin, metaclass=ModelMeta):
    """Define once - SQLAlchemy, API schemas auto-generated."""
    field: Optional[str] = Field(description="Description")

    table_comment: ClassVar[str] = "Table description"
    seed_data: ClassVar[List[Dict]] = []  # Or @classmethod

    class Create(BaseModel): ...
    class Update(BaseModel): ...
    class Search(ApplicationModel.Search): ...

    class ReferenceID:
        entity_id: str = Field(...)
        class Optional: entity_id: Optional[str] = None
        class Search: entity_id: Optional[StringSearchModel] = None

# Access SQLAlchemy model
Entity = EntityModel.DB(model_registry.DB.manager.Base)
```

### Network Model Pattern (Auto-generated, customize if needed)
```python
class EntityNetworkModel:
    class POST(BaseModel):
        entity: EntityModel.Create
    class PUT(BaseModel):
        entity: EntityModel.Update
    class SEARCH(BaseModel):
        entity: EntityModel.Search
    class ResponseSingle(BaseModel):
        entity: EntityModel
    class ResponsePlural(BaseModel):
        entities: List[EntityModel]
```

### Search Models
- `StringSearchModel` - inc (contains), sw (starts with), ew (ends with), eq (equals)
- `NumericalSearchModel` - lt, gt, lteq, gteq, neq, eq
- `DateSearchModel` - before, after, on, eq
- `BooleanSearchModel` - eq

### Manager Pattern
```python
class EntityManager(AbstractBLLManager, RouterMixin):
    _model = EntityModel
    prefix: ClassVar[str] = "/v1/entity"

    def __init__(self, model_registry, requester_id: str, target_id: Optional[str] = None, ...):
        super().__init__(model_registry=model_registry, requester_id=requester_id, ...)
        self._child_manager = None  # Lazy load to prevent circular imports

    @property
    def child_manager(self) -> "ChildManager":
        """Lazy-loaded to avoid circular imports."""
        if self._child_manager is None:
            from logic.BLL_Child import ChildManager
            self._child_manager = ChildManager(model_registry=self.model_registry, ...)
        return self._child_manager

    def create_validation(self, entity):
        """Override for custom validation before create."""
        if not entity.name:
            raise HTTPException(status_code=400, detail="Name required")

    def _register_search_transformers(self):
        """Register custom search logic."""
        self.register_search_transformer("name", self._transform_name_search)
```

### Extension Pattern
```python
class EXT_MyExtension(AbstractStaticExtension):
    name: str = "my_extension"
    version: str = "1.0.0"
    _env: Dict[str, Any] = {"MY_EXT_KEY": ""}
    dependencies: Dependencies = Dependencies([
        EXT_Dependency(name="core", reason="Core functionality"),
        PIP_Dependency(name="requests", semver=">=2.28.0", reason="HTTP"),
    ])

    @staticmethod
    @ability("my_ability")
    def my_ability(param: str) -> str: ...
```

### Extending Existing Models (Extensions Only)
```python
from lib.Pydantic2SQLAlchemy import extension_model

@extension_model(UserModel)
class MyExtension_UserModel:
    """Injects fields into UserModel."""
    custom_field: Optional[str] = Field(None, description="Extension field")

    class Create:
        custom_field: Optional[str] = None
    class Update:
        custom_field: Optional[str] = None
```

### Provider Pattern
```python
class PRV_MyProvider_MyExtension(EXT_MyExtension.AbstractProvider):
    name: str = "my_provider"
    _env: Dict[str, Any] = {"MY_PROVIDER_API_KEY": ""}

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return MyProviderInstance(instance)

    @ability("provider_action")
    def provider_action(self, **kwargs) -> Dict[str, Any]: ...
```

### Hook Pattern
```python
# Class-level: applies to ALL methods
@hook_bll(UserManager, timing=HookTiming.BEFORE, priority=5)
def audit_all(context: HookContext):
    logger.info(f"{context.method_name} by {context.manager.requester.id}")

# Method-specific: targets single method
@hook_bll(UserManager.create, timing=HookTiming.BEFORE, priority=10)
def validate_create(context: HookContext):
    context.kwargs['processed'] = True  # Modify args

# Conditional hook
@hook_bll(UserManager.update, timing=HookTiming.AFTER, priority=20,
          condition=lambda ctx: 'email' in ctx.kwargs)
def on_email_change(context: HookContext):
    send_verification(context.result.email)
```

### Hook Priority Ranges
- **1-10**: Critical (security, validation) - runs first
- **11-20**: Business logic
- **21-30**: Data enrichment/transformation
- **31-40**: Logging/audit (non-critical)
- **41-50**: Performance monitoring
- **90+**: Cleanup (AFTER hooks)

### HookContext Properties
- `context.manager` - BLL manager instance
- `context.method_name` - "create", "update", etc.
- `context.args` / `context.kwargs` - mutable method arguments
- `context.result` - method result (AFTER hooks only)
- `context.timing` - HookTiming.BEFORE or AFTER
- `context.skip_method()` - skip original method (BEFORE only)
- `context.set_result(value)` - override return value

### Service Pattern
```python
class MyService(AbstractService):
    def __init__(self, requester_id: str, interval_seconds: int = 60, ...):
        super().__init__(requester_id=requester_id, interval_seconds=interval_seconds, ...)

    async def execute(self):
        """Called each interval."""
        ...
```

## Testing

### Running Tests
```bash
source ./.venv.linux/bin/activate && python -m pytest <path> -v --lf
```

### Test Markers
- `-m db` - Database tests
- `-m bll` - Business logic tests
- `-m ep` - Endpoint tests
- `-m auth` - Authentication tests

### BLL Test Pattern
```python
class TestEntity(AbstractBLLTest):
    class_under_test = EntityManager
    create_fields = {"name": "Test"}
    update_fields = {"name": "Updated"}
    # Real database, no mocks
```

### Endpoint Test Pattern
```python
class TestEntityEndpoints(AbstractEPTest):
    base_endpoint = "entity"
    entity_name = "entity"
    required_fields = ["name"]
    create_fields = {"name": lambda: f"Test {uuid4()}"}
    update_fields = {"name": "Updated"}
```

### Extension Test Pattern
```python
class TestMyExtension(AbstractEXTTest):
    extension_class = EXT_MyExtension
    test_config = AbstractEXTTest.full_config(expected_abilities={"my_ability"})
    # Uses isolated database: test.{extension_name}.database.db
```

### Provider Test Pattern
```python
class TestMyProvider(AbstractPRVTest):
    provider_class = PRV_MyProvider_MyExtension
    test_config = AbstractPRVTest.full_config(
        expected_abilities={"provider_action"},
        expected_services={"service_name"}
    )
    # Inherits parent extension's test environment
```

## Development Commands
- Start: `python src/app.py`
- Tests: `pytest`
- Format: `black src/`
- Type check: `mypy src/`

## Seeding & Provider Rotation

### Seed Data Pattern
```python
class EntityModel(...):
    seed_data: ClassVar[List[Dict]] = [
        {"id": env("ROOT_ID"), "name": "Root Entity"},
    ]
    # OR dynamic:
    @classmethod
    def seed_data(cls, model_registry=None) -> List[Dict]:
        return [...]
```

### Root Rotation Access
```python
class EXT_MyExtension(AbstractStaticExtension):
    @classproperty
    def root(cls) -> Optional[RotationManager]:
        """Auto-discovered root rotation for this extension."""
        ...

# Usage
result = EXT_MyExtension.root.rotate(ExternalModel.create_via_provider, **kwargs)
```

## Authentication & Authorization
- **JWT-based authentication** with root API key for mutation of system entities
- **Role-based permissions** with team-scoped role hierarchies
- **System entities** require root API key for write operations
- **User context** automatically injected into all BLL operations

## Database Operations
- Migrations applied automatically on startup
- Multi-database support: PostgreSQL, SQLite, MariaDB, MSSQL, Vector databases
- Each DatabaseManager instance has its own declarative base

## Error Handling
- 400: Validation errors, malformed requests
- 401: Authentication required/invalid
- 403: Insufficient permissions
- 404: Resource not found
- 409: Resource conflict (duplicate, constraint violation)
- 500: Internal server errors

## Key Principles
1. **Single Source of Truth**: Pydantic models drive the entire stack
2. **Extension Isolation**: Custom code only in extensions/
3. **Real Testing**: No mocks for BLL/integration tests
4. **Early Failure**: Handle errors at function start, close to database
5. **Lazy Loading**: Child managers via @property to avoid circular imports
6. **Permission Enforcement**: SQL-level filtering for security
7. **Hook System**: Cross-cutting concerns via before/after hooks

## Documentation Philosophy
Write documentation optimized for AI and autistic/ADHD humans: concise architectural summaries with minimal code snippets, sufficient to reconstruct code with 95% accuracy.
