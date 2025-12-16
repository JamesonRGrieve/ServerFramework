# Endpoint Testing Framework

This document covers endpoint-specific testing patterns and best practices.

> **Common Testing Resources**: For testing tools, commands, fixtures, and best practices shared across all layers, see [Framework.Test.md](../Framework.Test.md#testing-tools-and-framework).

## Overview

The `AbstractEPTest` class provides a complete testing suite for REST endpoints with:

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
class TestResourceEndpoints(AbstractEPTest):
    # Required
    base_endpoint = "resource"
    entity_name = "resource"
    required_fields = ["name"]
    string_field_to_update = "name"

    # Data generation
    create_fields = {
        "name": lambda: f"Test {faker.word()}",
        "description": "Test description",
    }
    update_fields = {"name": "Updated Resource"}

    # Optional
    supports_search = True
    searchable_fields = ["name", "description"]

    def create_payload(self, name=None, parent_ids=None, team_id=None,
                       minimal=False, invalid_data=False):
        if invalid_data:
            return {"invalid": "data"}
        payload = {k: (v() if callable(v) else v)
                   for k, v in self.create_fields.items()
                   if not minimal or k in self.required_fields}
        if name:
            payload["name"] = name
        return {self.entity_name: payload}
```

## Configuration Properties

### Core Properties

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `base_endpoint` | str | Yes | URL path segment (e.g., "resource") |
| `entity_name` | str | Yes | JSON key for entity (e.g., "resource") |
| `required_fields` | List[str] | Yes | Fields required for creation |
| `string_field_to_update` | str | Yes | Field used in update tests |
| `create_fields` | Dict | Yes | Field generators for creation |
| `update_fields` | Dict | No | Field values for updates |

### Entity Characteristics

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `system_entity` | bool | False | Requires API key for writes |
| `user_scoped` | bool | True | Resources specific to user |
| `team_scoped` | bool | False | Resources specific to team |
| `requires_admin` | bool | False | Admin-only operations |

### Search Configuration

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `supports_search` | bool | True | Enable search tests |
| `searchable_fields` | List[str] | ["name"] | Fields for search tests |
| `search_example_value` | str | None | Example search value |

## Parent Entity Configuration

For nested resources:

```python
class TestInvitationEndpoints(AbstractEPTest):
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
            test_class=lambda: TestTeamEndpoints,
        )
    ]

    # Control nesting per operation
    NESTING_CONFIG_OVERRIDES = {
        "LIST": 1,    # /v1/team/{team_id}/invitation
        "CREATE": 1,
        "SEARCH": 1,
    }
```

### ParentEntity Fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | str | Entity name (e.g., "team") |
| `foreign_key` | str | FK field (e.g., "team_id") |
| `nullable` | bool | Can parent be null? |
| `system` | bool | Is parent a system entity? |
| `path_level` | int | Nesting depth (1, 2, etc.) |
| `is_path` | bool | Include in URL path? |
| `test_class` | Callable | Parent test class for fixtures |

## Generated Tests

### CRUD Tests

| Test | Status | Description |
|------|--------|-------------|
| `test_POST_201_single` | 201 | Create single entity |
| `test_POST_201_batch` | 201 | Batch create |
| `test_POST_201_minimal` | 201 | Required fields only |
| `test_POST_400` | 400 | Invalid data |
| `test_POST_401` | 401 | No auth |
| `test_POST_403_system` | 403 | System entity, wrong auth |
| `test_POST_404_parent` | 404 | Nonexistent parent |
| `test_GET_200_single` | 200 | Get by ID |
| `test_GET_200_list` | 200 | List entities |
| `test_GET_200_fields` | 200 | Field projection |
| `test_GET_200_includes` | 200 | Include relations |
| `test_GET_200_pagination` | 200 | Paginated results |
| `test_GET_401` | 401 | No auth |
| `test_GET_404` | 404 | Nonexistent entity |
| `test_POST_200_search` | 200 | Search entities |
| `test_PUT_200_single` | 200 | Update entity |
| `test_PUT_200_batch` | 200 | Batch update |
| `test_PUT_400` | 400 | Invalid data |
| `test_PUT_401` | 401 | No auth |
| `test_PUT_404` | 404 | Nonexistent entity |
| `test_DELETE_204_single` | 204 | Delete entity |
| `test_DELETE_204_batch` | 204 | Batch delete |
| `test_DELETE_401` | 401 | No auth |
| `test_DELETE_404` | 404 | Nonexistent entity |

### GraphQL Tests

| Test | Description |
|------|-------------|
| `test_GQL_query_single` | Single entity query |
| `test_GQL_query_list` | List query with pagination |
| `test_GQL_query_fields` | Field selection |
| `test_GQL_query_nested` | Nested entity queries |
| `test_GQL_mutation_create` | Create mutation |
| `test_GQL_mutation_update` | Update mutation |
| `test_GQL_mutation_delete` | Delete mutation |
| `test_GQL_mutation_validation` | Input validation |

### Search Tests

Auto-tests operators by field type:
- **String**: `eq`, `inc`, `sw`, `ew`
- **Numeric**: `eq`, `neq`, `lt`, `gt`, `lteq`, `gteq`
- **Date**: `before`, `after`, `on`
- **Boolean**: `is_true`

## System Entity Testing

```python
class TestExtensionEndpoints(AbstractEPTest):
    base_endpoint = "extension"
    entity_name = "extension"
    system_entity = True  # Requires API key for writes

    create_fields = {
        "name": lambda: f"test_extension_{faker.uuid4()}",
        "version": "1.0.0",
    }
