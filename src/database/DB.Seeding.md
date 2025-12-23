# Database Seeding

## Overview

The database seeding system provides enterprise-grade automatic database population with intelligent dependency resolution, extension integration, and sophisticated placeholder field resolution. The main seeding logic is implemented in `ModelRegistry._seed()` with helper functions in `StaticSeeder.py`. The system ensures entities are seeded in correct order through topological sorting of foreign key relationships.

## Architecture Philosophy

### Key Principles
1. **Intelligent Dependency Resolution** - Topological sorting with circular dependency detection
2. **Model-Centric Seed Data** - Seed data co-located with business logic models for maintainability  
3. **Dynamic Extension Discovery** - Runtime discovery of extensions and providers through `scoped_import`
4. **Hook-Based Extensibility** - Comprehensive hook system for seed data injection and modification
5. **Placeholder Field Resolution** - Advanced field resolution system for dynamic references

### Anti-Patterns Eliminated
- ❌ Hardcoded dependency ordering lists
- ❌ Centralized seed data files disconnected from models
- ❌ Manual maintenance of seeding order
- ❌ Static seed data that doesn't reflect runtime discoveries

## How It Works

### 1. Model Discovery
The seeding system uses the ModelRegistry to discover Pydantic models that have database counterparts. The main seeding entry point is `ModelRegistry._seed()`, which coordinates the process and calls helper functions from `StaticSeeder.py`:

```python
# Main entry point: ModelRegistry._seed() handles:
# - Model discovery and dependency ordering
# - Iteration over models in dependency order
# - Calling seed_model() for each model

# StaticSeeder provides helper functions used by ModelRegistry._seed()
from database.StaticSeeder import seed_model, _resolve_placeholder_fields

# Helper functions for entity lookup
def get_provider_by_name(session, provider_name, db_manager):
    """Helper function to look up a provider by name."""
def get_extension_by_name(session, extension_name, db_manager):
    """Helper function to look up an extension by name."""
```

### 2. Advanced Dependency Analysis
The system performs sophisticated dependency analysis with circular dependency detection:

```python
def _analyze_and_sort_model_dependencies(pydantic_models, db_manager=None):
    """
    Advanced dependency analysis with:
    - Foreign key relationship detection through ReferenceID classes
    - Method Resolution Order (MRO) traversal for inherited dependencies
    - Circular dependency detection and warning
    - Fallback handling for unresolvable dependencies
    """
    dependencies = {}
    for pydantic_model in pydantic_models:
        deps = _extract_model_dependencies_comprehensive(pydantic_model)
        dependencies[pydantic_model.__name__] = deps
```

### 3. Topological Sorting
Uses Kahn's algorithm to sort models in dependency order (dependencies first):

```
Team → User → UserTeam
Extension → Provider → ProviderInstance
Provider → Extension → ProviderExtension
```

### 4. Seeding Execution
Each model is seeded in dependency order, with extensions able to hook into the process.

## Defining Seed Data

### Basic Pattern
Add a `seed_data` property or method to your Pydantic model:

```python
class ExampleModel(DatabaseMixin):
    name: Optional[str] = Field(description="Example name")
    description: Optional[str] = Field(description="Example description")
    
    # Static seed data
    seed_data: ClassVar[List[Dict[str, Any]]] = [
        {"name": "Example 1", "description": "First example"},
        {"name": "Example 2", "description": "Second example"},
    ]
    
    # Or dynamic seed data
    @classmethod
    def seed_data(cls, model_registry=None) -> List[Dict[str, Any]]:
        """Return dynamically generated seed data."""
        return [
            {"name": "Dynamic Example", "description": "Generated at runtime"},
        ]
```

### Extension Discovery Pattern
Extensions can inject seed data through the hook system:

```python
# Extension hooks into seeding process
@hook("DB", "Seed", "UserModel", "inject_seed_data", "before")
def inject_user_seed_data(seed_list, model_class, session):
    """Extension injects additional seed data"""
    seed_list.append({
        "name": "Extension User",
        "email": "ext@example.com"
    })

# Load extensions during seeding
def _load_extensions(extensions_list: str = ""):
    extension_names = [name.strip() for name in extensions_list.split(",")]
    for ext_name in extension_names:
        # Import and initialize extensions
        ext_modules, ext_errors = scoped_import(
            file_type="EXT", 
            scopes=[f"extensions.{ext_name}"]
        )
```

