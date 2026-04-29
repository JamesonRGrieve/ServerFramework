# Endpoint Testing Framework

> **Common testing resources:** [Framework.Test.md](../Framework.Test.md) | **Endpoint architecture:** [EP.Abstraction.md](EP.Abstraction.md)

## Overview

`AbstractEPTest` provides automatic test generation for REST endpoints with CRUD, batch, auth, nesting, and GraphQL coverage.

## Basic Implementation

```python
class TestItemEndpoints(AbstractEPTest):
    base_endpoint = "item"
    entity_name = "item"
    required_fields = ["name"]
    string_field_to_update = "name"

    create_fields = {"name": lambda: f"Test {faker.word()}"}
    update_fields = {"name": "Updated"}
```

## Configuration Reference

| Property                 | Type        | Default    | Description                  |
| ------------------------ | ----------- | ---------- | ---------------------------- |
| `base_endpoint`          | `str`       | Required   | URL segment (e.g., `"item"`) |
| `entity_name`            | `str`       | Required   | Resource name in payloads    |
| `required_fields`        | `List[str]` | Required   | Fields for validation tests  |
| `string_field_to_update` | `str`       | `"name"`   | Field used in update tests   |
| `system_entity`          | `bool`      | `False`    | Requires API key for writes  |
| `user_scoped`            | `bool`      | `True`     | User-specific resources      |
| `team_scoped`            | `bool`      | `False`    | Team-specific resources      |
| `supports_search`        | `bool`      | `True`     | Enable search tests          |
| `searchable_fields`      | `List[str]` | `["name"]` | Fields for search tests      |
| `create_fields`          | `Dict`      | `{}`       | Field generators for create  |
| `update_fields`          | `Dict`      | `{}`       | Field values for update      |

## Auto-Generated Tests

### CRUD Tests

| Test                          | Status | Description                      |
| ----------------------------- | ------ | -------------------------------- |
| `test_POST_201`               | 201    | Create resource                  |
| `test_POST_201_minimal`       | 201    | Create with required fields only |
| `test_POST_201_batch`         | 201    | Batch create                     |
| `test_POST_400`               | 400    | Invalid data                     |
| `test_POST_401`               | 401    | No auth                          |
| `test_POST_403_system`        | 403    | System entity without API key    |
| `test_GET_200_id`             | 200    | Get by ID                        |
| `test_GET_200_list`           | 200    | List resources                   |
| `test_GET_200_fields`         | 200    | Field projection                 |
| `test_GET_200_includes`       | 200    | Relationship includes            |
| `test_GET_200_pagination`     | 200    | Paginated list                   |
| `test_GET_401`                | 401    | No auth                          |
| `test_GET_404_nonexistent`    | 404    | Invalid ID                       |
| `test_PUT_200`                | 200    | Update resource                  |
| `test_PUT_200_batch`          | 200    | Batch update                     |
| `test_PUT_400`                | 400    | Invalid data                     |
| `test_PUT_401`                | 401    | No auth                          |
| `test_PUT_404_nonexistent`    | 404    | Invalid ID                       |
| `test_DELETE_204`             | 204    | Delete resource                  |
| `test_DELETE_204_batch`       | 204    | Batch delete                     |
| `test_DELETE_401`             | 401    | No auth                          |
| `test_DELETE_404_nonexistent` | 404    | Invalid ID                       |

### Search Tests

`test_GET_200_search` auto-generates operator tests by field type:

| Field Type | Operators                               |
| ---------- | --------------------------------------- |
| String     | `eq`, `inc`, `sw`, `ew`                 |
| Numeric    | `eq`, `neq`, `lt`, `gt`, `lteq`, `gteq` |
| Date       | `before`, `after`, `on`                 |
| Boolean    | `is_true`                               |

### GraphQL Tests

| Test                       | Description            |
| -------------------------- | ---------------------- |
| `test_GQL_query_single`    | Single entity query    |
| `test_GQL_query_list`      | List query             |
| `test_GQL_query_fields`    | Field selection        |
| `test_GQL_query_nested`    | Nested relationships   |
| `test_GQL_mutation_create` | Create mutation        |
| `test_GQL_mutation_update` | Update mutation        |
| `test_GQL_mutation_delete` | Delete mutation        |
| `test_GQL_subscription`    | Real-time subscription |

### Scalability Tests

`test_scalability_GET_list_n_factor` is parametrized over `metric ∈ {TIME, QUERY_COUNT, MEMORY}` and skipped unless the subclass sets `scalability_profile`:

```python
from lib.Scalability import ScalabilityProfile, ScalingMetric

class TestItemEndpoints(AbstractEPTest):
    base_endpoint = "item"
    entity_name = "item"
    scalability_profile = ScalabilityProfile.default(
        n_values=[5, 15, 50],
        metrics=[ScalingMetric.TIME, ScalingMetric.QUERY_COUNT],
    )
```

