# SDK Patterns

This document outlines SDK-specific design patterns and conventions.

> **Common Patterns**: For CRUD model patterns, error handling, and configuration patterns shared across all layers, see [Framework.md](../Framework.md#common-patterns-across-layers).

## Architecture

The SDK follows a modular design pattern with these key components:

- **AbstractSDKHandler**: Base class for all SDK handlers providing HTTP functionality
- **SDK**: Central entry point that instantiates and provides access to all modules
- **Module handlers**: Specialized classes for different API domains (Auth, Providers, Extensions, etc.)

```
SDK (main entry point)
├── AbstractSDKHandler (base functionality)
│   ├── AuthSDK (authentication and user management)
│   ├── ProvidersSDK (provider management)
│   └── ExtensionsSDK (extension management)
└── ... (other modules)
```

## Naming Conventions

- Main SDK class: `SDK`
- Module handlers: `<Module>SDK` (e.g., `AuthSDK`, `ProvidersSDK`)
- Test classes: `Test<Module>SDK` (e.g., `TestAuthSDK`)
- Exception classes: `<ExceptionType>Error` (e.g., `AuthenticationError`)

## Request Pattern

All API requests follow this common pattern:

```python
def some_action(self, param1, param2, **kwargs):
    """Docstring with clear description.
    
    Args:
        param1: Description
        param2: Description
        **kwargs: Additional parameters
        
    Returns:
        Description of return value
        
    Raises:
        ExceptionType: When this happens
    """
    endpoint = f"/v1/resource/{param1}"
    
    data = {
        "resource_name": {
            "field1": param1,
            "field2": param2,
            **kwargs
        }
    }
    
    return self.post(endpoint, data, resource_name="resource_name")
```

## Error Handling

The SDK uses specialized exception classes for different error types:

- `SDKException`: Base exception class for all SDK errors
- `AuthenticationError`: Authentication-related errors
- `ResourceNotFoundError`: When a requested resource doesn't exist
- `ValidationError`: For input validation errors
- `ResourceConflictError`: When creating a resource that already exists or other conflicts

Example error handling:

```python
try:
    result = sdk.auth.login(email="user@example.com", password="password")
except AuthenticationError as e:
    logger.debug(f"Authentication failed: {e.message}")
    logger.debug(f"Status code: {e.status_code}")
    logger.debug(f"Details: {e.details}")
except SDKException as e:
    logger.debug(f"General SDK error: {e.message}")
```

## Authentication

The SDK supports these authentication methods:

1. JWT token authentication:
   ```python
   sdk = SDK(base_url="https://api.example.com", token="your-jwt-token")
   ```

2. API key authentication:
   ```python
   sdk = SDK(base_url="https://api.example.com", api_key="your-api-key")
   ```

3. Login with credentials:
   ```python
   sdk = SDK(base_url="https://api.example.com")
   sdk.login(email="user@example.com", password="password")
   ```

## Response Format

All API responses follow a consistent format matching the backend API:

1. Single resource response:
   ```python
   {
       "resource_name": {
           "id": "...",
           "field1": "value1",
           "field2": "value2",
           ...
       }
   }
   ```

2. Multiple resources response:
   ```python
   {
       "resource_names": [
           {
               "id": "...",
               "field1": "value1",
               ...
           },
           ...
       ]
   }
   ```

## Resource Operations

For each resource type, the SDK typically provides these operations:

1. **Create**: `create_resource()`
2. **Get**: `get_resource()`
3. **List**: `list_resources()`
4. **Update**: `update_resource()`
5. **Delete**: `delete_resource()`
6. **Search**: `search_resources()`

Additionally, resources may have custom actions like:
- `install_extension()`
- `uninstall_extension()`
- `revoke_all_invitations()`

## Pagination

List operations support pagination parameters:

```python
results = sdk.module.list_resources(
    offset=0,    # Starting position
    limit=100,   # Maximum number of items
    sort_by="name",  # Field to sort by
    sort_order="asc"  # Sort direction
)
```

## Including Related Entities

Some endpoints support including related entities:

```python
user = sdk.auth.get_user(user_id="123", include=["teams", "roles"])
``` 