### Advanced Placeholder Resolution
The seeding system features sophisticated placeholder resolution with cross-reference lookup:

```python
# Enhanced placeholder resolution with validation
def _resolve_placeholder_fields(item, session, model_name, db_manager):
    """
    Resolve placeholders with:
    - Cross-table reference lookup
    - Validation of referenced entities
    - Error handling for missing references
    - Support for complex placeholder patterns
    """
    
# In seed data - multiple placeholder types
{
    "name": "Complex Rotation", 
    "_extension_name": "ai_provider",
    "_provider_name": "openai",
    "_provider_instance_name": "root_openai"
}

# Gets resolved with validation
{
    "name": "Complex Rotation",
    "extension_id": "uuid-for-ai-provider", 
    "provider_id": "uuid-for-openai",
    "provider_instance_id": "uuid-for-root-openai"
}
```

**Supported Placeholder Patterns:**
- `_extension_name` → `extension_id` (with existence validation)
- `_provider_name` → `provider_id` (with availability check)
- `_rotation_name` → `rotation_id` (with dependency verification)
- `_provider_instance_name` → `provider_instance_id` (with ownership validation)
- Custom placeholder patterns through extension hooks

## Real-World Examples

### 1. Extension Discovery
```python
# BLL_Extensions.py - ExtensionModel
@classmethod
def seed_data(cls) -> List[Dict[str, Any]]:
    """Dynamically discover and return extension seed data."""
    from lib.Import import scoped_import
    
    ext_modules, ext_errors = scoped_import(file_type="EXT", scopes=["extensions"])
    extension_classes = cls._get_extension_classes_from_modules(ext_modules)
    
    seed_data = []
    for ext_class in extension_classes:
        ext_data = {
            "name": ext_class.name,
            "namespace": ext_class.namespace,
            "description": ext_class.description,
            "enabled": True,
        }
        seed_data.append(ext_data)
    
    return seed_data
```

### 2. Provider Instance Creation
```python
# BLL_Providers.py - ProviderInstanceModel
@classmethod
def seed_data(cls) -> List[Dict[str, Any]]:
    """Create root provider instances for providers with configured environment variables."""
    
    prv_modules, prv_errors = scoped_import(file_type="PRV", scopes=["extensions"])
    provider_classes = cls._get_provider_classes_from_modules(prv_modules)
    
    seed_data = []
    for prv_class in provider_classes:
        # Check if environment variables exist for this provider
        if cls._has_environment_config(prv_class):
            instance_data = {
                "name": f"Root {prv_class.name}",
                "_provider_name": prv_class.name,  # Resolved to provider_id
                "user_id": env("ROOT_ID"),
                "enabled": True,
            }
            seed_data.append(instance_data)
    
    return seed_data
```

### 3. Relationship Creation
```python
# BLL_Providers.py - ProviderExtensionModel
@classmethod
def seed_data(cls) -> List[Dict[str, Any]]:
    """Create links between providers and extensions."""
    
    # Get all discovered providers and extensions
    providers = ProviderModel.seed_data()
    extensions = ExtensionModel.seed_data()
    
    seed_data = []
    for provider in providers:
        for extension in extensions:
            # Check if provider supports this extension
            if cls._provider_supports_extension(provider, extension):
                link_data = {
                    "_provider_name": provider["name"],
                    "_extension_name": extension["name"],
                }
                seed_data.append(link_data)
    
    return seed_data
```

## Seeding Workflow

### 1. Initialization
```python
def seed(db_manager: DatabaseManager, extensions_list: str = "", model_registry=None):
    """Main seeding entry point."""
    logger.debug("Starting database seeding process...")
    
    # Load and initialize extensions before seeding
    _load_extensions(extensions_list)
    
    # Get database session
    session = db_manager.get_session()
    
    # Get models that need seeding from ModelRegistry
    models_to_seed = get_all_models(db_manager, extensions_list, model_registry)
    
    # Seed each model in dependency order
    for model in models_to_seed:
        seed_model(model, session, db_manager, model_registry)
```