Seeds N entities at each `n`, fires `GET /<base_endpoint>` with admin auth, and asserts the observed Big-O exponent stays within the per-metric threshold. Catches list endpoints that materialize per-row, serializers with O(n²) field walks, and `include=` resolvers that fan out one upstream call per item. See [LIB.Scalability.md](../lib/LIB.Scalability.md).

## Nested Resources

### ParentEntity Configuration

```python
class TestCommentEndpoints(AbstractEPTest):
    base_endpoint = "comment"
    entity_name = "comment"

    parent_entities = [
        ParentEntity(
            name="post",
            foreign_key="post_id",
            nullable=False,
            path_level=1,
            is_path=True,
            test_class=lambda: TestPostEndpoints
        )
    ]

    NESTING_CONFIG_OVERRIDES = {
        "LIST": 1,    # GET /posts/{post_id}/comments
        "CREATE": 1,  # POST /posts/{post_id}/comments
    }
```

### ParentEntity Fields

| Field         | Type       | Description                    |
| ------------- | ---------- | ------------------------------ |
| `name`        | `str`      | Parent entity name             |
| `foreign_key` | `str`      | FK field (e.g., `"post_id"`)   |
| `nullable`    | `bool`     | Can parent be null             |
| `path_level`  | `int`      | Nesting depth (1, 2, ...)      |
| `is_path`     | `bool`     | Include in URL path            |
| `test_class`  | `Callable` | Parent test class for fixtures |

## System Entity Testing

```python
class TestExtensionEndpoints(AbstractEPTest):
    base_endpoint = "extension"
    entity_name = "extension"
    system_entity = True  # Auto-tests API key auth for writes

    create_fields = {"name": lambda: f"ext_{faker.uuid4()}"}
```

System entities auto-test:
- **Read**: JWT auth
- **Write**: API key auth required
- **403**: JWT on write operations

## Skipping Tests

Tests can be skipped using the `_skip_tests` class attribute with `SkipThisTest` entries:

```python
_skip_tests = [
    SkipThisTest(
        name="test_GET_200_list_pagination",
        reason=SkipReason.NOT_IMPLEMENTED,
        details="Pagination not yet implemented",
    ),
    SkipThisTest(
        name="test_GET_200_filter",
        reason=SkipReason.NOT_IMPLEMENTED,
        details="Filtering not yet implemented",
    ),
]
```

**Pagination, Filtering, and Search-Pagination (Item 29 — closed-by-verification):**

Pagination and filtering are wired through the auto-generated CRUD layer for every `RouterMixin`-tagged manager. `test_GET_200_list_pagination`, `test_POST_200_search_pagination`, and `test_GET_200_filter` are **live tests in `AbstractEPTest`** — they run by default for every concrete EP test class. The `page` / `pageSize` parameters are accepted by `manager.list()` and translated to `limit` / `offset`; `filters` are accepted by both `manager.list()` and `manager.search()` and pass through to the DB layer. Field selection (`fields=`) coexists with pagination and filtering parameters.

Concrete subclasses opt out of these tests via `_skip_tests` only when the entity legitimately does not support a standard list endpoint (for example, `EP_Auth_test.UserModel` skips `test_GET_200_list_pagination` because users are not exposed via a standard global LIST for privacy / security reasons — that is per-entity policy, not a missing-feature gate).

| SkipReason        | When                      |
| ----------------- | ------------------------- |
| `NOT_IMPLEMENTED` | Feature not built         |
| `FLAKY`           | Unstable in CI            |
| `SLOW`            | Too slow for regular runs |
| `ENVIRONMENT`     | Env-specific issues       |
| `DEPRECATED`      | Being removed             |

## Fixtures

Standard fixtures (from conftest):

| Fixture              | Description         |
| -------------------- | ------------------- |
| `server`             | FastAPI test client |
| `admin_a`, `admin_b` | Admin users         |
| `user_a`, `user_b`   | Regular users       |
| `team_a`, `team_b`   | Teams               |
| `db`                 | Database session    |

## Custom Payload

```python
def create_payload(self, name=None, parent_ids=None, team_id=None,
                  minimal=False, invalid_data=False):
    if invalid_data:
        return {"invalid": "data"}

    payload = {}
    for field, value in self.create_fields.items():
        if minimal and field not in self.required_fields:
            continue
        payload[field] = value() if callable(value) else value

    if name:
        payload["name"] = name

    return {self.entity_name: payload}
```

## Custom Assertions

```python
def _assert_entity_response(self, response_data: Dict, expected_data: Dict):
    entity = response_data[self.entity_name]
    assert "id" in entity
    assert "created_at" in entity
    # Add custom assertions
```

## Test Dependencies

Tests use pytest dependency markers:

```python
@pytest.mark.dependency(depends=["test_POST_201"])
def test_GET_200_id(self, server, admin_a, team_a):
    # Depends on entity being created first
    pass
```
