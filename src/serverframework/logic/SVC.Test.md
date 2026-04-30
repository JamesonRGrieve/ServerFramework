# Service Layer Testing Framework

This document describes service-specific testing patterns and best practices.

> **Common Testing Resources**: For testing tools, commands, fixtures, and best practices shared across all layers, see [Framework.Test.md](../Framework.Test.md#testing-tools-and-framework).

## Overview

The service testing framework ensures proper lifecycle management, execution loop behavior, error handling, and configuration for long-running background services. It tests both the AbstractService framework itself and provides patterns for testing service-specific logic.

## AbstractSVCTest Base Class

Located in `src/logic/AbstractSVCTest.py`, this class inherits from `AbstractTest` and provides structured testing for service components.

### Required Configuration

Each service test class must configure these attributes:

```python
from serverframework.logic.AbstractSVCTest import AbstractSVCTest
from services.SVC_Example import ExampleService
from unittest.mock import MagicMock

class TestExampleService(AbstractSVCTest):
    # Required: The service class being tested
    service_class = ExampleService
    
    # Required: Default initialization parameters
    service_init_params = {
        "interval_seconds": 1,  # Use short intervals for testing
        "max_failures": 3,
        "retry_delay_seconds": 1,
        # Add service-specific params as needed
    }
    
    # Optional: Additional parameters for mocked service fixture
    mock_init_params = {
        "custom_setting": "test_value"
    }
    
    # Optional: Skip specific tests
    skip_tests = [
        SkipThisTest(
            name="test_max_failures",
            reason=SkipReason.IRRELEVANT,
            details="Service doesn't use standard failure handling"
        )
    ]
```

## Test Fixtures

### Service Fixtures

#### `service(db, requester_id)`
- Creates a standard instance of the service class
- Uses real database session and requester ID
- Automatically calls `cleanup()` after test completion
- Use for testing actual service logic

#### `mocked_service(db, requester_id)`
- Creates service instance with mocked dependencies
- `update()` method is replaced with `AsyncMock`
- Additional mocks applied via `_get_mocks()`
- Use for testing service framework behavior

### Mock Configuration

Override `_get_mocks()` to provide service-specific mocks:

```python
def _get_mocks(self) -> Dict[str, MagicMock]:
    """Provide mocks for external dependencies"""
    return {
        "external_api_call": MagicMock(return_value=True),
        "send_notification": AsyncMock()
    }
```

## Standard Test Methods

### Lifecycle Testing

#### `test_service_lifecycle(mocked_service)`
Tests basic service state management:
- Service starts as not running and not paused
- `start()` sets running=True, paused=False
- `pause()` maintains running=True, sets paused=True
- `resume()` maintains running=True, sets paused=False  
- `stop()` sets running=False

#### `test_cleanup(mocked_service)`
Tests service cleanup:
- Service stops running after `cleanup()`
- Resources are properly released
- State is reset appropriately

### Execution Testing

#### `test_run_service_loop(mocked_service)`
Tests the main service execution loop:
- Service loop calls `update()` method periodically
- Uses `_run_service_loop_for_time()` helper for time-limited testing
- Validates mocked `update()` was called

#### `test_pause_resume(mocked_service)`
Tests pause/resume functionality:
- `update()` not called while service is paused
- `update()` resumes after `resume()` called
- Uses mock reset to verify call patterns

### Error Handling Testing

#### `test_error_handling(mocked_service)`
Tests error recovery mechanisms:
- Configures `update()` to raise exceptions
- Validates failure count increments
- Verifies retry attempts occur
- Uses realistic error scenarios

#### `test_max_failures(mocked_service)`
Tests failure limit enforcement:
- Sets low `max_failures` value
- Configures `update()` to consistently fail
- Validates service stops after exceeding limit
- Confirms `running` flag becomes False

#### `test_handle_failure(service)`
Tests failure handling logic:
- Validates `_handle_failure()` increments failure count
- Tests return value indicates retry allowed
- Validates exception thrown when max failures exceeded
- Uses real service instance for authentic behavior

### Configuration and Initialization Testing

#### `test_configure_service()`
Tests service configuration during initialization:
- Mocks `_configure_service()` method
- Validates it's called during service creation
- Confirms proper initialization flow

#### `test_db_property(service, db)`
Tests database session management:
- Validates `service.db` returns active session
- Checks session is not None
- Verifies session activity state
- Compares with provided database session when appropriate

#### `test_reset_failures(service)`
Tests failure counter reset:
- Sets failure count to non-zero value
- Calls `_reset_failures()`
- Validates counter returns to zero

## Testing Async Operations

### Time-Limited Testing Helper

The framework provides `_run_service_loop_for_time()` for controlled async testing:

```python
async def _run_service_loop_for_time(self, service, seconds):
    """Run service loop for specified duration"""
    loop_task = asyncio.create_task(service.run_service_loop())
    await asyncio.sleep(seconds)
    service.stop()
    
    try:
        await asyncio.wait_for(loop_task, timeout=1)
    except asyncio.TimeoutError:
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass
```

### Async Test Patterns

Most service tests are async and use this pattern:

```python
async def test_custom_behavior(self, mocked_service):
    """Test custom service behavior"""

    # Configure service
    mocked_service.start()
    
    # Run service for limited time
    task = asyncio.create_task(self._run_service_loop_for_time(mocked_service, 2))
    await task
    
    # Validate behavior
    assert mocked_service.update.called
    mocked_service.cleanup()
```

## Testing Actual Service Logic

For testing real service functionality (not just the framework), use the standard `service` fixture:

```python
async def test_actual_update_logic(self, service):
    """Test the real service update logic"""

    # Setup test data in database
    # ...
    
    # Call actual update method
    await service.update()
    
    # Validate database changes
    # ...
    
    # Check side effects
    # ...
```

## Advanced Testing Patterns

### Mock Service for Complex Testing

For services with complex dependencies, create a mock service class:

```python
class MockMyService(MyService):
    """Mock version for testing"""
    
    def _configure_service(self, **kwargs):
        """Override to avoid external dependencies"""
        self.batch_size = kwargs.get("batch_size", 10)
        self.api_client = Mock()
        self.test_data = kwargs.get("test_data", [])
    
    async def _get_pending_items(self):
        """Return test data instead of database query"""
        return self.test_data
    
    async def _process_item(self, item):
        """Mock processing"""
        await asyncio.sleep(0.01)
        return f"processed_{item}"

# Use in tests
async def test_with_mock_service():
    service = MockMyService(
        requester_id="test_user",
        interval_seconds=0.1,
        test_data=["item1", "item2", "item3"]
    )
    
    await service.update()
    assert service.metrics["items_processed"] == 3
```

### Error Simulation Patterns

Test different error scenarios:

```python
async def test_specific_error_handling(self, mocked_service):
    """Test handling of specific error types"""
    # Test connection errors
    mocked_service.update.side_effect = ConnectionError("Network timeout")
    # Run and validate retry behavior
    
    # Test authentication errors
    mocked_service.update.side_effect = AuthenticationError("Invalid credentials")
    # Run and validate service stops
    
    # Test rate limiting
    mocked_service.update.side_effect = RateLimitError("Too many requests")
    # Run and validate backoff behavior
```

### Configuration Testing

Test service configuration with different parameters:

```python
def test_service_configuration_validation():
    """Test service handles invalid configuration"""
    with pytest.raises(ValueError):
        service = MyService(
            requester_id="test_user",
            invalid_param="value"
        )
    
    # Test valid configuration
    service = MyService(
        requester_id="test_user",
        interval_seconds=60,
        max_failures=5
    )
    assert service.interval_seconds == 60
    assert service.max_failures == 5
```

## Running Service Tests

### Execute All Service Tests
```bash
pytest -v src/services/*_test.py
```

### Execute Specific Service Test
```bash
pytest -v src/services/SVC_Example_test.py
```

### Execute Single Test Method
```bash
pytest -v src/services/SVC_Example_test.py::TestExampleService::test_service_lifecycle
```

### Run with Async Support
Service tests require pytest-asyncio:
```bash
pytest -v --asyncio-mode=auto src/services/
```

## Service-Specific Best Practices

In addition to [common testing best practices](../Framework.Test.md#best-practices):

### Test Design
- **Use Appropriate Fixtures** - `mocked_service` for framework testing, `service` for logic testing
- **Short Intervals** - Use brief intervals (1 second) for faster test execution
- **Time Limits** - Use `_run_service_loop_for_time()` for controlled async testing

### Error Testing
- **Failure Limits** - Verify max_failures enforcement
- **Recovery Testing** - Test service recovery after errors
- **Resource Cleanup** - Ensure cleanup happens after failures

### Performance Testing
- **Async Behavior** - Test proper async/await usage
- **Resource Management** - Verify database connections are handled properly
- **Timing Verification** - Test interval compliance

### Service-Specific Testing
- **Business Logic** - Test actual service functionality separately
- **State Management** - Test service state persistence and recovery

## Integration with Service Registry

Test service registry interactions:

```python
def test_service_registry_integration():
    """Test service works with ServiceRegistry"""
    service = MyService(requester_id="test_user", service_id="test_service")
    
    # Register service
    ServiceRegistry.register("test_service", service)
    
    # Verify registration
    assert ServiceRegistry.get("test_service") == service
    assert "test_service" in ServiceRegistry.list()
    
    # Test bulk operations
    ServiceRegistry.start_all()
    assert service.running
    
    ServiceRegistry.stop_all()
    assert not service.running
    
    ServiceRegistry.cleanup_all()
```

## Extending the Framework

To create tests for new services:

1. **Inherit from AbstractSVCTest**
2. **Configure service_class and initialization parameters**
3. **Override _get_mocks() for external dependencies**
4. **Add custom test methods for service-specific logic**
5. **Define `_skip_tests` for incompatible standard tests (auto skip applied)**
6. **Follow async/await patterns for proper testing**

This comprehensive testing framework ensures all background services maintain reliable operation, proper error handling, and consistent lifecycle management across the entire system. 
