# BLL Testing Framework

This document describes BLL-specific testing patterns and best practices.

> **Common Testing Resources**: For testing tools, commands, fixtures, and best practices shared across all layers, see [Framework.Test.md](../Framework.Test.md#testing-tools-and-framework).

## Overview

The BLL testing system provides exhaustive testing for business logic layer functionality through the `AbstractBLLTest` base class. It ensures proper behavior of CRUD operations, search abilities, batch operations, validation, and hooks.

## AbstractBLLTest Base Class

Located in `src/logic/AbstractBLLTest.py`, this class inherits from `AbstractTest` and provides comprehensive BLL testing functionality.

### Required Configuration

Each test class must override these attributes:

```python
class TestEntityManager(AbstractBLLTest):
    # Required: The BLL manager class being tested
    class_under_test = EntityManager
    
    # Required: Fields for creating test entities
    create_fields = {
        "name": "Test Entity",
        "description": "Test Description",
        "field1": "value1"
    }
    
    # Required: Fields for testing updates
    update_fields = {
        "name": "Updated Entity",
        "description": "Updated Description"
    }
    
    # Required: Fields that should be unique (for assertions)
    unique_fields = ["name", "email"]  
    
    # Optional: Skip specific tests with reasons (inherited from AbstractTest)
    skip_tests = [
        SkipThisTest(
            name="test_batch_update",
            reason=SkipReason.IRRELEVANT,
            details="Custom reason for skipping"
        )
    ]
    
    # Optional: Parent entity dependencies
    parent_entities = [
        ParentEntity(
            name="team",
            foreign_key="team_id", 
            test_class=TestTeamManager
        )
    ]
    
    # Optional: Set True for system entities that use SYSTEM_ID as requester
    is_system_entity = False
```

### Test Configuration

Default test configuration targets LOGIC category:

```python
test_config: ClassOfTestsConfig = ClassOfTestsConfig(
    categories=[CategoryOfTest.LOGIC]
)
```

## Standard Test Methods

### Core CRUD Tests

#### `test_create(admin_a, team_a)`
- Creates a test entity using `_create()` method
- Validates entity creation with `_create_assert()`
- Checks entity has valid ID and required fields
- Uses `` for test ordering

#### `test_get(admin_a, team_a)` 
- Depends on `test_create`
- Creates entity for testing, then retrieves it
- Validates successful retrieval with `_get_assert()`
- Ensures entity data integrity

#### `test_list(admin_a, team_a)`
- Depends on `test_create`
- Creates multiple entities (list_1, list_2, list_3)
- Lists all entities and validates they're included
- Checks returned data is proper list format

#### `test_search(admin_a, team_a, server, model_registry, search_field, search_operator)`
- Parametrized test for comprehensive search functionality
- Tests each field/operator combination automatically based on model field types
- Validates search results contain expected entities
- Supports string, numeric, date, and boolean field search operations
- Uses `generate_search_test_parameters()` class method to auto-discover testable field/operator combinations

**Parametrization Mechanism:**
The `test_search` method uses pytest's parametrize decorator with the `generate_search_test_parameters()` class method. This method introspects the BLL model's fields and generates all valid (field, operator) combinations based on field types:

```python
@pytest.mark.parametrize(
    "search_field,search_operator",
    generate_search_test_parameters.__func__(None)  # Called at class definition time
)
def test_search(self, admin_a, team_a, server, model_registry, search_field, search_operator):
    # Test implementation
```

The `generate_search_test_parameters()` method:
1. Inspects the model's `__annotations__` to find field types
2. Maps each field type to supported operators:
   - `str` → `["value", "eq", "inc", "sw", "ew"]`
   - `int`/`float` → `["value", "eq", "neq", "lt", "gt", "lteq", "gteq"]`
   - `datetime` → `["after", "before", "eq", "on"]`
   - `bool` → `["value", "eq"]`
3. Returns a list of `(field_name, operator)` tuples for pytest to parametrize

#### `test_update(admin_a, team_a)`
- Depends on `test_create`
- Creates entity, then updates with `update_fields`
- Validates all update fields were applied correctly
- Checks field values match expected updates

#### `test_delete(admin_a, team_a)`
- Depends on `test_create`
- Creates entity, then deletes it
- Validates entity no longer exists or throws proper exception
- Checks for expected error message format

### Batch Operations Tests

#### `test_batch_update(admin_a, team_a)`
- Depends on `test_create`
- Creates multiple entities (batch_1, batch_2, batch_3)
- Updates all entities in single batch operation
- Validates correct number of entities updated
- Checks individual entity field updates

#### `test_batch_delete(admin_a, team_a)`
- Depends on `test_create`
- Creates multiple entities for deletion
- Deletes all entities in single batch operation
- Validates entities no longer exist

### Hook System Tests

#### `test_hooks(admin_a, team_a)`
- Depends on `test_create`
- Validates hook registration and structure
- Checks manager has hooks attribute
- Verifies create hooks have before/after arrays
- Confirms proper hook discovery system

### Scalability Tests

#### `test_scalability_list_n_factor(admin_a, team_a, server, model_registry, metric)`
- Parametrized over `metric ∈ {TIME, QUERY_COUNT, MEMORY}`.
- Skipped unless the subclass sets `scalability_profile` (a `ScalabilityProfile` from `lib.Scalability`).
- Seeds N entities at each `n` in `scalability_profile.n_values`, then calls `manager.list()` and asserts the observed Big-O exponent stays within the per-metric threshold.
- Defaults: `k ≤ 1.4` for time/memory, `k ≤ 0.4` for query count (catches N+1 in resolver chains and hook-storm regressions).
- See [LIB.Scalability.md](../lib/LIB.Scalability.md).

## Entity Management

### Tracked Entities System

AbstractBLLTest tracks created entities for automatic cleanup:

```python
# Entities are stored in tracked_entities dict
self.tracked_entities = {}

# Example usage in tests
self._create(admin_a.id, team_a.id, "create")  # Stores as tracked_entities["create"]
self._get(admin_a.id, team_a.id, "get_result", "create")  # Gets tracked_entities["create"]
```

### Automatic Cleanup

The `_cleanup_test_entities()` method automatically removes all tracked entities after tests, preventing database pollution.

### Parent Entity Support

For entities with dependencies, define parent entities:

```python
parent_entities = [
    ParentEntity(
        name="user",
        foreign_key="user_id",
        test_class=TestUserManager
    ),
    ParentEntity(
        name="team", 
        foreign_key="team_id",
        test_class=TestTeamManager
    )
]
```

## Test Utilities

### Helper Methods

- `_create()` - Create entity and store in tracked_entities
- `_get()` - Retrieve entity by ID
- `_list()` - List entities with optional filters
- `_search()` - Search entities with parameters
- `test_search()` - Parametrized comprehensive search testing for all field types
  - Automatically tests all applicable operators based on field types
  - String fields: `value`, `eq`, `inc`, `sw`, `ew`
  - Numeric fields: `value`, `eq`, `neq`, `lt`, `gt`, `lteq`, `gteq`
  - Date fields: `after`, `before`, `eq`, `on`
  - Boolean fields: `value`, `eq`
  - Uses `generate_search_test_parameters()` class method for auto-discovery
- `_update()` - Update entity with update_fields
- `_delete()` - Delete entity by ID
- `_batch_update()` - Update multiple entities
- `_batch_delete()` - Delete multiple entities

### Assertion Methods

- `_create_assert()` - Validate successful creation
- `_get_assert()` - Validate successful retrieval
- `_list_assert()` - Validate list results contain expected entities
- `_search_assert()` - Validate search results
- `_update_assert()` - Validate update field changes
- `_delete_assert()` - Validate entity deletion
- `_batch_update_assert()` - Validate batch update results
- `_batch_delete_assert()` - Validate batch deletion

### System Entity Support

For system-level entities, set `is_system_entity = True` to use `SYSTEM_ID` instead of user IDs.

## Module-Specific Test Examples

### Authentication Module (`BLL_Auth_test.py`)

Comprehensive tests for all authentication managers:

#### UserManager Tests
- Custom create/delete logic for self-deletion rules
- Password requirements and validation
- Invitation code processing during registration
- Metadata handling

#### TeamManager Tests  
- Hierarchical team structure testing
- Metadata integration
- Encryption key management

#### RoleManager Tests
- System vs team-specific role testing
- Role hierarchy and inheritance
- MFA and security requirement validation

#### InvitationManager Tests
- Invitation code generation patterns
- Team vs app-level invitations
- Email invitation workflows
- Unified acceptance methods
- Registration-time invitation acceptance

#### PermissionManager Tests
- Four permission patterns:
  1. User-specific permissions
  2. Team-wide permissions  
  3. Team+role permissions
  4. Role-specific permissions
- Permission validation and business rules

### Extensions Module (`BLL_Extensions_test.py`)

Simple CRUD testing for:
- **ExtensionManager** - Basic extension management
- **AbilityManager** - Extension abilities with parent relationships

### Providers Module (`BLL_Providers_test.py`)

Complex relationship testing:
- **ProviderManager** - Provider CRUD with settings
- **ProviderInstanceManager** - Instance management with usage tracking
- **RotationManager** - Provider rotation logic
- Multiple junction table managers for relationships

## Advanced Testing Patterns

### Custom Test Overrides

Override standard tests for specific business logic:

```python

def test_create(self, admin_a, team_a):
    """Override for custom creation logic"""
    # Custom creation logic
    self._create(admin_a.id, team_a.id)
    self._create_assert("create")
    
    # Additional custom validations
    entity = self.tracked_entities["create"]
    assert entity.custom_field == expected_value
```

### Skip Patterns

Use `SkipThisTest` for incompatible tests:

```python
skip_tests = [
    SkipThisTest(
        name="test_batch_delete",
        reason=SkipReason.IRRELEVANT,
        details="Only user can delete their own account"
    )
]
```

### Complex Business Logic Testing

For managers with complex business logic, add custom test methods:

```python

def test_invitation_acceptance_workflow(self, admin_a, team_a):
    """Test complete invitation acceptance flow"""
    # Create invitation
    # Add invitee
    # Accept invitation
    # Verify team membership
    # Check metadata updates
```

## Running BLL Tests

See [Framework.Test.md](../Framework.Test.md#testing-commands) for common test commands. BLL-specific examples:

```bash
# Run all BLL tests
pytest src/logic/*_test.py

# Run specific BLL module
pytest src/logic/BLL_Auth_test.py

# Run specific test class
pytest src/logic/BLL_Auth_test.py::TestUserManager

# Run single test method
pytest src/logic/BLL_Auth_test.py::TestUserManager::test_create
```

## BLL-Specific Best Practices

In addition to [common testing best practices](../Framework.Test.md#best-practices):

### Test Design
- **Use Dependencies** - Leverage pytest.mark.dependency for proper test ordering
- **Business Logic** - Override standard tests for custom business rules
- **Hook Testing** - Test before/after hooks thoroughly

### Data Management
- **Parent Dependencies** - Properly configure parent_entities for relationships
- **Unique Data** - Use `faker` or UUID for unique test data
- **Cleanup** - Let AbstractBLLTest handle entity cleanup automatically

### Extending the Framework
1. Inherit from AbstractBLLTest
2. Configure required attributes (class_under_test, create_fields, update_fields, unique_fields)
3. Define parent_entities if needed
4. Override standard tests for custom business logic
5. Add custom test methods for specific functionality
6. Configure skip_tests for incompatible standard tests

## Testing Utilities

### Faker Integration
Tests use `Faker` for generating realistic test data:

```python
faker = Faker()

create_fields = {
    "email": faker.unique.email,
    "name": f"Test {faker.word()}",
    "description": faker.sentence()
}
```

### Environment Integration
Tests use environment variables for system IDs and configuration:

```python
from zephyrex.lib.Environment import env

requester_id = env("ROOT_ID")
system_id = env("SYSTEM_ID")
user_role_id = env("USER_ROLE_ID")
```

This comprehensive testing framework ensures all BLL managers maintain consistent behavior, proper validation, and reliable functionality across the entire system.