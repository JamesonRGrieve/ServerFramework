# GraphQL Testing Framework

> **Common testing resources:** [Framework.Test.md](../Framework.Test.md) | **Endpoint testing:** [EP.Test.md](EP.Test.md)

## Overview

The GraphQL testing framework provides comprehensive testing for GraphQL queries, mutations, and subscriptions using the `GraphQLTestMixin` class. Tests are parameterized to cover all GraphQL operation types automatically.

## GraphQLTestMixin

Located in `src/extensions/AbstractPRVTest.py`, this mixin provides GraphQL testing capabilities that can be combined with other test base classes.

### Configuration

```python
from serverframework.extensions.AbstractPRVTest import GraphQLTestMixin, GraphQLTestConfig, GraphQLTestType

class TestEntityGraphQL(GraphQLTestMixin, AbstractEPTest):
    graphql_config = GraphQLTestConfig(
        test_types={
            GraphQLTestType.QUERY_SINGLE,
            GraphQLTestType.QUERY_LIST,
            GraphQLTestType.MUTATION_CREATE,
            GraphQLTestType.MUTATION_UPDATE,
            GraphQLTestType.MUTATION_DELETE,
        },
        external_model_class=EntityModel,
        external_manager_class=EntityManager,
        external_entity_name="entity",
        external_string_field="name",
        external_graphql_fields=["id", "createdAt", "updatedAt"],
        supports_mutations=True,
        supports_subscriptions=True,
        supports_navigation=False,
    )
```

### GraphQLTestConfig Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `test_types` | `Set[GraphQLTestType]` | All types | Which test types to run |
| `external_model_class` | `Type` | None | The BLL model class |
| `external_manager_class` | `Type` | None | The BLL manager class |
| `external_entity_name` | `str` | None | GraphQL entity name |
| `external_string_field` | `str` | `"name"` | Field used in test data |
| `external_graphql_fields` | `List[str]` | `["id", "createdAt", "updatedAt"]` | Fields to query |
| `supports_mutations` | `bool` | `True` | Enable mutation tests |
| `supports_subscriptions` | `bool` | `True` | Enable subscription tests |
| `supports_navigation` | `bool` | `False` | Enable navigation property tests |

## GraphQL Test Types

### GraphQLTestType Enum

| Type | Description |
|------|-------------|
| `QUERY_SINGLE` | Test single entity query by ID |
| `QUERY_LIST` | Test list query for multiple entities |
| `MUTATION_CREATE` | Test create mutation |
| `MUTATION_UPDATE` | Test update mutation |
| `MUTATION_DELETE` | Test delete mutation |
| `SUBSCRIPTION` | Test real-time subscriptions |
| `NAVIGATION` | Test navigation properties (relationships) |

## Auto-Generated Tests

The mixin provides a single parametrized test method that covers all configured test types:

```python
@pytest.mark.parametrize("test_type", sorted([
    GraphQLTestType.QUERY_SINGLE,
    GraphQLTestType.QUERY_LIST,
    GraphQLTestType.MUTATION_CREATE,
    GraphQLTestType.MUTATION_UPDATE,
    GraphQLTestType.MUTATION_DELETE,
    GraphQLTestType.SUBSCRIPTION,
    GraphQLTestType.NAVIGATION,
]))
def test_graphql_functionality(self, extension_server, test_type: GraphQLTestType):
    """Parameterized GraphQL tests."""
    # Automatically dispatches to appropriate test method
```

## Test Methods

### Query Tests

**`_test_query_single(extension_server)`**
```graphql
query {
    entity(id: "test_123") {
        id
        createdAt
        updatedAt
        name
    }
}
```

**`_test_query_list(extension_server)`**
```graphql
query {
    entities {
        id
        createdAt
        updatedAt
        name
    }
}
```

### Mutation Tests

**`_test_mutation_create(extension_server)`**
```graphql
mutation {
    createEntity(input: {name: "Test Entity"}) {
        id
        createdAt
        updatedAt
        name
    }
}
```

