# Database Testing Architecture

This document describes database-specific testing patterns and best practices.

> **Common Testing Resources**: For testing tools, commands, fixtures, and best practices shared across all layers, see [Framework.Test.md](../Framework.Test.md#testing-tools-and-framework).

## Overview
The database testing architecture (`AbstractDBTest.py`, `AbstractDatabaseEntity_test.py`) provides enterprise-grade test coverage for database entities, sophisticated permission systems, dependency resolution seeding, and core database functionality. The system uses advanced inheritance patterns and parameterized testing to ensure comprehensive validation across all database models and return types.

## Core Testing Components

### AbstractDBTest (`AbstractDBTest.py`)
The foundation class for all database entity tests, providing:

**Key Features:**
- Comprehensive CRUD operation testing (create, get, list, count, exists, update, delete)
- Permission-aware testing with multi-user scenarios
- Hook system testing for create/update/delete operations
- Automatic cleanup of test entities
- Support for parent entity relationships
- Parameterized testing across return types (`dict`, `db`, `model`)

**Required Child Class Overrides:**
```python
# Automatic class determination through server fixture integration
create_fields = {...}                   # Required fields for entity creation  
update_fields = {...}                   # Fields for entity updates
unique_fields = ["name"]               # Fields requiring unique values for conflict resolution
```

**Configuration Options:**
```python
is_system_entity = False               # System-flagged entity
has_permission_references = False      # Permission inheritance enabled
reference_fields = {...}               # Reference mapping for permissions
parent_entities = [...]                # Parent entity dependencies
```

**Core Test Methods:**
- `test_CRUD_create()` - Entity creation with permission checks across return types
- `test_CRUD_get()` - Entity retrieval with filtering across return types
- `test_CRUD_list()` - List operations with pagination across return types
- `test_CRUD_count()` - Count operations with filters
- `test_CRUD_exists()` - Existence checks
- `test_CRUD_update()` - Updates with permission validation across return types
- `test_CRUD_delete()` - Soft deletion with access control
- `test_CRUD_soft_delete()` - Deleted entity accessibility for ROOT_ID
- `test_ORM_*()` - Direct SQLAlchemy operations for comparison
- `test_scalability_list_n_factor(metric)` - Big-O assertion (parametrized over `TIME`/`QUERY_COUNT`/`MEMORY`); skipped unless the subclass sets `scalability_profile`. Catches N+1 and super-linear regressions in `sqlalchemy_model.list()`. See [LIB.Scalability.md](../lib/LIB.Scalability.md).

### AbstractDatabaseEntity Testing (`AbstractDatabaseEntity_test.py`)
Unit tests for the core database entity functionality:

**Mixin Testing:**
- `BaseMixin` - Core ID, timestamps, and CRUD operations
- `UpdateMixin` - Update tracking and soft deletion
- `ParentMixin` - Hierarchical relationships
- `ImageMixin` - Image URL handling

**Core Function Testing:**
- Hook system functionality (`HookDict`, `HooksDescriptor`)
- Permission checking methods
- CRUD operation implementations
- Reference mixin generation
- DTO conversion and return type handling

**Advanced Features:**
- Integration testing with multiple mixins
- Edge case handling (null values, invalid IDs)
- Special ID handling (ROOT_ID, SYSTEM_ID, TEMPLATE_ID)
- Circular reference protection

## Entity-Specific Test Suites

### Authentication Entities (`DB_Auth_test.py`)
Comprehensive tests for all authentication-related entities:

**User Management:**
- `TestUser` - User entity with team-based visibility
- `TestUserCredential` - Password and authentication data
- `TestUserRecoveryQuestion` - Security question management
- `TestUserMetadata` - User configuration storage

**Team & Role Management:**
- `TestTeam` - Team entities with encryption keys
- `TestTeamMetadata` - Team configuration storage
- `TestRole` - Role definitions with MFA requirements
- `TestUserTeam` - User-team membership relationships

**Security & Access:**
- `TestPermission` - Permission records with resource targeting
- `TestInvitation` - Team invitation system
- `TestInvitee` - Invitation recipient tracking
- `TestFailedLoginAttempt` - Security monitoring
- `TestSession` - Session management with JWT
- `TestRateLimitPolicy` - API rate limiting configuration

### Extension Entities (`DB_Extensions_test.py`)
Tests for the extension system:

**Core Components:**
- `TestExtension` - Extension definitions
- `TestAbility` - Extension ability definitions

**Parent-Child Relationships:**
- Ability entities reference their parent extensions
- Proper cleanup and dependency handling

### Provider Entities (`DB_Providers_test.py`)
Tests for the provider and rotation system:

**Provider Management:**
- `TestProvider` - Provider definitions with agent settings
- `TestProviderInstance` - Specific provider configurations
- `TestProviderExtension` - Extension-provider mappings
- `TestProviderExtensionAbility` - Ability mappings

**Instance Management:**
- `TestProviderInstanceUsage` - Usage tracking and billing
- `TestProviderInstanceSetting` - Instance-specific configuration
- `TestProviderInstanceExtensionAbility` - Instance ability states

**Rotation System:**
- `TestRotation` - Rotation definitions
- `TestRotationProviderInstance` - Provider rotation assignments

## Permission System Testing (`StaticPermissions_test.py`)

### Core Permission Functions
Comprehensive tests for permission checking and enforcement:

**ID Type Validation:**
- `TestSystemIDs` - ROOT_ID, SYSTEM_ID, TEMPLATE_ID recognition
- Special access patterns for system users

**Permission Checking:**
- `TestPermissionChecks` - Basic permission validation
- Owner access patterns
- Non-owner denial scenarios
- Resource not found handling

**Advanced Permission Features:**
- `TestPermissionReferences` - Permission inheritance through references
- `TestTimeLimitedPermissions` - Expiration handling
- `TestCreatePermissionReference` - Creation permission chains
- `TestPermissionDelegation` - Permission management rights

**System Protections:**
- `TestCircularReferenceProtection` - Prevents infinite loops
- `TestTeamHierarchyDepth` - Limits team nesting
- `TestDeletedRecordsAccess` - Controls deleted record visibility
- `TestTemplatePermissions` - Template resource special handling

## Seed Management Testing (`StaticSeeder_test.py`)

### Seed System Validation
Tests for the database seeding infrastructure:

**Model Discovery:**
- `TestGetAllModels` - Model ordering and dependency resolution
- Proper handling of inheritance hierarchies
- Subclass integration

**Seed Processing:**
- `TestSeedModel` - Individual model seeding
- Idempotent seeding (no duplicates)
- Error handling and recovery
- Special entity handling (ProviderInstance)

**Seed Data Management:**
- Callable seed lists
- Custom seed ID handling
- Get seed list method support
- Error resilience

## Testing Patterns & Best Practices

### Inheritance Pattern
```python
class TestEntityName(AbstractDBTest):
    create_fields = {
        "name": "Test Entity",
        "description": "Test Description"
    }
    update_fields = {
        "description": "Updated Description"
    }
    unique_fields = ["name"]
    
    # Automatically uses server fixture to determine entity class
    def test_example(self, db, server, admin_a, team_a):
        self.db = db
        self._server = server  # Store server for access by helper methods
        self.ensure_model(server)  # Automatically determines sqlalchemy_model
```

### Scalability Opt-In
```python
from serverframework.lib.Scalability import ScalabilityProfile, ScalingMetric

class TestEntityName(AbstractDBTest):
    create_fields = {...}
    scalability_profile = ScalabilityProfile.default(
        n_values=[5, 15, 50],
        metrics=[ScalingMetric.TIME, ScalingMetric.QUERY_COUNT],
    )
```
The inherited `test_scalability_list_n_factor` seeds N entities at each step and asserts `sqlalchemy_model.list()` stays within the per-metric Big-O bound. Defaults: `k ≤ 1.4` for time/memory, `k ≤ 0.4` for query count (catches N+1).

### Parent Entity Pattern
```python
parent_entities = [
    ParentEntity(
        name="parent_entity", 
        foreign_key="parent_id", 
        test_class=TestParentEntity
    )
]
```

### Permission Testing
The system provides comprehensive permission testing utilities:

```python
# Grant permissions for testing
self.grant_permission(user_id, entity_id, PermissionType.VIEW)

# Verify permission enforcement
self.assert_permission_check(user_id, entity_id, PermissionType.EDIT, PermissionResult.DENIED)

# Test permission inheritance
self.verify_permission_checks(entity_id, allowed_users, denied_users)
```

### Test Isolation
- Transaction-based isolation for database tests
- Automatic cleanup of created entities
- Unique field generation to prevent conflicts
- Session management with proper rollback

### Mock Integration
- Comprehensive mocking of external dependencies
- Test-specific database models
- Permission system mocking for isolated testing
- Fake data generation with realistic patterns

## Test Configuration

### Test Dependencies
Tests are marked with pytest dependencies to ensure proper execution order:
```python
# @pytest.mark.dependency(depends=["test_CRUD_create"])
def test_CRUD_get(self, admin_a, team_a, return_type):
```

### Test Parameterization
Critical operations are tested across multiple return types:
```python
@pytest.mark.parametrize("return_type", ["dict", "db", "model"])
def test_CRUD_create(self, admin_a, team_a, return_type):
```

### Test Fixtures
Common fixtures provide consistent test environments:
- `admin_a`, `team_a` - Standard user and team entities
- Database sessions with proper isolation
- Clean up mechanisms for test data

## Error Handling & Edge Cases

### System ID Testing
- ROOT_ID bypass for all permission checks
- SYSTEM_ID special handling for system entities
- TEMPLATE_ID visibility rules
- Regular user restrictions

### Permission Edge Cases
- Expired permission handling
- Circular reference detection
- Missing reference validation
- Deep hierarchy limits

### Data Integrity
- Null value handling
- Invalid ID format processing
- Transaction rollback scenarios
- Constraint violation handling

## Integration & CI

### Test Execution
- Database tests run with transaction isolation
- Comprehensive test coverage across all entities
- Performance benchmarking for critical operations
- Multiple database backend testing (SQLite, PostgreSQL)

### Quality Assurance
- Automatic cleanup prevents test pollution
- Consistent test patterns across all entities
- Permission testing matrix validation
- Edge case coverage ensures robustness

This testing architecture ensures comprehensive coverage of the database layer with particular attention to permission enforcement, data integrity, and system security. The inheritance-based approach provides consistency while allowing entity-specific customization where needed.