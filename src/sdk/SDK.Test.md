# SDK Testing Guide

This document outlines SDK-specific testing patterns and best practices.

> **Common Testing Resources**: For testing tools, commands, fixtures, and best practices shared across all layers, see [Framework.Test.md](../Framework.Test.md#testing-tools-and-framework).

## Testing Framework

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

## Mocking HTTP Requests

The `AbstractSDKTest` class includes tools for mocking HTTP requests:

```python
# Mock a successful JSON response
self.mock_response_json({"key": "value"})

# Mock an error response
self.mock_response_error(404, "Resource not found")

# Mock a validation error
self.mock_response_validation_error({"field": ["This field is required"]})

# Verify request details
self.assert_request_called_with(
    method="POST",
    endpoint="/v1/resource",
    data={"key": "value"},
    params={"filter": "value"}
)
```

## Testing Different Response Types

Test various response scenarios:

```python

def test_successful_request(self):
    response_data = {"user": {"id": "123", "name": "Test User"}}
    self.mock_response_json(response_data)
    result = self.sdk_handler.get_user("123")
    assert result == response_data


def test_error_response(self):
    self.mock_response_error(404, "User not found")
    with pytest.raises(ResourceNotFoundError) as exc_info:
        self.sdk_handler.get_user("nonexistent")
    assert exc_info.value.status_code == 404


def test_request_exception(self, mocker):
    # Use pytest-mock's mocker fixture
    mocker.patch("httpx.Client.request", side_effect=Exception("Connection error"))
    with pytest.raises(SDKException) as exc_info:
        self.sdk_handler.get_user("123")
    assert "Connection error" in str(exc_info.value)
```

## Using Test Fixtures

Create pytest fixtures for commonly used data:

```python
@pytest.fixture
def user_data(self):
    """Fixture providing test user data."""
    return {
        "id": "123",
        "name": "Test User",
        "email": "test@example.com"
    }


def test_with_fixture(self, user_data):
    """Test using the fixture data."""
    self.mock_response_json({"user": user_data})
    result = self.sdk_handler.get_user(user_data["id"])
    assert result["user"] == user_data
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

Verify correct request parameters using the standard assertion method:

```python

def test_query_parameters(self):
    # Set up mock response
    self.mock_response_json({"results": []})
    
    # Call method with query parameters
    self.sdk_handler.list_resources(offset=10, limit=50, sort_by="name")
    
    # Verify request
    self.assert_request_called_with(
        "GET", 
        "/v1/resource", 
        params={"offset": 10, "limit": 50, "sort_by": "name"}
    )
```

## Integration Testing

While most tests are unit tests that mock HTTP requests, you might want some integration tests against a real API:

```python
@pytest.mark.integration

def test_integration_login(self):
    """Integration test requiring real API access."""
    if not pytest.importorskip("os").environ.get("RUN_INTEGRATION_TESTS"):
        pytest.skip("Skipping integration test")
        
    # Use real API for this test
    sdk = self.class_under_test(base_url="https://api.example.com")
    result = sdk.login(
        email=pytest.importorskip("os").environ.get("TEST_USER_EMAIL"),
        password=pytest.importorskip("os").environ.get("TEST_USER_PASSWORD")
    )
    assert "token" in result
```

## SDK-Specific Best Practices

In addition to [common testing best practices](../Framework.Test.md#best-practices):

1. **Extend AbstractSDKTest** - Always extend the base test class for SDK modules
2. **Provide Required Overrides** - Set `class_under_test`, `create_fields`, and `update_fields`
3. **Use Standard Assertions** - Use `self.assert_request_called_with()` for request verification
4. **Mock HTTP Responses** - SDK tests mock HTTP rather than use real API calls

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