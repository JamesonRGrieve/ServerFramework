# Providers

This document describes provider-specific patterns and architecture.

> **Common Patterns**: For CRUD model patterns, error handling, and configuration patterns shared across all layers, see [Framework.md](../Framework.md#common-patterns-across-layers).

Providers are the service integration layer for extensions, offering standardized interfaces to external services and abilities. They are implemented as **static/abstract classes** that require no instantiation and provide all functionality through class methods and static properties. Providers are **automatically discovered** by their parent extension through filesystem scanning.

## Key Concepts

- **Static Architecture**: All providers are static classes inheriting from `AbstractStaticProvider`
- **Auto-Discovery**: Extensions automatically discover providers via `extension.providers` property
- **Instance Bonding**: Providers use `bond_instance()` to create bonded instances for API operations
- **Ability System**: Providers declare abilities using the `@ability` decorator
- **Rotation Integration**: Providers integrate with RotationManager for failover; load balancing across active-active providers is NOT a concern of this system. Production deployments place HAProxy or an equivalent L7 load balancer in front of provider endpoints when load distribution is desired. The rotation chain is a failover mechanism, not a load distributor. Round-robin, weighted, latency-based routing, and percentage-canary routing are all explicitly out of scope for the framework. The one carve-out is application-level session stickiness (pinning an LLM conversation to one upstream); that remains rotation's concern because L7 cannot see conversation IDs.
- **Type-Based Environment Variables**: External extensions auto-register API-related env vars

## Table of Contents
1. [Provider Architecture](#provider-architecture)
2. [Provider Hierarchy](#provider-hierarchy)
3. [Provider Rotation System](#provider-rotation-system)
4. [Provider Instance Bonding](#provider-instance-bonding)
5. [External Model Integration](#external-model-integration)
6. [Provider Configuration](#provider-configuration)
7. [Environment Variables](#environment-variables)
8. [Ability Management](#ability-management)
9. [Auto-Discovery System](#auto-discovery-system)
10. [Extension Integration](#extension-integration)
11. [Creating Providers](#creating-providers)
12. [Testing Integration](#testing-integration)
13. [Best Practices](#best-practices)

## Provider Architecture

### Core Provider Structure
All providers inherit from `AbstractStaticProvider` which provides static/classmethod functionality:

```python
from typing import ClassVar, Dict, Any, Set
from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticProvider, ability
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency, SYS_Dependency
from zephyrex.logic.BLL_Providers import ProviderInstanceModel
from zephyrex.extensions.AbstractExtensionProvider import AbstractProviderInstance

class PRV_MyProvider_MyExtension(AbstractStaticProvider):
    # Static provider metadata
    name: str = "my_provider"
    description: str = "My service provider"
    
    # Unified dependencies using the Dependencies class  
    dependencies: ClassVar[Dependencies] = Dependencies([
        PIP_Dependency(name="requests", semver=">=2.28.0", reason="HTTP requests"),
        SYS_Dependency.for_all_platforms(
            name="curl", 
            apt_pkg="curl", 
            brew_pkg="curl",
            winget_pkg="cURL.cURL",
            reason="HTTP client tools"
        )
    ])
    
    # Environment variables this provider needs
    _env: ClassVar[Dict[str, Any]] = {
        "MY_PROVIDER_SETTING": "default_value",
        "MY_PROVIDER_API_KEY": ""
    }
    
    # Static abilities
    _abilities: ClassVar[Set[str]] = set()
    
    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        """Bond a provider instance for API operations."""
        return MyProviderInstance(instance)
        
    @classmethod
    def get_abilities(cls) -> Set[str]:
        """Return abilities this provider supports."""
        return cls._abilities.copy()
```

### Auto-Discovery System
Providers are automatically discovered by their parent extension through filesystem scanning:
- Extensions scan their folder for `PRV_*.py` files
- All classes inheriting from `AbstractStaticProvider` are automatically discovered
- No manual registration required - providers are accessible via `extension.providers`
- Discovery is cached for performance
- Providers are matched to extensions via their `extension_type` attribute

### Provider Discovery Process
1. **File Placement**: Provider files placed as `PRV_*.py` in extension directory
2. **Automatic Scanning**: Extension scans for provider files during first access to `.providers`
3. **Class Detection**: System discovers all `AbstractStaticProvider` subclasses in provider files
4. **Caching**: Results cached at the extension class level for performance
5. **Access**: Providers accessible through `extension.providers` list

## Provider Hierarchy

### AbstractStaticProvider (Base Class)
The foundation for all providers, providing static functionality:
- Environment variable management with class-level access via `get_env_value()`
- Static ability tracking with registration methods (`register_ability()`, `has_ability()`)
- Static dependency management through unified Dependencies class
- Configuration validation framework (`validate_config()`, `is_configured()`)
- Extension type attribute for linking to extensions

### Abstract Provider Pattern
Extensions define abstract providers that concrete providers must implement. The abstract provider can be defined either as an inner class within the extension or as a separate class in the same file:

```python
from abc import abstractmethod
from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticProvider, ability

# Option 1: Inner class pattern
class EXT_MyExtension(AbstractStaticExtension):
    name = "my_extension"
    
    class AbstractProvider(AbstractStaticProvider):
        """Abstract provider for my extension - providers should inherit from this."""
    
    @abstractmethod
    @ability("process_data")
    def process_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Process data - must be implemented by concrete providers."""
        pass

# Option 2: Separate class in same file
class AbstractProvider_MyExtension(AbstractStaticProvider):
    """Abstract provider for my extension - providers should inherit from this."""
    extension_type = "my_extension"
    
    @abstractmethod
    @ability("process_data")
    def process_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Process data - must be implemented by concrete providers."""
        pass
        
    @abstractmethod
    @ability("validate_input")
    def validate_input(self, input_data: str) -> bool:
        """Validate input - must be implemented by concrete providers."""
        pass

# In PRV_MyProvider_MyExtension.py  
class PRV_MyProvider_MyExtension(EXT_MyExtension.AbstractProvider):
    # Static metadata
    name: str = "my_provider"
    description: str = "My API service provider"
    
    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        """Bond a provider instance for API operations."""
        return MyProviderInstance(instance)
    
    def process_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Implement the abstract process_data ability."""
        # Use self._instance to access bonded instance
        return self._instance.api_process(data)
        
    def validate_input(self, input_data: str) -> bool:
        """Implement the abstract validate_input ability."""
        return len(input_data) > 0
```

### Environment Variable Auto-Registration
```python
# Environment variables are automatically registered based on extension type
# External extensions (those with PRV files or external models) get standard API-related env vars

class EXT_MyExtension(AbstractStaticExtension):
    name = "my_extension"
    # If extension has ExtensionType.EXTERNAL in its types:
    # These are automatically registered:
    # MY_EXTENSION_API_KEY, MY_EXTENSION_SECRET_KEY, MY_EXTENSION_WEBHOOK_SECRET, 
    # MY_EXTENSION_CURRENCY, MY_EXTENSION_TIMEOUT, MY_EXTENSION_RETRY_COUNT

class PRV_MyProvider_MyExtension(EXT_MyExtension.AbstractProvider):
    name: str = "my_provider"
    
    # Provider-specific env vars can be added
    _env: Dict[str, Any] = {
        "MY_PROVIDER_CUSTOM_SETTING": "default_value"
    }
```

## Provider Rotation System

### Rotation Algorithm
Linear failover: single sequence of providers tried in order until success or exhaustion.

**Structure:**
- Single linear linked list
- Each provider: 0 or 1 parent (unique constraint on `parent_id`)
- One provider with `parent_id=None` (root)
- Forms single chain: A → B → C → D → E

**Flow:**
1. Try first provider
2. On failure (any exception), try next
3. Continue until success/exhaustion
4. Raise HTTPException 500 if all fail

**Failure Detection:** Any exception triggers rotation (src/logic/BLL_Providers.py:1035)

**Implementation:** `rotate()` (src/logic/BLL_Providers.py:956-1061), `_get_ordered_rotation_provider_instances()` (lines 1063-1118)

**Usage:**
```python
result = EXT_Payment.root.rotate(
    lambda instance: instance.create_customer(email="user@example.com")
)
if result.get('success'):
    customer_data = result['data']
```

**Example:**
```
Provider_A (parent=None) → Provider_B (parent=A) → Provider_C (parent=B) → Provider_D (parent=C)

Rotation Order: A → B → C → D
```

### Provider Instance Bonding Pattern
The Provider Rotation System uses instance bonding to manage API credentials and configuration:

```python
from zephyrex.logic.BLL_Providers import ProviderInstanceModel, RotationManager

class PRV_Stripe_Payment(AbstractProvider_Payment):
    """Stripe payment provider for rotation system."""
    
    name: ClassVar[str] = "stripe"
    
    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        """Bond a provider instance for API operations."""
        return StripeProviderInstance(instance)
    
    def create_payment(self, amount: Decimal, currency: str, **kwargs) -> Dict[str, Any]:
        """Create a payment using Stripe API."""
        # Access bonded instance via self._instance
        stripe_charge = self._instance.create_charge(
            amount=int(amount * 100),  # Convert to cents
            currency=currency,
            **kwargs
        )
        return {"success": True, "charge_id": stripe_charge.id}
```

### Provider Instance Implementation
```python
class Stripe_ProviderInstance(AbstractProviderInstance_SDK):
    """Stripe provider instance with SDK integration."""
    
    def __init__(self, api_key: str, webhook_secret: str = None, currency: str = "USD"):
        import stripe
        stripe.api_key = api_key
        super().__init__(stripe)  # Pass SDK to parent
        
        self.webhook_secret = webhook_secret
        self.currency = currency
    
    def create_customer(self, email: str, name: str = None, **metadata) -> dict:
        """Create a Stripe customer."""
        try:
            customer = self.sdk.Customer.create(
                email=email,
                name=name,
                metadata=metadata
            )
            return {"success": True, "data": customer}
        except Exception as e:
            return {"success": False, "error": str(e)}
```

### Rotation Manager Integration
```python
# Extension provides access to rotation manager
class EXT_Payment(AbstractStaticExtension):
    @classproperty
    def root(cls) -> Optional[RotationManager]:
        """Get the Root RotationManager for this extension."""
        # Cached access to Root_Payment rotation
        return cls._root_rotation_cache

# Usage through rotation system
payment_root = EXT_Payment.root
if payment_root:
    result = payment_root.rotate(
        lambda instance: instance.create_customer(
            email="user@example.com",
            name="John Doe"
        )
    )
```

## Provider Instance Bonding

### Abstract Provider Instance Pattern
```python
from zephyrex.extensions.AbstractExtensionProvider import AbstractProviderInstance

class AbstractProviderInstance(ABC):
    """Base class for provider instances."""
    pass

class AbstractProviderInstance_SDK(AbstractProviderInstance):
    """Provider instance with SDK integration."""
    
    def __init__(self, sdk):
        if not sdk:
            raise Exception("An SDK is required for this provider.")
        self._sdk = sdk
    
    @property
    def sdk(self):
        return self._sdk
```

### Provider Instance Lifecycle
1. **Instance Creation**: ProviderInstanceModel created with credentials
2. **Bonding**: Provider class bonds instance using `bond_instance()`
3. **SDK Initialization**: Provider instance initializes SDK with credentials
4. **Operation Execution**: Rotation system calls methods on bonded instance
5. **Error Handling**: Instance returns standardized success/error responses

### Bonding Implementation Patterns
```python
# Simple API key bonding
@classmethod
def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
    """Bond instance with API key."""
    return SimpleProviderInstance(instance.api_key)

# Complex bonding with multiple credentials
@classmethod
def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
    """Bond instance with multiple credentials."""
    return ComplexProviderInstance(
        api_key=instance.api_key,
        secret_key=instance.get_setting("secret_key"),
        region=instance.get_setting("region", "us-east-1"),
        endpoint=instance.model_name  # Custom field usage
    )

# SDK-based bonding
@classmethod
def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
    """Bond instance with SDK initialization."""
    import external_sdk
    
    sdk_client = external_sdk.Client(
        api_key=instance.api_key,
        base_url=instance.get_setting("base_url")
    )
    
    return SDK_ProviderInstance(sdk_client)
```

## External Model Integration

### AbstractExternalModel Pattern
Providers can integrate with external APIs through standardized external models:

```python
from zephyrex.extensions.AbstractExternalModel import AbstractExternalModel

class Stripe_CustomerModel(AbstractExternalModel):
    """External model representing Stripe customers."""
    
    # Pydantic model fields
    id: str = Field(..., description="Stripe customer ID")
    email: str = Field(..., description="Customer email")
    name: Optional[str] = Field(None, description="Customer name")
    created: Optional[int] = Field(None, description="Creation timestamp")
    
    # External API resource identifier
    external_resource: str = "customers"
    
    # Field mappings between internal format and external API format
    field_mappings: Dict[str, str] = {
        "created_at": "created",
        "display_name": "name"
    }
    
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
            "created_at": external_data.get("created"),
            "metadata": external_data.get("metadata", {})
        }
    
    @staticmethod
    def create_via_provider(provider_instance, **kwargs) -> Dict[str, Any]:
        """Create customer via Stripe provider instance."""
        return provider_instance.create_customer(**kwargs)
    
    @staticmethod
    def get_via_provider(provider_instance, external_id: str) -> Dict[str, Any]:
        """Get customer via Stripe provider instance."""
        return provider_instance.get_customer(external_id)
    
    @staticmethod
    def list_via_provider(provider_instance, **kwargs) -> Dict[str, Any]:
        """List customers via Stripe provider instance."""
        return provider_instance.list_customers(**kwargs)
    
    @staticmethod
    def update_via_provider(provider_instance, external_id: str, **kwargs) -> Dict[str, Any]:
        """Update customer via Stripe provider instance.""" 
        return provider_instance.update_customer(external_id, **kwargs)
    
    @staticmethod
    def delete_via_provider(provider_instance, external_id: str) -> Dict[str, Any]:
        """Delete customer via Stripe provider instance."""
        return provider_instance.delete_customer(external_id)
```

### External Manager Integration
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
```

### External API Client Integration
```python
from zephyrex.extensions.AbstractExternalModel import AbstractExternalAPIClient

class CustomExternalAPIClient(AbstractExternalAPIClient):
    """Custom API client for external model operations."""
    
    def __init__(self, provider_rotation_manager, model_class):
        """Initialize with rotation manager and model class."""
        super().__init__(provider_rotation_manager, model_class)
    
    def create(self, requester_id: str, **kwargs) -> Any:
        """Create entity via external API using provider rotation."""
        # Convert internal format to external API format
        external_data = self.model_class.to_external_format(kwargs)
        
        # Call external API via provider rotation
        result = self.rotation_manager.rotate(
            self.model_class.create_via_provider, **external_data
        )
        
        if result.get("success"):
            # Convert external format back to internal format
            internal_data = self.model_class.from_external_format(result.get("data", {}))
            return self.model_class(**internal_data)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to create {self.model_class.__name__}: {result.get('error')}"
            )
```

## Provider Configuration

### Static Configuration Management
Configuration is handled at the class level:

```python
from typing import ClassVar, Dict, Any
from zephyrex.lib.Environment import env

class PRV_MyProvider_MyExtension(AbstractProvider_MyExtension):
    # Environment variables  
    _env: ClassVar[Dict[str, Any]] = {
        "MY_PROVIDER_API_KEY": "",
        "MY_PROVIDER_REGION": "us-east-1",
        "MY_PROVIDER_RETRY_COUNT": "3"
    }
    
    @classmethod
    def get_env_value(cls, env_var_name: str) -> str:
        """Get environment variable value."""
        return env(env_var_name, cls._env.get(env_var_name, ""))
    
    @classmethod
    def get_configuration(cls) -> Dict[str, Any]:
        """Access configuration."""
        return {
            "api_key": cls.get_env_value("MY_PROVIDER_API_KEY"),
            "region": cls.get_env_value("MY_PROVIDER_REGION"),
            "retry_count": int(cls.get_env_value("MY_PROVIDER_RETRY_COUNT"))
        }
```

### Configuration Validation
```python
@classmethod
def validate_config(cls) -> bool:
    """Validate provider configuration with API-specific checks."""
    # Check basic configuration
    if not cls.get_env_value("MY_PROVIDER_API_KEY"):
        logger.warning(f"{cls.name}: No API key configured")
        return False
        
    api_uri = cls.get_env_value("MY_PROVIDER_BASE_URL")
    if api_uri and not api_uri.startswith(("http://", "https://")):
        logger.error(f"{cls.name}: Invalid API URI format")
        return False
        
    return True

@classmethod
def is_configured(cls) -> bool:
    """Check if provider has minimum required configuration."""
    for env_var_name, default_value in cls.env.items():
        # If no default is provided (empty string), it's required
        if not default_value:
            current_value = cls.get_env_value(env_var_name)
            if not current_value or current_value.strip() == "":
                return False
    return True
```

## Environment Variables

### Static Variable Management
Environment variables are handled at the class level:

```python
class PRV_MyProvider_MyExtension(AbstractProvider_MyExtension):
    # Environment variables using ClassVar
    _env: ClassVar[Dict[str, Any]] = {
        "MY_PROVIDER_API_KEY": "",
        "MY_PROVIDER_REGION": "us-east-1",
        "MY_PROVIDER_RETRY_COUNT": "3"
    }
    
    @classmethod
    def get_env_value(cls, env_var_name: str) -> str:
        """Get environment variable value."""
        return env(env_var_name, cls._env.get(env_var_name, ""))
```

### Variable Registration and Access
```python
# Environment variables are automatically cached when first accessed
api_key = MyProvider.get_env_value("MY_PROVIDER_API_KEY")
timeout = int(MyProvider.get_env_value("MY_PROVIDER_TIMEOUT"))

# Configuration validation without instantiation
if MyProvider.is_configured():
    # Provider has all required configuration
    pass

# Register environment variables with the system
MyProvider.register_env_vars()
```

## Ability Management

### Static Ability Registration
```python
from zephyrex.extensions.AbstractExtensionProvider import ability

class PRV_MyProvider_MyExtension(AbstractProvider_MyExtension):
    # Static abilities set
    _abilities: ClassVar[Set[str]] = set()
    
    @ability("process_data")
    def process_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Process data using provider instance."""
        return self._instance.process(data)
        
    @ability("validate_input")
    def validate_input(self, input_data: str) -> bool:
        """Validate input data."""
        return len(input_data) > 0
    
    @classmethod
    def get_abilities(cls) -> Set[str]:
        """Return abilities this provider supports."""
        # Abilities are auto-discovered from @ability decorators
        return cls._abilities.copy()

    @classmethod  
    def has_ability(cls, ability: str) -> bool:
        """Check if provider has an ability."""
        return ability in cls._abilities
```

### Runtime Ability Detection
```python
@classmethod
def supports_feature_x(cls) -> bool:
    """Check if feature X is supported based on configuration."""
    return bool(cls.get_env_value("MY_PROVIDER_FEATURE_X_ENABLED"))

@classmethod
def get_services(cls) -> List[str]:
    """Return a list of services provided by this provider."""
    return list(cls.get_abilities())
```

## Auto-Discovery System

### Provider File Structure
```
my_extension/
├── EXT_MyExtension.py      # Extension definition
├── PRV_ServiceProvider.py  # Auto-discovered provider
├── PRV_APIProvider.py      # Auto-discovered provider  
└── PRV_UtilityProvider.py  # Auto-discovered provider
```

### Discovery Implementation
```python
# Extensions automatically discover providers via filesystem scanning
class EXT_MyExtension(AbstractStaticExtension):
    name = "my_extension"
    
    # .providers property automatically scans for PRV_*.py files
    # Returns list of discovered provider classes
    # Results are cached for performance via @classproperty @lru_cache
```

### Provider Discovery Access
```python
# Access all providers for an extension
extension_providers = MyExtension.providers
for provider_class in extension_providers:
    abilities = provider_class.get_abilities()
    if provider_class.is_configured():
        # Provider is ready to use
        pass

# No manual registration required
# No global registry to manage
# No registration method calls needed
```

## Extension Integration

### Extension-Provider Communication
```python
# Extensions can access their providers directly
class EXT_MyExtension(AbstractStaticExtension):
    @classmethod
    def use_provider_service(cls, data: str) -> str:
        """Use provider service through extension auto-discovery."""
        for provider_class in cls.providers:
            if provider_class.has_ability("data_processing") and provider_class.is_configured():
                return provider_class.process_data(data)
        return "No suitable provider available"
```

### Provider Discovery by Extension
```python
@classmethod
def get_available_providers(cls) -> List[Type[AbstractExtensionProvider]]:
    """Get list of available providers for this extension."""
    return [provider for provider in cls.providers if provider.is_configured()]

@classmethod
def get_providers_with_ability(cls, ability: str) -> List[Type[AbstractExtensionProvider]]:
    """Get providers that support a specific ability."""
    return [provider for provider in cls.providers 
            if provider.has_ability(ability) and provider.is_configured()]
```

## Provider Integration

### Service Integration with Static Methods
```python
# Concrete provider implementation with static methods
class ExternalServiceProvider(AbstractExtensionProvider):
    name = "external_service_provider"
    extension = EXT_MyExtension  # Link to parent extension
    
    env = {
        "EXTERNAL_SERVICE_API_KEY": "",
        "EXTERNAL_SERVICE_ENDPOINT": "https://api.service.com"
    }
    
    abilities = {"data_processing", "file_conversion"}
    
    @classmethod
    def process_data(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """Process data using external service."""
        if not cls.is_configured():
            raise ValueError("Provider not configured")
            
        api_key = cls.get_env_value("EXTERNAL_SERVICE_API_KEY")
        endpoint = cls.get_env_value("EXTERNAL_SERVICE_ENDPOINT")
        
        # Implementation here
        return {"processed": True, "data": data}
    
    @classmethod
    def validate_config(cls) -> bool:
        """Validate provider configuration."""
        return bool(cls.get_env_value("EXTERNAL_SERVICE_API_KEY"))
```

### Dependency Management Integration
```python
class MyProvider(AbstractExtensionProvider):
    extension = EXT_MyExtension
    
    # Unified dependencies using the Dependencies class
    dependencies = Dependencies([
        PIP_Dependency(name="requests", semver=">=2.28.0", reason="HTTP requests"),
        SYS_Dependency.for_all_platforms(
            name="curl",
            apt_pkg="curl",
            brew_pkg="curl",
            winget_pkg="cURL.cURL",
            reason="HTTP client tools"
        )
    ])
    
    @classmethod
    def install_dependencies(cls, only_missing: bool = True) -> Dict[str, bool]:
        """Install all dependencies for this provider using the unified Dependencies system."""
        if not cls.dependencies:
            return {}
        return cls.dependencies.install(only_missing=only_missing)
    
    @classmethod
    def check_dependencies(cls, loaded_extensions: Optional[Dict[str, str]] = None) -> Dict[str, bool]:
        """Check if all dependencies for this provider are satisfied."""
        if not cls.dependencies:
            return {}
        return cls.dependencies.check(loaded_extensions)
    
    @classmethod
    def get_missing_dependencies(cls, loaded_extensions: Optional[Dict[str, str]] = None) -> Dependencies:
        """Get missing dependencies."""
        if not cls.dependencies:
            return Dependencies([])
        return cls.dependencies.get_missing(loaded_extensions)
```

## Creating Providers

### Step 1: Define Abstract Provider (Optional)
Extensions can define an inner AbstractProvider class for their providers:

```python
# extensions/my_extension/EXT_MyExtension.py
from abc import abstractmethod
from typing import Dict, Any
from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension, AbstractStaticProvider, ability

class EXT_MyExtension(AbstractStaticExtension):
    """My extension with providers."""
    name = "my_extension"
    
    class AbstractProvider(AbstractStaticProvider):
        """Abstract provider for my extension."""
    
    @abstractmethod
    @ability("process_data")
    def process_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Process data - must be implemented by concrete providers."""
        pass
        
    @abstractmethod
    @ability("upload_file")
    def upload_file(self, file_path: str) -> bool:
        """Upload file - must be implemented by concrete providers."""
        pass
```

### Step 2: Create Concrete Provider
Create a provider implementation file:

```python
# extensions/my_extension/PRV_MyService_MyExtension.py  
from typing import Dict, Any, Set
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.extensions.my_extension.EXT_MyExtension import EXT_MyExtension

class PRV_MyService_MyExtension(EXT_MyExtension.AbstractProvider):
    name: str = "my_service"
    
    dependencies: ClassVar[Dependencies] = Dependencies([
        PIP_Dependency(name="requests", semver=">=2.28.0", reason="HTTP requests")
    ])
    
    _env: ClassVar[Dict[str, Any]] = {
        "MY_SERVICE_API_KEY": "",
        "MY_SERVICE_ENDPOINT": "https://api.myservice.com"
    }
    
    _abilities: Set[str] = set()  # Auto-populated from @ability decorators
    
    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        """Bond a provider instance for API operations."""
        return MyServiceProviderInstance(instance)
    
    def process_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Implement the abstract process_data ability."""
        # Use self._instance to access bonded instance
        return self._instance.api_process(data)
        
    def upload_file(self, file_path: str) -> bool:
        """Implement the abstract upload_file ability."""
        return self._instance.upload(file_path)
```

### Step 3: Access Provider (Automatic)
```python
# Provider is automatically discovered by extension
providers = EXT_MyExtension.providers
service_provider = next(p for p in providers if p.name == "my_service")

# Use provider via rotation system
if EXT_MyExtension.root:
    result = await EXT_MyExtension.root.rotate(
        EXT_MyExtension.AbstractProvider.process_data,
        data={"test": "data"}
    )
```

## Testing Integration

### Extension Test Environment Inheritance
Providers can use their parent extension's ServerMixin for testing:

```python
class EXT_MyExtension(AbstractStaticExtension):
    name = "my_extension"
    # Extension provides ServerMixin property for isolated testing

class TestMyServiceProvider(EXT_MyExtension.ServerMixin):
    """Test provider using extension's isolated environment."""
    
    def test_provider_functionality(self, extension_server, extension_db):
        """Test provider within parent extension's isolated environment."""
        # This runs in test.my_extension.database.db with APP_EXTENSIONS=my_extension
        # Provider tests inherit all isolation from parent extension
        pass

class TestMyExtension(AbstractEXTTest):
    extension_class = EXT_MyExtension
    
    def test_provider_discovery(self, extension_server, extension_db):
        """Test that extension discovers its providers."""
        providers = self.extension_class.providers
        assert len(providers) > 0
        
        # Find specific provider
        service_provider = next(p for p in providers if p.name == "my_service_provider")
        assert service_provider is not None
```

### Test Architecture Overview

```
Extension Test Environment:
┌─────────────────────────────────────┐
│ Database: test.{ext_name}.database.db │
│ Server: APP_EXTENSIONS={ext_name}    │
│ Environment: Extension-isolated      │
│ Tests: AbstractEXTTest               │
└─────────────────────────────────────┘
                    ↓ (inherited by)
Provider Test Environment:
┌─────────────────────────────────────┐
│ Database: Same as parent extension   │
│ Server: Same as parent extension     │
│ Environment: Same as parent          │
│ Tests: AbstractPRVTest               │
└─────────────────────────────────────┘
```

## Best Practices

### Provider Design
1. **Abstract Provider Pattern**: Extensions can define inner AbstractProvider class for their providers
2. **Auto-Discovery**: Providers are automatically discovered from `PRV_*.py` files via `extension.providers`
3. **Instance Bonding**: Use `bond_instance()` pattern for provider instance management
4. **Ability Decorators**: Use `@ability` decorator to mark provider abilities
5. **Ability Auto-Discovery**: Abilities are automatically discovered from decorated methods
6. **Dependency Management**: Use the unified Dependencies system
7. **Extension Types**: Extensions are typed as ENDPOINTS, DATABASE, or EXTERNAL based on components

### Environment Variable Management
1. **Type Declaration**: Use `_env: Dict[str, Any]` for environment variables
2. **Direct Access**: Use `env()` function directly for environment variable access
3. **Clear Naming**: Use consistent naming patterns for environment variables
4. **Default Values**: Provide sensible defaults where appropriate in the `_env` dictionary
5. **Auto-Registration**: External extensions automatically register API-related env vars
6. **Error Handling**: Handle missing configuration gracefully with appropriate defaults
7. **Extension Types**: Extensions with ExtensionType.EXTERNAL get standard API env vars

### Static Method Design
1. **No State**: Providers should be stateless - all state comes from environment/configuration
2. **Class Methods**: Use `@classmethod` for configuration and bonding methods
3. **Auto-Discovery**: Extensions discover providers via filesystem scanning of `PRV_*.py`
4. **Configuration Based**: All behavior should be determined by static configuration
5. **Error Handling**: Handle errors at the class level with appropriate exceptions
6. **Provider Caching**: Discovery results are cached via `_providers` class attribute

### Ability Management
1. **Static Declaration**: Declare abilities as class attributes using `abilities: Set[str]`
2. **Dynamic Detection**: Determine abilities based on configuration state in `get_abilities()`
3. **Clear Boundaries**: Clearly distinguish supported from unsupported features
4. **Configuration Dependencies**: Tie abilities to actual configuration availability
5. **Service Mapping**: Use `get_services()` to map abilities to service names

### Testing Integration
1. **ServerMixin Usage**: Use extension's ServerMixin property for isolated test environment
2. **Test Environment**: Each extension provides isolated database and server configuration
3. **Static Testing**: Test provider functionality through class methods without instantiation
4. **Configuration Testing**: Test with various configuration scenarios within isolated environment
5. **Dependency Integration**: Test how provider dependencies integrate with extension dependencies
6. **Auto-Discovery Testing**: Test that providers are correctly discovered by their extension

### Security Considerations
1. **Credential Security**: Never log or expose API keys or credentials
2. **Input Validation**: Validate all inputs before processing
3. **Output Sanitization**: Sanitize data received from external services
4. **Configuration Validation**: Validate configuration to prevent insecure setups
5. **Static Access**: Ensure static methods don't expose sensitive configuration

### Auto-Discovery Best Practices
1. **File Naming**: Use clear, descriptive names for `PRV_*.py` files
2. **Single Responsibility**: One provider per file for clear discovery
3. **Extension Linking**: Always link providers to their parent extension
4. **Performance**: Trust caching system for discovery performance
5. **Testing**: Test discovery functionality in extension tests

### Documentation
1. **Clear Interface**: Document all public class methods and their parameters with type hints
2. **Configuration Guide**: Provide clear configuration instructions with examples
3. **Static Usage**: Document how to use providers through class methods
4. **Auto-Discovery**: Document provider discovery patterns and file organization
5. **Environment Variables**: Document all required and optional environment variables

## Provider Cross-Cutting Concerns

These primitives wrap every provider call; an author who follows the patterns above inherits the behavior without writing the cross-cutting code.

### Typed external errors
`*_via_provider` methods raise typed exceptions on failure and return the unwrapped external payload on success. The `BaseExternalError` hierarchy in `extensions.ExternalErrors` defines `TransientExternalError` (5xx — retryable, advances chain on exhaustion), `RateLimitExternalError` (429 — backoff against same provider), `AuthExternalError` (401/403 — cool-down + advance), `InvalidInputExternalError` (4xx — never rotates), `PermanentExternalError`. The dict envelope `{"success", "data", "error"}` is internal to the rotation system and never escapes to caller code.

### Failure classification and rotation policy
Each provider declares `rotation_policy: ClassVar[RotationPolicy]` carrying `transient_max_retries`, `transient_base_ms`/`max_ms`/`jitter`, `rate_limit_base_ms`/`max_ms`, `auth_cooldown_seconds`, and a per-provider `header_parser` for `Retry-After` / `X-RateLimit-Reset` semantics. 4xx are caught at the request-homogenization layer before the call leaves us and never trigger rotation.

### `AbstractProviderInstance` contract
The bonded `_instance` is typed via `AbstractProviderInstance` (an `abc.ABC` with `__init__(self, instance: ProviderInstanceModel)`, `validate_credentials() -> bool`, `close()`, plus extension-specific abstract methods). Provider classes declare `_instance: ClassVar[Optional[AbstractProviderInstance]] = None`; `bond_instance` is typed `(cls, ProviderInstanceModel) -> AbstractProviderInstance`. Static type checking catches contract violations at import time.

### Manager constructor consistency
Every BLL manager accepts `model_registry` as its first positional parameter (`(model_registry, requester_id, target_id=None, target_team_id=None, parent=None)`). `RotationManager` honors the same shape. A registry-finalize pass at startup walks `inspect.signature(cls.__init__)` for every manager class and fails fast if the first parameter is not `model_registry`.

### Pluggable authentication strategies
`ProviderInstanceModel.auth_strategy_name: str` plus an opaque credentials blob; the strategy registry hydrates one of `APIKeyAuth`, `OAuth2Auth`, `AWSSigV4Auth`, `JWTBearerAuth`, `MTLSAuth`. The strategy contract is `headers_for(requester) -> dict`, `params_for(requester) -> dict`, `body_modifier(requester, body) -> body`, `refresh_if_needed()`. Outbound calls route every auth header through the strategy; rotation of refresh-bearing tokens hot-swaps without re-bonding the entire instance.

### Typed `Settings` and ability declarations
Each abstract provider declares `Settings` as an inner Pydantic model with typed fields, defaults, validators, and `Secret` markers. `_env` is replaced by an `EnvSchema` Pydantic model. Abilities are typed callables with Pydantic-validated input/output models; the string-set abilities registry is derived from typed declarations. A startup test walks every concrete provider's `Settings` and verifies the inheritance chain is non-narrowing on required fields.

### Rate limits and quotas
Providers declare `rate_limit: ClassVar[Optional[RateLimit]]` with steady-state `rps` and `burst`. The framework's per-provider token bucket (Postgres-or-Redis backed by `DistributedCounter`) acquires a token before issuing; `Retry-After`-style signals pause that provider rather than rotating. Quota tracking is a different dimension and applies independently.

### `is_configured` vs `health_check`
`is_configured() -> bool` reflects "all required env vars present" — startup readiness. `health_check() -> HealthStatus` performs a live, lightweight call (`OK` / `DEGRADED` / `DOWN`) cached with a configurable TTL (default 60s). Rotation skips `DOWN` providers without consuming a retry slot.

### Sticky-session routing
`RotationManager.rotate` accepts an optional `RoutingHint(stickiness_key: Optional[str])`. With a key, the rotation system pins repeated calls within that key to the first provider that succeeded for it (LLM conversation memory, partial outputs) until the provider becomes unhealthy or the sticky-session TTL expires. Canary routing is HAProxy's job — not duplicated inside rotation.

### Credential vault with OpenBao default
`ProviderInstanceModel.api_key` is a `CredentialRef` resolving in tier order: OpenBao → environment variable → encrypted database column (Fernet by default; KMS-backed `EncryptionBackend` for cloud). `Secret[T]`-tagged Pydantic fields integrate with the redaction layer; logging never emits a plaintext credential. Auth rejection invalidates the cached credential and forces a fresh resolve; a re-resolved-identical credential transitions the provider to `DOWN` and halts retry until an operator intervenes. PyJWT is the canonical JWS/JWE library; the algorithm registry is versioned (`JWS_ALG_VERSION`) with documented HS256→EdDSA rotation procedure.

### Sandbox/live discriminator
Paired env vars are named `{PROVIDER}_API_KEY_TEST` and `{PROVIDER}_API_KEY_LIVE`, selected by top-level `APP_ENV` (`development` / `staging` / `production`). The discriminator runs inside `CredentialRef.resolve` only when the resolved tier is the env-var fallback; OpenBao paths embed the environment in the storage path (`secret/data/{env}/{provider}/api_key`). Production refuses to start with `_TEST_` resolved; development warns on `_LIVE_`.

### Shared outbound HTTP client
`ProviderHTTPClient` is the single layer every outbound HTTP call routes through. In order it applies: trace propagation, authentication strategy, idempotency-key injection, rate-limit token acquisition, timeout, send, response-class detection, header parsing, log redaction. Providers wrapping an SDK route the SDK's transport through this client when supported. Connection pooling is shared process-wide; TLS configuration and egress proxy are policy.

### Upstream API version pinning
Providers declare `external_api_version: ClassVar[Optional[str]]`; the shared HTTP client injects the appropriate header (`Stripe-Version`, `X-Api-Version`, etc.). `field_mappings` uses references to fields on a paired Pydantic external DTO that mirrors the upstream schema for the pinned version, so renames are caught at type-check time. Drift snapshots are pinned to the same version.

### Idempotency primitive
`AbstractExternalManager.idempotency_key(operation, args) -> str` defaults to a hash of `(requester_id, operation, canonicalized_args)`. Mutating `*_via_provider` methods are decorated `@idempotent`. The canonical store is the outbox row when the operation is enrolled in the outbox (typical for mutations) and the request envelope when the call is purely synchronous; the in-memory cache is a same-process retry-budget optimization only. The key is minted by the BLL caller at the mutation boundary, not by rotation.

### Shared distributed primitives
`AdvisoryLock` (`async with acquire_lock("namespace:{id}", timeout=...)`) backs outbox claim, quota decrement, and link-field write-back. Backends: Postgres `pg_advisory_lock` (default, transaction-scoped), Redis Redlock (load-shed alternative). `DistributedCounter(key, limit, period_key).try_consume(amount) / release(amount) / reset(period_key)` is the canonical multi-process atomic counter consumed by token-bucket rate limits, quota decrement, and the per-tenant fairness virtual-time scheduler.

### Schema drift and contract testing
Providers targeting an upstream with a published OpenAPI spec declare `openapi_url: ClassVar[Optional[str]]`. A canonical snapshot lives at `src/extensions/{name}/contracts/{provider}.openapi.json`. CI fetches the live spec on a per-provider cadence, structurally diffs against the snapshot, and fails on breaking changes (removed fields, narrowed types, removed enum values). Non-breaking diffs open a PR updating the snapshot. Providers without machine-readable specs use real recorded responses normalized to remove instance-specific identifiers.

### Sandbox-credential test contract
`@pytest.mark.external_api(provider="stripe")` runs against real (sandbox) credentials when present and is automatically xfailed with a clear reason naming the missing env vars when absent. `@pytest.mark.external_smoke` deliberately runs without credentials and asserts configuration-failure paths surface correctly. No mocks of `RotationManager`, `*_via_provider` methods, or extension functionality. Optional `PRV_Fake_*` providers are an opt-in for offline CI; sandbox credentials are the recommended path.

### Distributed tracing and provider-call metrics
W3C `traceparent` and `baggage` propagate from the inbound request through `RequestContext`, into `RotationManager.rotate`, into `ProviderHTTPClient`, and out to the upstream. `RotationManager.rotate` opens one span per attempt tagged with provider name and attempt index, so a multi-provider rotation appears as a parent span with N attempt-children. Span naming follows OTel HTTP-client semantic conventions (`http.method`, `http.url.template`, `http.response.status_code`, `peer.service`). The framework emits a fixed metric set: latency histogram per `(provider, ability)`, success/error rate, rotation-attempt counter, per-provider health gauge, idempotency-key cache hit rate. Backends are pluggable — Prometheus, OpenTelemetry, Statsd — with a no-op default. Metric labels are bounded.

### Data residency and regional provider pools
Two distinct concepts: `ResidencyJurisdiction` is the legal/policy umbrella (`EU`, `US`, `UK`, `CA`, `APAC`, `HEALTHCARE_HIPAA`); `ResidencyRegion` is the physical placement of a specific provider instance (`eu-west-1`, `eu-central-1`, `us-east-1`). Each jurisdiction declares the set of regions it includes, mapped at deployment time via `JurisdictionRegistry`. `ProviderInstance` carries `region: Optional[ResidencyRegion]`. Tenants carry `data_residency: Optional[ResidencyJurisdiction]`. Provider resolution skips instances whose `region` is not mapped under the requester's tenant jurisdiction; if none qualify, raises `NoInJurisdictionProviderError`. The shared HTTP client does not enforce regional egress (that is HAProxy / regional egress proxy concern); residency at the application layer is about provider-instance selection.

### Abstract provider templates
The framework ships abstract provider templates for the infrastructure categories every non-trivial application needs. Each template defines abilities using the typed-ability declarations, with paired Pydantic input/output models, and ships with at least one toy reference implementation.

- **Object / blob storage** — abilities for upload, download, list, delete, presigned URL generation, multipart upload. Reference targets: S3, GCS, Azure Blob, local filesystem.
- **Cache** — abilities for get, set with TTL, delete, increment, set-if-not-exists, multi-get. Reference targets: Redis, memcached, in-memory.
- **Queue / job scheduler** — abilities for enqueue, schedule (delayed), cron-style recurring, dead-letter, status query. Reference targets: Celery, RQ, Arq, native (`QueueConsumerService`).
- **Search index** — abilities for index, query, delete, bulk-index, schema management. Reference targets: Elasticsearch, OpenSearch, Meilisearch, Algolia.
- **AI / LLM** — abilities for completion, chat, embedding, streaming completion, tool/function calling, image generation. Reference targets: OpenAI, Anthropic, local providers.
- **Notification fan-out** — abilities for push (mobile), SMS, in-app — coordinated alongside the existing email provider so a single `notify` call composes over the existing email contract and selects additional channels per recipient preferences.

`AbstractProvider_AI` implements pre-estimate / post-true-up quota inside the abstract itself so all LLM providers inherit correct quota semantics by default. Before issuing the call, the provider computes a conservative upper-bound token estimate (input tokens + `max_tokens` ceiling) and pre-decrements quota. After the response returns, the framework reconciles: if actual < pre-estimate the difference is credited back; if actual > pre-estimate the overage is debited and the requester sees a typed `QuotaOverrunWarning`, with the operator able to configure whether overruns hard-fail subsequent calls or warn-and-continue. For streamed responses the true-up runs incrementally per chunk so a runaway stream is cancelled when the budget is depleted.

Nested quotas: `Quota` rows declare an optional `qualifier: dict` (e.g. `{"model": "gpt-4-turbo"}`); rows without a qualifier match all calls to that ability. A single call debits every matching row in one transaction so partial debits are impossible. Tool-calling and structured-output translation: the abstract template defines the canonical vocabulary (`ToolDefinition`, `ToolCall`, `ToolResult`) and concrete providers translate to and from their upstream's specific shape via the field-mapping pipeline. Structured-output / JSON-mode is normalized to a single `output_format: Optional[JsonSchema]` parameter.

**Tool-calling testing harness.** `extensions/PRV_AI_ToolCallingHarness.py` ships `AbstractToolCallingHarness` — a reusable contract test bundle that concrete AI providers extend by overriding `make_provider()`, `make_instance()`, and `model_name()`. The harness verifies the response-shape contract (`validate_chat_response_shape`), translates upstream-specific tool-call payloads into the canonical `ToolCall` (`validate_tool_call_payload` handles OpenAI `function.arguments` JSON strings, Anthropic `input` dicts, and the canonical name+arguments dict), runs the full round-trip (model emits `ToolCall` → caller executes → `ToolResult` is fed back into the next chat turn), and validates the pre-estimate / post-true-up quota helpers. The framework's reference `FakeToolCallingProvider` satisfies the harness without an upstream so provider authors have a known-good baseline; concrete providers gate their harness subclass on the `external_api` pytest marker (Item 15) so it auto-xfails when no sandbox credential is configured.

### Graceful degradation contract
Each provider declares `degradation_policy: ClassVar[Optional[DegradationPolicy]]` per ability or per operation. Three modes: `FAIL_FAST` (default; raise on rotation exhaustion — correct for transactional emails, password resets, anything user-blocking), `QUEUE_AND_RETRY` (write to outbox, return 202 with a tracking id, drain via the background outbox service when the upstream recovers), `SILENT_DROP` (log and return success; valid only for genuinely fire-and-forget operations like analytics, and emits a mandatory `provider_silent_drop_total{provider, ability}` metric so operators can dashboard the rate and catch unintentional silent failures). The choice between modes is part of the API contract — clients calling a `QUEUE_AND_RETRY` operation must know to handle 202 responses, so the choice is reflected in the OpenAPI documentation, the SDK, and the GraphQL surface. Switching an operation between modes is a breaking change.

### Cost observability per tenant
Each provider with billable upstream calls declares `cost_model: ClassVar[Optional[CostModel]]` — a `(request, response) -> Decimal` callable returning USD (or the deployment's configured base currency). Four ready-made implementations ship: `ConstantCostModel` (flat per-call), `TokenBasedCostModel` (LLM: `prompt_tokens * prompt_price + completion_tokens * completion_price + per_request_fixed_cost`), `PercentOfAmountCostModel` (payment processors: `2.9% + $0.30` style), `FreeCostModel` (explicit zero, distinguishable from "no model attached"). All arithmetic is `Decimal`-based. The framework emits `provider_cost_usd_total{tenant, provider, ability}` as a counter and writes per-request cost into the audit log. Cost rows roll up daily into a `CostSummary` table for fast queries; the audit log remains the source of truth. `TenantCostCap(cap_usd, window, behavior=hard_fail|warn_and_continue)` extends quota enforcement with a per-tenant USD cap. Currency conversion is out of scope — a deployment with multi-currency upstreams declares a single base currency.