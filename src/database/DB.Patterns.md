# Database Patterns

## Overview
The database layer implements a sophisticated mixed architecture that combines the flexibility of manual SQLAlchemy models with the power of automatic generation through business logic models. This hybrid approach, managed by `DatabaseManager.py`, provides both enterprise-grade database management and developer-friendly patterns for rapid development.

## Production Architecture: Hybrid Model Approach

### Current Implementation Strategy
The system implements a mature hybrid architecture designed for enterprise scalability:

1. **Manual SQLAlchemy Models**: Traditional DB_*.py files for complex relationships and custom behavior
2. **AbstractDatabaseEntity**: Enterprise mixin system (`BaseMixin`, `UpdateMixin`, `ParentMixin`, etc.)
3. **DatabaseMixin**: Automatic SQLAlchemy generation through `.DB` property with lazy loading
4. **DatabaseManager**: Configurable database manager providing multi-database support and session management
5. **with_session Decorator**: Unified session management for BLL operations
6. **Extension System Integration**: Seamless integration with extension-based architecture
7. **ModelRegistry Integration**: Database operations integrated with model registry system

### Pattern Overview
```python
# AbstractDatabaseEntity.py - Core mixin patterns
class BaseMixin:
    """Base functionality for all database entities"""
    system = False
    seed_list = []
    
    # Auto-generated UUID primary key (always String for compatibility)
    @declared_attr
    def id(cls):
        return Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    
    @declared_attr
    def created_at(cls):
        return Column(DateTime, default=func.now())
    
    @declared_attr
    def created_by_user_id(cls):
        return Column(String, nullable=True)
    
    # Hook system for extensibility
    hooks = HooksDescriptor()
    
    @classmethod
    def create_foreign_key(cls, target_entity, model_registry=None, **kwargs):
        """Create standardized foreign key relationships with proper PK type"""
        pk_type = model_registry.DB.manager.PK_TYPE if model_registry else String
        # ... constraint creation logic

# Manual SQLAlchemy models with mixins
class User(Base, BaseMixin, UpdateMixin):
    __tablename__ = "users"
    email = Column(String, nullable=False, unique=True)
    password_hash = Column(String, nullable=True)
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
**Purpose**: Separate models for different operation types.

```python
class UserModel:
    # Base model - for responses and general use
    email: Optional[str] = Field(description="User's email address")
    
    class Create(BaseModel):
        # Required fields for creation
        email: str = Field(..., description="User's email address")
        password: Optional[str] = Field(None, description="User's password")
        
        @model_validator(mode="after")
        def validate_email(self):
            if "@" not in self.email:
                raise ValueError("Invalid email format")
            return self
            
    class Update(BaseModel):
        # All fields optional for updates
        email: Optional[str] = Field(None, description="User's email address")
        
    class Search(ApplicationModel.Search):
        # Search-specific fields with operators
        email: Optional[StringSearchModel] = None
        active: Optional[bool] = None
```

**Benefits:**
- Operation-specific validation rules
- Clear API contracts
- Prevents partial update issues
- Search operator support

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
**Purpose**: Production-grade database management with multi-process support and comprehensive session handling.

```python
# Get singleton database manager instance
db_manager = DatabaseManager()

# Parent process initialization
db_manager.init_engine_config()

# Worker process initialization  
db_manager.init_worker()

# Each manager instance maintains isolated declarative base
Base = db_manager.Base

# Generated models use manager-specific base
class UserModel(DatabaseMixin):
    email: Optional[str] = Field(description="User's email address")

# Lazy generation with caching
User = UserModel.DB  # Automatically uses db_manager.Base
```

**Benefits:**
- Database-specific declarative bases
- Consolidated configuration management
- Thread-safe singleton access
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
**Purpose**: Generate SQLAlchemy models only when needed.

```python
class DatabaseDescriptor:
    def __get__(self, obj, objtype=None):
        """Lazy generation of SQLAlchemy models"""
        if objtype is None:
            return self
            
        # Check cache first
        if objtype.__name__ not in DatabaseMixin._db_cache:
            # Generate model on first access
            sqlalchemy_model = create_sqlalchemy_model(objtype)
            DatabaseMixin._db_cache[objtype.__name__] = sqlalchemy_model
            
        return DatabaseMixin._db_cache[objtype.__name__]
```

**Benefits:**
- Models generated only when needed
- Caching prevents regeneration
- Memory efficient
- Fast subsequent access

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

## Migration Patterns

### Legacy Compatibility Pattern
**Purpose**: Smooth migration from DB_*.py to BLL-first approach with consolidated database management.

```python
# Phase 1: Maintain both patterns
class User(Base, BaseMixin):  # Legacy SQLAlchemy model
    pass

class UserModel(DatabaseMixin):  # New Pydantic model
    pass

# Phase 2: Generated model replaces legacy
User = UserModel.DB  # Generated model replaces manual one

# Phase 3: Remove legacy files entirely
# DB_Auth.py deleted, Base.py consolidated into StaticDatabase.py
# Only BLL_Auth.py remains with DatabaseManager providing Base access
```

**Benefits:**
- Gradual migration path
- No breaking changes
- Validation during transition
- Consolidated database management
- Clean final state

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
    User = UserModel.DB
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

This hybrid architecture with enterprise database management represents a mature approach to database patterns that balances developer productivity with operational requirements. The `DatabaseManager.py` implementation provides production-ready database management with thread-safe operations, multi-database support, and comprehensive session handling. The mixed model approach allows teams to choose the right tool for each use case, while the sophisticated permission system and seeding infrastructure ensure security and maintainability at scale.

## Migration Strategy

### Legacy to Hybrid Migration
**Phase 1**: Maintain both manual DB_*.py files and generated models
**Phase 2**: Gradually migrate complex models to BLL-first approach  
**Phase 3**: Consolidate around hybrid architecture based on complexity needs

### Production Considerations
- **Performance**: Generated models with caching for high-traffic entities
- **Complexity**: Manual models for complex relationships and custom behavior
- **Maintainability**: BLL-first for standard CRUD entities
- **Testing**: Comprehensive coverage through AbstractDBTest inheritance