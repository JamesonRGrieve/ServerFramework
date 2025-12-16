# Endpoint Testing Framework

This document covers endpoint-specific testing patterns and best practices.

> **Common Testing Resources**: For testing tools, commands, fixtures, and best practices shared across all layers, see [Framework.Test.md](../Framework.Test.md#testing-tools-and-framework).

## Overview

The `AbstractEndpointTest` class (note: actual implementation uses `AbstractEndpointTest`, not `AbstractEPTest`) provides a complete testing suite for REST endpoints with:

- **Automatic Test Generation**: Standard CRUD, batch, and error tests
- **Multi-Level Nesting**: Support for complex parent-child relationships  
- **Authentication Testing**: JWT, API Key, Basic auth, and unauthorized scenarios
- **GraphQL Integration**: Full GraphQL query, mutation, and subscription testing
- **Team/User Scoping**: Multi-tenant and user-specific resource validation
- **System Entity Support**: Special handling for system-level entities
- **Dependency Management**: Proper test execution order with pytest dependencies

## Basic Implementation

### Simple Entity Test

```python
class TestResourceEndpoints(AbstractEndpointTest):
    # Core configuration
    base_endpoint = "resource"
    entity_name = "resource"
    required_fields = ["name", "description"]
    string_field_to_update = "name"
    
    # Data generation
    create_fields = {
        "name": lambda: f"Test {faker.word()}",
        "description": "Test description"
    }
    update_fields = {
        "name": "Updated Resource",
        "description": "Updated description"
    }
    
    # Search configuration
    supports_search = True
    searchable_fields = ["name", "description"]
    search_example_value = "Test Resource"
    
    def create_payload(self, name=None, parent_ids=None, team_id=None, 
                      minimal=False, invalid_data=False):
        """Create test payload with proper data structure."""
        if invalid_data:
            return {"invalid": "data structure"}
            
        payload = {}
        for field, value in self.create_fields.items():
            if minimal and field not in self.required_fields:
                continue
            payload[field] = value() if callable(value) else value
            
        if name:
            payload["name"] = name
            
        return {self.entity_name: payload}
```

### System Entity Test

```python
class TestExtensionEndpoints(AbstractEndpointTest):
    base_endpoint = "extension"
    entity_name = "extension"
    system_entity = True  # Requires API key for writes
    
    create_fields = {
        "name": lambda: f"test_extension_{faker.uuid4()}",
        "version": "1.0.0",
        "entry_point": "test_extension.py"
    }
    
    # System entities automatically test API key auth
```

### Nested Entity Test

```python
class TestInvitationEndpoints(AbstractEndpointTest):
    base_endpoint = "invitation"
    entity_name = "invitation"
    team_scoped = True
    
    parent_entities = [
        ParentEntity(
            name="team",
            foreign_key="team_id",
            nullable=False,
            path_level=1,
            is_path=True,
            test_class=lambda: TestTeamEndpoints
        )
    ]
    
    # Nesting configuration for different operations
    NESTING_CONFIG_OVERRIDES = {
        "LIST": 1,    # /v1/team/{team_id}/invitation
        "CREATE": 1,  # POST /v1/team/{team_id}/invitation
        "SEARCH": 1,  # POST /v1/team/{team_id}/invitation/search
    }
    
    create_fields = {
        "email": lambda: faker.email(),
        "role": "member"
    }
```

## Parent Entity Configuration

### ParentEntity Model

```python
class ParentEntity(BaseModel):
    name: str                           # Entity name (e.g., "team")
    foreign_key: str                    # Foreign key field (e.g., "team_id")
    nullable: bool = False              # Whether parent can be null
    system: bool = False                # Whether parent is system entity
    path_level: Optional[int] = None    # Nesting level (1, 2, etc.)
    is_path: bool = False              # Whether included in URL path
    test_class: Any = None             # Parent test class for fixtures
```

### Nesting Levels

Control endpoint nesting for different operations:

```python
# Default: all operations at root level (/v1/entity)
DEFAULT_NESTING_CONFIG = {
    "LIST": 0,
    "CREATE": 0,
    "DETAIL": 0,
    "SEARCH": 0,
}

# Override specific operations for nesting
NESTING_CONFIG_OVERRIDES = {
    "LIST": 1,    # /v1/parent/{parent_id}/entity
    "CREATE": 1,  # POST /v1/parent/{parent_id}/entity
}
```

## Standard Test Coverage

### CRUD Operations

**Create Tests:**
- `test_POST_201()` - Standard creation
- `test_POST_201_minimal()` - Required fields only
- `test_POST_201_batch()` - Batch creation
- `test_POST_201_null_parents()` - Nullable parents
- `test_POST_400()` - Invalid data validation
- `test_POST_401()` - Unauthorized access
- `test_POST_403_system()` - System entity without API key
- `test_POST_404_nonexistent_parent()` - Nonexistent parent

**Read Tests:**
- `test_GET_200_id()` - Single entity retrieval
- `test_GET_200_list()` - Entity listing
- `test_GET_200_fields()` - Field selection
- `test_GET_200_includes()` - Related entity inclusion via the `include` query argument (single targets, CSV lists, and safe field projection combinations)
- `test_GET_200_pagination()` - Paginated results
- `test_GET_401()` - Unauthorized access
- `test_GET_404_nonexistent()` - Nonexistent entity
- `test_GET_404_other_user()` - Cross-user restrictions

**Update Tests:**
- `test_PUT_200()` - Standard update
- `test_PUT_200_batch()` - Batch updates
- `test_PUT_400()` - Invalid data
- `test_PUT_401()` - Unauthorized
- `test_PUT_404_nonexistent()` - Nonexistent entity
- `test_PUT_404_other_user()` - Cross-user restrictions

**Delete Tests:**
- `test_DELETE_204()` - Standard deletion
- `test_DELETE_204_batch()` - Batch deletion
- `test_DELETE_401()` - Unauthorized
- `test_DELETE_404_nonexistent()` - Nonexistent entity
- `test_DELETE_404_other_user()` - Cross-user restrictions

The helper layer automatically normalizes projection parameters so tests emit `include`/`fields` query strings in CSV form. Field-plus-include combinations are only generated for relationships whose Pydantic annotations resolve to concrete models, protecting scenarios where mixin-provided `Any` annotations would otherwise trigger server-side conversion errors.

### Search and Filtering

- `test_GET_200_search()` - Comprehensive POST-based search testing
  - Automatically tests all applicable operators for each field type
  - String fields: `eq`, `inc`, `sw`, `ew`
  - Numeric fields: `eq`, `neq`, `lt`, `gt`, `lteq`, `gteq`
  - Date fields: `before`, `after`, `on`
  - Boolean fields: `is_true`
- `test_GET_200_filter()` - Query parameter filtering

### Team and User Scoping

- `test_GET_200_list_via_parent_team()` - Team-scoped listing
- `test_GET_404_list_no_parent_team()` - Invalid team access
- `test_GET_200_id_via_parent_team()` - Team-scoped retrieval
- `test_GET_404_id_no_parent_team()` - Invalid team entity access

## GraphQL Testing

### Query Tests

- `test_GQL_query_single()` - Single entity queries
- `test_GQL_query_single_by_id_only()` - ID-only queries
- `test_GQL_query_list()` - List queries with pagination
- `test_GQL_query_fields()` - Field selection
- `test_GQL_query_nested()` - Nested entity queries

### Mutation Tests

- `test_GQL_mutation_create()` - Entity creation
- `test_GQL_mutation_update()` - Entity updates
- `test_GQL_mutation_delete()` - Entity deletion
- `test_GQL_mutation_validation()` - Input validation

### Subscription Tests

- `test_GQL_subscription()` - Real-time subscriptions

## Authentication Testing

### Auth Type Coverage

Tests validate all authentication scenarios:

```python
def _get_appropriate_headers(self, token: str) -> Dict[str, str]:
    """Get headers for the appropriate auth type."""
    if self.system_entity:
        # System entities use API key for write operations
        return {"Authorization": f"Bearer {env.API_KEY}"}
    else:
        # Regular entities use JWT
        return {"Authorization": f"Bearer {token}"}
```

### System Entity Testing

- **Read Operations**: JWT authentication
- **Write Operations**: API key authentication  
- **Unauthorized Tests**: No auth provided
- **Forbidden Tests**: Wrong auth type

## Test Configuration

### Core Properties

```python
# Entity identification
base_endpoint: str = None           # "resource"
entity_name: str = None            # "resource"
required_fields: List[str] = None  # ["name", "description"]
string_field_to_update: str = "name"

# Entity characteristics  
system_entity: bool = False        # Requires API key
user_scoped: bool = True          # User-specific resources
team_scoped: bool = False         # Team-specific resources
requires_admin: bool = False      # Admin-only operations

# Search configuration
supports_search: bool = True
searchable_fields: List[str] = ["name"]
search_example_value: str = None

# Data generation
create_fields: Dict[str, Any] = {}
update_fields: Dict[str, Any] = {}
unique_fields: List[str] = []
```

### Entity Variants

```python
class EntityVariant(str, Enum):
    VALID = "valid"                    # Standard valid entity
    MINIMAL = "minimal"                # Only required fields
    INVALID = "invalid"                # Invalid data structure
    NULL_PARENTS = "null_parents"      # Nullable parents set to null
    NONEXISTENT_PARENTS = "nonexistent_parents"  # Invalid parent IDs
    SYSTEM = "system"                  # System entity variant
    OTHER_USER = "other_user"          # Cross-user access test
```

## Test Execution

### Dependency Management

Tests use pytest dependency markers:

```python
@pytest.mark.dependency(depends=["test_POST_201"])
def test_GET_200_id(self, server, admin_a, team_a):
    """Test getting entity by ID (depends on creation)."""
    pass
```

### Fixture Usage

Standard fixtures across all tests:

- `server`: FastAPI test client
- `admin_a`, `admin_b`: Admin user fixtures
- `user_a`, `user_b`: Regular user fixtures
- `team_a`, `team_b`: Team fixtures
- `db`: Database session

### Parent Entity Auto-Detection

Framework automatically detects and uses parent entities:

```python
def test_with_team(self, server, admin_a, team_a):
    # team_a.id automatically used for team_id parameter
    pass
```

## Skip Test Management

### Structured Test Skipping

```python
_skip_tests = [
    SkipThisTest(
        name="test_POST_201_batch",
        reason=SkipReason.NOT_IMPLEMENTED,
        details="Batch creation not yet implemented for this entity",
        gh_issue_number=42
    ),
    SkipThisTest(
        name="test_GQL_subscription",
        reason=SkipReason.FLAKY,
        details="Subscription tests unstable in CI environment"
    )
]
```

### Skip Reasons

```python
class SkipReason(str, Enum):
    NOT_IMPLEMENTED = "not_implemented"
    FLAKY = "flaky"
    SLOW = "slow"
    ENVIRONMENT = "environment"
    DEPRECATED = "deprecated"
```

## Advanced Features

### Matrix Testing

For entities with multiple variations:

```python
class AbstractEndpointMatrixTest(AbstractEndpointTest):
    """Test matrix for multiple entity configurations."""
    
    @pytest.mark.parametrize("variant", [
        EntityVariant.VALID,
        EntityVariant.MINIMAL,
        EntityVariant.NULL_PARENTS
    ])
    def test_creation_variants(self, server, admin_a, team_a, variant):
        """Test creation with different data variants."""
        pass
```

### Custom Assertions

Add entity-specific validations:

```python
def _assert_entity_response(self, response_data: Dict, expected_data: Dict):
    """Custom assertions for entity response validation."""
    entity = response_data[self.entity_name]
    
    # Standard assertions
    assert "id" in entity
    assert "created_at" in entity
    assert "updated_at" in entity
    
    # Entity-specific assertions
    if "email" in expected_data:
        assert entity["email"] == expected_data["email"]
```

## Integration with RouterMixin

The testing framework validates router behavior generated by the `RouterMixin` pattern through:

- **Actual HTTP Requests**: Tests real endpoint behavior from auto-generated routes
- **Authentication Validation**: Matches manager ClassVar auth configuration  
- **Manager Method Testing**: Validates that manager methods work correctly with generated routes
- **Error Handling**: Verifies manager exception handling converts to proper HTTP responses
- **Documentation**: Validates OpenAPI schema generation from model registry
- **System Entity Detection**: Tests automatic API key auth for system entities

## Endpoint-Specific Best Practices

In addition to [common testing best practices](../Framework.Test.md#best-practices):

1. **Complete Configuration**: Specify all required endpoint configuration properties
2. **Parent Entities**: Configure correctly for nested resources
3. **Authentication Scenarios**: Test all auth types (JWT, API Key, unauthorized)
4. **HTTP Status Codes**: Verify proper status codes for all scenarios
5. **Field Projection**: Test field selection and filtering
6. **GraphQL Testing**: Test queries, mutations, and subscriptions if applicable