```

System entities:
- **Read Operations**: JWT auth
- **Write Operations**: API key auth (auto-detected)

## Skipping Tests

```python
from endpoints.AbstractEPTest import SkipThisTest, SkipReason

class TestResourceEndpoints(AbstractEPTest):
    _skip_tests = [
        SkipThisTest(
            name="test_POST_201_batch",
            reason=SkipReason.NOT_IMPLEMENTED,
            details="Batch creation not implemented",
            gh_issue_number=42,
        ),
        SkipThisTest(
            name="test_GQL_subscription",
            reason=SkipReason.FLAKY,
            details="Unstable in CI",
        ),
    ]
```

### Skip Reasons

| Reason | Use Case |
|--------|----------|
| `NOT_IMPLEMENTED` | Feature not built yet |
| `FLAKY` | Intermittent failures |
| `SLOW` | Performance-related skip |
| `ENVIRONMENT` | Environment-specific |
| `DEPRECATED` | Deprecated functionality |

## Test Fixtures

Standard fixtures available:

| Fixture | Description |
|---------|-------------|
| `server` | FastAPI test client |
| `admin_a`, `admin_b` | Admin user fixtures |
| `user_a`, `user_b` | Regular user fixtures |
| `team_a`, `team_b` | Team fixtures |
| `db` | Database session |

## Custom Assertions

```python
def _assert_entity_response(self, response_data: Dict, expected_data: Dict):
    entity = response_data[self.entity_name]
    assert "id" in entity
    assert "created_at" in entity
    # Add entity-specific assertions
    if "email" in expected_data:
        assert entity["email"] == expected_data["email"]
```

## Entity Variants

For parametrized testing:

```python
class EntityVariant(str, Enum):
    VALID = "valid"              # Standard valid entity
    MINIMAL = "minimal"          # Required fields only
    INVALID = "invalid"          # Invalid data structure
    NULL_PARENTS = "null_parents"     # Nullable parents = null
    NONEXISTENT_PARENTS = "nonexistent_parents"  # Bad parent IDs
    SYSTEM = "system"            # System entity variant
    OTHER_USER = "other_user"    # Cross-user access test
```

## Endpoint-Specific Best Practices

In addition to [common testing best practices](../Framework.Test.md#best-practices):

1. **Complete Configuration**: Specify all required endpoint configuration properties
2. **Parent Entities**: Configure correctly for nested resources
3. **Authentication Scenarios**: Test all auth types (JWT, API Key, unauthorized)
4. **HTTP Status Codes**: Verify proper status codes for all scenarios
5. **Field Projection**: Test field selection and filtering
6. **GraphQL Testing**: Test queries, mutations, and subscriptions if applicable
