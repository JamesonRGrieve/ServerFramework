# Provider Layer Testing (`AbstractPRVTest`)

This document describes provider-specific testing patterns and best practices.

> **Common Testing Resources**: For testing tools, commands, fixtures, and best practices shared across all layers, see [Framework.Test.md](../Framework.Test.md#testing-tools-and-framework).

This document explains how to test server framework providers using the extension's ServerMixin. Providers are tested within their parent extension's isolated environment, ensuring proper integration and isolation.

## Core Concepts

- **Inheritance**: Test classes for providers inherit from their parent extension's ServerMixin.
- **Parent Extension Integration**: Provider tests automatically use their parent extension's isolated test environment.
- **Database and Server Isolation**: Providers test within their parent extension's isolated database and server environment.
- **Configuration**: Test classes can access the provider through the extension's `.providers` property.
- **Fixtures**: The extension's ServerMixin provides all necessary fixtures:
    - `db`: Database session from parent extension's isolated database (`test.{extension_name}.database.db`)
    - `extension_server`: Parent extension's isolated server with only that extension loaded
    - `model_registry`: Isolated model registry from the extension server
    - `db_manager`: Database manager from the extension server
- **Static Provider Testing**: All providers are static classes with no instantiation required.
- **Auto-Discovery**: Providers are automatically discovered by their parent extension via filesystem scanning.
- **Initialization Testing**: Verifies core attributes (`extension_id`, `friendly_name`, failure counters) and API-specific attributes (`api_key`, `api_uri`) are set correctly.
- **Service & Ability Testing**: Tests that the `services` property returns the expected list and that service delivery functionality works correctly, including handling of unsupported abilities.
- **Workspace Testing**: Tests the `safe_join` method for correct path joining and security against path traversal. Verifies the `WORKING_DIRECTORY` is created.
- **Failure Handling**: Tests the `_handle_failure` method increments the failure count and correctly raises an exception when `MAX_FAILURES` is exceeded.
- **Comprehensive Mocking**: Uses extensive mocking to isolate provider functionality and test various scenarios including API client behavior, library availability, and configuration states.

Providers focus on delivering specific services and managing their configuration. They do not load or manage other system components - that is handled by the import system. Provider tests inherit the isolated environment from their parent extension.

## Test Architecture

### Provider Test Environment Inheritance

Provider tests inherit their parent extension's isolated environment:

```
Extension Test Environment (Parent):
┌─────────────────────────────────────┐
│ Database: test.{ext_name}.database.db │
│ Server: APP_EXTENSIONS={ext_name}    │
│ Environment: Extension-isolated      │
└─────────────────────────────────────┘
                    ↓ (inherited by)
Provider Test Environment:
┌─────────────────────────────────────┐
│ Database: Same as parent extension   │
│ Server: Same as parent extension     │
│ Environment: Same as parent          │
│ Provider: Static provider class      │
└─────────────────────────────────────┘
```

### Test Inheritance Hierarchy

```
EXT_MyExtension.ServerMixin (extension's isolated test environment)
    ↓
YourProviderTest (specific provider tests)
```

Providers are tested using their parent extension's ServerMixin, which provides complete isolation.

### Parent Extension Integration

Providers are tested through their parent extension's ServerMixin:

1. **`db` fixture**: Provides session from parent extension's isolated database
2. **`extension_server` fixture**: Parent extension's isolated server
3. **Provider Access**: Via `extension.providers` property (auto-discovered)
4. **Static Testing**: All providers are static classes

## ServerMixin Features

The extension's ServerMixin provides comprehensive test support:
- **Isolated Environment**: Complete database and server isolation
- **Auto-Discovery**: Providers are discovered automatically from PRV_*.py files
- **Static Testing**: Test provider functionality through class methods
- **Fixture Management**: All necessary fixtures for testing
- **Test Helpers**: Common assertion and testing utilities

## Class Configuration

When creating a test class for a specific provider, configure these attributes:

```python
from serverframework.extensions.example.EXT_Example import EXT_Example
from serverframework.extensions.example.PRV_Example import PRV_Example
from serverframework.lib.Dependencies import Dependencies, PIP_Dependency, SYS_Dependency

class TestExampleProvider(EXT_Example.ServerMixin):
    # ServerMixin provides isolated test environment
    # Provider is auto-discovered by parent extension

    # Providers are static - no initialization needed
    # Configuration is handled through environment variables

    # List of ability names expected to be registered by the provider
    expected_abilities: List[str] = ["perform_action", "process_data"]

    # List of service names the provider claims to support
    expected_services: List[str] = ["example_service", "data_processing"]

    # Expected dependencies - using unified Dependencies system
    expected_dependencies: Dependencies = Dependencies([
        PIP_Dependency(name="requests", semver=">=2.28.0", reason="HTTP requests"),
        SYS_Dependency.for_all_platforms(
            name="curl",
            apt_pkg="curl",
            brew_pkg="curl",
            winget_pkg="cURL.cURL",
            reason="HTTP client tools"
        )
    ])

    # Test configuration
    test_config = ClassOfTestsConfig(
        categories=[CategoryOfTest.EXTENSION, CategoryOfTest.INTEGRATION],
        cleanup=True,
    )

    # Custom fixtures for provider-specific testing
    @pytest.fixture
    def mock_api_client(self):
        """Mock API client for testing"""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client.send.return_value = mock_response
        return mock_client

    @pytest.fixture
    def provider_with_mock_client(self, mock_api_client):
        """Provider instance with mocked API client"""
        with patch("serverframework.extensions.providers.PRV_Example.LIBRARY_AVAILABLE", True):
            with patch("serverframework.extensions.providers.PRV_Example.APIClient", return_value=mock_api_client):
                # Uses the inherited isolated environment
                provider = ExampleProvider(**self.provider_init_params)
                return provider

    @pytest.fixture
    def provider_without_library(self):
        """Provider instance when required library is not available"""
        with patch("serverframework.extensions.providers.PRV_Example.LIBRARY_AVAILABLE", False):
            provider = ExampleProvider(**self.provider_init_params)
            return provider
```

## Provided Fixtures

### Core Testing Fixtures

The extension's ServerMixin provides these essential fixtures:

#### `db` (scope="function")
Provides a database session from the parent extension's isolated database:
- **Database**: Uses `test.{parent_extension_name}.database.db`
- **Isolation**: Complete database isolation per extension
- **Session Management**: Automatic session lifecycle management
- **Access**: Provider tests run in the same isolated database environment

```python
def test_provider_database_operations(self, extension_db, extension_server):
    """Test provider database operations in parent extension's environment."""
    from serverframework.database.DatabaseManager import get_session
    
    session = get_session()
    try:
        # This runs in the parent extension's isolated database
        # e.g., test.email.database.db for EmailProvider
        pass
    finally:
        session.close()
```

#### `extension_server` (scope="class")
Provides the parent extension's isolated server:
- **Server**: FastAPI TestClient with only parent extension loaded
- **Environment**: `APP_EXTENSIONS={parent_extension_name}` only
- **Provider Access**: Providers are auto-discovered and available
- **API Testing**: Test provider integration with extension endpoints

```python
def test_provider_api_integration(self, extension_server):
    """Test provider API integration in parent extension's server."""
    # This tests provider within the parent extension's isolated server
    response = extension_server.get("/v1/extension-endpoint-using-provider")
    assert response.status_code == 200
```

#### Provider Access
Access providers through the extension's auto-discovery:
- **Discovery**: Providers found via `extension.providers` property
- **Static Classes**: All providers are static classes
- **No Instantiation**: Test functionality through class methods
- **Abilities**: Access via provider's `_abilities` property

```python
def test_provider_functionality(self):
    """Test provider functionality."""
    # Get providers from extension
    providers = EXT_Example.providers
    
    # Find specific provider
    provider = next(p for p in providers if p.name == "example")
    
    # Test static functionality
    abilities = provider._abilities
    assert isinstance(abilities, set)
```

#### Additional ServerMixin Fixtures
- **`model_registry`**: Isolated model registry from extension server
- **`db_manager`**: Database manager from extension server
- **`admin_a`, `admin_b`**: Test admin users
- **`team_a`, `team_b`**: Test teams for multi-tenancy

```python
def test_api_provider_functionality(self, api_provider):
    """Test API provider functionality (if applicable)."""
    if api_provider is None:
        pytest.skip("Provider is not an API provider")
    
    # Test API-specific functionality
    assert api_provider.api_key == "test_api_key"
    assert api_provider.api_uri == "https://api.test.com"
```

### Legacy Compatibility Fixtures

#### `test_server`
Legacy fixture name that delegates to `extension_server` for backward compatibility.

#### `temp_workspace`
Provides a temporary directory for testing workspace-related methods.

### Provider-Parent Extension Relationship

```python
# Provider inherits from extension's AbstractProvider or AbstractStaticProvider
class PRV_MyProvider(EXT_MyExtension.AbstractProvider):
    name = "my_provider"
    
    @classmethod
    def bond_instance(cls, instance):
        # Provider implementation...
        pass

# Test class uses parent extension's ServerMixin
class TestMyProvider(EXT_MyExtension.ServerMixin):
    # Automatically uses parent extension's environment
    
    def test_provider_in_extension_environment(self, extension_server, db):
        """Test provider within parent extension's isolated environment."""
        # This runs in test.my_extension.database.db with APP_EXTENSIONS=my_extension
        providers = EXT_MyExtension.providers
        assert any(p.name == "my_provider" for p in providers)
```

## Comprehensive Testing Strategy

### 1. Basic Initialization and Configuration Testing

Test provider initialization within the isolated extension environment:

```python
def test_provider_discovery(self):
    """Test that provider is discovered by parent extension"""
    # All providers are static and auto-discovered
    providers = EXT_Example.providers
    
    # Find our provider
    provider = next((p for p in providers if p.name == "example"), None)
    assert provider is not None
    
    # Test static provider properties
    assert hasattr(provider, "_abilities")
    assert hasattr(provider, "dependencies")
    assert hasattr(provider, "bond_instance")

def test_initialization_without_library_available(self, provider_without_library):
    """Test provider initialization when required library is not available"""
    is_static = inspect.isclass(provider_without_library)
    
    if is_static:
        # Static provider should handle missing library gracefully
        assert provider_without_library.validate_config() is False
    else:
        # Instance provider should handle missing library
        assert hasattr(provider_without_library, "client")

def test_configure_provider_success(self):
    """Test successful provider configuration in isolated environment"""
    with patch("serverframework.extensions.providers.PRV_Example.LIBRARY_AVAILABLE", True):
        with patch("serverframework.extensions.providers.PRV_Example.APIClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            # Provider is configured within parent extension's environment
            provider = ExampleProvider(**self.provider_init_params)

            if not inspect.isclass(provider):
                mock_client_class.assert_called_once_with(api_key="test_api_key")
                assert provider.client == mock_client
```

### 2. Static Provider Testing

Test static provider functionality:

```python
def test_provider_abilities(self):
    """Test provider abilities."""
    # All providers are static
    providers = EXT_Example.providers
    provider = next(p for p in providers if p.name == "example")
    
    abilities = provider._abilities
    assert isinstance(abilities, set)
    
    # Test expected abilities
    for expected_ability in self.expected_abilities:
        assert expected_ability in abilities

def test_provider_configuration(self):
    """Test provider configuration access."""
    providers = EXT_Example.providers
    provider = next(p for p in providers if p.name == "example")
    
    # Test environment variable access
    if hasattr(provider, "_env"):
        assert isinstance(provider._env, dict)
    
    # Test bond_instance method
    assert hasattr(provider, "bond_instance")
    assert callable(provider.bond_instance)

def test_static_provider_registry(self):
    """Test static provider registry integration."""
    if not inspect.isclass(self.provider_class):
        pytest.skip("Provider is not static")
    
    # Test provider registry access
    provider_by_name = AbstractExtensionProvider.get_provider_by_name(self.provider_class.__name__)
    assert provider_by_name == self.provider_class
```

### 3. Service and Ability Testing

Test service registration and ability management:

```python
def test_provider_bond_instance(self):
    """Test provider bond_instance method"""
    providers = EXT_Example.providers
    provider = next(p for p in providers if p.name == "example")
    
    # Mock provider instance model
    from serverframework.logic.BLL_Providers import ProviderInstanceModel
    mock_instance = ProviderInstanceModel(
        api_key="test_key",
        model_name="test_model"
    )
    
    # Test bond_instance
    bonded = provider.bond_instance(mock_instance)
    assert bonded is not None

def test_get_extension_info(self, provider):
    """Test getting extension info"""
    info = provider.get_extension_info()

    assert isinstance(info, dict)
    assert "name" in info
    assert "description" in info

def test_abilities_management(self, provider):
    """Test abilities management."""
    is_static = inspect.isclass(provider)
    
    abilities = provider.get_abilities()
    assert isinstance(abilities, set)
    
    # Test ability registration if supported
    if hasattr(provider, "register_ability"):
        test_ability = "test_ability_12345"
        provider.register_ability(test_ability)
        
        updated_abilities = provider.get_abilities()
        assert test_ability in updated_abilities
```

### 4. Isolated Environment Integration Testing

Test provider integration within the parent extension's environment:

```python
def test_provider_in_extension_database(self, db):
    """Test provider functionality within extension's isolated database."""
    # db fixture provides session from parent extension's database
    try:
        # Test provider database operations in isolated environment
        # This ensures provider works within its parent extension's context
        providers = EXT_Example.providers
        assert len(providers) > 0
    finally:
        db.close()

def test_provider_in_extension_server(self, extension_server):
    """Test provider functionality within extension's isolated server."""
    # Test that provider is available within the parent extension's server
    response = extension_server.get("/health")
    assert response.status_code == 200
    
    # Test provider-related endpoints if they exist
    # These would be endpoints that use the provider within the extension
```

### 5. Configuration Validation Testing

Test provider configuration validation in isolated environment:

```python
def test_provider_environment_variables(self):
    """Test provider environment variable configuration"""
    providers = EXT_Example.providers
    provider = next(p for p in providers if p.name == "example")
    
    # Test _env attribute
    if hasattr(provider, "_env"):
        assert isinstance(provider._env, dict)
        # External providers should have API-related env vars
        if EXT_Example.ExtensionType.EXTERNAL in EXT_Example.types:
            # Check for auto-registered env vars
            pass

def test_validate_config_missing_requirements(self):
    """Test configuration validation with missing requirements"""
    if inspect.isclass(self.provider_class):
        # Static provider configuration testing
        with patch.object(self.provider_class, "get_env_value", return_value=""):
            result = self.provider_class.validate_config()
            assert result is False
    else:
        # Instance provider configuration testing
        provider = self.provider_class(
            api_key="", 
            api_uri="", 
            extension_id="test"
        )
        result = provider.validate_config()
        assert result is False
```

### 6. Dependencies Testing

Test provider dependencies using unified Dependencies system:

```python
def test_provider_dependencies(self):
    """Test provider dependencies using unified system."""
    providers = EXT_Example.providers
    provider = next(p for p in providers if p.name == "example")
    
    assert hasattr(provider, "dependencies")
    assert isinstance(provider.dependencies, Dependencies)
    
    # Test dependency properties
    assert hasattr(self.provider_class.dependencies, "sys")
    assert hasattr(self.provider_class.dependencies, "pip")
    assert hasattr(self.provider_class.dependencies, "ext")

def test_install_dependencies(self):
    """Test dependency installation using unified system."""
    # Mock dependency installation
    with patch.object(self.provider_class.dependencies, "install", return_value={"curl": True}):
        results = self.provider_class.dependencies.install()
        assert isinstance(results, dict)

def test_check_dependencies(self):
    """Test dependency checking using unified system."""
    loaded_extensions = {"core": "1.0.0"}
    
    with patch.object(self.provider_class.dependencies, "check", return_value={"curl": True}):
        results = self.provider_class.dependencies.check(loaded_extensions)
        assert isinstance(results, dict)
```

### 7. Error Handling and Recovery Testing

Test provider's error handling within isolated environment:

```python
def test_provider_rotation_integration(self):
    """Test provider integration with rotation system"""
    # Test that provider can be used with rotation manager
    if EXT_Example.root:
        providers = EXT_Example.providers
        provider = next(p for p in providers if p.name == "example")
        
        # Provider should be compatible with rotation system
        assert hasattr(provider, "bond_instance")
        assert callable(provider.bond_instance)

def test_max_failures_reached(self, provider):
    """Test behavior when max failures is reached"""
    is_static = inspect.isclass(provider)
    
    if not is_static and hasattr(provider, "MAX_FAILURES"):
        # Set failures to max
        provider.failures = provider.MAX_FAILURES
        test_error = Exception("Test error")
        
        with pytest.raises(Exception):
            provider._handle_failure(test_error)

def test_graceful_degradation(self, provider):
    """Test graceful degradation in isolated environment"""
    is_static = inspect.isclass(provider)
    
    # Test that provider handles errors gracefully within extension environment
    abilities = provider.get_abilities()
    assert isinstance(abilities, set)
    
    # Provider should function even with limited configuration
    info = provider.get_extension_info()
    assert isinstance(info, dict)
```

### 8. Workspace and File Handling Testing

Test provider's workspace handling within isolated environment:

```python
def test_working_directory_setup(self, provider):
    """Test that working directory is properly set up"""
    is_static = inspect.isclass(provider)
    
    if not is_static:
        assert hasattr(provider, "WORKING_DIRECTORY")
        assert os.path.exists(provider.WORKING_DIRECTORY)
        assert os.path.isdir(provider.WORKING_DIRECTORY)

def test_safe_join_path_traversal_protection(self, provider, temp_workspace):
    """Test that safe_join protects against path traversal"""
    is_static = inspect.isclass(provider)
    
    if not is_static and hasattr(provider, "safe_join"):
        provider.WORKING_DIRECTORY = temp_workspace
        
        # Should prevent path traversal
        with pytest.raises(ValueError):
            provider.safe_join("../../../etc/passwd")
        
        # Should allow safe paths
        safe_path = provider.safe_join("subdir/file.txt")
        assert safe_path.startswith(temp_workspace)
```

## Included Test Methods

`AbstractPRVTest` provides these standard tests automatically:

### Core Provider Tests
- `test_initialization`: Checks basic provider attributes and workspace directory creation
- `test_api_provider_initialization`: Checks API-specific attributes (`api_key`, `api_uri`, timeouts) for API providers
- `test_configure_provider`: Verifies provider configuration methods
- `test_services`: Asserts that provider services match expected services
- `test_has_ability`: Tests ability checking with expected and non-existent abilities
- `test_get_abilities`: Tests ability retrieval and management
- `test_get_extension_info`: Checks extension info access
- `test_abilities_management`: Tests ability registration and management

### Environment Integration Tests
- `test_workspace_directory`: Verifies working directory configuration
- `test_custom_working_directory`: Tests custom workspace configuration
- `test_working_directory_permissions`: Tests workspace permissions
- `test_safe_join`: Tests path joining security

### Static Provider Tests
- `test_dependencies_structure`: Tests unified Dependencies structure
- `test_install_dependencies`: Tests dependency installation via unified system
- `test_check_dependencies`: Tests dependency checking functionality
- `test_get_missing_dependencies`: Tests missing dependency detection
- `test_dependencies_env_property`: Tests .ext.env CSV property
- `test_dependencies_server_context`: Tests .ext.server() context manager

### Error Handling Tests
- `test_failure_recovery`: Tests provider recovery mechanisms
- `test_error_handling_graceful_degradation`: Tests graceful error handling
- `test_provider_cleanup`: Tests resource cleanup

### Configuration Tests
- `test_provider_configuration_validation`: Tests configuration validation
- `test_provider_specific_attributes`: Tests provider-specific settings
- `test_provider_metadata`: Tests metadata access

### Inherited Test Methods (conditionally skipped)

Some methods are conditionally skipped based on provider type:
- `test_register_unsupported_ability`: Skipped for static providers
- `test_friendly_name`: Adapted for static vs instance providers
- Various instance-specific tests are skipped for static providers

## Real-World Examples

### Stripe Provider Testing (Payment Extension)

The Stripe provider test demonstrates proper architecture:

```python
class TestStripeProvider(EXT_Payment.ServerMixin):
    # Automatically uses payment extension's isolated environment
    
    provider_init_params = {
        "api_key": "sk_test_123",
        "webhook_secret": "whsec_test_123"
    }
    
    def test_stripe_functionality(self, extension_server, extension_db):
        """Test Stripe provider in payment extension's isolated environment."""
        # This runs in test.payment.database.db with APP_EXTENSIONS=payment
        from serverframework.database.DatabaseManager import get_session
        
        session = get_session()
        try:
            # Test Stripe provider operations within payment extension context
            pass
        finally:
            session.close()
```

### Database Provider Testing (Database Extension)

The database provider test shows static provider testing:

```python
class TestSQLiteProvider(EXT_Database.ServerMixin):
    # Tests run in database extension's isolated environment
    
    def test_provider_abilities(self):
        """Test database provider abilities."""
        providers = EXT_Database.providers
        sqlite_provider = next(p for p in providers if p.name == "sqlite")
        
        abilities = sqlite_provider._abilities
        assert "database_operations" in abilities
        assert "sql_execution" in abilities
```

## Provider-Specific Best Practices

In addition to [common testing best practices](../Framework.Test.md#best-practices):

### Provider-Extension Integration
- **Use ServerMixin**: Always inherit from parent extension's ServerMixin
- **Auto-Discovery**: Providers are discovered via `extension.providers` property
- **Test in Isolation**: Provider tests run in parent extension's isolated environment (same DB, same server)
- **Static Pattern**: All providers are static classes with no instantiation

### Provider Testing
- **Extension Context**: Test provider within the context of its parent extension
- **Bond Instance**: Test `bond_instance()` method for provider initialization
- **Services**: Verify provider services are available and functional
- **Abilities**: Test provider abilities through the rotation system

### Configuration Testing
- **Environment Variables**: Test provider's `_env` dictionary configuration
- **Extension Types**: Check extension types for auto-registered env vars
- **Static Methods**: All configuration through static class methods
1. **Unified System**: Test provider's Dependencies instance
2. **Extension Integration**: Provider dependencies work with extension dependencies
3. **Installation Mocking**: Mock dependency installation for safety
4. **Discovery Testing**: Ensure provider is discovered by extension

### Error Handling
1. **Exception Testing**: Test appropriate exception handling
2. **Failure Recovery**: Test provider failure and recovery mechanisms
3. **Graceful Degradation**: Test behavior when dependencies or configuration fail
4. **Static vs Instance**: Handle error testing differently for static vs instance providers

### Fixture Usage
1. **Parent Delegation**: Understand that fixtures delegate to parent extension
2. **Environment Awareness**: Be aware of the inherited isolated environment
3. **Custom Fixtures**: Create provider-specific fixtures as needed
4. **Mock Management**: Use fixtures to manage provider-specific mocks

## Testing Provider-Specific Abilities

The standard tests focus on the `AbstractExtensionProvider` framework. To test the actual implementation of your provider's abilities and services, you should:

1. Add custom test methods to your specific provider test class.
2. Use the inherited `extension_db` and `extension_server` fixtures from parent extension.
3. Test provider functionality within the parent extension's isolated environment.
4. Mock any external dependencies appropriately.
5. Test both static methods (for static providers) and instance methods (for instance providers).

```python
import asyncio
from unittest.mock import patch, AsyncMock

# In TestExampleProvider class
def test_provider_service_integration(self, extension_server, db):
    """Test provider service integration within parent extension environment."""
    # This test runs in the parent extension's isolated environment
    # Database: test.{parent_extension}.database.db
    # Server: APP_EXTENSIONS={parent_extension}
    
    try:
        # Get provider from extension
        providers = EXT_Example.providers
        provider = next(p for p in providers if p.name == "example")
        
        # Test static provider functionality
        if hasattr(provider, "perform_service"):
            result = provider.perform_service("test_data")
            assert result is not None
    finally:
        db.close()

@pytest.mark.asyncio
async def test_async_provider_functionality(self, extension_server):
    """Test async provider functionality within extension server."""
    # Test async provider methods within the parent extension's server context
    response = extension_server.post("/v1/extension/provider-endpoint", 
                                   json={"test": "data"})
    assert response.status_code == 200

def test_provider_configuration_in_extension_context(self):
    """Test provider configuration within extension context."""
    # Test that provider configuration works within parent extension environment
    providers = EXT_Example.providers
    provider = next(p for p in providers if p.name == "example")
    
    # Test provider has proper structure
    assert hasattr(provider, "name")
    assert hasattr(provider, "bond_instance")
    assert hasattr(provider, "_abilities")
``` 

## Reusable Security Mixins

Provider test files can inherit parametrized security-deny matrices from `AbstractPRVTest.py`. The mixin pattern lets every concrete provider gain identical denial coverage without duplicating tests per class.

### `AbstractEmailProviderSecurityTests`

Email-specific mixin that exercises a 9-row matrix (CRLF, NUL, oversized payload, traversal, homograph, malformed address) against any subclass of `AbstractEmailProvider`:

```python
class TestSendgridProvider(AbstractPRVTest, AbstractEmailProviderSecurityTests):
    provider_class = SendgridProvider
    # mixin contributes: test_send_email_rejects_malicious_input[recipient-CRLF-...] etc.
```

The mixin requires the host class to expose a `mock_provider_instance` fixture so the deny tests run without a live API key. **Do not gate denial tests on real credentials**: an env-conditional xfail silently masks regressions.

### Adding a new mixin

1. Define a `<NAME>_DENY_MATRIX` list of `(field, payload, reason)` tuples.
2. Wrap a parametrized test method in a mixin class.
3. Document the mixin here and in `Framework.Test.md`.
4. Inherit from the mixin in every provider's test class.

A 9-row matrix × 3 providers replaces ~150 lines of hand-rolled tests with one parametrize block; new providers automatically pick up the same coverage.
