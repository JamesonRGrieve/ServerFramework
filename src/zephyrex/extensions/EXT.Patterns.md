# Extensions

This document describes extension-specific patterns and architecture.

> **Common Patterns**: For CRUD model patterns, error handling, and configuration patterns shared across all layers, see [Framework.md](../Framework.md#common-patterns-across-layers).

Extensions are the primary mechanism for extending server functionality in ZephyrexFrameworkServer. They provide **static functionality, metadata organization, and component integration** through automated loading systems. Extensions are implemented as **static/abstract classes** that coordinate the loading of database models, business logic managers, endpoints, and providers through file naming conventions.

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
13. [External Federation (REST + GraphQL)](#step-5-external-federation-optional)
14. [Testing Integration](#testing-integration)
15. [Best Practices](#best-practices)
16. [Architectural Improvements](#architectural-improvements)

## Extension Architecture

### Core Structure
All extensions inherit from `AbstractStaticExtension` and must define static properties and class methods:

```python
from typing import Dict, List, Set
from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency, PIP_Dependency, SYS_Dependency

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
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency, PIP_Dependency, SYS_Dependency

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
from zephyrex.logic.AbstractLogicManager import hook_bll, HookContext, HookTiming

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
from zephyrex.extensions.AbstractExtensionProvider import ability

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
from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticProvider

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
from zephyrex.extensions.AbstractExternalModel import AbstractExternalModel, AbstractExternalManager

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

External models typically include format conversion methods:

```python
class Stripe_CustomerModel(AbstractExternalModel):
    """External model with format conversion methods."""

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
from zephyrex.extensions.AbstractExternalModel import external_navigation_property

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
from zephyrex.extensions.AbstractExternalModel import AbstractExternalManager

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
from zephyrex.lib.Environment import env

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
from zephyrex.lib.Pydantic2SQLAlchemy import extension_model, RemoveField
from zephyrex.logic.BLL_Auth import UserModel  # Import existing model

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
        from zephyrex.lib.Environment import inflection
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
from zephyrex.lib.Pydantic import classproperty

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
            from zephyrex.logic.BLL_Extensions import ExtensionModel
            from zephyrex.logic.BLL_Providers import RotationManager, RotationModel

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
                    from zephyrex.lib.Environment import inflection
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
from zephyrex.logic.AbstractLogicManager import AbstractBLLManager, ApplicationModel, DatabaseMixin

# Database models using DatabaseMixin
class MyEntityModel(ApplicationModel, DatabaseMixin):
    name: str = Field(..., description="Entity name")
    table_comment: str = "My extension entities"
    # The .DB() classmethod with SQLAlchemy model is automatically created

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
from zephyrex.endpoints.AbstractEndpointRouter import AbstractEPRouter

router = APIRouter(prefix="/my-extension", tags=["My Extension"])

@router.get("/custom-endpoint")
async def custom_endpoint():
    return {"message": "Custom endpoint"}
```

#### Provider Layer (PRV_*.py)
```python
# PRV files now contain providers, external models, and external managers
from zephyrex.extensions.database.EXT_Database import EXT_Database

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
from zephyrex.logic.AbstractLogicManager import ApplicationModel, DatabaseMixin

class MyDatabaseModel(ApplicationModel, DatabaseMixin):
    """Database model for extension."""
    custom_field: str = Field(..., description="Custom field")
    table_comment: str = "Extension database table"
    
    # The DatabaseMixin automatically creates a .DB() classmethod with the SQLAlchemy model
```

## Creating an Extension

### Step 1: Extension Class
```python
from typing import Dict, Any, List, Set
from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension, ability
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency, PIP_Dependency, SYS_Dependency

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
        from zephyrex.logic.AbstractLogicManager import hook_bll, HookContext, HookTiming
        
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
from zephyrex.logic.AbstractLogicManager import AbstractBLLManager, ApplicationModel, DatabaseMixin

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
from zephyrex.lib.Pydantic2FastAPI import RouterMixin, AuthType
from zephyrex.logic.AbstractLogicManager import AbstractBLLManager

class MyEntityManager(AbstractBLLManager, RouterMixin):
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
from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticProvider, ability

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

### Step 5: External Federation (Optional)

> **Detailed reference:** [../lib/LIB.Federation.md](../lib/LIB.Federation.md)

If your extension talks to a third-party API — REST or GraphQL — federation lifts the upstream into the framework's model registry so the same upstream becomes queryable through BOTH inbound surfaces (REST and GraphQL) without per-resource hand-coding. Pick the path that matches your upstream's wire format:

#### 5a. GraphQL upstream (`AbstractGraphQLProvider`)

When the upstream speaks GraphQL, declare a provider that subclasses `AbstractGraphQLProvider`. The framework introspects the upstream at startup, transforms the SDL through the configured pipeline (rename / prefix / hide-fields / mask-arguments / override-resolvers), registers the transformed types into the merged schema, and lifts them into Pydantic so `Pydantic2Strawberry` and `Pydantic2FastAPI` project them onto both surfaces.

```python
# extensions/my_extension/PRV_MyGQL.py
from zephyrex.extensions.AbstractGraphQLProvider import AbstractGraphQLProvider

class PRV_MyGQL_Federated(AbstractGraphQLProvider):
    name: str = "my_gql"

    upstream_url: str = "https://api.example.com/graphql"
    federation_style: str = "stitching"      # | "apollo_v2" | "namespaced"
    type_namespace: Optional[str] = "Acme_"  # prefix every upstream type
    auth_strategy_name: str = "api_key"      # AuthStrategy registry key

    # Optional transformer overrides; the introspect→transform→register
    # pipeline reads these.
    schema_rename: Dict[str, str] = {}
    schema_hide_fields: Dict[str, set] = {"User": {"password"}}
    schema_mask_arguments: Dict[str, Dict[str, set]] = {}

    # Persistent (Redis-shaped) cache TTLs by upstream type.
    persistent_cache_ttls: Dict[str, float] = {"Acme_Customer": 30.0}

    # When True (default), the SDL is also lifted into Pydantic models so
    # the upstream appears on the local REST surface in addition to GQL.
    lift_into_pydantic: bool = True

    @classmethod
    def bond_instance(cls, instance):
        from zephyrex.extensions.AbstractExtensionProvider import (
            AbstractProviderInstance,
        )
        return AbstractProviderInstance(instance)
```

That's it — the framework handles introspection, schema transformation, registration with `MergedSchemaRegistry`, batched cross-subgraph resolution, per-request response caching, and selection-set push-down on every outbound call.

#### 5b. REST upstream (OpenAPI lift)

When the upstream speaks REST and ships an OpenAPI document, the extension advertises the document and a transport factory; the framework lifts every `components.schemas` entry into Pydantic, derives `AbstractExternalModel` subclasses bound to the transport, and routes their `*_via_provider` calls through `RESTUpstreamTransport`. The lifted models flow through both surfaces automatically.

```python
class EXT_MyExtension(AbstractStaticExtension):
    name: str = "my_extension"

    @classmethod
    def openapi_spec_provider(cls) -> Mapping[str, Any]:
        """Return the OpenAPI document for the upstream this extension federates."""

        # Load from the bundled snapshot under contracts/upstream.openapi.json,
        # or fetch live and cache. The snapshot path is preferred for
        # deterministic CI.
        import json
        from pathlib import Path

        snapshot = Path(__file__).parent / "contracts" / "upstream.openapi.json"
        return json.loads(snapshot.read_text())

    @classmethod
    def federation_rest_transport_factory(cls):
        """Build the RESTUpstreamTransport used by lifted models."""

        from zephyrex.lib.Federation_REST import (
            RESTUpstreamTransport,
            openapi_to_pydantic_models,
        )
        from zephyrex.lib.ProviderHTTPClient import ProviderHTTPClientSync

        spec = cls.openapi_spec_provider()
        operations = openapi_to_pydantic_models(spec).operations
        http = ProviderHTTPClientSync(
            provider_name=cls.name,
            # AuthStrategy / rotation / rate-limit pulled from the bonded
            # provider instance per the standard provider pipeline.
        )
        return RESTUpstreamTransport(
            http, base_url="https://api.example.com", operations=operations
        )
```

#### 5c. Federation matrix tests

Every extension that federates an external upstream gets 4 quadrants × 5 CRUD = 20 cells of homologation coverage automatically once it advertises a `federation_matrix_fixtures` classmethod (or relies on the OpenAPI/SDL shape from §5a/5b). The classmethod returns one or more `FederationFixture` instances; the framework's programmatic test generator emits a `Test_Federation_<extension>_<type>_Matrix` class per fixture into `extensions/Federation_Matrix_test.py`'s globals, and pytest collects them on the next run.

```python
class EXT_MyExtension(AbstractStaticExtension):
    @classmethod
    def federation_matrix_fixtures(cls):
        from zephyrex.extensions.AbstractFederationMatrixTest import (
            FederationFixture,
        )

        return [
            FederationFixture(
                name="EXT_MyExtension.Customer",
                upstream_kind="rest",                 # | "gql"
                transport=cls.federation_rest_transport_factory(),
                sample_id="cus_test",                 # seeded id the upstream knows
                type_name="Customer",                 # the lifted Pydantic class
                sdl_or_spec=cls.openapi_spec_provider(),
                create_payload={"name": "X"},         # body for create-test
                update_payload={"name": "Y"},         # body for update-test
                operations_supported=["get", "list", "create", "update", "delete"],
                crud_map={"get": "get_customer", "list": "list_customer", ...},
                requires_credentials=False,           # in-process upstream by default
                credentials_present=lambda: bool(os.getenv("MY_API_KEY")),
            )
        ]
```

The matrix asserts that the same logical operation through `/graphql` and through the REST surface returns equivalent payloads on every shared field. In-process upstreams (a tiny FastAPI ASGI app served via `httpx.ASGITransport`) are the default for CI determinism; live upstreams activate when `requires_credentials=True` and `credentials_present()` returns True (otherwise pytest auto-xfails per [EXT.Test.External.md](EXT.Test.External.md)).

#### 5d. Cross-linking external models with local ones

Local models reference federated entities via a single hand-written FK field; the rest of the external graph propagates through `@key`-driven federation (Apollo v2) or the matching `FederationLink` registry (stitching/REST). For example:

```python
class UserModel(ApplicationModel, DatabaseMixin):
    id: str
    # External FK — anchors the User → Stripe_Customer cross-link.
    # `external_navigation_property` reads the field at GraphQL resolve
    # time and dispatches through the BatchedNavigationResolver to honor
    # the request's `include` set (Item 9).
    stripe_customer_id: Optional[str] = None
    stripe_customer = external_navigation_property(
        Stripe_CustomerModel,
        local_field="stripe_customer_id",
    )
```

Referential integrity at the seam is best-effort eventual consistency — external systems do not enforce our FKs. The framework supports four enforcement points: write-time GET-before-persist (opt-in per field), inbound-webhook-driven invalidation (Item 5), periodic reconciliation jobs, and read-time tolerance (navigation returns `None` on upstream 404 in lenient mode).

#### 5e. What the framework runs at startup

`ModelRegistry.commit()` Phase 1.6 runs the federation pipeline synchronously:

1. Discover concrete `AbstractGraphQLProvider` subclasses and extensions exposing `openapi_spec_provider`.
2. For GraphQL upstreams: introspect (Apollo `_service { sdl }` probe or standard introspection), apply `SchemaTransformer`, register with `MergedSchemaRegistry`, lift to Pydantic, synthesize `AbstractExternalManager` subclasses, bind with the registry.
3. For REST upstreams: import OpenAPI, derive external models bound to the transport, synthesize managers, bind.
4. Mount GQL→REST projection routers under `/federated/{provider}/...` so REST clients can hit GQL upstreams.
5. Build the merged schema once.

Failures are isolated per provider — an unreachable upstream MUST NOT prevent the registry from committing or the rest of the framework from starting. The lifted classes appear on `model_registry._federation_report.models`; the GQL→REST routers are on `model_registry._federation_report.rest_routers`; per-provider errors are on `.errors`.

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

### Federation Matrix Tests (Auto-Generated)

> **Detailed reference:** [../lib/LIB.Federation.md](../lib/LIB.Federation.md#matrix-homologation-testing)

Extensions that federate an external upstream — REST or GraphQL — get 4 quadrants × 5 CRUD = 20 cells of homologation coverage automatically. The matrix proves that regardless of whether the upstream speaks REST or GraphQL, the same logical operation through `/graphql` and through the REST surface returns equivalent payloads on every shared field.

```
                │ external GQL │ external REST │
────────────────┼──────────────┼───────────────┤
local GQL surf  │   GQL→GQL    │   REST→GQL    │
local REST surf │   GQL→REST   │   REST→REST   │
────────────────┴──────────────┴───────────────┘
```

**To get matrix coverage for your extension:**

1. Declare a `federation_matrix_fixtures` classmethod returning one or more `FederationFixture` instances (see Step 5c above). Each fixture exercises one upstream type through the matrix.
2. Pytest's collection hook in `extensions/Federation_Matrix_test.py` calls `generate_matrix_tests(target_namespace=globals())` at import time, which mutates the test module's globals to inject one `Test_Federation_<extension>_<type>_Matrix` class per discovered fixture.
3. Run `pytest extensions/Federation_Matrix_test.py -v` and the matrix runs against your upstream alongside the framework's reference suites and every other extension's matrix.

**In-process by default, live runs gated on credentials.** The reference fixtures bind to in-process FastAPI ASGI apps so CI is deterministic. Real-upstream runs activate when the fixture's `requires_credentials=True` and `credentials_present()` returns True; otherwise pytest auto-xfails the suite per [EXT.Test.External.md](EXT.Test.External.md). The bundled `EXT_Payment` (Stripe) and `EXT_EMail` (SendGrid) extensions show both shapes.

**No mocks.** The matrix uses real Pydantic models, real `Pydantic2{Strawberry,FastAPI}` projections, real `RESTUpstreamTransport` / `GQLUpstreamTransport`, and real httpx ASGI transports. A failing cell points at a real divergence between surfaces, not a quirk of how the test set up its fakes.

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
7. **Federation Matrix Coverage**: When the extension federates an external upstream, declare a `federation_matrix_fixtures` classmethod so the programmatic test generator emits 4×CRUD = 20 cells of homologation coverage automatically. In-process upstreams cover CI; live-credential runs activate when the fixture's `requires_credentials` and `credentials_present()` are True.

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

## Field Injection Collision Detection

After extension discovery completes, the registry walks the merged model graph and rejects field-name collisions across extensions. When two extensions both inject a field of the same name into the same core model (e.g. both `payment` and `legacy_billing` adding `external_payment_id` to `UserModel`), this is a startup error — the application refuses to start, the error message names both extensions, the model, the colliding field, and the file paths and line numbers of both declarations.

The only exception is when both extensions declare an exactly identical field — same type, same default, same metadata. Type identity uses Pydantic's field-info comparison; "exactly identical" is strict. In that case the registry accepts the duplicate as a no-op duplication.

## Migration Ownership

Migration ownership has one authoritative resolution rule. File-path detection is the authoritative mechanism for extension-owned tables (tables whose models live in `src/extensions/{name}/BLL_*.py`). The `info={"extension": name}` entry on `__table_args__` is the authoritative mechanism for field injections into core tables via `@extension_model` — the decorator sets the info dict automatically, so authors do not write it by hand. The decorator-set info dict merges with any existing `__table_args__` rather than overwriting.

`MigrationManager.env_is_table_owned_by_extension(table)` checks the info dict first (covering the injection case) and falls back to file-path inspection (covering the new-table case). A small CLI command lists every table and its owning extension (or core, when applicable), so operators can audit ownership.

## Cross-Extension Migration Ordering

Migration ordering is computed as a topological sort over the union of (a) declared `EXT_Dependency` relationships and (b) FK references discovered by inspecting model definitions. An extension whose model has an FK into another extension's model implicitly depends on that extension for migration purposes, even if it did not declare the dependency explicitly. Cycles in the merged graph (an FK from A to B and another from B to A) fail at startup with a clear error naming the offending tables and extensions.

The topological sort runs once at startup and is cached. Cross-extension FK detection inspects both `@extension_model` field injections and standalone extension tables. FK detection requires the model classes to be loaded before migrations run; the framework's extension registry imports all models on startup before delegating to the Alembic env, and the migration runner depends on this load order. Extensions that genuinely need bidirectional references introduce a join table owned by one of the extensions, rather than direct FKs in both directions.

## Out-of-Tree Extensions

`ExtensionRegistry.__init__` accepts `extensions_path`, and the path-resolution helpers honor it. Module loading uses `importlib.util.spec_from_file_location` + `module_from_spec` + `spec.loader.exec_module` against a synthesized module name (e.g. `zephyrex_ext_<name>_<file>`), registered under both its synthesized name and `extensions.<name>.<file>` in `sys.modules` so existing intra-extension imports (`from extensions.payment.BLL_Payment import ...`) keep resolving.

Migration discovery for out-of-tree extensions uses the same mechanism: `database/migrations/env.py` consults `lib.Paths.extensions_dir()` instead of computing the path inline. When the registry is constructed with `extensions_path`, that path becomes the search root for migrations as well as for code. Alembic's `script_location` setup tolerates multiple roots — one for the framework's core migrations, N for each extension.

A consumer pointing the framework at `./my_extensions` has every extension under that path discovered, imported, registered, and operational, identical to in-package extensions.

## Optional Dependencies and Startup Banner

Each `EXT_Dependency` declared as `optional=True` accepts an `on_optional_missing` callback. The default callback logs a structured warning naming the missing dependency and the abilities it would have enabled. At startup, the framework prints a banner listing every skipped optional dependency and the resulting disabled abilities, so operators see at a glance what the running configuration omits. Extensions can register richer fallback behavior (use a degraded local implementation, disable a feature flag, send an admin notification) via the callback hook.

The banner is emitted on stdout during startup and also written to a structured event in the audit log, so post-mortem debugging can recover the configuration state. The disabled-abilities portion requires extensions to declare which abilities depend on which optional dependencies — a small additional metadata declaration. The condition is queryable at runtime via an admin endpoint.

## Manifest-Driven Installation

A `manifest.toml` per extension declares metadata, dependencies (extensions, pip, system), entry points, and version. The schema is in `extensions/Manifest.py` (`ExtensionManifest`). Manifest loading is additive: extensions without a `manifest.toml` continue to be discovered by the legacy filesystem walk in `ExtensionRegistry`. `extensions/auth_mfa/manifest.toml` is the bundled example.

`extensions/Install.py` exposes `install_from_manifest(source, *, registry, run_migrations=True, overwrite=False, max_download_bytes=50MiB, http_timeout_seconds=30)`. `source` can be a local directory, a local archive (`.tar.gz` / `.zip`), or an `http(s)://` / `file://` URL pointing at one of those. The flow is: resolve source (download + extract when needed; archive entries are validated against path-traversal escapes), validate `manifest.toml`, fail fast if any required `extension_dependencies` are missing from the registry, copy into `lib.Paths.extensions_dir()` (refusing to overwrite by default), run migrations via `MigrationManager.run_extension_migration` (best-effort; never drops tables), and update `registry.loaded_extensions`. The function never raises; failures are returned as `InstallResult(success=False, error_detail=...)`.

`extensions/HotReload.py` provides `discover_on_disk_versions`, `compute_diff(prev, cur) -> RegistryDiff`, and `rebuild_registry(prev_registry, *, extensions_csv=None, run_migrations=True) -> (new_registry, RegistryDiff)`. The diff distinguishes added / removed / changed_version / unchanged. Removed extensions are simply absent from the new registry; the caller drops the old registry to release its hooks/routers/services.

SIGHUP triggers `app.install_sighup_handler`, which performs the diff in-process (so operator logs document the change) and then exits with code `SIGHUP_RESTART_EXIT_CODE = 75` so the supervisor (systemd `Restart=on-failure`, k8s `restartPolicy: Always`, `docker --restart=always`) respawns with the new code on disk. This is the explicit "graceful exit and respawn" fallback called out in the Item 20 refinement. Under `PYTEST_CURRENT_TEST` the exit is suppressed so tests can drive `rebuild_registry` directly. On platforms without SIGHUP (Windows) the handler installer is a no-op; operators on those platforms run blue-green deployments.

Migrations during install / rebuild are non-destructive — uninstall (operator removes the extension directory and SIGHUPs) leaves migration history and tables in place so reinstalling later preserves user data. A failed install rolls back by setting `overwrite=True` on the next attempt or by manual directory removal.

True in-process hot reload of code without restart is not supported. The phrase "preserve static class identity across reloads" is the entire problem and cannot be solved without a class-registry rewrite that tracks every place a class object is captured (cached references in other modules, hook decorators that registered at import time, Pydantic models that cache `__pydantic_validator__` against class objects, SQLAlchemy mappers that cannot be cleanly unmapped, Strawberry schemas baked at startup). Deployments that need code-update-without-restart use blue-green at the process level. Install/uninstall via clean restart is the contract.
