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
    sdk_class = YourSDK
    resource_name = "your_resource"
    sample_data = {
        "name": "Test Entity",
        "description": "A test entity",
    }
    update_data = {
        "name": "Updated Entity",
    }

    # Required abstract method implementations
    def create_test_data(self, resource_type: str, count: int = 1):
        """@abstractmethod - Create test data for the specified resource type."""
        return [{"name": f"test_{i}"} for i in range(count)]

    def assert_valid_response_structure(self, response, expected_keys, resource_key=None):
        """@abstractmethod - Assert that a response has the expected structure."""
        if resource_key:
            assert resource_key in response
        for key in expected_keys:
            assert key in response

    def test_your_method(self, server, admin_a):
        """Test SDK method with real server."""
        sdk = self.create_authenticated_sdk(server, admin_a)
        result = sdk.your_resources.list()
        assert result is not None
```

## Required Overrides

### Class Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `sdk_class` | Type[AbstractSDKHandler] | The SDK class being tested |
| `resource_name` | str | Primary resource name being tested |
| `sample_data` | Dict[str, Any] | Sample data for creating test entities |
| `update_data` | Dict[str, Any] | Sample data for updating test entities |

### Abstract Methods (must be implemented)

| Method | Signature | Description |
|--------|-----------|-------------|
| `create_test_data` | `(resource_type: str, count: int = 1) -> List[Dict]` | Create test data for the specified resource type |
| `assert_valid_response_structure` | `(response: Dict, expected_keys: List[str], resource_key: str = None)` | Assert response has expected structure |

## Optional Overrides

| Attribute | Type | Description |
|-----------|------|-------------|
| `sdk_test_config` | SDKTestConfig | Test configuration options |
| `resource_configs` | Dict[str, ResourceConfig] | Expected resource configurations |
| `_skip_tests` | List[SkipThisTest] | Tests to skip with reasons |

## Key Methods

### SDK Creation

```python
# Create unauthenticated SDK
sdk = self.create_sdk(server)

# Create authenticated SDK with user fixture
sdk = self.create_authenticated_sdk(server, admin_a)

# Create with specific credentials
sdk = self.create_sdk(server, token="jwt_token", api_key="api_key")
```

### Test Data Generation

```python
# Get test data with unique values
data = self.get_test_data(TestVariant.VALID)
data = self.get_test_data(TestVariant.MINIMAL)
data = self.get_test_data(TestVariant.INVALID)

# Create multiple test data items
items = self.create_test_data("resource_type", count=5)
```

### Assertions

```python
# Assert response structure
self.assert_valid_response_structure(response, ["id", "name"], "resource_key")

# Assert pagination
self.assert_pagination_response(response, "items")

# Assert entity created
entity = self.assert_entity_created(response)

# Assert entity updated
entity = self.assert_entity_updated(response, {"name": "updated"})
```

## Standard Test Methods

The base class provides standard test methods that are automatically inherited:

```python
class TestYourModule(AbstractSDKTest):
    # These methods are inherited and will work automatically

    def test_sdk_initialization(self, server)
    def test_resource_configuration(self, server)
    def test_resource_managers_created(self, server)
    def test_headers_with_token(self, server)
    def test_headers_with_api_key(self, server)
    def test_url_building(self, server)
    def test_unauthenticated_request_fails(self, server)
```

## Testing Real HTTP Interactions

Tests use pytest fixtures to get a real test server:

```python
def test_successful_request(self, server, admin_a):
    """Test actual SDK request against real server."""
    sdk = self.create_authenticated_sdk(server, admin_a)
    result = sdk.users.get(admin_a.id)
    assert result is not None

def test_error_response(self, server, admin_a):
    """Test SDK error handling with real server errors."""
    sdk = self.create_authenticated_sdk(server, admin_a)
    with pytest.raises(ResourceNotFoundError) as exc_info:
        sdk.users.get("nonexistent-id")
    assert exc_info.value.status_code == 404

def test_validation_error(self, server, admin_a):
    """Test SDK validation with real server validation."""
    sdk = self.create_authenticated_sdk(server, admin_a)
    with pytest.raises(ValidationError) as exc_info:
        sdk.users.create({"invalid": "data"})
    assert exc_info.value.status_code == 422
```

## Using Test Fixtures

SDK tests use the same fixtures from `conftest.py` as other framework tests:

| Fixture | Scope | Description |
|---------|-------|-------------|
| `server` | session | TestClient for API requests |
| `admin_a` | session | Admin user with JWT token |
| `team_a` | session | Team fixture for testing |
| `admin_b` | session | Second admin user |
| `team_b` | session | Second team fixture |
| `db` | function | Database session |

```python
def test_with_fixtures(self, server, admin_a, team_a):
    """Test using framework fixtures."""
    sdk = self.create_authenticated_sdk(server, admin_a)

    # Get current user
    user = sdk.get_current_user()
    assert user is not None

    # List teams
    teams = sdk.teams.list()
    assert teams is not None
```

## Code Coverage

Aim for high test coverage (90%+) for all SDK components. The test script includes coverage reporting.

To view detailed coverage:

```bash
pytest --cov=sdk --cov-report=html
# Then open htmlcov/index.html in your browser
```

## End-to-End Testing

All SDK tests validate real behavior against the test server:

```python
def test_complete_workflow(self, server, admin_a):
    """Test complete SDK workflow with real operations."""
    sdk = self.create_authenticated_sdk(server, admin_a)

    # List resources
    resources = sdk.resources.list()
    assert resources is not None

    # Create resource
    data = self.get_test_data()
    created = sdk.resources.create(data)
    entity = self.assert_entity_created(created)

    # Retrieve resource
    retrieved = sdk.resources.get(entity["id"])
    assert retrieved is not None

    # Update resource
    updated = sdk.resources.update(entity["id"], self.update_data)
    self.assert_entity_updated(updated, self.update_data)

    # Delete resource
    sdk.resources.delete(entity["id"])

    # Verify deletion
    with pytest.raises(ResourceNotFoundError):
        sdk.resources.get(entity["id"])
```

## SDK-Specific Best Practices

In addition to [common testing best practices](../Framework.Test.md#best-practices):

1. **Extend AbstractSDKTest** - Always extend the base test class for SDK modules
2. **Provide Required Overrides** - Set `sdk_class`, `resource_name`, `sample_data`, and `update_data`
3. **Use Real Server** - Test against actual test server, never mock HTTP
4. **Use Fixtures** - Leverage `server`, `admin_a`, `team_a` fixtures
5. **Test Complete Workflows** - Validate end-to-end SDK functionality with real requests

## Running SDK Tests

See [Framework.Test.md](../Framework.Test.md#testing-commands) for common test commands. SDK-specific examples:

```bash
# Run all SDK tests
pytest src/sdk/ -v

# Run a specific SDK test file
pytest src/sdk/SDK_Auth_test.py -v

# Run a specific SDK test class
pytest src/sdk/SDK_Auth_test.py::TestUserSDK -v

# Run a specific SDK test method
pytest src/sdk/SDK_Auth_test.py::TestUserSDK::test_get_current_user -v

# Run with coverage
pytest src/sdk/ --cov=sdk --cov-report=term-missing
```

## Test Categories

SDK tests use the `CategoryOfTest.SDK` category:

```python
test_config: ClassOfTestsConfig = ClassOfTestsConfig(
    categories=[CategoryOfTest.SDK]
)
```