### 2. Model Seeding Process
```python
def seed_model(model_class, session, db_manager, model_registry=None):
    """Seed a specific model class."""
    
    # 1. Trigger before_seed_model hook
    AbstractStaticExtension.trigger_hook("DB", "Seed", model_class.__name__, 
                                         "before_seed_model", "before", 
                                         model_class, session)
    
    # 2. Find corresponding Pydantic model and get seed data
    pydantic_model = None
    if model_registry and hasattr(model_registry, "bound_models"):
        for pmodel in model_registry.bound_models:
            if hasattr(pmodel, "DB") and pmodel.DB(db_manager.Base) == model_class:
                pydantic_model = pmodel
                break
    
    # Get seed data from Pydantic model
    if pydantic_model and hasattr(pydantic_model, "seed_data"):
        seed_data_attr = getattr(pydantic_model, "seed_data")
        if callable(seed_data_attr):
            # Try with model_registry parameter first
            try:
                seed_list = seed_data_attr(model_registry=model_registry)
            except TypeError:
                seed_list = seed_data_attr()
        else:
            seed_list = seed_data_attr
    else:
        seed_list = []
    
    # 3. Trigger seed injection hook (allows extensions to add data)
    hook_results = AbstractStaticExtension.trigger_hook("DB", "Seed", model_class.__name__, 
                                                        "inject_seed_data", "before", 
                                                        seed_list, model_class, session)
    
    # 4. Process each seed item
    for item in seed_list:
        # Resolve placeholders like _provider_name, _extension_name
        resolved_item = _resolve_placeholder_fields(item, session, model_class.__name__, db_manager)

        # Determine field to check for existence (priority: id > name > email)
        if "id" in resolved_item:
            check_field = "id"
        else:
            check_field = next((k for k in ["name", "email"] if k in resolved_item), None)

        # Check if item already exists
        if resolved_item and check_field and not model_class.exists(
            env("ROOT_ID"), model_registry, **{check_field: resolved_item[check_field]}
        ):
            # Create new item - check pydantic_model.seed_creator_id first
            creator_id = env("SYSTEM_ID")
            if pydantic_model and hasattr(pydantic_model, "seed_creator_id"):
                creator_id = pydantic_model.seed_creator_id

            if hasattr(model_class, "create"):
                model_class.create(creator_id, model_registry, return_type="db", **resolved_item)
            else:
                # Fallback to direct SQLAlchemy creation for models without create method
                new_instance = model_class(**resolved_item)
                session.add(new_instance)
                session.flush()
    
    # 5. Trigger after_seed_model hook
    AbstractStaticExtension.trigger_hook("DB", "Seed", model_class.__name__, 
                                         "after_seed_model", "after", 
                                         model_class, session, items_created)
```

## Dependency Resolution Details

### Algorithm: Topological Sort (Kahn's Algorithm)
1. **Build dependency graph** by analyzing `ReferenceID` classes
2. **Calculate in-degrees** (how many models depend on each model)
3. **Process models with zero in-degree** (no dependencies) first
4. **Remove processed models** and update in-degrees of dependent models
5. **Repeat until all models processed**

### Dependency Detection
The system analyzes several sources for dependencies:

#### Direct ReferenceID Classes
```python
class ProviderInstanceModel(ApplicationModel):
    class ReferenceID:
        provider_id: str  # Creates dependency on ProviderModel
```

#### Inherited References
```python
class UserTeamModel(ApplicationModel, UserReferenceModel, TeamReferenceModel):
    # Inherits dependencies on UserModel and TeamModel
```

#### Base Class Analysis
The system walks the Method Resolution Order (MRO) to find all inherited references.

### Circular Dependency Handling
If circular dependencies are detected:
1. System logs a warning with the affected models
2. Remaining models are added to the end of the sort order
3. Seeding continues (may require multiple passes for full resolution)

## Extension Integration

### Seeding Hooks
Extensions can hook into the seeding process at multiple points:

