# Extensions

Extensions are the primary mechanism for extending server functionality in AGInfrastructure. They provide **static functionality, metadata organization, and component integration** through automated loading systems. Extensions are implemented as **static/abstract classes** that coordinate the loading of database models, business logic managers, endpoints, and providers through file naming conventions.

## Table of Contents
1. [Extension Architecture](#extension-architecture)
2. [Extension Registry](#extension-registry)
3. [Dependencies Management](#dependencies-management)
4. [Hook System](#hook-system)
5. [Ability System](#ability-system)
6. [Environment Variables](#environment-variables)
7. [Database Integration](#database-integration)
8. [Table Extension Mechanisms](#table-extension-mechanisms)
9. [Seeding System](#seeding-system)
10. [Root Rotations](#root-rotations)
11. [Component Organization](#component-organization)
12. [Creating an Extension](#creating-an-extension)
13. [Testing Integration](#testing-integration)
14. [Best Practices](#best-practices)
15. [Architectural Improvements](#architectural-improvements)

## Extension Architecture

### Core Structure
All extensions inherit from `AbstractStaticExtension` and must define static properties and class methods:

```python
from typing import Dict, List, Set
from extensions.AbstractExtensionProvider import AbstractStaticExtension
from lib.Dependencies import Dependencies, EXT_Dependency, PIP_Dependency, SYS_Dependency

class EXT_MyExtension(AbstractStaticExtension):
    # Required static metadata
    name: str = "my_extension"
    version: str = "1.0.0"
    description: str = "Description of what this extension does"
    
    # Environment variables this extension needs
    _env: Dict[str, Any] = {
        "MY_EXTENSION_API_KEY": "",
        "MY_EXTENSION_DEBUG": "false"
    }
    
    # Unified dependencies using the Dependencies class
    dependencies: Dependencies = Dependencies([
        EXT_Dependency(name="core", reason="Core functionality"),
        PIP_Dependency(name="requests", semver=">=2.28.0", reason="HTTP requests"),
        SYS_Dependency.for_all_platforms(
            name="git", 
            apt_pkg="git", 
            brew_pkg="git", 
            winget_pkg="Git.Git",
            reason="Version control operations"
        )
    ])
    
    # Static abilities
    _abilities: Set[str] = set()
    
    # Provider cache (populated by auto-discovery)
    _providers: List[Type] = []

### Static Extension Registry
Extensions are automatically registered via the `ExtensionRegistry` class through the `__init_subclass__` hook. This enables:
- Cross-extension communication through static methods
- Dependency resolution at the class level
- Static hook coordination
- Extension lookup by name without instantiation
- Provider test environment inheritance through extension linking
- Automatic extension type detection based on file patterns

```python
# Extensions are registered automatically via ExtensionRegistry
registry = ExtensionRegistry(extensions_csv="auth_mfa,database,email,payment")

# Access extensions through the registry
extension_class = registry.get_extension_by_name("my_extension")
abilities = extension_class.get_abilities()

# List all loaded extensions
all_extensions = list(registry.registry.values())

# Extension types are automatically detected via the .types property
extension_types = extension_class.types  # Set of ExtensionType enums (ENDPOINTS, DATABASE, EXTERNAL)

# Single type string also available for convenience
extension_type = extension_class.extension_type  # String: "external", "database", "endpoints", or "unknown"
```

### Extension Type Detection
The system automatically detects extension types based on file patterns:
- **External**: Has PRV_*.py files (providers) or external models
- **Database**: Has BLL_*.py files with DatabaseMixin usage (models with __tablename__ or table_comment)
- **ENDPOINTS**: Has BLL_*.py files with RouterMixin usage

Extension types are non-mutually exclusive - an extension can be multiple types simultaneously (e.g., both Database and External). This automatic detection replaces the need for explicit type mixins (, , ).

### Provider Auto-Discovery with Caching
Extensions automatically discover their providers through filesystem scanning with caching:

```python
class EXT_MyExtension(AbstractStaticExtension):
    name = "my_extension"
    
    # Define inner abstract provider class
    class AbstractProvider(AbstractExtensionProvider):
        """Abstract provider interface for this extension."""
        extension = None  # Will be set to EXT_MyExtension after class definition
        
        @classmethod
        @abstractmethod
        def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
            """Bond a provider instance for API operations."""
            pass
    
    @classproperty
    @lru_cache(maxsize=1)
    def providers(cls) -> List[Type]:
        """
        Auto-discover all providers in this extension's folder.
        Cached after first access.
        """
        # Automatically scans extension directory for PRV_*.py files
        # Discovers all AbstractProvider subclasses (inner class)
        # Returns cached list of provider classes
        pass

# Set the extension reference after class definition
EXT_MyExtension.AbstractProvider.extension = EXT_MyExtension
```

### Abstract Provider Pattern
Extensions define their abstract provider interface using separate classes with an extension_type attribute:

```python
# In AbstractProvider_Payment.py or PRV_*.py files
class AbstractPaymentProvider(AbstractStaticProvider):
    """Abstract payment provider interface."""
    # Extensions no longer need extension_type - type is auto-detected
    
    @classmethod
    @abstractmethod
    def create_customer(cls, **kwargs) -> Dict[str, Any]:
        """Create a customer in the payment system."""
        pass

# Providers inherit from the abstract provider
class PaymentExtensionStripeProvider(AbstractPaymentProvider):
    name: str = "stripe"
    # Extensions no longer need extension_type - type is auto-detected
    
    @classmethod
    def create_customer(cls, **kwargs) -> Dict[str, Any]:
        # Implementation
        pass
```

## Extension Registry

### Global Static Registry
The extension registry (`extension_registry`) is a class-level dictionary that tracks all loaded extension classes:

```python
# Registry structure
extension_registry: Dict[str, Type["AbstractStaticExtension"]] = {}

# Accessing extension classes
extension_class = AbstractStaticExtension.get_extension_by_name("auth")

# List all loaded extension classes
all_extension_classes = AbstractStaticExtension.extension_registry.values()
```

### Extension Lookup
```python
# By name
auth_extension_class = AbstractStaticExtension.get_extension_by_name("auth")
if auth_extension_class:
    abilities = auth_extension_class.get_abilities()

# Check if extension is loaded
if "my_extension" in AbstractStaticExtension.extension_registry:
    logger.debug("Extension is loaded")
```

## Dependencies Management

### Unified Dependencies System
Extensions use the unified Dependencies system for managing all types of dependencies:

```python
from lib.Dependencies import Dependencies, EXT_Dependency, PIP_Dependency, SYS_Dependency

class EXT_MyExtension(AbstractStaticExtension):
    # Unified dependencies declaration
    dependencies = Dependencies([
        # Extension dependencies
        EXT_Dependency(
            name="core_extension",
            friendly_name="Core Extension",
            optional=False,
            reason="Required for base functionality",
            semver=">=1.0.0"
        ),
        
        # Python package dependencies
        PIP_Dependency(
            name="requests",
            friendly_name="HTTP Requests Library",
            semver=">=2.28.0",
            reason="HTTP communication with external APIs"
        ),
        
        # System package dependencies
        SYS_Dependency.for_all_platforms(
            name="postgresql-client",
            friendly_name="PostgreSQL Client",
            apt_pkg="postgresql-client",
            brew_pkg="postgresql",
            winget_pkg="PostgreSQL.PostgreSQL",
            reason="Database connectivity tools"
        )
    ])
```

### Static Dependency Management
```python
class EXT_MyExtension(AbstractStaticExtension):
    dependencies = Dependencies([...])  # Unified dependencies
    
    @classmethod
    def check_dependencies(cls, loaded_extensions: Dict[str, str] = None) -> Dict[str, bool]:
        """Check dependency satisfaction at class level."""
        return cls.dependencies.check(loaded_extensions or {})
    
    @classmethod
    def install_dependencies(cls, only_missing: bool = True) -> Dict[str, bool]:
        """Install missing dependencies."""
        return cls.dependencies.install(only_missing=only_missing)
    
    @classmethod
    def get_missing_dependencies(cls, loaded_extensions: Dict[str, str] = None) -> Dependencies:
        """Get missing dependencies."""
        return cls.dependencies.get_missing(loaded_extensions or {})
```

### Dependency Resolution
```python
# Check dependency satisfaction
loaded_extensions = {"core": "1.0.0", "auth": "2.1.0"}
dependency_status = MyExtension.check_dependencies(loaded_extensions)

# Install missing dependencies  
install_results = MyExtension.install_dependencies(only_missing=True)

# Get missing dependencies
missing_deps = MyExtension.get_missing_dependencies(loaded_extensions)

# Resolve loading order for multiple extensions
extension_classes = {"ext1": Ext1Class, "ext2": Ext2Class}
loading_order = AbstractStaticExtension.resolve_dependencies(extension_classes)
```

## Hook System

### Static Hook Registration and Discovery
The system uses static method registration for hooks using the `@hook` decorator with automatic discovery:

```python
from logic.AbstractLogicManager import hook_bll, HookContext, HookTiming

class EXT_MyExtension(AbstractStaticExtension):
    # Hooks registered by this extension
    hooks: Dict[HookPath, List[Callable]] = {}
    
    @classmethod
    def _discover_static_hooks(cls) -> None:
        """Discover and register static hook methods in the extension class."""
        for name, method in getmembers(cls, predicate=isfunction):
            if hasattr(method, "_hook_info"):
                for hook_path in method._hook_info:
                    if hook_path not in cls.hooks:
                        cls.hooks[hook_path] = []
                    cls.hooks[hook_path].append(method)
                    logger.debug(
                        f"Registered static hook {hook_path} -> {method.__name__}"
                    )
    
    # Method-specific hooks - target individual methods using ClassName.method_name
    @hook_bll(UserManager.create, timing=HookTiming.BEFORE, priority=10)
    def validate_user_creation(context: HookContext) -> None:
        """Hook that runs ONLY before UserManager.create method."""
        user_data = context.kwargs.get('data', {})
        if not user_data.get('email'):
            raise ValueError("Email is required")

    @hook_bll(UserManager.login, timing=HookTiming.AFTER, priority=20)
    def track_login_success(context: HookContext) -> None:
        """Hook that runs ONLY after UserManager.login method."""
        if context.result:
            logger.info(f"Successful login for user: {context.result.id}")

    # Class-level hooks - applied to ALL methods using ClassName only
    @hook_bll(UserManager, timing=HookTiming.AFTER)
    def audit_all_user_operations(context: HookContext) -> None:
        """Hook that runs after ANY UserManager operation (create, get, list, update, delete, etc.)."""
        logger.info(f"UserManager.{context.method_name} executed by {context.manager.requester.id}")
        
    # Conditional class-level hooks using method name detection
    @hook_bll(UserManager, timing=HookTiming.AFTER)
    def initialize_payment_data(context: HookContext):
        """Initialize payment data for new users only."""
        if context.method_name == "create":
            user = context.result
            if user and hasattr(user, "external_payment_id"):
                logger.debug(f"User {user.id} created with payment extension support")
    
    @staticmethod
    def hook(
        layer: str, domain: str, entity: str, function: str, time: str
    ) -> Callable:
        """Decorator to mark a static method as a hook handler."""

        def decorator(method: Callable) -> Callable:
            if not hasattr(method, "_hook_info"):
                method._hook_info = []
            hook_path = (layer, domain, entity, function, time)
            method._hook_info.append(hook_path)
            logger.debug(
                f"Decorated static method {method.__name__} as hook for {hook_path}"
            )
            return method

        return decorator
```

### Hook Targeting Options

The hook system supports two targeting approaches:

1. **Method-Specific Hooks (`ClassName.method_name`)**: Target individual methods only
   - `@hook_bll(UserManager.create, ...)` - runs only for `create` method
   - `@hook_bll(UserManager.login, ...)` - runs only for `login` method
   - `@hook_bll(UserManager.update, ...)` - runs only for `update` method

2. **Class-Level Hooks (`ClassName`)**: Apply to ALL methods of a manager class
   - `@hook_bll(UserManager, ...)` - runs for create, get, list, update, delete, search, etc.
   - Use `context.method_name` to conditionally handle specific methods
   - Perfect for cross-cutting concerns like auditing, logging, or monitoring

### Hook Registration Process
1. **Decorator-Based Registration**: Hooks are registered using `@hook_bll` decorator or `@AbstractStaticExtension.hook` decorator
2. **Automatic Discovery**: Hooks are discovered at class definition time via metaclass
3. **Manager Integration**: Hooks integrate with `AbstractBLLManager` infrastructure
4. **Context-Based Execution**: Hooks receive `HookContext` with method information

### Hook Context Usage
The `HookContext` provides access to method execution details:

```python
@hook_bll(UserManager, timing=HookTiming.BEFORE)
def my_hook(context: HookContext) -> None:
    # Access method details
    manager_instance = context.manager
    method_name = context.method_name
    args = context.args
    kwargs = context.kwargs
    
    # For AFTER hooks, access results
    if context.timing == HookTiming.AFTER:
        result = context.result
```

### Hook Timing and Priority
```python
# Before hooks run before method execution
@hook_bll(UserManager.create, timing=HookTiming.BEFORE, priority=10)
def validate_before(context: HookContext) -> None:
    # Validation logic
    pass

# After hooks run after method execution  
@hook_bll(UserManager.create, timing=HookTiming.AFTER, priority=20)
def process_after(context: HookContext) -> None:
    # Post-processing logic
    pass
```

### Static Hook Triggering
```python
# Hooks are triggered automatically by the BLL manager system
# Manual triggering is also possible:
@classmethod
def trigger_hook(
    cls,
    layer: str,
    domain: str,
    entity: str,
    function: str,
    time: str,
    *args,
    **kwargs,
) -> List[Any]:
    """Trigger all static hooks registered for a specific path."""
    hook_path = (layer, domain, entity, function, time)
    results = []

    if hook_path in cls.hooks:
        for handler in cls.hooks[hook_path]:
            try:
                result = handler(*args, **kwargs)
                results.append(result)
            except Exception as e:
                logger.error(f"Error executing static hook {hook_path}: {e}")
                results.append(None)

    return results
```

## Ability System

### Meta Abilities vs Abstract Abilities
The ability system distinguishes between two types of abilities:

1. **Meta Abilities (Extension-Level)**: Defined on extensions, agnostic of any specific provider
2. **Abstract Abilities (Provider-Level)**: Defined at the extension level but implemented by providers

The `@ability` decorator can be used standalone and automatically detects the context based on the class it's applied to.

### Static Ability Declaration and Discovery
Abilities are declared as static class methods with decorators and automatically discovered:

```python
from extensions.AbstractExtensionProvider import ability

class EXT_MyExtension(AbstractStaticExtension):
    # Static abilities registry
    _abilities: Set[str] = set()
    
    # Meta ability - extension-level functionality
    @staticmethod
    @ability("translate_text", enabled=True)
    def translate(text: str, target_language: str = "en") -> str:
        """Translate text to target language."""
        # Implementation here
        return translated_text

    @staticmethod
    @ability()  # Uses method name as ability name
    def process_data(data: Dict) -> Dict:
        """Process data with custom logic."""
        return processed_data

# Provider abilities - provider-specific functionality
class MyExtensionProvider(EXT_MyExtension.AbstractProvider):
    _abilities: Set[str] = set()  # Auto-populated from @ability decorators
    
    @staticmethod
    @ability("custom_operation")
    def custom_op(**kwargs) -> Dict[str, Any]:
        """Provider-specific operation."""
        return {"result": "success"}
    
    @classmethod
    def _discover_static_abilities(cls) -> None:
        """Discover and register static ability methods in the extension class."""
        for name, method in getmembers(cls, predicate=isfunction):
            if hasattr(method, "_ability_info"):
                ability_info = method._ability_info
                ability_name = ability_info["name"]
                cls.abilities.add(ability_name)
                logger.debug(
                    f"Registered static ability {ability_name} -> {method.__name__}"
                )
```

### Static Ability Management
```python
class EXT_MyExtension(AbstractStaticExtension):    
    @classmethod
    def get_abilities(cls) -> Set[str]:
        """Get static abilities of this extension."""
        return cls.abilities.copy()

    @classmethod
    def has_static_ability(cls, ability_name: str) -> bool:
        """Check if extension has a specific static ability."""
        return ability_name in cls.abilities

    @classmethod
    def execute_static_ability(cls, ability_name: str, **kwargs) -> Any:
        """Execute a static ability by name."""
        for name, method in getmembers(cls, predicate=isfunction):
            if (
                hasattr(method, "_ability_info")
                and method._ability_info["name"] == ability_name
            ):
                try:
                    return method(**kwargs)
                except Exception as e:
                    logger.error(
                        f"Error executing static ability '{ability_name}': {e}"
                    )
                    raise

        raise ValueError(
            f"Ability '{ability_name}' not found in extension '{cls.name}'"
        )
```

### Ability Configuration Structure
```python
# Static ability configuration
extension_class.agent_config = {
    "abilities": {
        "translate_text": "true",  # Meta ability from extension
        "process_data": "false",   # Meta ability from extension
        "api_call": "true"         # Abstract ability from provider
    }
}

# The system automatically distinguishes between meta and abstract abilities
# based on where they were defined (extension vs provider)
```

## Provider Rotation System

### Static Provider Architecture
Extensions integrate with the Provider Rotation System for external API management through static classes:

```python
from extensions.AbstractExtensionProvider import AbstractStaticProvider

class EXT_Payment(AbstractStaticExtension):
    """Payment extension with provider rotation support."""
    
    # Provider discovery - cache for providers  
    _providers: List[Type] = []
    
    # Inner AbstractProvider class can be defined for type safety
    class AbstractProvider(AbstractStaticProvider):
        """Abstract provider for payment extension."""
    
    @abstractmethod
    @ability("create_payment")
    def create_payment(self, amount: Decimal, currency: str, **kwargs) -> Dict[str, Any]:
        """Create a payment - must be implemented by concrete providers."""
        pass

# Concrete provider implementation
class PRV_Stripe_Payment(EXT_Payment.AbstractProvider):
    """Stripe payment provider for rotation system."""
    
    name: str = "stripe"
    
    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        """Bond a provider instance for API operations."""
        return StripeProviderInstance(instance)
    
    def create_payment(self, amount: Decimal, currency: str, **kwargs) -> Dict[str, Any]:
        """Implement the abstract create_payment ability."""
        # Implementation using Stripe API
        pass
```

### Provider Discovery and Auto-Loading
Extensions automatically discover their providers through filesystem scanning:

```python
# Providers are discovered from extensions/{name}/PRV_*.py files
# Example structure:
extensions/payment/
├── PRV_Stripe_Payment.py        # Stripe provider implementation  
├── PRV_Square_Payment.py        # Square provider implementation
├── EXT_Payment.py               # Extension definition
├── AbstractProvider_Payment.py  # Abstract provider definition (required for provider extensions)
├── BLL_Payment.py               # Business logic with model extensions
```

### Provider Static Pattern
Providers use a static pattern with `bond_instance()` for configuration:

```python
class PRV_Stripe_Payment(AbstractProvider_Payment):
    name: str = "stripe"
    
    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        """Bond a provider instance for API operations."""
        # Configure the provider with instance credentials
        return StripeProviderInstance(instance)
    
    def create_payment(self, amount: Decimal, currency: str, **kwargs) -> Dict[str, Any]:
        """Create a payment using the bonded instance."""
        # Implementation uses self._instance (bonded instance)
        stripe_result = self._instance.create_charge(
            amount=amount,
            currency=currency,
            **kwargs
        )
        return stripe_result
```

### Root Rotation Access
Extensions get automatic access to their root rotation for system operations:

```python
class EXT_Payment(AbstractStaticExtension):
    @classproperty
    def root(cls) -> Optional[RotationManager]:
        """
        Get the Root RotationManager for this extension.
        Each extension gets its own root rotation based on its name.
        Uses proper caching with _root_rotation_cache attribute.
        """
        if cls._root_rotation_cache is not None:
            return cls._root_rotation_cache
            
        # Automatic discovery of root rotation by extension name
        # Caches result for performance
        pass

# Usage
payment_root = EXT_Payment.root
if payment_root:
    result = payment_root.rotate(Stripe_CustomerModel.create_via_provider, **kwargs)
```

## External Model Integration

### External Models in PRV Files
External models and managers are now defined alongside providers in PRV_*.py files:

```python
# In PRV_Stripe.py
from extensions.AbstractExternalModel import AbstractExternalModel, AbstractExternalManager

class Stripe_CustomerModel(AbstractExternalModel):
    """External model representing Stripe customers."""
    
    # Pydantic model fields
    id: str = Field(..., description="Stripe customer ID")
    email: str = Field(..., description="Customer email")
    name: Optional[str] = Field(None, description="Customer name")

class Stripe_CustomerManager(AbstractExternalManager):
    """Manager for Stripe customer operations."""
    Model = Stripe_CustomerModel
    
    # Inherits standard BLL methods routed through external APIs

class PRV_Stripe(EXT_Payment.AbstractProvider):
    """Stripe payment provider."""
    name = "stripe"
    
    # Provider implementation
```

This pattern keeps all external-related code (models, managers, providers) in the same file for better organization.
    
    @classmethod
    def to_external_format(cls, internal_data: Dict[str, Any]) -> Dict[str, Any]:
        """Convert internal data format to Stripe API format."""
        return {
            "email": internal_data.get("email"),
            "name": internal_data.get("name"),
            "metadata": internal_data.get("metadata", {})
        }
    
    @classmethod
    def from_external_format(cls, external_data: Dict[str, Any]) -> Dict[str, Any]:
        """Convert Stripe API format to internal data format."""
        return {
            "id": external_data.get("id"),
            "email": external_data.get("email"), 
            "name": external_data.get("name"),
            "created_at": external_data.get("created")
        }
    
    @staticmethod
    def create_via_provider(provider_instance, **kwargs) -> Dict[str, Any]:
        """Create customer via Stripe provider."""
        # Implementation calls Stripe API using provider_instance credentials
        pass
```

### External Navigation Properties
External models support navigation properties for automatic relationship resolution:

```python
from extensions.AbstractExternalModel import external_navigation_property

@extension_model(UserModel)
class Payment_UserModel(BaseModel):
    """Payment extension for User model with navigation property."""
    
    external_payment_id: Optional[str] = Field(None, description="Stripe customer ID")
    
    # Automatic navigation property that resolves Stripe customer data
    stripe_customer: Optional[Stripe_CustomerModel] = external_navigation_property(
        Stripe_CustomerModel,
        local_field="external_payment_id"
    )

# Usage
user = user_manager.get(id=user_id)
if user.stripe_customer:  # Automatically resolves via Provider Rotation System
    logger.debug(f"Stripe customer: {user.stripe_customer.email}")
```

### External Manager Pattern
Extensions provide managers for external resources that integrate with the BLL system:

```python
from extensions.AbstractExternalModel import AbstractExternalManager

class Stripe_CustomerManager(AbstractExternalManager):
    """Manager for Stripe customer operations via Provider Rotation System."""
    
    Model = Stripe_CustomerModel
    
    def __init__(self, requester_id: str, rotation_manager=None, **kwargs):
        """Initialize with rotation manager for provider selection."""
        super().__init__(
            requester_id=requester_id,
            rotation_manager=rotation_manager,
            **kwargs
        )
    
    # Inherits all standard BLL methods (create, get, list, update, delete)
    # but routes through external APIs via Provider Rotation System

# Usage in BLL extensions
def get_or_create_payment_customer(self, user_id: str) -> dict:
    """Get or create a payment customer for a user."""
    stripe_manager = Stripe_CustomerManager(
        requester_id=user_id,
        rotation_manager=None  # Uses default rotation
    )
    
    customer = stripe_manager.create(
        email=user.email,
        name=user.display_name
    )
    return customer
```

## Environment Variables

### Static Environment Variable Management
Environment variables are managed at the class level:

```python
from typing import Dict, Any
from lib.Environment import env

class EXT_MyExtension(AbstractStaticExtension):
    # Use _env with ClassVar for environment variables
    _env: Dict[str, Any] = {
        "MY_EXTENSION_API_KEY": "",          # Required, no default
        "MY_EXTENSION_DEBUG": "false",       # Boolean with default
        "MY_EXTENSION_TIMEOUT": "30"         # Numeric with default
    }
    
    @classmethod
    def get_env_value(cls, env_var_name: str) -> str:
        """Get environment variable value."""
        return env(env_var_name, cls._env.get(env_var_name, ""))
    
    @classmethod
    def get_configuration(cls) -> Dict[str, Any]:
        """Get all environment configuration."""
        return {
            "api_key": cls.get_env_value("MY_EXTENSION_API_KEY"),
            "debug": cls.get_env_value("MY_EXTENSION_DEBUG") == "true",
            "timeout": int(cls.get_env_value("MY_EXTENSION_TIMEOUT"))
        }

    @classmethod
    def is_configured(cls) -> bool:
        """Check if extension has required environment variables configured."""
        for env_var_name, default_value in cls._env.items():
            # If no default is provided (empty string), it's required
            if not default_value:
                current_value = cls.get_env_value(env_var_name)
                if not current_value or current_value.strip() == "":
                    return False
        return True
```

### Automatic Environment Variable Registration
```python
# Environment variables are registered automatically based on extension type:
# - External extensions: {NAME}_API_KEY, {NAME}_SECRET_KEY, {NAME}_WEBHOOK_SECRET, etc.
# - Database extensions: {NAME}_DB_CONNECTION, {NAME}_MIGRATION_ENABLED
# - Internal/endpoint extensions: Typically don't need special env vars

# Access configuration through class methods
api_key = MyExtension.get_env_value("MY_EXTENSION_API_KEY")
debug_mode = MyExtension.get_env_value("MY_EXTENSION_DEBUG") == "true"
timeout = int(MyExtension.get_env_value("MY_EXTENSION_TIMEOUT"))

# Check configuration
config = MyExtension.get_configuration()
if config["api_key"]:
    # Extension is configured
    pass

# Registration happens automatically via _register_env_vars() during class initialization
```

## Database Integration

### Static Database Integration
Extensions handle database integration through class-level methods:

```python
class EXT_MyExtension(AbstractStaticExtension):
    @classmethod
    def get_database_models(cls) -> List[Type]:
        """Return database models for this extension."""
        # Return models that should be registered
        return [MyDataModel, MyOtherModel]
    
    @classmethod
    def initialize_database(cls) -> bool:
        """Initialize database components for this extension."""
        # Perform any database initialization
        return True
```

## Table Extension Mechanisms

Extensions can extend existing tables or create table modifications through several mechanisms:

### Primary Extension Pattern: @extension_model Decorator
Extensions use the `@extension_model` decorator to inject fields directly into existing models:

```python
# In BLL_MyExtension.py
from lib.Pydantic2SQLAlchemy import extension_model, RemoveField
from logic.BLL_Auth import UserModel  # Import existing model

@extension_model(UserModel)
class MyExtension_UserModel:
    """
    Extension for User model.
    Injects fields directly into the base UserModel.
    """
    
    # Add extension-specific fields
    my_extension_field: Optional[str] = Field(
        None, 
        description="Custom field from extension"
    )
    custom_preferences: Optional[Dict[str, Any]] = Field(
        None, 
        description="Extension-specific preferences"
    )

    # Extend nested models
    class Create:
        my_extension_field: Optional[str] = None
        custom_preferences: Optional[Dict[str, Any]] = None

    class Update:
        my_extension_field: Optional[str] = None
        custom_preferences: Optional[Dict[str, Any]] = None

    class Search:
        my_extension_field: Optional[StringSearchModel] = None
```

### Extension-Specific Table Creation
Extensions can create their own tables that reference core entities:

```python
class EXT_MyExtension(AbstractStaticExtension):
    @classmethod
    def get_database_models(cls) -> List[Type]:
        """Return extension-specific models that extend core functionality."""
        
        class UserExtensionData(ApplicationModel, DatabaseMixin):
            """Extension-specific data for users."""
            
            # Reference to core user
            user_id: str = Field(..., description="Reference to core User")
            
            # Extension-specific fields
            extension_config: Dict[str, Any] = Field(default_factory=dict)
            last_extension_activity: Optional[datetime] = None
            extension_preferences: Optional[Dict[str, str]] = None
            
            # Table configuration
            table_comment: str = "MyExtension user data"
            __table_args__ = {
                "extend_existing": True,
                "info": {"extension": "my_extension"}
            }
            
        
        return [UserExtensionData]
```

### Migration Integration
Table extensions integrate with the Alembic migration system through automatic ownership detection:

```python
# Migration system automatically detects table ownership
# Based on @extension_model decorator registry and file location

# Extensions are responsible for their own table modifications:
# 1. Tables created in extension directories (extensions/{name}/BLL_*.py with DatabaseMixin)
# 2. Core tables modified via @extension_model decorator
# 3. Detected via MigrationManager.env_is_table_owned_by_extension()
```

### Extension Table Configuration
Extensions should configure tables with proper metadata:

```python
class MyExtensionModel(ApplicationModel, DatabaseMixin):
    """Model extended by MyExtension."""
    
    # Extension-specific fields
    custom_field: Optional[str] = None
    
    # Proper table configuration for extensions
    __table_args__ = {
        "extend_existing": True,  # Allow table modification
        "info": {
            "extension": "my_extension",  # Mark as extension table
            "version": "1.0.0",           # Extension version
            "migration_source": "extension"  # Migration tracking
        },
        "comment": "Table extended by MyExtension"
    }
```

### Best Practices for Table Extension

1. **Use @extension_model Decorator**: Primary pattern for extending existing models
2. **Mark Extension Tables**: Use `info` metadata to identify extension tables
3. **Namespace Fields**: Prefix extension fields to avoid conflicts
4. **Migration Compatibility**: Extensions automatically handled by migration system
5. **Reference Integrity**: Maintain foreign key relationships when extending tables
6. **Performance Considerations**: Index extension fields appropriately

### Static Extension Table Management
```python
class EXT_MyExtension(AbstractStaticExtension):
    @classmethod
    def get_extended_tables(cls) -> Dict[str, Type]:
        """Get tables extended by this extension."""
        return {
            "users": ExtendedUserModel,
            "teams": ExtendedTeamModel
        }
    
    @classmethod
    def validate_table_extensions(cls) -> bool:
        """Validate that table extensions are properly configured."""
        for table_name, model_class in cls.get_extended_tables().items():
            # Validate extend_existing is set
            if not getattr(model_class, "__table_args__", {}).get("extend_existing"):
                logger.warning(f"Table {table_name} missing extend_existing configuration")
                return False
        return True
```

## Seeding System

### Static Seeding Hooks
The system provides automatic seeding through static hook-based data injection:

```python
class EXT_MyExtension(AbstractStaticExtension):
    @classmethod
    def get_rotations_seed_data(cls) -> List[Dict[str, Any]]:
        """Provide rotation seed data for this extension."""
        # Check if root rotation already exists
        existing_rotation = cls.root
        if existing_rotation is not None:
            return []  # Already exists
            
        # Create rotation name using pluralization
        from lib.Environment import inflection
        extension_name_plural = inflection.plural(cls.name)
        rotation_name = f"Root_{extension_name_plural.capitalize()}"
        
        return [{
            "name": rotation_name,
            "description": f"Root rotation for {cls.name} extension",
            "extension_id": "extension_uuid",  # Set by seeding system
            "user_id": None,  # Set to ROOT_ID by seeding system
            "team_id": None
        }]

    @classmethod
    def get_rotation_provider_instances_seed_data(cls) -> List[Dict[str, Any]]:
        """Provide rotation/provider-instance instance associations."""
        # Automatically connects system provider instances to root rotation
        root_rotation = cls.root
        if not root_rotation:
            return []
            
        # Find associated providers and create associations
        seed_data = []
        # Implementation connects providers to rotation
        return seed_data
```

### Static Seeding Hook Triggers
```python
# Trigger seeding across all extensions
provider_seeds = AbstractStaticExtension.trigger_seeding_hooks("providers")
rotation_seeds = AbstractStaticExtension.trigger_seeding_hooks("rotations")

# Extensions can implement any get_{type}_seed_data class method
@classmethod
def get_custom_data_seed_data(cls) -> List[Dict[str, Any]]:
    """Custom seeding hook."""
    return [{"custom": "data"}]
```

## Root Rotations

### Static Root Rotation Access with Caching
Extensions automatically get access to their root rotation via static class properties with caching:

```python
from functools import lru_cache
from lib.Pydantic import classproperty

class EXT_MyExtension(AbstractStaticExtension):
    @classproperty
    @lru_cache(maxsize=1)
    def root(cls):
        """
        Get the Root RotationManager for this extension.
        Each extension gets its own root rotation based on its name.
        Cached after first access.
        """
        try:
            from logic.BLL_Extensions import ExtensionModel
            from logic.BLL_Providers import RotationManager, RotationModel

            root_id = env("ROOT_ID")
            session = get_session()

            try:
                # Try to find by extension relationship first
                stmt = select(ExtensionModel.DB).where(
                    ExtensionModel.DB(self.db_manager.Base).name == cls.name
                )
                extension_record = session.execute(stmt).scalar_one_or_none()

                if extension_record:
                    stmt = (
                        select(RotationModel.DB)
                        .where(
                            RotationModel.DB(self.db_manager.Base).extension_id == str(extension_record.id),
                            RotationModel.DB(self.db_manager.Base).created_by_user_id == root_id,
                        )
                        .limit(1)
                    )
                    rotation_record = session.execute(stmt).scalar_one_or_none()

                # Fallback to name-based lookup
                if not rotation_record:
                    from lib.Environment import inflection
                    extension_name_plural = inflection.plural(cls.name)
                    rotation_name = f"Root_{extension_name_plural.capitalize()}"

                    stmt = (
                        select(RotationModel.DB)
                        .where(
                            RotationModel.DB(self.db_manager.Base).name == rotation_name,
                            RotationModel.DB(self.db_manager.Base).created_by_user_id == root_id,
                        )
                        .limit(1)
                    )
                    rotation_record = session.execute(stmt).scalar_one_or_none()

                if rotation_record:
                    return RotationManager(
                        requester_id=root_id,
                        target_id=str(rotation_record.id),
                        db=None,
                    )

                return None

            finally:
                session.close()

        except Exception as e:
            logger.error(
                f"Error retrieving root rotation for extension {cls.name}: {e}"
            )
            return None

# Usage
root_rotation = MyExtension.root
if root_rotation:
    logger.debug(f"Root rotation: {root_rotation.name}")
```

### Static Root Rotation Discovery Process
1. **Extension Lookup**: Finds extension record in database by name
2. **Relationship Query**: Searches for rotations linked to extension via `extension_id`
3. **Name Fallback**: Falls back to name-based search using pluralized extension name
4. **Root User Filter**: Filters by `ROOT_ID` to find system rotations
5. **Error Handling**: Gracefully handles database errors and missing records
6. **Caching**: Results cached after first access for performance

## Component Organization

### File Naming Conventions
The import system automatically loads components based on file names:

```
my_extension/
├── EXT_MyExtension.py     # Required: Extension definition
├── BLL_MyExtension.py     # Optional: Business logic managers (with DatabaseMixin for models)
├── PRV_MyProvider.py      # Optional: Provider implementations (with external models)
└── AbstractProvider_MyDomain.py  # Optional: Abstract provider for domain
```

### Component Types

#### Business Logic Layer (BLL_*.py)
```python
from logic.AbstractLogicManager import AbstractBLLManager, ApplicationModel, DatabaseMixin

# Database models using DatabaseMixin
class MyEntityModel(ApplicationModel, DatabaseMixin):
    name: str = Field(..., description="Entity name")
    table_comment: str = "My extension entities"
    # The .DB property with SQLAlchemy model is automatically created

class MyEntityManager(AbstractBLLManager):
    Model = MyEntityModel
    ReferenceModel = MyEntityReferenceModel
    NetworkModel = MyEntityNetworkModel
    
    def custom_operation(self, param):
        # Custom business logic
        pass
```

#### Endpoint Layer (BLL_*.py with RouterMixin)
```python
from fastapi import APIRouter, Depends
from endpoints.AbstractEndpointRouter import AbstractEPRouter

router = APIRouter(prefix="/my-extension", tags=["My Extension"])

@router.get("/custom-endpoint")
async def custom_endpoint():
    return {"message": "Custom endpoint"}
```

#### Provider Layer (PRV_*.py)
```python
# PRV files now contain providers, external models, and external managers
from extensions.database.EXT_Database import EXT_Database

# External models (if applicable)
class MongoDB_DocumentModel(AbstractExternalModel):
    """MongoDB document model."""
    _id: str
    data: Dict[str, Any]

# External managers (if applicable)
class MongoDB_DocumentManager(AbstractExternalManager):
    Model = MongoDB_DocumentModel

# Provider implementation
class PRV_MongoDB(EXT_Database.AbstractProvider):
    name = "mongodb"
    
    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        # Implementation
        pass
```

#### Database Layer (BLL_*.py with DatabaseMixin)
```python
# BLL files contain Pydantic models with DatabaseMixin for database functionality
from logic.AbstractLogicManager import ApplicationModel, DatabaseMixin

class MyDatabaseModel(ApplicationModel, DatabaseMixin):
    """Database model for extension."""
    custom_field: str = Field(..., description="Custom field")
    table_comment: str = "Extension database table"
    
    # The DatabaseMixin automatically creates a .DB property with the SQLAlchemy model
```

## Creating an Extension

### Step 1: Extension Class
```python
from typing import Dict, Any, List, Set
from extensions.AbstractExtensionProvider import AbstractStaticExtension, ability
from lib.Dependencies import Dependencies, EXT_Dependency, PIP_Dependency, SYS_Dependency

class EXT_MyExtension(AbstractStaticExtension):
    # Static metadata using ClassVar
    name: str = "my_extension"
    version: str = "1.0.0"
    description: str = "My custom extension"
    
    # Environment variables  
    _env: Dict[str, Any] = {
        "MY_EXTENSION_SETTING": "default_value",
        "MY_EXTENSION_API_KEY": ""
    }
    
    # Unified dependencies
    dependencies: Dependencies = Dependencies([
        EXT_Dependency(name="core", optional=False, reason="Core functionality"),
        PIP_Dependency(name="requests", semver=">=2.28.0", reason="HTTP requests"),
        SYS_Dependency.for_all_platforms(
            name="git",
            apt_pkg="git",
            brew_pkg="git", 
            winget_pkg="Git.Git",
            reason="Version control operations"
        )
    ])
    
    # Abilities
    _abilities: Set[str] = set()
    
    # Provider cache
    _providers: List[Type] = []
        
    @classmethod
    def on_initialize(cls) -> bool:
        """Custom initialization logic."""
        api_key = cls.get_env_value("MY_EXTENSION_API_KEY")
        if api_key:
            cls.configure_api_client(api_key)
        return True
        
    @classmethod
    @ability("my_ability", enabled=True)
    def my_ability(cls, param: str) -> str:
        """Custom ability implementation."""
        return f"Processed: {param}"
        
    @classmethod
    def setup_hooks(cls):
        """Set up hooks for this extension."""
        from logic.AbstractLogicManager import hook_bll, HookContext, HookTiming
        
        @hook_bll(CoreManager.create, timing=HookTiming.BEFORE, priority=10)
        def validate_creation(context: HookContext) -> None:
            """Validate creation parameters."""
            # Hook logic here
            pass

    @classmethod
    def get_rotations_seed_data(cls) -> List[Dict[str, Any]]:
        """Provide seed data for rotations."""
        existing_rotation = cls.root
        if existing_rotation:
            return []  # Already exists
            
        return [{
            "name": f"Root_{cls.name.capitalize()}s",
            "description": f"Root rotation for {cls.name} extension",
            "extension_id": None,  # Set by seeding system
            "user_id": None,       # Set to ROOT_ID by seeding system
            "team_id": None
        }]

# Extension is automatically registered via ExtensionRegistry and __init_subclass__
```

### Step 2: Business Logic (Optional)
```python
# BLL_MyExtension.py
from logic.AbstractLogicManager import AbstractBLLManager, ApplicationModel, DatabaseMixin

class MyEntityModel(ApplicationModel, DatabaseMixin):
    name: str = Field(..., description="Entity name")
    table_comment: str = "My extension entities"

class MyEntityManager(AbstractBLLManager):
    Model = MyEntityModel
    # Implementation
```

### Step 3: Endpoints (Optional)
```python
# Endpoints are defined in BLL files with RouterMixin
from lib.Pydantic2FastAPI import RouterMixin, AuthType
from logic.AbstractLogicManager import AbstractBLLManager

class MyEntityManager(RouterMixin, AbstractBLLManager):
    prefix: ClassVar[str] = "/v1/my-extension"
    tags: ClassVar[List[str]] = ["My Extension"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    Model = MyEntityModel

    # Standard CRUD methods automatically generate endpoints
    # Custom routes can be added with decorators
```

### Step 4: Providers (Optional)
```python
# AbstractProvider_MyExtension.py - Define abstract provider
from extensions.AbstractExtensionProvider import AbstractStaticProvider, ability

class AbstractProvider_MyExtension(AbstractStaticProvider):
    # Extension type is auto-detected - no need to specify
    
    @abstractmethod
    @ability("my_service")
    def my_service(self, data: str) -> Dict[str, Any]:
        """Abstract ability that providers must implement."""
        pass

# PRV_MyProvider_MyExtension.py - Concrete provider implementation
class PRV_MyProvider_MyExtension(AbstractProvider_MyExtension):
    name: str = "my_provider"
    
    _env: Dict[str, Any] = {
        "MY_PROVIDER_API_KEY": "",
        "MY_PROVIDER_BASE_URL": "https://api.example.com"
    }
    
    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        """Bond a provider instance for API operations."""
        return MyProviderInstance(instance)
        
    def my_service(self, data: str) -> Dict[str, Any]:
        """Implement the abstract ability."""
        # Use self._instance to access bonded instance
        return self._instance.process_data(data)
```

## Testing Integration

### Extension Test Environment Isolation
Extensions provide isolated test environments for comprehensive testing:

```python
class EXT_MyExtension(AbstractStaticExtension):
    # Extension implementation...
    pass

class TestMyExtension(EXT_MyExtension.ServerMixin):
    # Uses extension's ServerMixin for isolated environment
    
    def test_extension_functionality(self, extension_server, db):
        """Test extension in isolated environment."""
        # This runs in test.my_extension.database.db with APP_EXTENSIONS=my_extension
        try:
            # Test extension-specific functionality
            pass
        finally:
            db.close()
```

### Provider Test Environment Inheritance
Providers inherit their parent extension's isolated test environment:

```python
class PRV_MyProvider(EXT_MyExtension.AbstractProvider):
    name = "my_provider"
    # Provider implementation...

class TestMyProvider(EXT_MyExtension.ServerMixin):
    # Uses parent extension's ServerMixin for isolated environment
    
    def test_provider_functionality(self, extension_server, db):
        """Test provider within parent extension's isolated environment."""
        # This runs in test.my_extension.database.db with APP_EXTENSIONS=my_extension
        # Provider tests inherit all isolation from parent extension
        providers = EXT_MyExtension.providers
        assert any(p.name == "my_provider" for p in providers)
```

### Test Architecture Overview

```
Extension Test Environment:
┌─────────────────────────────────────┐
│ Database: test.{ext_name}.database.db │
│ Server: APP_EXTENSIONS={ext_name}    │
│ Environment: Extension-isolated      │
│ Mixin: EXT_Extension.ServerMixin     │
└─────────────────────────────────────┘
                    ↓ (inherited by)
Provider Test Environment:
┌─────────────────────────────────────┐
│ Database: Same as parent extension   │
│ Server: Same as parent extension     │
│ Environment: Same as parent          │
│ Mixin: Uses extension's ServerMixin  │
└─────────────────────────────────────┘
```

## Best Practices

### Extension Design
1. **Static Implementation**: All extensions should be static/abstract classes with no instantiation
2. **Clear Metadata**: Define clear static metadata (name, version, description)
3. **Registry Integration**: Always register extensions in the static registry via `register_extension()`
4. **Class-Level Operations**: Use class methods for all extension functionality
5. **Static Dependencies**: Manage dependencies at the class level through the Dependencies system
6. **Configuration Management**: Handle configuration through static environment variable access with caching
7. **Auto-Discovery**: Leverage filesystem-based provider discovery with `@classproperty` caching
8. **Inner Class Pattern**: Define AbstractProvider as inner class for type safety and clear hierarchy
9. **Type Detection**: Trust automatic extension type detection based on file patterns
10. **External Models**: Define external models and managers in PRV files alongside providers

### Hook Usage
1. **Static Registration**: Register hooks through static class methods with `@hook_bll` decorator
2. **Method References**: Use `hook_bll` with manager method references for type safety
3. **Automatic Discovery**: Trust `_discover_static_hooks()` for automatic hook registration
4. **Minimal Impact**: Hooks should be lightweight and fast
5. **Error Isolation**: Hook failures shouldn't crash the main operation
6. **Clear Purpose**: Each hook should have a specific, documented purpose
7. **Context Usage**: Use `HookContext` for accessing method execution details

### Ability Design
1. **Static Methods**: Implement abilities as static class methods with `@classmethod`
2. **Clear Interface**: Abilities should have well-defined parameters and return types
3. **Automatic Discovery**: Use `_discover_static_abilities()` for automatic ability registration
4. **Parameter Validation**: Validate input parameters
5. **Documentation**: Include docstrings for ability documentation
6. **Static Registration**: Use the `@ability` decorator for automatic registration
7. **Error Handling**: Handle ability execution errors gracefully
8. **Meta vs Abstract**: Understand the distinction between meta abilities (extension-level) and abstract abilities (provider-level)
9. **Context Detection**: The ability decorator automatically detects context (no need for meta parameter)

### Dependencies Management
1. **Unified System**: Use the Dependencies class for all dependency types
2. **Static Access**: Access dependencies through class-level methods
3. **Clear Dependencies**: Declare all dependencies explicitly with reasons
4. **Optional Dependencies**: Mark non-critical dependencies as optional
5. **Version Constraints**: Specify version requirements for pip dependencies
6. **Dependency Resolution**: Use `resolve_dependencies()` for loading order

### Database Integration
1. **Static Models**: Define database models through class-level methods
2. **Migration Safety**: Ensure database changes are backward compatible
3. **Seed Data**: Provide necessary seed data through static seeding hooks
4. **Root Rotations**: Use the static root rotation system via `@classproperty` with caching
5. **Extension Models**: Use `@extension_model` decorator for extending existing models

### Environment Variables
1. **Static Access**: Use class methods for all environment variable access
2. **Cached Access**: Use `@classproperty` and `@lru_cache` for cached configuration
3. **Clear Naming**: Use consistent naming patterns for environment variables
4. **Default Values**: Provide sensible defaults in the `env` dictionary
5. **Configuration Methods**: Provide class methods for configuration access
6. **Error Handling**: Handle missing configuration gracefully
7. **Registration**: Use `_register_env_vars()` for environment variable registration

### Component Organization
1. **Naming Consistency**: Follow established naming conventions (EXT_, BLL_, PRV_)
2. **File Organization**: Keep related components together in logical groupings
3. **Static Imports**: Use static imports and class-level access patterns
4. **Registry Access**: Use registry methods for extension lookup and communication
5. **Automatic Loading**: Trust the file naming convention system for component discovery
6. **Provider Discovery**: Use cached filesystem scanning for provider discovery
7. **PRV File Structure**: Place providers, external models, and external managers in PRV files
8. **AbstractProvider Location**: Define as inner class in EXT file or separate AbstractProvider_*.py file
9. **Type-Based Organization**: Let automatic type detection guide component organization

### Static Method Design
1. **No State**: Extensions should be stateless at the class level
2. **Class Methods**: Use `@classmethod` for all extension functionality
3. **Registry Access**: Use the static registry for extension discovery and access
4. **Configuration Based**: All behavior should be determined by static configuration
5. **Error Handling**: Handle errors at the class level with appropriate exceptions
6. **Caching**: Use `@classproperty` and `@lru_cache` for expensive operations

### Testing Integration
1. **Isolated Testing**: Each extension gets its own isolated test environment
2. **Provider Inheritance**: Providers inherit parent extension's isolated test environment automatically
3. **Database Isolation**: Use separate databases for each extension's tests
4. **Server Isolation**: Run tests with only the target extension loaded
5. **Environment Consistency**: Maintain consistent environment configuration across tests
6. **Extension Linking**: Always link providers to parent extensions for test inheritance

### Testing and Validation
1. **Static Testing**: Test all functionality through class methods without instantiation
2. **Configuration Testing**: Test with various configuration scenarios
3. **Dependency Testing**: Test dependency resolution and installation
4. **Hook Testing**: Test hook registration and execution through discovery system
5. **Registry Testing**: Test extension registration and discovery mechanisms
6. **Provider Integration**: Ensure provider tests inherit extension environment correctly
7. **Ability Testing**: Test ability discovery and execution

### Performance Optimization
1. **Caching**: Use `@classproperty` and `@lru_cache` for expensive operations
2. **Discovery Optimization**: Trust cached provider and ability discovery
3. **Lazy Loading**: Load components only when needed
4. **Static Access**: Minimize database queries through cached static access
5. **Hook Efficiency**: Keep hooks lightweight for performance
6. **Registry Efficiency**: Use static registry for fast extension lookup

### Security Considerations
1. **Credential Security**: Never log or expose API keys or credentials
2. **Input Validation**: Validate all inputs in abilities and hooks
3. **Configuration Validation**: Validate configuration to prevent insecure setups
4. **Static Access**: Ensure static methods don't expose sensitive configuration
5. **Hook Security**: Validate hook context data before processing
6. **Extension Isolation**: Maintain proper isolation between extensions

### Documentation
1. **Clear Interface**: Document all public class methods and their parameters with type hints
2. **Configuration Guide**: Provide clear configuration instructions with examples
3. **Static Usage**: Document how to use extensions through class methods
4. **Auto-Discovery**: Document discovery patterns and file organization
5. **Environment Variables**: Document all required and optional environment variables
6. **Hook Documentation**: Document hook registration and execution patterns
7. **Ability Documentation**: Document ability registration and usage patterns
