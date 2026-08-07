# Database Patterns

This document describes database-specific patterns and implementation details.

> **Common Patterns**: For CRUD model patterns, error handling, and configuration patterns shared across all layers, see [Framework.md](../Framework.md#common-patterns-across-layers).

## Overview
The database layer implements a Pydantic-first architecture with automatic SQLAlchemy generation through the `DatabaseMixin` pattern. This approach, managed by `DatabaseManager.py`, provides enterprise-grade database management with developer-friendly patterns for rapid development.

## Production Architecture: Pydantic-First Approach

### Current Implementation Strategy
The system implements a Pydantic-first architecture designed for enterprise scalability:

1. **Pydantic Models with DatabaseMixin**: Business models automatically generate SQLAlchemy models via `.DB` property
2. **AbstractDatabaseEntity**: Enterprise mixin system (`BaseMixin`, `UpdateMixin`, `ParentMixin`, etc.) for common patterns
3. **DatabaseManager**: Configurable database manager providing multi-database support and session management
4. **with_session Decorator**: Unified session management for BLL operations
5. **Extension System Integration**: Seamless integration with extension-based architecture
6. **ModelRegistry Integration**: Database operations integrated with model registry system

### Pattern Overview
```python
# Pydantic-first approach with DatabaseMixin
from zephyrex.logic.AbstractLogicManager import ApplicationModel, DatabaseMixin

class UserModel(ApplicationModel, DatabaseMixin):
    """User model - Pydantic definition that generates SQLAlchemy model"""
    email: str = Field(..., description="User email address")
    password_hash: Optional[str] = Field(None, description="Password hash")

    # Table configuration
    table_comment: ClassVar[str] = "User accounts"

    # The .DB() classmethod automatically creates SQLAlchemy model with:
    # - UUID primary key (String type for SQLite compatibility)
    # - created_at, created_by_user_id timestamps
    # - All Pydantic fields converted to SQLAlchemy columns

# Access the generated SQLAlchemy model
User = UserModel.DB(db_manager.Base)  # Generated SQLAlchemy model
```

## Core Database Patterns

### Mixin Composition Pattern
**Purpose**: Compose database entity functionality through multiple inheritance.

```python
# BaseMixin - Core entity functionality  
class BaseMixin:
    system = False
    seed_list = []
    
    @declared_attr
    def id(cls):
        return Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    
    @declared_attr  
    def created_at(cls):
        return Column(DateTime, default=func.now())
        
    @declared_attr
    def created_by_user_id(cls):
        return Column(String, nullable=True)

# UpdateMixin - Update and delete functionality
class UpdateMixin:
    @declared_attr
    def updated_at(cls):
        return Column(DateTime, default=func.now(), onupdate=func.now())
        
    @declared_attr
    def deleted_at(cls):
        return Column(DateTime, default=None)
```

**Benefits:**
- Modular functionality composition
- Consistent patterns across all models
- Automatic column generation
- Hook system integration

### Reference Creation Pattern
**Purpose**: Standardized foreign key relationships with automatic constraint naming.

```python
class BaseMixin:
    @classmethod
    def create_foreign_key(cls, target_entity, db_manager=None, **kwargs):
        """Create a foreign key column with standardized naming"""
        constraint_name = kwargs.pop("constraint_name", None)
        if not constraint_name:
            constraint_name = f"fk_{cls.__tablename__}_{target_entity.__tablename__}_id"
        
        fk = ForeignKey(f"{target_entity.__tablename__}.id", 
                       ondelete=kwargs.pop("ondelete", None), 
                       name=constraint_name)
        
        pk_type = db_manager.PK_TYPE if db_manager else String
        return Column(pk_type, fk, **kwargs)

# Usage in entity models
class Document(Base, BaseMixin, UpdateMixin):
    __tablename__ = "documents"
    title = Column(String, nullable=False)
    
    # Standardized foreign key creation
    user_id = BaseMixin.create_foreign_key(User, nullable=False)
    team_id = BaseMixin.create_foreign_key(Team, nullable=True)
```

**Benefits:**
- Consistent foreign key naming conventions
- Automatic constraint naming
- Database-specific primary key type handling
- Nullable variant support

### Reference Model Factory Pattern
**Purpose**: Create strongly-typed foreign key relationships dynamically.

```python
class UserModel:
    class ReferenceID:
        user_id: str = Field(..., description="The ID of the related user")
        
        class Optional:
            user_id: Optional[str] = None
            
        class Search:
            user_id: Optional[StringSearchModel] = None

# Usage in other models
class DocumentModel(
    ApplicationModel.Optional,
    UserModel.ReferenceID,        # Required user reference
    TeamModel.ReferenceID.Optional,  # Optional team reference
    
):
    title: Optional[str] = Field(description="Document title")
```

**Generated SQLAlchemy:**
```python
# Automatically creates:
class Document(Base, BaseMixin, UserRefMixin, TeamRefMixin.Optional):
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    team_id = Column(String, ForeignKey("teams.id"), nullable=True)
    user = relationship("User", backref="documents")
    team = relationship("Team", backref="documents")
```

**Benefits:**
- Type-safe foreign key relationships
- Consistent naming conventions across models
- Automatic SQLAlchemy relationship generation
- Built-in nullable variants

### CRUD Model Pattern

See [Framework.md CRUD Model Pattern](../Framework.md#crud-model-pattern) for the common pattern used across all layers.

**Database-Specific Implementation:**
```python
class UserModel:
    # Base model with DatabaseMixin for SQLAlchemy generation
    email: Optional[str] = Field(description="User's email address")

    # Inherits Create, Update, Search from parent or defines them
    # DatabaseMixin automatically generates SQLAlchemy model via .DB() classmethod
```

**Database Benefits:**
- Automatic SQLAlchemy model generation from Pydantic via DatabaseMixin
- Search operators map to SQL WHERE clauses
- Migration generation from model changes

### Network Model Pattern
**Purpose**: Define API request/response structures.

```python
class UserNetworkModel:
    class POST(BaseModel):
        user: UserModel.Create
        
    class PUT(BaseModel):
        user: UserModel.Update
        
    class SEARCH(BaseModel):
        user: UserModel.Search
        
    class ResponseSingle(BaseModel):
        user: UserModel
        
        @model_validator(mode="before")
        @classmethod
        def validate_partial_data(cls, data):
            # Handle partial responses gracefully
            return data
            
    class ResponsePlural(BaseModel):
        users: List[UserModel]
```

**Benefits:**
- Consistent API structure
- Request/response separation
- Automatic OpenAPI generation
- Validation at API boundaries

## Database Generation Patterns

### Automatic Type Mapping Pattern
**Purpose**: Convert Pydantic types to SQLAlchemy columns automatically.

```python
def _create_column_from_field(name: str, field_type: Any, field_info: Any = None) -> Optional[Column]:
    """Convert Pydantic field to SQLAlchemy column"""
    
    # Extract nullable from Optional[T]
    nullable = False
    if hasattr(field_type, "__origin__") and field_type.__origin__ is Union:
        args = field_type.__args__
        if len(args) == 2 and type(None) in args:
            nullable = True
            field_type = next(arg for arg in args if arg is not type(None))
    
    # Map Pydantic types to SQLAlchemy types
    type_mapping = {
        str: String,
        int: Integer,
        bool: Boolean,
        datetime: DateTime,
        float: Float,
    }
    
    sqlalchemy_type = type_mapping.get(field_type, String)
    return Column(sqlalchemy_type, nullable=nullable, comment=field_info.description)
```

**Benefits:**
- No manual column definitions
- Consistent type mapping
- Automatic nullable handling
- Comment generation from descriptions

### Relationship Generation Pattern
**Purpose**: Create SQLAlchemy relationships from reference patterns.

```python
def _process_reference_fields(pydantic_model, class_dict, existing_columns, tablename):
    """Generate foreign keys and relationships from reference model patterns"""
    
    for base in pydantic_model.__bases__:
        if hasattr(base, '__annotations__'):
            for field_name, field_type in base.__annotations__.items():
                if field_name.endswith('_id'):
                    # Extract entity name from field (user_id -> User)
                    entity_name = field_name[:-3]
                    target_entity = get_entity_class_name(entity_name)
                    
                    if target_entity and field_name not in existing_columns:
                        # Create foreign key
                        nullable = 'Optional' in str(base)
                        class_dict[field_name] = Column(
                            PK_TYPE, 
                            ForeignKey(f"{tablename}.id"),
                            nullable=nullable
                        )
                        
                        # Create relationship
                        class_dict[entity_name] = relationship(
                            target_entity,
                            backref=f"{tablename}s"
                        )
```

**Benefits:**
- Automatic foreign key creation
- Consistent relationship naming
- Backref generation
- Nullable variant support

### Validation Translation Pattern
**Purpose**: Convert Pydantic validators to SQLAlchemy validators.

```python
def _translate_pydantic_validators(pydantic_model, class_dict):
    """Convert Pydantic model validators to SQLAlchemy @validates decorators"""
    
    # Extract model validators
    if hasattr(pydantic_model, '__pydantic_validators__'):
        for validator_name, validator_func in pydantic_model.__pydantic_validators__.items():
            if validator_name.startswith('validate_'):
                field_name = validator_name[9:]  # Remove 'validate_' prefix
                
                # Create SQLAlchemy validator
                @validates(field_name)
                def validate_field(self, key, value):
                    # Apply original Pydantic validation logic
                    return validator_func(value)
                    
                class_dict[validator_name] = validate_field
```

**Benefits:**
- Consistent validation across API and database
- No duplicate validation logic
- Automatic validator registration
- Error message consistency

## Database Management Integration Patterns

### Enterprise Database Manager Pattern
**Purpose**: Production-grade database management with multi-process support and comprehensive session handling via dependency injection.

```python
# Get database manager via dependency injection (e.g., from app.state.model_registry)
db_manager = app.state.model_registry.database_manager

# Parent process initialization
db_manager.init_engine_config()

# Worker process initialization
db_manager.init_worker()

# Each manager instance maintains isolated declarative base
Base = db_manager.Base

# Generated models use manager-specific base
class UserModel(DatabaseMixin):
    email: Optional[str] = Field(description="User's email address")

# Lazy generation with caching - always pass declarative Base
User = UserModel.DB(db_manager.Base)
```

**Benefits:**
- Database-specific declarative bases
- Consolidated configuration management
- Dependency injection for testability
- Isolated testing support

### Isolated Database Instance Pattern
**Purpose**: Create separate database instances for testing and multi-tenancy.

```python
# Production database
prod_db = self.db_manager
prod_db.init_engine_config()

# Test database with prefix
test_db = DatabaseManager(
    db_prefix="test.integration",
    test_connection=True
)

# Each has independent base and configuration
prod_base = prod_db.Base
test_base = test_db.Base

# Models generated against specific instance
class UserModel(DatabaseMixin):
    # Can be used with any database instance
    pass

# Access database-specific properties
prod_pk_type = prod_db.PK_TYPE  # UUID for PostgreSQL
test_pk_type = test_db.PK_TYPE  # String for SQLite
```

**Benefits:**
- Independent database environments
- Isolated testing capabilities
- Multi-tenant support
- Configuration flexibility

## Manager Integration Patterns

### with_session Decorator Pattern
**Purpose**: Unified session management for all database operations.

```python
@with_session
def create(
    cls: Type[T],
    requester_id: str,
    model_registry,
    return_type: Literal["db", "dict", "dto", "model"] = "dict",
    **kwargs,
) -> T:
    """Create a new database entity with automatic session management."""
    # Extract db and db_manager from kwargs (injected by decorator)
    db = kwargs.pop("db")
    db_manager = kwargs.pop("db_manager")
    
    # Session and database manager are automatically provided
    # Automatic commit/rollback handling
    entity = cls(**kwargs)
    db.add(entity)
    # Commit handled by decorator
    
    return db_to_return_type(entity, return_type)
```

### Manager-Model Coupling Pattern
**Purpose**: Tight integration between BLL managers and generated models with ModelRegistry.

```python
class UserManager(AbstractBLLManager):
    Model = UserModel                    # Pydantic model for validation
    ReferenceModel = UserReferenceModel  # Reference patterns
    NetworkModel = UserNetworkModel      # API models
    
    def create(self, **kwargs):
        # Use model registry for database operations
        return self.Model.DB.create(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            **kwargs
        )
```

**Benefits:**
- Single model definition drives everything
- Type safety from BLL to database
- Automatic validation integration
- Clear separation of concerns

### Permission Integration Pattern
**Purpose**: Seamless permission checking with generated models.

```python
class AbstractBLLManager:
    def __init__(self, requester_id: str, **kwargs):
        self.requester = self.Model.DB.get(
            requester_id=env("ROOT_ID"),
            db=self.db,
            id=requester_id
        )
        
    def get(self, **kwargs):
        # Permission checking uses generated SQLAlchemy model
        return self.DB.get(
            requester_id=self.requester.id,
            db=self.db,
            return_type="dto",
            override_dto=self.Model,  # Return as Pydantic model
            **kwargs
        )
```

**Benefits:**
- Consistent permission patterns
- Automatic model conversion
- Type-safe returns
- Unified error handling

## Performance Patterns

### Lazy Model Generation Pattern
**Purpose**: Generate SQLAlchemy models only when needed using the `DatabaseMixin.DB()` classmethod.

```python
class DatabaseMixin:
    """Mixin for Pydantic models that provides access to SQLAlchemy models."""

    @classmethod
    def DB(cls, declarative_base):
        """
        Get or create the SQLAlchemy model for this Pydantic model.
        Models are cached per declarative base to prevent regeneration.

        Args:
            declarative_base: The SQLAlchemy declarative base to use

        Returns:
            The SQLAlchemy model class corresponding to this Pydantic model
        """
        # Create a registry key based on the model and declarative base
        registry_key = f"{cls.__module__}.{cls.__name__}"

        # Check if we already have this model cached in the declarative base
        if hasattr(declarative_base, "_pydantic_models"):
            if registry_key in declarative_base._pydantic_models:
                return declarative_base._pydantic_models[registry_key]

        # Generate the SQLAlchemy model and cache it
        sqlalchemy_model = create_sqlalchemy_model(
            cls,
            model_registry,
            tablename=None,        # Optional: override auto-generated table name
            table_comment=None,    # Optional: custom table comment
            base_model=declarative_base
        )
        declarative_base._pydantic_models[registry_key] = sqlalchemy_model

        return sqlalchemy_model
```

**Usage:**
```python
# Access the SQLAlchemy model via the DB() classmethod
User = UserModel.DB(db_manager.Base)
```

**Benefits:**
- Models generated only when needed
- Caching per declarative base prevents regeneration
- Supports multiple database instances with isolated caches
- Fast subsequent access via registry lookup

### Dependency Resolution Pattern
**Purpose**: Handle model dependencies during generation.

```python
def _analyze_model_dependencies(bll_models):
    """Analyze and resolve model dependencies for correct generation order"""
    
    dependencies = {}
    for model in bll_models:
        deps = []
        for base in model.__bases__:
            if hasattr(base, '__annotations__'):
                for field_name, field_type in base.__annotations__.items():
                    if field_name.endswith('_id'):
                        entity_name = field_name[:-3]
                        target_model = get_target_model(entity_name)
                        if target_model:
                            deps.append(target_model)
        dependencies[model] = deps
    
    # Topological sort for generation order
    return topological_sort(dependencies)
```

**Benefits:**
- Correct generation order
- Handles circular references
- Prevents missing dependencies
- Deterministic model creation

## DatabaseMixin Pattern

### Automatic Model Generation
**Purpose**: Generate SQLAlchemy models automatically from Pydantic definitions.

```python
# Define Pydantic model with DatabaseMixin
class UserModel(ApplicationModel, DatabaseMixin):
    email: str = Field(..., description="User email")
    display_name: Optional[str] = Field(None, description="Display name")

    table_comment: ClassVar[str] = "User accounts"

# Access generated SQLAlchemy model
User = UserModel.DB(db_manager.Base)

# Use in database operations
with db_manager.get_db() as session:
    user = session.query(User).filter(User.email == "test@example.com").first()
```

### Table Name Convention

When `__tablename__` is not explicitly specified, the framework automatically generates it using `default_name_processor.generate_resource_name()` from `lib.AbstractPydantic2`:

```python
# Implementation in Pydantic2SQLAlchemy.py
from zephyrex.lib.AbstractPydantic2 import default_name_processor

if not tablename:
    tablename = default_name_processor.generate_resource_name(
        pydantic_model.__name__, use_plural=True
    )
```

The `generate_resource_name()` function applies these transformations:
1. **Extract base name**: Strip "Model" or "Manager" suffix via `NameProcessor.extract_base_name()`
2. **Convert to snake_case**: Transform PascalCase to snake_case using `stringcase.snakecase()`
3. **Pluralize**: Apply English pluralization via `inflection.plural()`

**Examples:**
| Pydantic Model | Generated `__tablename__` |
|----------------|--------------------------|
| `UserModel` | `users` |
| `ProviderInstanceModel` | `provider_instances` |
| `TeamModel` | `teams` |
| `ExtensionAbilityModel` | `extension_abilities` |

**Override**: To specify a custom table name, define `__tablename__` as a class variable:
```python
class UserModel(ApplicationModel, DatabaseMixin):
    __tablename__: ClassVar[str] = "custom_users_table"
```

Or use `table_comment` which will use the default naming but add a comment:
```python
class UserModel(ApplicationModel, DatabaseMixin):
    table_comment: ClassVar[str] = "User accounts table"
```

**Benefits:**
- Single source of truth (Pydantic model)
- Automatic SQLAlchemy generation
- Type safety from API to database
- Automatic validation
- Clean, maintainable code

### Schema Evolution Pattern
**Purpose**: Handle schema changes through BLL models.

```python
class UserModel(DatabaseMixin):
    # Add new field
    timezone: Optional[str] = Field(None, description="User timezone")
    
    # Database metadata for migration
    table_comment: ClassVar[str] = "Core user accounts"
    migration_notes: ClassVar[str] = "Added timezone field in v2.1"
```

**Benefits:**
- Schema changes in one place
- Automatic migration detection
- Documentation in code
- Type-safe evolution

## Testing Patterns

### Generated Model Testing Pattern
**Purpose**: Test both Pydantic and SQLAlchemy aspects with isolated database instances.

```python
def test_user_model_generation():
    """Test that UserModel generates correct SQLAlchemy model"""
    
    # Create isolated test database instance
    test_db = DatabaseManager(
        db_prefix="test.model_generation",
        test_connection=False
    )
    
    # Test Pydantic model
    user_data = {"email": "test@example.com"}
    user = UserModel(**user_data)
    assert user.email == "test@example.com"
    
    # Test generated SQLAlchemy model
    User = UserModel.DB(test_db.Base)
    assert hasattr(User, "email")
    assert User.__tablename__ == "users"
    
    # Test relationship generation
    assert hasattr(User, "user_teams")
    assert hasattr(User, "credentials")
    
    # Test database-specific properties
    assert test_db.DATABASE_TYPE in ["sqlite", "postgresql"]
    assert test_db.PK_TYPE is not None

def test_manager_with_isolated_database():
    """Test manager operations with isolated database"""
    
    # Create test database manager
    test_db = DatabaseManager(
        db_prefix="test.manager_ops"
    )
    
    # Create manager with test database
    manager = UserManager(
        requester_id="test-user-id",
        db_manager=test_db
    )
    
    # Test operations use isolated database
    with test_db.get_db() as db:
        # Test database operations
        result = manager.create(email="test@example.com")
        assert result is not None
```

**Benefits:**
- Comprehensive validation
- Both model types tested
- Relationship verification
- Schema consistency checks
- Isolated testing environments
- Database-agnostic test patterns

This Pydantic-first architecture with enterprise database management represents a modern approach to database patterns that maximizes developer productivity and operational reliability. The `DatabaseManager.py` implementation provides production-ready database management with thread-safe operations, multi-database support, and comprehensive session handling. The DatabaseMixin approach provides a single source of truth, while the sophisticated permission system and seeding infrastructure ensure security and maintainability at scale.

## Schema Integrity Doctrine

The codebase enforces integrity at the database layer wherever the
invariant is non-negotiable. "Loose schema" — tables without foreign
keys, nullable columns where a value is required, application-only
enforcement of relational constraints — is **not** the codebase
pattern and must not be claimed as one. Migration authors who skip
DB-level constraints because "the manager enforces it" are wrong;
manager-layer enforcement is a complement to DB-level integrity, not a
substitute.

### Required: foreign keys on every cross-table reference

Every column whose value references the primary key of another table
declares a `sa.ForeignKeyConstraint` (or the per-column `sa.ForeignKey`
shorthand) in the migration. This includes:

- Cross-extension references (e.g. an extension table's `user_id`
  pointing at the core `users.id`).
- Within-extension references (e.g. `objectives.quest_id` →
  `quests.id`).
- Self-references (e.g. `factions.parent_id` → `factions.id`,
  `objectives.prerequisite_objective_id` → `objectives.id`).
- Multi-FK columns referencing the same target (e.g.
  `combat_action_logs.actor_person_id` and `target_person_id` both →
  `persons.id` — declare each as its own `ForeignKeyConstraint`).

Cyclic references (e.g. `locations.container_item_instance_id` ↔
`item_instances.location_id`) are resolved by creating both tables
without the cycle-closing FK, then adding the second FK via
`op.batch_alter_table(...).create_foreign_key(...)` once both tables
exist. This preserves DB-level enforcement at steady state.

### Required: NOT NULL on required references

If a row is meaningless without a particular reference, that column is
`nullable=False`. Optional references are `nullable=True`. The Pydantic
side mirrors this via `Reference` vs `Reference.Optional` mixins.

### Required: CHECK constraints for non-relational invariants

When an invariant cannot be expressed via FK + NOT NULL — the canonical
case is **endpoint XOR** on a polymorphic edge table (exactly one of N
mutually-exclusive endpoint columns is set per row) — the migration
declares a `sa.CheckConstraint` (or
`op.batch_alter_table(...).create_check_constraint(...)`). Examples:

- `relationships(person_id, faction_id)` endpoint XOR per side. The
  CHECK is added by the extension that injects the additional endpoint
  columns (`rpg_state` widens `genealogy.relationships` and is
  responsible for the CHECK that spans both extensions' columns).
- Quantity ≥ 1 on stack rows: `CHECK (quantity >= 1)`.
- State-machine transitions: `CHECK (status IN (...))` for closed
  enums where the application would otherwise drift.

Authors prefer portable CHECK syntax that works across SQLite,
PostgreSQL, and MySQL — booleans-as-integers (e.g.
`(person_id IS NOT NULL) + (faction_id IS NOT NULL) = 1`) is the
portable XOR. Database-specific clauses are last-resort.

### Required: migration ownership stamping

Every `op.create_table` declares `info={"source_module": "...",
"extension": "..."}` so `MigrationOwnership` and downstream tooling
can route schema mutations correctly. When an extension widens
another extension's table via `@extension_model` field injection, the
injecting extension's migration owns the `op.add_column`,
`op.alter_column`, and `op.create_check_constraint` calls; the
`info` dict on those calls (or on the surrounding
`batch_alter_table`) names the injecting extension as the owner of
the new columns. The original-table-owner is unaffected.

### Cross-extension column injection

Extensions may widen another extension's table via the
`@extension_model` decorator on the Pydantic side and matching
`op.add_column` (and, if needed,
`op.alter_column` to relax nullability, plus
`op.create_check_constraint` for new invariants spanning the injected
columns) on the migration side. The canonical example is the `payment`
extension injecting `external_payment_id` onto `users`; `rpg_state`
injects character columns onto `persons` and faction endpoints onto
`relationships`. The injecting extension's migration is the one that
must drop the columns on `downgrade`.

### When manager-layer enforcement is appropriate

Manager-layer validators are appropriate only for invariants the DB
genuinely cannot express:

- **Cycle prevention** in self-referential parent chains (a Location
  whose ancestor chain contains itself; a quest that prerequisites
  itself). Recursive walks need iteration caps; the manager rejects
  edits that would close a cycle. (Depth caps are not appropriate —
  they limit GMs / authors without correctness payoff.)
- **Equipment-slot coherence** (a Location with `kind='equipment_slot'`
  must have `associated_person_id` set). Could be a CHECK in
  PostgreSQL but loses portability.
- **Cross-row invariants** (no two `kind='leader'` member_of
  relationships per faction at the same time). PostgreSQL exclusion
  constraints handle some of these; portable solutions go to the
  manager.

When in doubt, push the constraint down to DDL. Application code is
where invariants go to die; DB constraints survive bug fixes,
refactors, and concurrent writers.

## Soft-Delete Enforcement

Soft-delete is a `SoftDeleteMixin` on `AbstractDatabaseEntity` that adds the `deleted_at` column and registers a SQLAlchemy `before_compile` query event that auto-injects `deleted_at IS NULL` into every read against tables tagged with the mixin. A bypass parameter (`include_deleted=True`) is offered for admin queries that genuinely need tombstoned records; without it, deleted rows are invisible to the BLL.

The bypass flag uses a session-scoped marker so admin queries are explicit. BLL authors do not write `if user["deleted_at"]:` checks by hand — those leak across BLL and a single missed filter is a tombstoned-record bug, security-relevant in cases like login. The DB-layer enforcement is defense-in-depth alongside RLS for tenant isolation: RLS filters cross-tenant access, soft-delete filters tombstoned rows, and both apply at the query-compile layer rather than relying on author discipline.

## Read-Replica Routing

Two complementary opt-in mechanisms route read-only operations to a replica without each extension reaching into the session-management layer.

- **Method-level annotation.** A `@read_only` decorator on BLL methods declares the method is safe to route to a replica. The framework's session binder consults the annotation at method dispatch and binds a replica session when one is configured, falling back to primary when no replica is configured or when the request is inside a write-transaction.
- **Request-context flag.** `RequestContext.read_only: bool` forces all session binding within the request to use replicas. Useful for dedicated read-only endpoints.

Read-after-write consistency is preserved: within a single logical request, once any write has occurred against primary, subsequent reads in the same request bind primary regardless of `@read_only` annotation. The framework supports a configured pool of replicas with simple round-robin selection between them; advanced load shaping is delegated to HAProxy or the database's own load balancer. Replica health is consulted before binding; an unhealthy replica is removed from rotation per a configurable health-check interval. Read-only mode requested with no replica configured is a deployment-config-only fallback to primary (with a warn-once log), not a runtime error.

## Production Considerations

### Best Practices
- **Performance**: Leverage DatabaseMixin caching for high-traffic entities
- **Type Safety**: Use Pydantic validation throughout the stack
- **Maintainability**: Pydantic-first for all entities ensures consistency
- **Testing**: Comprehensive coverage through AbstractDBTest inheritance without mocks