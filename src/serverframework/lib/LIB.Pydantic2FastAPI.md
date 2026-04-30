# FastAPI Router Generation

## Overview
Router generation system providing automatic FastAPI router creation from BLL managers through the RouterMixin pattern, eliminating the need for manual endpoint file creation while maintaining flexibility for custom routes.

## Core Components

### RouterMixin (`RouterMixin`)
Mixin class providing router generation functionality for BLL managers with declarative configuration through class variables.

**Configuration ClassVars:**
- **prefix**: Router URL prefix
- **tags**: OpenAPI tags for documentation
- **auth_type**: Default authentication type
- **routes_to_register**: Specific routes to generate
- **route_auth_overrides**: Per-route authentication overrides
- **custom_routes**: Additional route definitions
- **nested_resources**: Child resource configurations
- **example_overrides**: Custom OpenAPI examples

**Manager Factory Configuration:**
- **factory_params**: Parameters for manager instantiation
- **auth_dependency**: Authentication dependency override
- **requires_root_access**: Root access requirement flag

**Model Integration Pattern:**
Each Pydantic model class defines its associated manager through a ClassVar:
```python
class UserModel(BaseModel):
    # Model fields...
    Manager: ClassVar[Optional[Type]] = None

# After manager class definition:
UserModel.Manager = UserManager
```

### Authentication System

#### AuthType Enumeration
Standardized authentication types for consistent security implementation.

**Authentication Types:**
- **NONE**: Public endpoints without authentication
- **JWT**: JSON Web Token authentication for user operations
- **API_KEY**: API key authentication for system operations
- **BASIC**: Basic authentication for login endpoints

#### Authentication Integration
Automatic authentication dependency injection with route-specific overrides.

**Features:**
- Default authentication per router
- Per-route authentication overrides
- System entity auto-configuration
- Custom authentication dependencies

### Static Route Decorator

#### Route Definition
Decorator system for defining static routes on extension methods.

**Decorator Features:**
- Method specification (GET, POST, PUT, DELETE)
- Path definition with parameters
- Authentication type override
- Response model specification
- OpenAPI documentation integration

**Usage Patterns:**
- Extension status endpoints
- Configuration endpoints
- Health check endpoints
- Administrative operations

### Router Generation Process

#### Manager Discovery
Direct manager access through Model.Manager ClassVar pattern for explicit model-manager relationships.

**Discovery Features:**
- Model.Manager ClassVar access
- Manager class validation
- RouterMixin inheritance verification
- Router method availability check

#### Route Creation
Dynamic route generation based on manager capabilities and configuration.

**Generated Routes:**
- CRUD operations (create, read, update, delete)
- Search and filtering operations
- Batch operations
- Custom method routes

#### Authentication Assignment
Intelligent authentication assignment based on entity type and operation sensitivity.

**Assignment Rules:**
- System entities use API key authentication for write operations
- User entities use JWT authentication by default
- Public operations can override to no authentication
- Custom overrides take precedence

### Manager Factory Pattern

#### Factory Function Generation
Dynamic creation of manager factory functions with proper dependency injection.

**Factory Features:**
- Authentication dependency injection
- Parameter extraction from request
- Manager instantiation with context
- Error handling and validation

#### Dependency Management
Automatic dependency resolution for manager instantiation.

**Dependencies:**
- User authentication context
- Database session management
- Model registry access
- Extension configuration

### Query Parameter Handling
- `create_query_model_dependency()` materializes network query models directly from the request query string.
- Supports repeated parameters (`?include=a&include=b`) and CSV lists (`?fields=id,name`) while respecting field aliases.
- Automatically normalizes list-compatible annotations and preserves primitive defaults for scalar-only fields.
- Field projections run after response model validation to guarantee DTO integrity while trimming payloads to requested fields and preserving explicitly included relationships.

### Custom Route Support

#### Static Route Integration
Support for custom static routes defined through decorators.

**Features:**
- Method and path specification
- Authentication override capability
- Response model integration
- OpenAPI documentation

#### Dynamic Route Addition
Runtime addition of custom routes to generated routers.

**Methods:**
- Direct FastAPI router decoration
- Helper method registration
- Bulk route addition
- Route modification

### System Integration

#### Model Registry Integration
Deep integration with ModelRegistry for consistent model handling.

**Integration Features:**
- Model binding validation
- Schema generation coordination
- Database manager access
- Extension model support

#### Extension System Integration
Support for extension-provided routes and authentication.

