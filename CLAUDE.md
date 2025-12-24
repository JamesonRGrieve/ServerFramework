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
- `*_test.py` - Test files

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

### Manager Pattern
```python
class EntityManager(AbstractBLLManager, RouterMixin):
    _model = EntityModel
    prefix: ClassVar[str] = "/v1/entity"

    def __init__(self, model_registry, requester_id: str, target_id: Optional[str] = None, ...):
        super().__init__(model_registry=model_registry, requester_id=requester_id, ...)
```

### Extension Pattern
```python
class EXT_MyExtension(AbstractStaticExtension):
    name: str = "my_extension"
    version: str = "1.0.0"
    _env: Dict[str, Any] = {"MY_EXT_KEY": ""}
    dependencies: Dependencies = Dependencies([...])

    @staticmethod
    @ability("my_ability")
    def my_ability(param: str) -> str: ...
```

### Hook Pattern
```python
# Class-level: applies to ALL methods
@hook_bll(UserManager, timing=HookTiming.BEFORE, priority=5)
def audit_all(context: HookContext): ...

# Method-specific: targets single method
@hook_bll(UserManager.create, timing=HookTiming.BEFORE, priority=10)
def validate_create(context: HookContext): ...
```

## Testing

### Running Tests
```bash
source ./.venv.linux/bin/activate && python -m pytest <path> -v --lf
```

### Test Pattern
```python
class TestEntity(AbstractBLLTest):
    class_under_test = EntityManager
    create_fields = {"name": "Test"}
    update_fields = {"name": "Updated"}
    # Real database, no mocks, comprehensive coverage
```

## Development Commands
- Start: `python src/app.py`
- Tests: `pytest` (markers: `-m db`, `-m bll`, `-m ep`, `-m auth`)
- Format: `black src/`
- Type check: `mypy src/`

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
