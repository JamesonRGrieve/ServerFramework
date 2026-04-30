# Pydantic2Strawberry

This module provides automatic GraphQL schema generation from Pydantic models using Strawberry GraphQL.

## Overview

The `Pydantic2Strawberry` module dynamically generates GraphQL schemas from Pydantic models registered in the ModelRegistry. It creates complete CRUD operations (queries, mutations, subscriptions) for each model without requiring manual schema definitions.

## Key Components

### GraphQLManager

The main class that orchestrates schema generation with comprehensive error handling and relationship management:

- **`__init__(model_registry)`**: Initialize with a ModelRegistry instance and set up type registries
- **`create_schema()`**: Generate complete GraphQL schema from registered models with Query, Mutation, and Subscription types
- **`_generate_components_for_model(model_class, manager_class)`**: Generate all GraphQL components for a specific model with error isolation
- **`_convert_python_type_to_gql(python_type)`**: Convert Python types to GraphQL types with comprehensive type handling
- **`_analyze_model_relationships(model_class)`**: Analyze and register forward/reverse relationships between models
- **`_create_gql_type_from_model(model_class)`**: Create GraphQL output types with relationship navigation support
- **`_create_input_type_from_model(model_class, suffix)`**: Create GraphQL input types for mutations
- **`_create_filter_type_from_model(model_class)`**: Create filter input types for queries

### Type Conversion

The module handles various Python type conversions:

- **Basic Types**: str, int, float, bool, datetime, date
- **Complex Types**: Dict, List, Optional, Any
- **Enum Types**: Regular enums and string-based enums
- **Nested Models**: Pydantic models as GraphQL types

### Enum Handling

Special handling for enum types to ensure compatibility:

1. **String-based enums** (e.g., `class MyEnum(str, Enum)`) are converted to simple enum values
2. **IntEnum types** are converted to int type for GraphQL compatibility
3. **Regular enums** (e.g., `class MyEnum(Enum)`) preserve their values when possible
4. **Extension enums** get prefixed to avoid naming collisions
5. **Dict/non-type objects** are detected and converted to string type
6. **Problematic enums** fall back to string type with a warning logged

### Scalar Types

Custom scalar types for complex data:

- **DateTimeScalar**: ISO format datetime serialization
- **DateScalar**: ISO format date serialization
- **ANY_SCALAR**: JSON-serializable values
- **DICT_SCALAR**: JSON objects
- **LIST_SCALAR**: JSON arrays

## Generated Operations

For each model, the following operations are generated with authentication and context handling:

### Queries
- **`{modelName}(id: String!)`**: Get single item by ID (special user handling for self-queries)
- **`{modelNamePlural}(filter: FilterInput, limit: Int, offset: Int)`**: List items with filtering and pagination

### Mutations
- **`create{ModelName}(input: CreateInput!)`**: Create new item with automatic context injection
- **`update{ModelName}(id: String!, input: UpdateInput!)`**: Update existing item (special user handling for self-updates)
- **`delete{ModelName}(id: String!)`**: Delete item with broadcast events

### Subscriptions
- **`{modelName}Created`**: Subscribe to creation events via broadcaster
- **`{modelName}Updated`**: Subscribe to update events via broadcaster
- **`{modelName}Deleted`**: Subscribe to deletion events via broadcaster

### Authentication Handling
- All operations require authentication via JWT or API key
- User operations are restricted to self-access (users can only query/update themselves)
- Context extraction from GraphQL Info object with fallback authentication
- Root API key support for system-level operations

## Advanced Features

### Relationship Management
Automatic discovery and handling of model relationships:

- **Forward Relationships**: Many-to-one relationships via foreign key fields (e.g., `user_id` -> `user`)
- **Reverse Relationships**: One-to-many relationships with navigation properties
- **Relationship Analysis**: Automatic detection of relationships from field naming conventions
- **Navigation Resolvers**: Dynamic resolver creation for relationship traversal
- **Circular Dependency Handling**: Safe handling of circular model references

### Error Handling
Comprehensive error handling with graceful degradation:

- **Model-Level Isolation**: Failed models don't prevent schema generation for other models
- **Batch Operations**: Multiple operations grouped with error recovery
- **Type Registry**: Prevents duplicate type creation and infinite recursion
- **Fallback Types**: ANY_SCALAR fallback for problematic type conversions
- **Detailed Logging**: Comprehensive error reporting with module and model context

### Extension Support

The module handles extension models specially:

- Extension models that enhance existing types are skipped from schema generation
- Extension enums get prefixed with the extension name to avoid collisions
- Type names from extensions are prefixed to ensure uniqueness
- Extension-specific resolver handling

## Error Handling

The schema generation is resilient to individual model failures:

- Failed models are logged but don't stop the entire schema generation
- Enum conversion errors fall back to string type
- Complex types that can't be converted use ANY_SCALAR

## Usage Example

```python
from serverframework.lib.Pydantic import ModelRegistry
from serverframework.lib.Pydantic2Strawberry import GraphQLManager

# Create and populate model registry
registry = ModelRegistry()
registry.register_models(...)

# Generate GraphQL schema
graphql_manager = GraphQLManager(registry)
schema = graphql_manager.create_schema()

# Use with FastAPI/Strawberry
from strawberry.fastapi import GraphQLRouter
graphql_app = GraphQLRouter(schema)
```

## Recent Changes

### Enum Conversion Fix (2025-07-04)

Fixed issues with enum conversion that were causing GraphQL schema generation to fail:

- Added proper handling for string-based enums (e.g., `ConversationVisibility(str, Enum)`)
- Added try-catch wrapper around enum conversion to gracefully fall back to string type
- Improved error messages to identify which enums are causing issues
- String-based enums now convert their values to simple strings for GraphQL compatibility

This fix ensures that all enum types can be successfully converted to GraphQL, preventing schema generation failures while maintaining type safety where possible.