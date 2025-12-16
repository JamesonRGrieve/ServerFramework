# GraphQL Integration

> **REST endpoints:** [EP.Abstraction.md](EP.Abstraction.md) | **Testing:** [EP.Test.md](EP.Test.md)

## Overview

GraphQL schemas are auto-generated from Pydantic BLL models. Works alongside REST endpoints from RouterMixin.

## Auto-Generated Operations

| Operation Type | Naming Pattern | Example |
|----------------|----------------|---------|
| Query (single) | `get_<resource>` | `get_project(id: "...")` |
| Query (list) | `<resources>` | `projects` |
| Create | `create_<resource>` | `create_project(input: {...})` |
| Update | `update_<resource>` | `update_project(id: "...", input: {...})` |
| Delete | `delete_<resource>` | `delete_project(id: "...")` |
| Subscription | `<resource>_created/updated/deleted` | `project_created` |

## Type Generation

Pydantic models → GraphQL types automatically:

| Pydantic | GraphQL |
|----------|---------|
| `str` | `String` |
| `int` | `Int` |
| `float` | `Float` |
| `bool` | `Boolean` |
| `datetime` | `DateTimeScalar` (ISO format) |
| `Optional[T]` | Nullable `T` |
| `List[T]` | `[T]` |
| Nested model | Referenced type |

## FastAPI Integration

```python
from strawberry.fastapi import GraphQLRouter

Query, Mutation, Subscription = build_dynamic_strawberry_types()
schema = strawberry.Schema(query=Query, mutation=Mutation, subscription=Subscription)

app.include_router(
    GraphQLRouter(schema=schema, graphiql=True),
    prefix="/graphql"
)
```

## Authentication

Same as REST - via `Authorization` header:

```python
# Extracted in resolver context
auth_header = request.headers.get("Authorization")
user = UserManager.auth(auth_header)
requester_id = user.id
```

## Subscriptions

Real-time via `broadcaster` library:

```python
# Auto-generated channels
project_created   # New projects
project_updated   # Modified projects
project_deleted   # Removed projects
```

**Client subscription:**
```graphql
subscription {
  project_created {
    id
    name
  }
}
```

## Example Queries

### Get Single

```graphql
query {
  get_project(id: "uuid") {
    id
    name
    tasks { id, status }
  }
}
```

### List

```graphql
query {
  projects {
    id
    name
  }
}
```

### Create

```graphql
mutation {
  create_project(input: {name: "New", description: "..."}) {
    id
    name
  }
}
```

### Update

```graphql
mutation {
  update_project(id: "uuid", input: {name: "Updated"}) {
    id
    name
  }
}
```

## Configuration

```python
# Increase recursion depth for deeply nested models (default: 3)
Query, Mutation, Subscription = build_dynamic_strawberry_types(max_recursion_depth=4)

# Enable camelCase field names
from strawberry.schema.config import StrawberryConfig
config = StrawberryConfig(auto_camel_case=True)
```

## Model Requirements

For GraphQL generation, BLL models need:

1. **Main model**: `ProjectModel` with type annotations
2. **Reference model**: `ProjectReferenceModel` (for relationships)
3. **Network model**: `ProjectNetworkModel` (for input types)
4. **Manager class**: `ProjectManager` with CRUD methods

## Debugging

| Issue | Check |
|-------|-------|
| Missing type | Model has proper type annotations |
| Circular reference error | Reduce `max_recursion_depth` or simplify relationships |
| Resolver error | Manager method exists and handles context |
| Subscription not firing | Broadcast channel name matches, broadcaster initialized |
| Auth failure | `Authorization` header present, valid JWT |
