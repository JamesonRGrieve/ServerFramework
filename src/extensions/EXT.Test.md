# Extension Layer Testing (`AbstractEXTTest`)

This document describes extension-specific testing patterns and best practices.

> **Common Testing Resources**: For testing tools, commands, fixtures, and best practices shared across all layers, see [Framework.Test.md](../Framework.Test.md#testing-tools-and-framework).

This class provides a framework for testing the initialization, dependency management, and core functionalities (hooks, abilities) of classes inheriting from `AbstractStaticExtension`.

## Core Concepts

- **Inheritance**: Test classes for extensions inherit from `AbstractEXTTest`.
- **Isolated Environment**: Each extension gets its own isolated database and server environment for testing via the extension's ServerMixin.
- **Database Isolation**: Extension tests use separate SQLite databases (`test.{extension_name}.database.db`).
- **Server Isolation**: Extension tests run with only their specific extension loaded (`APP_EXTENSIONS={extension_name}`).
- **Configuration**: Each test class specifies the extension it tests (`extension_class`) and lists expected components like abilities and dependencies.
- **Fixtures**: The ServerMixin provides isolated `extension_db`, `extension_server`, and `extension` fixtures for comprehensive testing.
- **Component Verification**: Includes tests to verify that the extension correctly declares its name, version, description, and dependencies.
- **Dependency Testing**: Tests the `check_dependencies` and `resolve_dependencies` static methods for validating and ordering extension loading based on declared dependencies.
- **Hook and Ability Testing**: Verifies the registration and execution mechanisms for hooks (`register_hook`, `trigger_hook`) and abilities (`execute_ability`, `_get_ability_args`).
- **Comprehensive Mocking**: Uses extensive mocking to isolate extension functionality and test various configuration scenarios.

Extensions provide metadata and static functionality to organize and structure import orders. The actual loading of DB tables, BLL managers, EP routers, and providers is handled automatically by the import system.

## Test Architecture

### Extension Test Isolation via ServerMixin

Each extension provides its own ServerMixin for complete test isolation:

```
Extension Test Environment:
┌─────────────────────────────────────┐
│ Database: test.{name}.database.db   │
│ Server: APP_EXTENSIONS={name} only  │ 
│ Environment: Isolated configuration │
│ Mixin: EXT_MyExtension.ServerMixin │
└─────────────────────────────────────┘
```

### Test Inheritance Hierarchy

```
EXT_MyExtension.ServerMixin (extension-specific isolated environment)
    ↓
YourExtensionTest (specific extension tests)
```

Note: Extensions provide their own ServerMixin which includes all necessary fixtures and isolation.

### Database and Server Isolation

The extension's ServerMixin provides isolated testing environments:

1. **`extension_db` fixture**: Creates isolated database `test.{extension_name}.database.db`
2. **`extension_server` fixture**: Creates FastAPI server with only the target extension loaded
3. **`model_registry` fixture**: Provides the isolated model registry from the extension server
4. **`db_manager` fixture**: Provides the database manager from the extension server
5. **`db` fixture**: Provides a database session for testing

## Base Class (`AbstractTest`)

`AbstractEXTTest` inherits from `AbstractTest`, which provides common test functionality:
- **Test Skipping**: Provide `_skip_tests` entries (`SkipThisTest` objects) and the base class automatically applies the skips to matching test methods
- **Test Categories**: Uses the `test_config` attribute with testing categories, timeouts, and other configuration settings
- **Assertion Helpers**: Provides common assertion methods like `assert_objects_equal` and `assert_has_audit_fields`
- **Setup/Teardown**: Implements common setup and teardown patterns that subclasses can extend

This inheritance provides consistent test functionality across all test layers. See `AbstractTest.py` for more details.

## Class Configuration

When creating a test class for a specific extension, configure these attributes:

```python
from extensions.EXT_Example import EXT_Example # The Extension class
from lib.Dependencies import Dependencies, EXT_Dependency, PIP_Dependency, SYS_Dependency

class TestExampleExtension(EXT_Example.ServerMixin):
    # Extension's ServerMixin provides isolated test environment
    # No need to specify extension_class - it's bound to the ServerMixin

    # Expected abilities and abilities
    expected_abilities: List[str] = ["do_example_thing", "process_data"]
    expected_abilities: List[str] = ["example_ability", "data_processing"]

    # Expected dependencies declared by the extension class
    expected_dependencies = Dependencies([
        EXT_Dependency(name="core", optional=False, reason="Core functionality"),
        PIP_Dependency(name="requests", semver=">=2.28.0", reason="HTTP requests")
    ])

    # Test configuration
    test_config = ClassOfTestsConfig(
        categories=[CategoryOfTest.EXTENSION, CategoryOfTest.INTEGRATION],
        cleanup=True,
    )

    # Custom fixtures for extension-specific testing
    @pytest.fixture
    def mock_env_configured(self):
        """Mock environment with proper configuration"""
        with patch("extensions.EXT_Example.env") as mock_env:
            mock_env.side_effect = lambda key: {
                "API_KEY": "test_key",
                "API_URI": "https://test.api.com",
                "LOG_LEVEL": "INFO",
            }.get(key, "")
            yield mock_env

    @pytest.fixture
    def mock_dependency_extension(self):
        """Mock a dependency extension"""
        mock_ext = AsyncMock()
        mock_ext.has_ability.return_value = True
        mock_ext.execute_ability.return_value = {"success": True}
        return mock_ext
```

## Provided Fixtures

### Core Testing Fixtures

The extension's ServerMixin provides these essential fixtures:

#### `extension_server` (scope="class")
Creates an isolated test server for the extension:
- **Environment**: `APP_EXTENSIONS={extension_name}` (only target extension)
- **Database**: Uses the isolated extension database
- **Configuration**: Extension-specific environment overrides
- **Client**: Returns FastAPI TestClient for API testing

```python
def test_database_operations(self, extension_db, extension_server):
    """Test database operations in isolated environment."""
    from database.DatabaseManager import get_session
    
    session = get_session()
    try:
        # Test database operations here
        pass
    finally:
        session.close()
```

#### `extension_db` (scope="class") 
Alias for `db` fixture for backward compatibility. Provides database session from the extension server.

```python
def test_api_endpoints(self, extension_server):
    """Test API endpoints in isolated server."""
    response = extension_server.get("/v1/extension-endpoint")
    assert response.status_code == 200
```

#### `db` (scope="function")
Provides a database session for testing:
- **Database**: Uses isolated `test.{extension_name}.database.db`
- **Session Management**: Automatically handles session lifecycle
- **Usage**: For testing database operations in isolation

```python
def test_extension_abilities(self, extension):
    """Test extension abilities."""
    abilities = extension.get_abilities()
    assert "expected_ability" in abilities
```

### Additional ServerMixin Fixtures

#### `model_registry`
Provides the isolated model registry from the extension server.

#### `db_manager`
Provides the database manager from the extension server.

#### `test_server`
Legacy fixture name that delegates to `extension_server` for backward compatibility.

#### Admin and Team Fixtures
The ServerMixin also provides fixtures for test users and teams:
- `admin_a`, `admin_b`: Admin users for testing
- `team_a`, `team_b`: Teams for testing multi-tenancy

### Additional Custom Fixtures

Common patterns for extension-specific fixtures:

```python
@pytest.fixture
def mock_env_configured(self):
    """Mock environment with proper configuration"""
    with patch.object(self.extension_class, "get_env_value") as mock_get_env:
        mock_get_env.side_effect = lambda key: {
            "EXTENSION_API_KEY": "test_key",
            "EXTENSION_DEBUG": "true",
        }.get(key, "")
        yield mock_get_env

@pytest.fixture
def mock_env_not_configured(self):
    """Mock environment without configuration"""
    with patch.object(self.extension_class, "get_env_value", return_value=""):
        yield

@pytest.fixture
def mock_dependency_extension(self):
    """Mock a dependency extension"""
    mock_ext = MagicMock()
    mock_ext.get_abilities.return_value = {"required_ability"}
    mock_ext.has_ability.return_value = True
    return mock_ext
```

## Comprehensive Testing Strategy

### 1. Extension Metadata and Structure Testing

Test that extension metadata is properly defined and accessible:

```python
def test_extension_metadata(self):
    """Test extension metadata and basic attributes"""
    extension = self.extension_class
    assert extension.name == "example"
    assert extension.version == "1.0.0" 
    assert "Example extension" in extension.description
    assert hasattr(extension, "dependencies")
    assert hasattr(extension, "_env")

def test_dependencies_structure(self):
    """Test that dependencies are properly structured using unified system"""
    extension = self.extension_class
    assert isinstance(extension.dependencies, Dependencies)
    
    # Test dependency properties
    assert hasattr(extension.dependencies, "sys")
    assert hasattr(extension.dependencies, "pip") 
    assert hasattr(extension.dependencies, "ext")
    
    # Test each dependency type
    for dep in extension.dependencies.ext:
        assert hasattr(dep, "name")
        assert hasattr(dep, "optional")
        assert hasattr(dep, "reason")
```

### 2. Isolated Environment Testing

Test that the extension works correctly in its isolated environment:

```python
def test_isolated_database_access(self, db):
    """Test database access in isolated environment."""
    # db fixture provides session from isolated database
    try:
        # Test database operations specific to this extension
        # This runs against the isolated database only
        pass
    finally:
        db.close()

def test_isolated_server_environment(self, extension_server):
    """Test server runs with only this extension loaded."""
    # Verify server is configured correctly
    response = extension_server.get("/health")
    assert response.status_code == 200
    
    # Test extension-specific endpoints
    response = extension_server.get("/v1/extension-specific-endpoint")
    # Assertions for extension functionality
```

### 3. Extension Initialization and Configuration Testing

Test extension initialization under various configuration scenarios:

```python
def test_initialization_with_dependencies_available(self, mock_env_configured):
    """Test extension initialization when dependencies are available"""
    extension = self.extension_class
    # Test that extension detects proper configuration
    config = extension.get_configuration()
    assert config["api_key"] == "test_key"

def test_initialization_without_dependencies(self, extension, mock_env_not_configured):
    """Test extension initialization when dependencies are not available"""
    # Test graceful degradation
    abilities = extension.get_abilities()
    assert "conditional_ability" not in abilities

def test_extension_registration(self):
    """Test that extension is properly registered."""
    from extensions.AbstractExtensionProvider import ExtensionRegistry
    # Extension should be discoverable
    assert self.extension_class is not None
```

### 4. Static Abilities and Abilities Testing

Test ability management and ability execution:

```python
def test_get_abilities(self):
    """Test getting extension abilities"""
    extension = self.extension_class
    abilities = extension.abilities

    assert isinstance(abilities, set)
    for expected_ability in self.expected_abilities:
        assert expected_ability in abilities

def test_register_ability(self, extension):
    """Test registering new ability"""
    new_ability = "test_ability"
    extension.register_ability(new_ability)

    abilities = extension.get_abilities()
    assert new_ability in abilities

def test_static_ability_execution(self, extension):
    """Test static ability execution"""
    # Test abilities through static methods
    if hasattr(extension, "process_data"):
        result = extension.process_data({"test": "data"})
        assert result is not None

def test_ability_availability(self, extension):
    """Test ability availability checking"""
    for expected_ability in self.expected_abilities:
        assert hasattr(extension, expected_ability), f"Missing ability: {expected_ability}"
```

### 5. Dependency Management Testing

Test dependency resolution and management:

```python
def test_check_dependencies(self, extension):
    """Test dependency checking using unified system."""
    loaded_extensions = {"core": "1.0.0", "other_ext": "2.0.0"}
    
    # Test dependency checking
    dependency_status = extension.dependencies.check(loaded_extensions)
    assert isinstance(dependency_status, dict)

def test_install_dependencies(self, extension):
    """Test dependency installation using unified system."""
    # Mock dependency installation
    with patch.object(extension.dependencies, "install", return_value={"git": True}):
        results = extension.dependencies.install()
        assert isinstance(results, dict)

def test_resolve_dependencies(self, extension):
    """Test dependency resolution."""
    # Create mock extensions for testing
    class TestExt1(AbstractStaticExtension):
        name = "test_ext_1"
        dependencies = Dependencies([])

    class TestExt2(AbstractStaticExtension):
        name = "test_ext_2"
        dependencies = Dependencies([
            EXT_Dependency(name="test_ext_1", optional=False, reason="Required")
        ])

    available_extensions = {
        "test_ext_1": TestExt1,
        "test_ext_2": TestExt2,
    }

    # Test dependency resolution
    loading_order = extension.resolve_dependencies(available_extensions)
    assert "test_ext_1" in loading_order
    assert "test_ext_2" in loading_order
    assert loading_order.index("test_ext_1") < loading_order.index("test_ext_2")
```

### 6. Environment Variable and Configuration Testing

Test extension configuration handling:

```python
def test_environment_variable_access(self, extension, mock_env_configured):
    """Test environment variable access through static methods."""
    api_key = extension.get_env_value("EXTENSION_API_KEY")
    assert api_key == "test_key"

def test_configuration_validation(self, extension, mock_env_configured):
    """Test configuration validation."""
    # Test with proper configuration
    assert extension.validate_config() is True

def test_configuration_missing_requirements(self, extension, mock_env_not_configured):
    """Test configuration validation with missing requirements"""
    # Test with missing configuration
    result = extension.validate_config()
    # Should handle missing configuration gracefully
    assert isinstance(result, (bool, list))
```

### 7. Hook System Testing

Test hook registration and execution:

```python
def test_hook_registration(self, extension):
    """Test hook registration system."""
    # Test that extension can register hooks
    if hasattr(extension, "setup_hooks"):
        extension.setup_hooks()
        # Verify hooks are registered
        assert len(AbstractStaticExtension.registered_hooks) >= 0

def test_hook_triggering(self, extension):
    """Test hook triggering mechanism."""
    # Test static hook triggering
    results = extension.trigger_hook(
        "BLL", "Test", "Entity", "create", "before",
        test_data={"param": "value"}
    )
    assert isinstance(results, list)
```

### 8. Database Model and Extension Testing

Test database integration and model extensions:

```python
def test_database_model_access(self, db):
    """Test access to extended database models."""
    # This test would access extension-specific models
    # in the isolated database environment
    try:
        # Test extension model operations
        # Example: if extension has models
        if hasattr(self.extension_class, "models"):
            models = self.extension_class.models
            # Test the extension models
    finally:
        db.close()

def test_extension_specific_tables(self, extension_db, extension_server):
    """Test extension-specific database tables."""
    from database.DatabaseManager import get_session
    
    session = get_session()
    try:
        # Test that extension tables exist and work
        # This runs in the isolated database environment
        pass
    finally:
        session.close()
```

### 9. Error Handling and Graceful Degradation

Test how the extension handles errors and missing dependencies:

```python
def test_graceful_degradation_without_dependencies(self, extension, mock_env_not_configured):
    """Test that extension works gracefully without optional dependencies"""
    # Extension should still function for core abilities
    abilities = extension.get_abilities()
    assert "core_ability" in abilities
    # Optional abilities should not be available
    assert "conditional_ability" not in abilities

def test_error_handling_in_abilities(self, extension):
    """Test error handling in extension abilities."""
    # Test that abilities handle errors gracefully
    if hasattr(extension, "error_prone_ability"):
        with patch.object(extension, "some_dependency", side_effect=Exception("Test error")):
            # Should handle error gracefully
            result = extension.error_prone_ability()
            assert "error" in str(result).lower() or result is None

def test_missing_configuration_handling(self, extension, mock_env_not_configured):
    """Test handling of missing configuration."""
    # Extension should detect missing configuration
    config_status = extension.validate_config()
    # Should return appropriate status (False, empty list, etc.)
    assert config_status is not None
```

## Standard Test Patterns

When testing extensions using ServerMixin, follow these patterns:

### Extension Property Tests
- Test extension metadata (name, version, description)
- Test extension types via `.types` property (returns Set[ExtensionType])
- Test extension abilities via `.abilities` property
- Test extension providers via `.providers` property (auto-discovered)
- Test extension models via `.models` property
- Test extension root rotation via `.root` property

### Dependency Tests
- Test dependency structure using unified Dependencies system
- Test dependency checking and resolution
- Test dependency installation mocking

### Hook Tests
- Test hook registration via `@hook_bll` decorator
- Test hook execution through HookContext

### Provider Discovery Tests
- Test that `.providers` property discovers PRV_*.py files
- Test provider abilities and configuration

### Inherited Test Methods (skipped for static extensions)

These methods are inherited but skipped since extensions are static:
- `test_execute_ability`: Skipped (static extensions don't have instance abilities)
- `test_get_ability_args`: Skipped (static extensions don't have instance ability args)
- `test_on_initialize`: Skipped (static extensions don't have lifecycle methods)
- `test_on_start`: Skipped (static extensions don't have lifecycle methods)
- `test_on_stop`: Skipped (static extensions don't have lifecycle methods)
- `test_get_available_abilities`: Skipped (static extensions don't have instance abilities)
- `test_abilities_discovery`: Skipped (static extensions don't have instance abilities)
- `test_metadata_access`: Skipped (static extensions don't have instance metadata)

## Real-World Examples

### Payment Extension Testing

The payment extension test demonstrates proper architecture:

```python
class TestExtendedUser(EXT_Payment.ServerMixin):
    """Test the extended User model with external_payment_id field."""
    
    @property
    def class_under_test(self):
        """Get the class under test after extension is loaded."""
        return ExtendedUserModel.DB

    def test_extended_user_creation(self, extension_server, extension_db):
        """Test creating an extended user with external_payment_id."""
        from database.DatabaseManager import get_session
        
        session = get_session()
        try:
            # Test operations in isolated database
            create_data = {
                "email": "test@example.com",
                "external_payment_id": "stripe_customer_123"
            }
            
            user = self.class_under_test.create(
                requester_id="test_user_id",
                db=session,
                **create_data
            )
            
            assert user.external_payment_id == "stripe_customer_123"
        finally:
            session.close()
```

### Database Extension Testing

The database extension test shows API endpoint testing:

```python
class TestEXTDatabase(EXT_Database.ServerMixin):
    # Tests run in isolated environment

    def test_extension_metadata(self):
        """Test extension metadata and basic attributes"""
        assert EXT_Database.name == "database"
        assert EXT_Database.version == "1.0.0"
        assert "Database extension" in EXT_Database.description

    def test_api_endpoints(self, extension_server):
        """Test database extension API endpoints"""
        response = extension_server.get("/v1/database/providers")
        assert response.status_code == 200
```

## Extension-Specific Best Practices

In addition to [common testing best practices](../Framework.Test.md#best-practices):

### Test Organization
- **Single Extension Per Test**: Each test class should test only one extension
- **Isolated Environment**: Always use the provided `extension_db` and `extension_server` fixtures
- **Static Testing**: Test extension functionality through class methods, not instances

### Database Testing
- **Use Isolated Database**: Always use `extension_db` fixture for database operations (test.{extension_name}.database.db)
- **Test Extension Models**: Test any extended database models in isolation
- **Migration Testing**: Verify that extension migrations work correctly

### Server Testing
- **Use Extension Server**: Always use `extension_server` fixture for API testing
- **Isolated Environment**: Tests run with only the target extension loaded via APP_EXTENSIONS
- **Extension-Specific Endpoints**: Focus on testing extension-specific functionality

### Configuration Testing
- **Multiple Scenarios**: Test configured, not configured, and partially configured states
- **Graceful Degradation**: Test that extensions handle missing configuration gracefully
5. **Validation Logic**: Test configuration validation thoroughly

### Dependency Testing
1. **Unified System Testing**: Test the Dependencies class functionality
2. **Resolution Testing**: Test dependency resolution and loading order
3. **Installation Testing**: Mock dependency installation for safety
4. **Missing Dependencies**: Test behavior when dependencies are unavailable
5. **Circular Dependencies**: Test that circular dependency detection works

### Fixture Usage
1. **Scope Awareness**: Understand fixture scopes (class vs function)
2. **Dependency Injection**: Use fixtures for dependency injection rather than imports
3. **Custom Fixtures**: Create extension-specific fixtures as needed
4. **Mock Management**: Use fixtures to manage mocks and their lifecycle
5. **Cleanup**: Rely on fixture cleanup rather than manual cleanup

### Error Handling
1. **Exception Testing**: Test that appropriate exceptions are raised
2. **Graceful Degradation**: Test behavior when dependencies fail
3. **Error Messages**: Verify that error messages are helpful and specific
4. **Static Error Handling**: Test error handling at the class level
5. **Recovery Testing**: Test that extensions can recover from transient failures

## Testing Extension-Specific Logic

Beyond the standard abstract tests, add custom test methods for your extension's unique functionality:

```python
def test_custom_extension_functionality(self, extension_server, db):
    """Test extension-specific functionality"""
    # Setup specific test conditions in isolated environment
    try:
        # Test the custom functionality using isolated database
        result = self.extension_class.perform_custom_action(param="test")
        assert result["status"] == "success"
    finally:
        db.close()

def test_extension_api_integration(self, extension_server):
    """Test extension API integration"""
    # Test API endpoints in isolated server environment
    response = extension_server.post("/v1/extension/custom-endpoint", 
                                   json={"param": "value"})
    assert response.status_code == 200
    assert "expected_result" in response.json()

def test_integration_with_other_extensions(self, extension):
    """Test how this extension integrates with other extensions"""
    # Test cross-extension communication through registry
    other_extension = AbstractStaticExtension.get_extension_by_name("other_extension")
    if other_extension:
        result = extension.collaborate_with_extension(other_extension)
        assert result is not None
``` 