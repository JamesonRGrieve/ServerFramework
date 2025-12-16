# SDK Testing Guide

This document outlines SDK-specific testing patterns and best practices.

> **Common Testing Resources**: For testing tools, commands, fixtures, and best practices shared across all layers, see [Framework.Test.md](../Framework.Test.md#testing-tools-and-framework).

## Testing Framework

The SDK follows the framework's revolutionary no-mock approach:
- **Real HTTP Requests**: Tests use actual HTTP client with real server responses
- **No Mocks**: Tests validate actual SDK behavior, not mock assumptions
- **pytest-based**: Uses pytest for test discovery and execution
- **AbstractSDKTest**: Base class providing standardized SDK testing patterns

## Test Structure

Tests are organized as follows:

```
sdk/
├── AbstractSDKTest.py     # Base test class with common functionality
├── SDK_Auth_test.py       # Tests for AuthSDK
├── SDK_Extensions_test.py # Tests for ExtensionsSDK
├── SDK_Providers_test.py  # Tests for ProvidersSDK
└── fixtures/             # Test fixtures
```

All test classes extend `AbstractSDKTest` which provides common functionality for testing SDK components.

## Test Base Class

All test classes must extend `AbstractSDKTest` and provide required overrides:

```python
import pytest
from sdk.AbstractSDKTest import AbstractSDKTest
from sdk.YourSDK import YourSDK

class TestYourModule(AbstractSDKTest):
    # Required overrides
    class_under_test = YourSDK
    create_fields = {
        "name": "Test Entity",
        "description": "A test entity",
        # ... other fields for create operations
    }
    update_fields = {
        "name": "Updated Entity",
        "description": "An updated entity",
        # ... other fields for update operations
    }
    
    
    def test_your_method(self):
        # Set up mock response
        mock_response = {"key": "value"}
        self.mock_response_json(mock_response)
        
        # Call method
        result = self.sdk_handler.your_method()
        
        # Verify request and response
        self.assert_request_called_with("GET", "/expected/endpoint")
        assert result == mock_response
```

## Standard Test Methods

The base class provides standard test methods that are automatically available:

```python
class TestYourModule(AbstractSDKTest):
    # ... required overrides ...

    # These methods are inherited and will work automatically
    
    def test_create(self)  # Tests entity creation
    
    # @pytest.mark.dependency(depends=["test_create"])
    def test_get(self)     # Tests entity retrieval
    
    # @pytest.mark.dependency(depends=["test_create"])
    def test_list(self)    # Tests entity listing
    
    # @pytest.mark.dependency(depends=["test_create"])
    def test_update(self)  # Tests entity updating
    
    # @pytest.mark.dependency(depends=["test_create"])
    def test_delete(self)  # Tests entity deletion
```

## Testing Real HTTP Interactions

The `AbstractSDKTest` class tests actual HTTP requests against a real test server:

```python
def test_successful_request(self, server):
    """Test actual SDK request against real server."""
    sdk = self.class_under_test(base_url=server.url)
    result = sdk.get_user("123")
    assert result["id"] == "123"
    assert "name" in result

def test_error_response(self, server):
    """Test SDK error handling with real server errors."""
    sdk = self.class_under_test(base_url=server.url)
    with pytest.raises(ResourceNotFoundError) as exc_info:
        sdk.get_user("nonexistent-id")
    assert exc_info.value.status_code == 404

def test_validation_error(self, server):
    """Test SDK validation with real server validation."""
    sdk = self.class_under_test(base_url=server.url)
    with pytest.raises(ValidationError) as exc_info:
        sdk.create_user({"invalid": "data"})
    assert "required" in str(exc_info.value).lower()
```

## Using Test Fixtures

Leverage standard framework fixtures:

```python
def test_with_fixtures(self, server, admin_a):
    """Test using framework fixtures."""
    sdk = self.class_under_test(base_url=server.url)

    # Authenticate with real admin user
    result = sdk.login(email=admin_a.email, password="test_password")
    assert "token" in result

    # Use token for authenticated requests
    sdk.set_token(result["token"])
    user = sdk.get_user(admin_a.id)
    assert user["id"] == admin_a.id
```

## Code Coverage

Aim for high test coverage (90%+) for all SDK components. The test script includes coverage reporting. 

To view detailed coverage:

```bash
pytest --cov=sdk --cov-report=html
# Then open htmlcov/index.html in your browser
```

## Testing Authentication

The base class automatically tests authentication. Override only if needed:

```python

def test_authentication(self):
    """Test custom authentication behavior."""
    super().test_authentication()
    # Add custom authentication tests
```

## Testing Request Parameters

Test real request parameter handling:

```python
def test_query_parameters(self, server):
    """Test SDK properly sends query parameters."""
    sdk = self.class_under_test(base_url=server.url)

    # Call with parameters - server validates them
    results = sdk.list_resources(offset=10, limit=50, sort_by="name")

    # Server returns results based on actual parameters
    assert len(results) <= 50
    assert all(r["name"] for r in results)  # Verify sort worked
```

## End-to-End Testing

All SDK tests validate real behavior against the test server:

```python
def test_complete_workflow(self, server, admin_a):
    """Test complete SDK workflow with real operations."""
    sdk = self.class_under_test(base_url=server.url)

    # Login with real user
    auth_result = sdk.login(email=admin_a.email, password="test_password")
    sdk.set_token(auth_result["token"])

    # Create resource
    created = sdk.create_resource({"name": "Test Resource"})
    assert created["id"]

    # Retrieve resource
    retrieved = sdk.get_resource(created["id"])
    assert retrieved["name"] == "Test Resource"

    # Update resource
    updated = sdk.update_resource(created["id"], {"name": "Updated"})
    assert updated["name"] == "Updated"

    # Delete resource
    sdk.delete_resource(created["id"])

    # Verify deletion
    with pytest.raises(ResourceNotFoundError):
        sdk.get_resource(created["id"])
```

## SDK-Specific Best Practices

In addition to [common testing best practices](../Framework.Test.md#best-practices):

1. **Extend AbstractSDKTest** - Always extend the base test class for SDK modules
2. **Provide Required Overrides** - Set `class_under_test`, `create_fields`, and `update_fields`
3. **Use Real Server** - Test against actual test server, never mock HTTP
4. **Test Complete Workflows** - Validate end-to-end SDK functionality with real requests

## Running SDK Tests

See [Framework.Test.md](../Framework.Test.md#testing-commands) for common test commands. SDK-specific examples:

```bash
# Run all SDK tests
pytest sdk/

# Run a specific SDK test file
pytest sdk/SDK_Auth_test.py

# Run a specific SDK test class
pytest sdk/SDK_Auth_test.py::TestAuthSDK

# Run a specific SDK test method
pytest sdk/SDK_Auth_test.py::TestAuthSDK::test_login

# Run integration tests only
pytest sdk/ -m "integration"
``` 