```python
# Before model seeding starts
@hook("DB", "Seed", "UserModel", "before_seed_model", "before")
def prepare_user_seeding(model_class, session):
    # Prepare for user seeding
    pass

# Before seed list is processed
@hook("DB", "Seed", "UserModel", "before_seed_list", "before")
def modify_user_seed_data(seed_list, session):
    # Add or modify seed data
    seed_list.append({"name": "Extension User", "email": "ext@example.com"})

# After model seeding completes
@hook("DB", "Seed", "UserModel", "after_seed_model", "after")
def post_user_seeding(model_class, session):
    # Post-processing after user seeding
    pass
```

### Extension Seed Data Injection
Extensions can inject their own seed data:

```python
class MyExtension(AbstractStaticExtension):
    @staticmethod
    def get_seed_data_for_model(model_name: str) -> List[Dict[str, Any]]:
        if model_name == "UserModel":
            return [{"name": "Extension User", "email": "ext@example.com"}]
        return []
```

## Debugging and Troubleshooting

### Enable Debug Logging
```python
import logging
logging.getLogger("database.StaticSeeder").setLevel(logging.DEBUG)
```

### Common Issues

#### 1. Circular Dependencies
**Symptom**: Warning about circular dependencies in logs
**Solution**: Review model relationships, consider making some references optional

#### 2. Missing Dependencies
**Symptom**: Foreign key constraint violations during seeding
**Solution**: Ensure all referenced models have seed data or make references optional

#### 3. Placeholder Resolution Failures
**Symptom**: KeyError or items not found during placeholder resolution
**Solution**: Verify referenced entities exist in seed data with correct names

#### 4. Extension Discovery Issues
**Symptom**: Expected extensions not found in seed data
**Solution**: Check extension module naming and ensure they inherit from `AbstractStaticExtension`

#### 5. DB Property Access Errors
**Symptom**: TypeError about calling DB property without base parameter
**Solution**: Ensure all `.DB` calls include the declarative base: `model.DB(db_manager.Base)`

### Diagnostic Commands
```python
# Check dependency order
models = get_all_models(db_manager)
logger.debug([model.__name__ for model in models])

# Check specific model seed data
seed_data = ExtensionModel.seed_data()
logger.debug(f"Extension seed data: {seed_data}")

# Check dependency graph
dependencies = _analyze_and_sort_model_dependencies(pydantic_models)
logger.debug(f"Dependencies: {dependencies}")
```

## Best Practices

### 1. Model Design
- Use clear, descriptive field names for foreign keys
- Follow the `entity_id` naming convention
- Make optional relationships truly optional with `Optional` variants

### 2. Seed Data
- Keep seed data minimal but complete
- Use environment variables for configuration
- Prefer dynamic discovery over static lists
- Document any special placeholder requirements

### 3. Testing
- Test seeding with empty database
- Verify dependency order with complex relationships
- Test extension hook integration
- Validate placeholder resolution

### 4. Performance
- Consider lazy loading for large seed datasets
- Use batch operations for bulk seeding
- Cache expensive discovery operations where appropriate

## Migration from Legacy Seeding

### Old Pattern (Anti-Pattern)
```python
# StaticSeeder.py - DON'T DO THIS
def seed_providers():
    return [
        {"name": "OpenAI", "description": "OpenAI Provider"},
        {"name": "Anthropic", "description": "Anthropic Provider"},
    ]

dependency_order = ["Team", "User", "Provider", ...]  # Manual ordering
```

### New Pattern (Correct)
```python
# BLL_Providers.py - DO THIS
class ProviderModel(ApplicationModel):
    @classmethod
    def seed_data(cls) -> List[Dict[str, Any]]:
        """Dynamically discover providers."""
        # Dynamic discovery logic here
        return discovered_providers
```

### Migration Steps
1. Move seed data from `StaticSeeder.py` to appropriate BLL model classes
2. Convert static lists to `seed_data()` methods
3. Replace manual discovery with `scoped_import` patterns
4. Remove hardcoded dependency ordering
5. Test with clean database

## Security Considerations

### Seed Data Security
- Never include production secrets in seed data
- Use environment variables for sensitive configuration
- Validate seed data before database insertion
- Consider separate seed data for development vs production

### Access Control
- Ensure seeded entities have appropriate permissions
- Use system IDs for system-created entities
- Review default roles and permissions in seed data

---

*This seeding system is designed to be self-maintaining and extensible. When you add new models with foreign key relationships, they will automatically be seeded in the correct order without any manual configuration.*