**`_test_mutation_update(extension_server)`**
```graphql
mutation {
    updateEntity(id: "test_123", input: {name: "Updated Entity"}) {
        id
        createdAt
        updatedAt
        name
    }
}
```

**`_test_mutation_delete(extension_server)`**
```graphql
mutation {
    deleteEntity(id: "test_123")
}
```

### Subscription Tests

**`_test_subscription(extension_server)`**
```graphql
subscription {
    entityCreated {
        id
        createdAt
        updatedAt
        name
    }
}
```

### Navigation Tests

**`_test_navigation(extension_server)`**

Tests schema introspection for relationship/navigation properties.

## Helper Methods

### Entity Name Helpers

```python
# Get singular entity name (camelCase)
entity_name = self._get_graphql_entity_name()  # "entity"

# Get plural entity name
entities_name = self._get_graphql_entity_name(plural=True)  # "entities"

# Get mutation name
mutation_name = self._get_mutation_name("create")  # "createEntity"
```

### Test Data Creation

```python
# Create test data with unique values
test_data = self._create_test_data()
# Returns: {"name": "External Test word"}
```

### Query Field Building

```python
# Build list of fields for GraphQL query
fields = self._build_query_fields(include_string_field=True)
# Returns: ["id", "name", "createdAt", "updatedAt"]
```

### GraphQL Execution

```python
def _execute_graphql_test(
    self,
    extension_server,
    query: str,
    operation_type: str,
    expected_status: List[int] = None
):
    """Execute GraphQL query and validate response."""
```

## Fixtures

GraphQL tests use the `extension_server` fixture which provides an isolated test server for extension testing:

| Fixture | Description |
|---------|-------------|
| `extension_server` | TestClient with extension-specific configuration |

## Example Implementation

```python
from serverframework.extensions.AbstractPRVTest import (
    GraphQLTestMixin,
    GraphQLTestConfig,
    GraphQLTestType,
)
from serverframework.endpoints.AbstractEPTest import AbstractEPTest


class TestUserGraphQL(GraphQLTestMixin, AbstractEPTest):
    """GraphQL tests for User entity."""

    base_endpoint = "user"
    entity_name = "user"

    graphql_config = GraphQLTestConfig(
        test_types={
            GraphQLTestType.QUERY_SINGLE,
            GraphQLTestType.QUERY_LIST,
            GraphQLTestType.MUTATION_CREATE,
            GraphQLTestType.MUTATION_UPDATE,
            GraphQLTestType.MUTATION_DELETE,
        },
        external_entity_name="user",
        external_string_field="email",
        external_graphql_fields=["id", "email", "displayName", "createdAt"],
        supports_mutations=True,
        supports_subscriptions=False,  # User subscriptions not implemented
    )

    create_fields = {
        "email": lambda: f"test_{faker.uuid4()}@example.com",
        "display_name": lambda: faker.user_name(),
    }
```

## Skipping GraphQL Tests

Tests are automatically skipped when:
1. The test type is not in `graphql_config.test_types`
2. Mutations are disabled via `supports_mutations=False`
3. Subscriptions are disabled via `supports_subscriptions=False`
4. Navigation is disabled via `supports_navigation=False`

```python
graphql_config = GraphQLTestConfig(
    test_types={
        GraphQLTestType.QUERY_SINGLE,
        GraphQLTestType.QUERY_LIST,
        # Mutation and subscription tests will be skipped
    },
    supports_mutations=False,
)
```

## Running GraphQL Tests

```bash
# Run all GraphQL tests
pytest -k "graphql" -v

# Run specific GraphQL test type
pytest -k "test_graphql_functionality[query_single]" -v

# Run GraphQL tests for specific entity
pytest src/endpoints/EP_User_test.py -k "graphql" -v
```

## Error Handling

The `_execute_graphql_test` method handles errors gracefully:
- Validates response status code (default: 200)
- Checks for `data` key in response
- Only fails on syntax/parse errors, not schema errors (expected in test fixtures)

```python
# Custom expected status codes
self._execute_graphql_test(
    extension_server,
    subscription_query,
    "subscription",
    expected_status=[200, 400]  # 400 acceptable for subscriptions
)
```