**Extension Features:**
- Extension-specific routes
- Custom authentication providers
- Route inheritance patterns
- Dynamic route discovery

### Metadata Extraction

#### Manager Metadata Discovery
Automatic extraction of configuration from BLL manager classes through introspection.

**Extraction Process:**
- Model class property inspection
- Endpoint configuration detection
- System entity status identification
- Nested resource auto-discovery

**Auto-Discovery Rules:**
- Properties ending with 's' (plural) considered as potential nested resources
- Common patterns recognized: metadata, session, team, credential, invitation, permission, role
- Internal properties explicitly skipped: extensions, instances, rotations, abilities, users, provider_instances
- Manual configuration always takes precedence over auto-discovery

### Router Organization

#### Nested Router Support
Hierarchical router organization for complex resource relationships.

**Features:**
- Parent-child resource mapping
- Parameter inheritance
- Path prefix management
- Authentication propagation

#### Router Composition
Composition of multiple routers into unified API structures.

**Composition Features:**
- Multiple router inclusion
- Prefix management
- Tag organization
- Documentation coordination

## Advanced Features

### Documentation Generation
Automatic OpenAPI documentation generation with example data.

**Documentation Features:**
- Route summaries and descriptions
- Request/response examples
- Authentication requirements
- Error response documentation

### Example Generation (`ExampleGenerator`)
Intelligent example generation system based on model fields and patterns for enhanced OpenAPI documentation.

#### Core Features
- **Smart Field Recognition**: Analyzes field names and types to generate appropriate examples
- **Type System Support**: Handles strings, integers, floats, booleans, dates, Optional types, Lists, and Dicts
- **Faker Integration**: Uses Faker library for realistic fake data generation with configurable patterns
- **Caching System**: Caches generated examples by model's fully qualified name for performance
- **Pattern Recognition**: 40+ field name patterns for intelligent value generation
- **Operation Examples**: Generates complete example sets for all CRUD and batch operations
- **Customization Support**: Allows overriding generated examples with custom values and dot-notation paths
- **Search Optimization**: Automatically filters search examples to relevant fields only
- **Batch Example Generation**: Supports batch operations with proper target_ids arrays

#### Field Pattern Recognition
The system recognizes field name patterns using regex matching:

| Field Pattern | Generated Example |
|---------------|-------------------|
| `id`, `*_id` | UUID strings (e.g., `f47ac10b-58cc-4372-a567-0e02b2c3d479`) |
| `*first_name*` | First names (e.g., `John`) |
| `*last_name*` | Last names (e.g., `Doe`) |
| `*user_name*` | Usernames (e.g., `john_doe`) |
| `*display_name*` | Full names (e.g., `John Doe`) |
| `*company_name*` | Company names (e.g., `TechCorp Inc.`) |
| `description` | Multi-sentence paragraphs |
| `content` | Longer paragraph content |
| `*path*` | File paths (`/path/to/file.txt`) or relative paths |
| `*url*` | HTTPS URLs (e.g., `https://example.com`) |
| `role*` | Role names (`admin`, `user`, `owner`, `editor`, `viewer`) |
| `email` | Email addresses (e.g., `user@example.com`) |
| `phone` | Phone numbers |
| `address` | Full addresses |
| `*city*`, `*state*`, `*country*` | Geographic locations |
| `*zip*`, `*postal*` | Postal codes |
| `status` | Status values (`active`, `inactive`, `pending`, `completed`) |
| `type` | Type identifiers (`standard`, `premium`, `basic`, `advanced`) |
| `category` | Categories (`general`, `specific`, `important`, `urgent`) |
| `priority` | Priorities (`low`, `medium`, `high`, `critical`) |
| `code` | Alphanumeric codes (e.g., `ABC123`) |
| `token` | Token strings (e.g., `tk-abcdefgh`) |
| `*api_key*` | API key strings (e.g., `ak-1234567890abcdef`) |
| `*secret*` | Secret strings (32-character passwords) |
| `version` | Version strings (e.g., `1.2.3`) |
| `*date*`, `*_at` | ISO date/datetime strings |
| `price`, `amount` | Monetary values with appropriate ranges |
| `age` | Ages (18-80 range) |
| Boolean `is_*`, `has_*` | `true` |
| Boolean `*active*`, `*enabled*` | `true` |
| Boolean `*verified*`, `*confirmed*` | `true` |

#### Usage Examples

**Basic Model Example Generation:**
```python
from pydantic import BaseModel
from serverframework.lib.Pydantic2FastAPI import ExampleGenerator

class UserModel(BaseModel):
    id: str
    email: str
    first_name: str
    last_name: str
    is_active: bool
    created_at: datetime

# Generate example
example = ExampleGenerator.generate_example_for_model(UserModel)
# Result: {
#     'id': 'f47ac10b-58cc-4372-a567-0e02b2c3d479',
#     'email': 'user@example.com', 
#     'first_name': 'John',
#     'last_name': 'Doe',
#     'is_active': True,
#     'created_at': '2024-03-15T14:30:00'
# }
```

**Operation Examples Generation:**
```python
# For a complete NetworkModel class
examples = ExampleGenerator.generate_operation_examples(
    UserNetworkModel, "user"
)

# Access specific operation examples
create_example = examples["create"]       # POST operation
get_example = examples["get"]            # GET by ID
list_example = examples["list"]          # GET list
update_example = examples["update"]      # PUT operation  
search_example = examples["search"]      # POST search
batch_update = examples["batch_update"]  # PUT batch
batch_delete = examples["batch_delete"]  # DELETE batch
```

**Example Customization:**
```python
# Generate base example
example = ExampleGenerator.generate_example_for_model(UserModel)

# Apply customizations
customized = ExampleGenerator.customize_example(
    example,
    {
        "email": "custom@example.com",
        "first_name": "Custom",
    }
)

# Nested field customization with dot notation
nested_customization = ExampleGenerator.customize_example(
    {"user": {"settings": {"theme": "light"}}},
    {
        "user.settings.theme": "dark",
        "user.settings.language": "en"
    }
)
```

#### Integration with RouterMixin
Examples are automatically generated and integrated into OpenAPI documentation:

```python
class UserManager(RouterMixin, AbstractLogicManager):
    prefix: ClassVar[str] = "/v1/user"
    tags: ClassVar[List[str]] = ["User Management"]
    
    # Custom example overrides (optional)
    example_overrides: ClassVar[Dict[str, Dict]] = {
        "get": {"user": {"email": "demo@example.com"}},
        "create": {"user": {"first_name": "Demo User"}},
    }
```

#### Generated Operation Examples Structure
For a resource named `user`, the system generates:

```python
{
    "get": {
        "user": {/* single user example */}
    },
    "list": {
        "users": [{/* user example */}]
    },
    "create": {
        "user": {/* creation fields example */}
    },
    "update": {
        "user": {/* update fields example */}
    },
    "search": {
        "user": {/* filtered search fields */}
    },
    "batch_update": {
        "user": {/* update fields */},
        "target_ids": ["uuid1", "uuid2"]
    },
    "batch_delete": {
        "target_ids": ["uuid1", "uuid2"]
    }
}
```

#### Performance and Caching
- **Caching Mechanism**: Examples are cached by model's fully qualified name
- **Cache Management**: `ExampleGenerator.clear_cache()` for manual clearing
- **Performance Benefits**: First analysis performs model introspection, subsequent calls use cache
- **Memory Efficient**: Only caches final examples, not intermediate processing

### Error Handling
Consistent error handling across generated routes.

**Error Features:**
- Standard HTTP status codes
- Structured error responses
- Exception type mapping
- Validation error handling

## Usage Patterns

### Basic Router Generation
1. Inherit RouterMixin in BLL manager
2. Configure class variables
3. Implement Router classmethod
4. Set Model.Manager ClassVar on model class
5. Register with model registry

### Custom Route Addition
1. Define static route methods
2. Apply route decorators
3. Configure authentication
4. Document endpoints

### Authentication Configuration
1. Set default authentication type
2. Configure route overrides
3. Handle system entities
4. Implement custom dependencies

## Best Practices

1. **Configuration Management**: Use class variables for declarative router configuration
2. **Authentication Strategy**: JWT for users, API keys for system operations
3. **Route Organization**: Logical grouping with appropriate prefixes and tags
4. **Documentation**: Comprehensive examples and descriptions
5. **Error Handling**: Consistent error responses across routes
6. **Security**: Proper authentication and authorization implementation
7. **Testing**: Integration with testing framework for route validation
8. **Example Generation**: 
   - Use descriptive field names that follow pattern recognition conventions
   - Provide explicit examples for domain-specific fields using Pydantic `Field` metadata
   - Leverage `example_overrides` ClassVar for custom business logic examples
   - Clear cache periodically in long-running applications after model changes
   - Use `ExampleGenerator.add_field_generator()` for application-specific patterns
